"""Retrieve under filter, then expand links: Gate 1 in every query.

Per sub-query, keyword search (FTS5/BM25) and embedding search both run with the ACL
predicate inside the index query. Results are merged with reciprocal rank fusion across
retrievers and platforms, which needs no tuning. Top items are expanded one hop along
their explicit links, and every expanded item passes Gate 1 on its own.

The retriever also asks the index for an audit-only shadow list: items that matched but
that the asker's principals do not permit. Those never enter the candidate list; they
are recorded as deny:not_member so the audit trail is complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import Settings
from ..models import Decision, Plan, PrincipalSet
from .embeddings import Embedder
from .index import Filters, Hit, Index, IndexedItem, normalize_iso

RRF_K = 60


@dataclass
class ChunkHit:
    chunk_id: str
    text: str
    sha256: str
    score: float


@dataclass
class Candidate:
    item: IndexedItem
    chunks: list[ChunkHit]
    score: float
    rule: str  # the principal token that granted access at Gate 1
    source: str = "retrieval"  # retrieval | expansion
    expanded_from: str | None = None


@dataclass
class Retrieval:
    candidates: list[Candidate] = field(default_factory=list)
    denied: list[Decision] = field(default_factory=list)  # audit-only Gate 1 denials


def _ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def rrf(lists: list[list[Hit]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for hits in lists:
        for rank, hit in enumerate(hits):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


class Retriever:
    def __init__(self, index: Index, embedder: Embedder, settings: Settings):
        self.index = index
        self.embedder = embedder
        self.settings = settings

    @staticmethod
    def _since(window_days: int | None) -> str | None:
        if not window_days:
            return None
        return normalize_iso((datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat())

    def retrieve(self, plan: Plan, principals: PrincipalSet) -> Retrieval:
        tokens = list(principals.tokens)
        k = self.settings.top_k_per_retriever
        ranked_lists: list[list[Hit]] = []
        chunk_items: dict[str, str] = {}
        shadow: dict[str, IndexedItem] = {}

        for sq in plan.subqueries:
            filters = Filters(platform=sq.platform, since=self._since(sq.window_days or plan.window_days), container=sq.container)
            qvec = self.embedder.embed([sq.query])[0]
            min_sim = self.embedder.min_similarity
            fts = self.index.search_fts(sq.query, tokens, filters, k)
            vec = self.index.search_vec(qvec, tokens, filters, k, min_similarity=min_sim)
            if not fts and not vec and sq.container:
                # A container hint is a heuristic, never a security boundary: relax it when it finds nothing.
                filters = Filters(platform=sq.platform, since=filters.since, container=None)
                fts = self.index.search_fts(sq.query, tokens, filters, k)
                vec = self.index.search_vec(qvec, tokens, filters, k, min_similarity=min_sim)
            ranked_lists += [fts, vec]
            for hit in fts + vec:
                chunk_items[hit.chunk_id] = hit.item_id
            for item in self.index.shadow_denied(sq.query, qvec, tokens, filters, min_similarity=max(min_sim * 2, 0.25)):
                shadow.setdefault(item.item_id, item)

        fused = rrf(ranked_lists)
        ordered_chunks = sorted(fused.items(), key=lambda kv: -kv[1])[: self.settings.fused_candidates]

        by_item: dict[str, list[tuple[str, float]]] = {}
        for chunk_id, score in ordered_chunks:
            by_item.setdefault(chunk_items[chunk_id], []).append((chunk_id, score))

        items = self.index.get_items(list(by_item))
        candidates: list[Candidate] = []
        for item_id, chunk_scores in by_item.items():
            item = items.get(item_id)
            if item is None:
                continue
            rule = self.index.granting_token(item_id, tokens)
            if rule is None:  # cannot happen (the query filtered), but never trust a race
                continue
            chunks = []
            for chunk_id, score in chunk_scores:
                chunk = self.index.get_chunk(chunk_id)
                if chunk is not None:
                    chunks.append(ChunkHit(chunk.chunk_id, chunk.text, chunk.sha256, score))
            if not chunks:
                continue
            item_score = max(c.score for c in chunks) + 0.02 * (len(chunks) - 1)
            candidates.append(Candidate(item=item, chunks=chunks, score=item_score, rule=rule))
        # relevance first, newest first among equals
        candidates.sort(key=lambda c: (-round(c.score, 6), -_ts(c.item.last_modified)))
        candidates = candidates[: self.settings.gate2_candidates]

        permitted_ids = {c.item.item_id for c in candidates}
        denied = [
            Decision(doc=item.item_id, platform=item.platform, gate1="deny", gate2="skipped", rule="not_member", version=item.version, container=item.container)  # type: ignore[arg-type]
            for item in shadow.values()
            if item.item_id not in permitted_ids
        ]
        return Retrieval(candidates=candidates, denied=denied)

    def expand_links(self, candidates: list[Candidate], principals: PrincipalSet) -> tuple[list[Candidate], list[Decision]]:
        """One hop along explicit links. Every expanded item passes Gate 1 on its own."""
        tokens = list(principals.tokens)
        known = {c.item.item_id for c in candidates}
        extra: list[Candidate] = []
        denied: list[Decision] = []
        for parent in candidates[: self.settings.expand_top_items]:
            for link in parent.item.links:
                if link in known:
                    continue
                known.add(link)
                item = self.index.get_item(link)
                if item is None:
                    continue  # not indexed (or deleted): nothing to say, nothing to leak
                rule = self.index.granting_token(link, tokens)
                if rule is None:
                    denied.append(Decision(doc=link, platform=item.platform, gate1="deny", gate2="skipped", rule="not_member", version=item.version, source="expansion", container=item.container))  # type: ignore[arg-type]
                    continue
                chunks = [ChunkHit(c.chunk_id, c.text, c.sha256, parent.score * 0.8) for c in self.index.get_chunks(link)]
                if chunks:
                    extra.append(Candidate(item=item, chunks=chunks, score=parent.score * 0.8, rule=rule, source="expansion", expanded_from=parent.item.item_id))
        return extra, denied
