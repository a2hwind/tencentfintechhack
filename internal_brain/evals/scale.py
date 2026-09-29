"""Gate 1 at scale: build an index from a synthetic company through the production ingestion
path, then check every permission-filtered query against a brute-force oracle.

    built = asyncio.run(build(generate_company(users=1000, items=20_000), SyntheticEmbedder()))
    report = check_gate1(built, n_queries=200, seed=1)

The oracle is deliberately naive and independent of the code under test:
  keywords  the same FTS5 MATCH with no permission predicate and no LIMIT; filter the rows in
            Python by (item ACL tokens intersect the asker's tokens); keep the first k
  vectors   every stored vector (read from the chunk_vectors table, not the in-memory cache),
            dot product with the query, keep permitted rows, top k
Gate 1 must return exactly the oracle's top k (same scores; ids equal up to ties), and never
an item outside the asker's permissions. The same run also measures what a naive post-filter
(top k over everything, then drop what the asker cannot see) would have returned, which is
the failure mode that filtering inside the query avoids.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from ..core.embeddings import Embedder
from ..core.events import EventBus
from ..core.index import Filters, Index
from ..core.sync import SyncWorker
from ..mocks.store import CompanyStore
from .synthetic import adapters_for, native_items, topic_queries

PLATFORMS = ("confluence", "jira", "slack", "gdrive")


@dataclass
class Built:
    index: Index
    embedder: Embedder
    company: dict
    principals: dict[str, list[str]]  # Brain user id -> tokens
    acl: dict[str, frozenset[str]]  # item id -> tokens
    items: list[str]
    ingest_s: float
    chunks: int


async def build(company: dict, embedder: Embedder, db_path: str = ":memory:", progress=None) -> Built:
    """Fetch, DLP-mask, chunk, embed and index every item through the real adapters (over the
    mock APIs) and SyncWorker.fetch_and_index; resolve every user's tokens through the adapters."""
    store = CompanyStore(company)
    adapters, clients = adapters_for(store)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    index = Index(conn=conn)
    sync = SyncWorker(adapters, index, embedder, EventBus(), interval_s=0)
    items = [item_id for item_id, _, _ in native_items(store)]
    t0 = time.perf_counter()
    try:
        for n, item_id in enumerate(items):
            await sync.fetch_and_index(item_id)
            if progress and n % 1000 == 0:
                progress(n, len(items))
        ingest_s = time.perf_counter() - t0
        principals: dict[str, list[str]] = {}
        for user in store.users.values():
            tokens: set[str] = set()
            for platform, pid in user.platform_ids.items():
                tokens.update(await adapters[platform].principals_for(pid))
            principals[user.id] = sorted(tokens)
    finally:
        for client in clients:
            await client.aclose()
    acl: dict[str, set[str]] = {}
    for row in conn.execute("SELECT item_id, principal FROM item_principals"):
        acl.setdefault(row[0], set()).add(row[1])
    chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    return Built(index, embedder, company, principals, {k: frozenset(v) for k, v in acl.items()}, items, ingest_s, chunks)


# ---------------------------------------------------------------------------
# Oracles
# ---------------------------------------------------------------------------
def fts_all(index: Index, query: str, filters: Filters) -> list[tuple[str, str, float]]:
    """Every chunk the MATCH finds, best first, with NO permission predicate and no LIMIT."""
    match = index.fts_match(query)  # the same MATCH expression Gate 1 uses; only the permission filter differs
    if match is None:
        return []
    where, params = filters.sql()
    sql = (
        "SELECT chunks_fts.chunk_id, chunks_fts.item_id, bm25(chunks_fts) AS rank FROM chunks_fts JOIN items i ON i.item_id = chunks_fts.item_id "
        f"WHERE chunks_fts MATCH ? AND {where} ORDER BY rank"
    )
    with index.lock:
        rows = index.conn.execute(sql, [match, *params]).fetchall()
    return [(r[0], r[1], -float(r[2])) for r in rows]


class VectorOracle:
    """All vectors straight from the durable table, independent of the in-memory cache."""

    def __init__(self, index: Index):
        with index.lock:
            rows = index.conn.execute(
                "SELECT v.chunk_id, v.item_id, v.vec, i.platform, i.last_modified, i.container, i.container_label "
                "FROM chunk_vectors v JOIN items i ON i.item_id = v.item_id"
            ).fetchall()
        self.chunk_ids = [r[0] for r in rows]
        self.item_ids = [r[1] for r in rows]
        self.matrix = np.stack([np.frombuffer(r[2], dtype=np.float32) for r in rows]) if rows else np.zeros((0, 1), np.float32)
        self.platform = [r[3] for r in rows]
        self.modified = [r[4] for r in rows]
        self.container = [(r[5] or "").lower() for r in rows]
        self.label = [(r[6] or "").lower() for r in rows]

    def _passes(self, row: int, filters: Filters) -> bool:
        if filters.platform and self.platform[row] != filters.platform:
            return False
        if filters.since and self.modified[row] < filters.since:
            return False
        if filters.container:
            hint = filters.container.lower().lstrip("#")
            if not (self.label[row] in (hint, "#" + hint) or self.container[row].endswith(":" + hint)):
                return False
        return True

    def all_scores(self, query_vec: np.ndarray, filters: Filters) -> list[tuple[str, str, float]]:
        scores = self.matrix @ np.asarray(query_vec, dtype=np.float32)
        order = np.argsort(-scores, kind="stable")
        return [(self.chunk_ids[i], self.item_ids[i], float(scores[i])) for i in order if self._passes(int(i), filters)]


def _top_permitted(rows: list[tuple[str, str, float]], tokens: frozenset[str], acl: dict[str, frozenset[str]], k: int, floor: float | None = None) -> list[tuple[str, str, float]]:
    out = []
    for chunk_id, item_id, score in rows:
        if floor is not None and score <= floor:
            continue
        if acl.get(item_id, frozenset()) & tokens:
            out.append((chunk_id, item_id, score))
            if len(out) == k:
                break
    return out


def same_top_k(got: list[tuple[str, float]], want: list[tuple[str, str, float]], tol: float = 1e-5) -> bool:
    """Equal scores rank by rank, and every returned id is one the oracle could have returned
    (ties at the k-th score may resolve either way)."""
    if len(got) != len(want):
        return False
    if any(abs(g[1] - w[2]) > tol for g, w in zip(got, want)):
        return False
    if not want:
        return True
    kth = want[-1][2]
    allowed = {w[0] for w in want} | set()
    return all(g[0] in allowed or abs(g[1] - kth) <= tol for g in got)


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------
@dataclass
class Gate1Report:
    queries: int = 0
    fts_mismatches: list[dict] = field(default_factory=list)
    vec_mismatches: list[dict] = field(default_factory=list)
    unpermitted_returned: int = 0
    fts_ms: list[float] = field(default_factory=list)
    vec_ms: list[float] = field(default_factory=list)
    retrieve_ms: list[float] = field(default_factory=list)
    token_counts: list[int] = field(default_factory=list)
    postfilter_recall: list[float] = field(default_factory=list)  # |post-filtered top k| / |true permitted top k|
    postfilter_short: int = 0  # queries where post-filtering returned fewer results than exist

    def as_dict(self) -> dict:
        def pct(values: list[float], q: float) -> float:
            return round(float(np.percentile(values, q)), 3) if values else 0.0

        return {
            "queries": self.queries,
            "fts_mismatches": len(self.fts_mismatches),
            "vector_mismatches": len(self.vec_mismatches),
            "unpermitted_returned": self.unpermitted_returned,
            "fts_ms": {"p50": pct(self.fts_ms, 50), "p95": pct(self.fts_ms, 95)},
            "vector_ms": {"p50": pct(self.vec_ms, 50), "p95": pct(self.vec_ms, 95)},
            "retrieve_ms": {"p50": pct(self.retrieve_ms, 50), "p95": pct(self.retrieve_ms, 95)},
            "tokens_per_user": {"median": statistics.median(self.token_counts) if self.token_counts else 0, "max": max(self.token_counts or [0])},
            "postfilter_recall_mean": round(statistics.mean(self.postfilter_recall), 3) if self.postfilter_recall else 1.0,
            "postfilter_short_queries": self.postfilter_short,
        }


def check_gate1(built: Built, n_queries: int = 100, seed: int = 1, k: int = 20, retrieve: bool = True) -> Gate1Report:
    from ..config import Settings
    from ..core.planner import fallback_plan
    from ..core.retrieve import Retriever
    from ..models import PrincipalSet

    rng = random.Random(seed)
    oracle = VectorOracle(built.index)
    labels = sorted({label for label in oracle.label if label})
    report = Gate1Report()
    users = sorted(built.principals)
    queries = topic_queries(built.company, rng, n_queries)
    min_sim = built.embedder.min_similarity
    retriever = Retriever(built.index, built.embedder, Settings(data_dir="/tmp/unused-scale")) if retrieve else None
    now = datetime.now(timezone.utc)
    for n, query in enumerate(queries):
        user = rng.choice(users)
        tokens = built.principals[user]
        token_set = frozenset(tokens)
        report.token_counts.append(len(tokens))
        filters = Filters(
            platform=rng.choice([None, None, *PLATFORMS]),
            since=(now - timedelta(days=rng.choice([30, 90, 365]))).replace(microsecond=0).isoformat().replace("+00:00", "Z") if rng.random() < 0.3 else None,
            container=rng.choice(labels) if labels and rng.random() < 0.15 else None,
        )
        report.queries += 1

        t0 = time.perf_counter()
        fts = built.index.search_fts(query, tokens, filters, k)
        report.fts_ms.append((time.perf_counter() - t0) * 1000)
        everything = fts_all(built.index, query, filters)
        want = _top_permitted(everything, token_set, built.acl, k)
        if not same_top_k([(h.chunk_id, h.score) for h in fts], want):
            report.fts_mismatches.append({"query": query, "user": user, "filters": filters.__dict__, "got": [h.chunk_id for h in fts][:5], "want": [w[0] for w in want][:5]})
        report.unpermitted_returned += sum(1 for h in fts if not (built.acl.get(h.item_id, frozenset()) & token_set))
        naive = [row for row in everything[:k] if built.acl.get(row[1], frozenset()) & token_set]
        if want:
            report.postfilter_recall.append(len(naive) / len(want))
            report.postfilter_short += int(len(naive) < len(want))

        qvec = built.embedder.embed([query])[0]
        t0 = time.perf_counter()
        vec = built.index.search_vec(qvec, tokens, filters, k, min_similarity=min_sim)
        report.vec_ms.append((time.perf_counter() - t0) * 1000)
        want_v = _top_permitted(oracle.all_scores(qvec, filters), token_set, built.acl, k, floor=min_sim)
        if not same_top_k([(h.chunk_id, h.score) for h in vec], want_v):
            report.vec_mismatches.append({"query": query, "user": user, "filters": filters.__dict__, "got": [h.chunk_id for h in vec][:5], "want": [w[0] for w in want_v][:5]})
        report.unpermitted_returned += sum(1 for h in vec if not (built.acl.get(h.item_id, frozenset()) & token_set))

        if retriever is not None and n % 2 == 0:
            principals = PrincipalSet(user_id=user, tokens=tokens, platform_user_ids={}, resolved_at="")
            t0 = time.perf_counter()
            retrieval = retriever.retrieve(fallback_plan(query), principals)
            report.retrieve_ms.append((time.perf_counter() - t0) * 1000)
            report.unpermitted_returned += sum(1 for c in retrieval.candidates if not (built.acl.get(c.item.item_id, frozenset()) & token_set))
    return report


def build_sync(company: dict, embedder: Embedder, **kwargs) -> Built:
    return asyncio.run(build(company, embedder, **kwargs))
