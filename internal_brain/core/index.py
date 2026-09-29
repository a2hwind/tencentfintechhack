"""The index: one shared SQLite store with ACL metadata per chunk.

Gate 1 lives here. Every retrieval query carries the predicate
`allowed_principals ∩ user_principals ≠ ∅`, expressed in SQL as an IN-subquery on the
item_principals table (SQLite materialises the asker's permitted items once per query), so
top-k is computed only over permitted chunks and no unpermitted text ever leaves the
database. A query with an empty principal set matches nothing.

Keyword search is FTS5 (BM25) with the ACL predicate in the SQL. On a large index, query
terms that occur in more than a fifth of all chunks are dropped from the MATCH (dynamic
stopwords: BM25 gives them almost no weight, but each would pull a large share of the corpus
through scoring and the permission check); the rarest term always stays. Vector search runs on a
permission-aware in-memory index (core/vector_cache.py): the asker's principal tokens select
item posting lists, and only permitted rows are scored. SQLite stays the durable store for
both. Swap the vector part for Tencent Cloud VectorDB behind the same methods at production
scale (the ACL predicate becomes a metadata filter there).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..models import Acl, Item
from .chunking import sha256_text
from .embeddings import STOPWORDS, content_terms, tokenize
from .vector_cache import VectorCache

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    item_id        TEXT PRIMARY KEY,
    platform       TEXT NOT NULL,
    title          TEXT NOT NULL,
    version        INTEGER NOT NULL,
    last_modified  TEXT NOT NULL,
    container      TEXT,
    container_label TEXT,
    url            TEXT,
    author         TEXT,
    links_json     TEXT NOT NULL DEFAULT '[]',
    acl_json       TEXT NOT NULL DEFAULT '[]',
    indexed_at     TEXT NOT NULL,
    dlp_json       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS items_platform_modified ON items(platform, last_modified);
CREATE TABLE IF NOT EXISTS item_principals (
    item_id   TEXT NOT NULL,
    principal TEXT NOT NULL,
    PRIMARY KEY (item_id, principal)
);
CREATE INDEX IF NOT EXISTS item_principals_principal ON item_principals(principal);
CREATE TABLE IF NOT EXISTS chunks (
    rowid    INTEGER PRIMARY KEY,
    chunk_id TEXT NOT NULL UNIQUE,
    item_id  TEXT NOT NULL,
    platform TEXT NOT NULL,
    ord      INTEGER NOT NULL,
    text     TEXT NOT NULL,
    sha256   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_item ON chunks(item_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, item_id UNINDEXED, text, tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vocab USING fts5vocab(chunks_fts, 'row');
CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id TEXT PRIMARY KEY,
    item_id  TEXT NOT NULL,
    dim      INTEGER NOT NULL,
    vec      BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS chunk_vectors_item ON chunk_vectors(item_id);
CREATE TABLE IF NOT EXISTS sync_state (
    platform     TEXT PRIMARY KEY,
    cursor       TEXT,
    last_run     TEXT,
    items_synced INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def normalize_iso(value: str) -> str:
    """Normalise any ISO-8601 timestamp to YYYY-MM-DDTHH:MM:SSZ so string comparison orders by time."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


PRUNE_MIN_CHUNKS = 2000  # below this, document frequencies are too noisy to prune on (and nothing is slow)
PRUNE_MAX_DF = 0.2


def build_fts_query(text: str, df=None, max_df: float = PRUNE_MAX_DF) -> str | None:
    """An OR of the question's terms. With `df` (term -> share of chunks containing it), terms
    more common than `max_df` are dropped, keeping at least the rarest one."""
    tokens = [t for t in tokenize(text) if len(t) > 1 and t not in STOPWORDS]
    if not tokens:
        tokens = [t for t in tokenize(text) if len(t) > 1]
    if not tokens:
        return None
    seen: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.append(t)
    seen = seen[:24]
    if df is not None and len(seen) > 1:
        rates = {t: df(t) for t in seen}
        kept = [t for t in seen if rates[t] <= max_df]
        seen = kept or [min(seen, key=lambda t: rates[t])]
    return " OR ".join(f'"{t}"' for t in seen)


@dataclass
class IndexedItem:
    item_id: str
    platform: str
    title: str
    version: int
    last_modified: str
    container: str | None
    container_label: str | None
    url: str | None
    author: str | None
    links: list[str]
    allowed_principals: list[str]
    indexed_at: str
    dlp: dict = field(default_factory=dict)  # {kind: count} of values masked at ingestion


@dataclass
class IndexedChunk:
    chunk_id: str
    item_id: str
    ord: int
    text: str
    sha256: str


@dataclass
class Hit:
    chunk_id: str
    item_id: str
    score: float  # higher is better (already sign-adjusted for BM25)


@dataclass
class Filters:
    platform: str | None = None
    since: str | None = None  # ISO timestamp; items modified at or after
    container: str | None = None  # planner hint; matched against container_label or container suffix

    def sql(self, alias: str = "i") -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []
        if self.platform:
            clauses.append(f"{alias}.platform = ?")
            params.append(self.platform)
        if self.since:
            clauses.append(f"{alias}.last_modified >= ?")
            params.append(self.since)
        if self.container:
            hint = self.container.lower().lstrip("#")
            clauses.append(f"(lower({alias}.container_label) = ? OR lower({alias}.container_label) = ? OR lower({alias}.container) LIKE ?)")
            params += [hint, "#" + hint, "%:" + hint]
        return (" AND ".join(clauses) if clauses else "1=1"), params


class Index:
    def __init__(self, db_path: str | Path | None = None, conn: sqlite3.Connection | None = None, lock: threading.RLock | None = None):
        if conn is None:
            if db_path is not None and str(db_path) != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(db_path or ":memory:"), check_same_thread=False)
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.lock = lock or threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(items)").fetchall()}
            if "dlp_json" not in columns:  # databases created before DLP masking
                self.conn.execute("ALTER TABLE items ADD COLUMN dlp_json TEXT NOT NULL DEFAULT '{}'")
            self.conn.commit()
        self.vcache = VectorCache()
        self._n_chunks: int | None = None
        self._df_cache: dict[str, float] = {}
        self._load_vector_cache()

    # ------------------------------------------------------------------ keyword query text
    def chunk_count(self) -> int:
        if self._n_chunks is None:
            with self.lock:
                self._n_chunks = int(self.conn.execute("SELECT count(*) FROM chunks").fetchone()[0])
                self._df_cache = {}
        return self._n_chunks

    def fts_match(self, text: str) -> str | None:
        """The FTS5 MATCH expression for a question, with dynamic stopwords on a large index."""
        total = self.chunk_count()
        if total < PRUNE_MIN_CHUNKS:
            return build_fts_query(text)
        cache = self._df_cache  # reset whenever the index changes (chunk_count recomputes)

        def df(term: str) -> float:
            if term not in cache:
                with self.lock:
                    row = self.conn.execute("SELECT doc FROM chunks_vocab WHERE term = ?", (term,)).fetchone()
                cache[term] = (row[0] if row else 0) / total
            return cache[term]

        return build_fts_query(text, df=df)

    def _load_vector_cache(self) -> None:
        """Rebuild the in-memory vector index from the durable tables."""
        with self.lock:
            items = self.conn.execute("SELECT item_id, platform, last_modified, container, container_label FROM items").fetchall()
            principals: dict[str, list[str]] = {}
            for row in self.conn.execute("SELECT item_id, principal FROM item_principals"):
                principals.setdefault(row["item_id"], []).append(row["principal"])
            vectors: dict[str, list[tuple[str, np.ndarray]]] = {}
            for row in self.conn.execute(
                "SELECT v.item_id AS item_id, v.chunk_id AS chunk_id, v.vec AS vec FROM chunk_vectors v JOIN chunks c ON c.chunk_id = v.chunk_id ORDER BY v.item_id, c.ord"
            ):
                vectors.setdefault(row["item_id"], []).append((row["chunk_id"], np.frombuffer(row["vec"], dtype=np.float32)))
        for item in items:
            rows = vectors.get(item["item_id"]) or []
            if rows:
                self.vcache.upsert(
                    item["item_id"], item["platform"], item["last_modified"], item["container"], item["container_label"],
                    principals.get(item["item_id"], []), [c for c, _ in rows], np.stack([v for _, v in rows]),
                )

    # ------------------------------------------------------------------ writes
    def upsert_item(self, item: Item, acl: Acl, chunks: list[str], vectors: np.ndarray, dlp_counts: dict[str, int] | None = None) -> None:
        """Replace every chunk of an item in one transaction: a document is never half old, half new."""
        now = normalize_iso(datetime.now(timezone.utc).isoformat())
        with self.lock:
            cur = self.conn.cursor()
            try:
                cur.execute("BEGIN")
                self._delete_item_rows(cur, item.item_id)
                cur.execute(
                    "INSERT INTO items(item_id, platform, title, version, last_modified, container, container_label, url, author, links_json, acl_json, indexed_at, dlp_json)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item.item_id,
                        item.platform,
                        item.title,
                        item.version,
                        normalize_iso(item.last_modified),
                        item.container,
                        item.container_label,
                        item.url,
                        item.author,
                        json.dumps(item.links),
                        json.dumps(sorted(set(acl.allowed_principals))),
                        now,
                        json.dumps(dlp_counts or {}, sort_keys=True),
                    ),
                )
                cur.executemany(
                    "INSERT OR IGNORE INTO item_principals(item_id, principal) VALUES (?,?)",
                    [(item.item_id, p) for p in set(acl.allowed_principals)],
                )
                chunk_ids: list[str] = []
                for ord_, text in enumerate(chunks):
                    chunk_id = f"{item.item_id}#{ord_}"
                    chunk_ids.append(chunk_id)
                    cur.execute(
                        "INSERT INTO chunks(chunk_id, item_id, platform, ord, text, sha256) VALUES (?,?,?,?,?,?)",
                        (chunk_id, item.item_id, item.platform, ord_, text, sha256_text(text)),
                    )
                    # the FTS row shares the chunk's rowid, so deletes are keyed lookups, not scans
                    cur.execute("INSERT INTO chunks_fts(rowid, chunk_id, item_id, text) VALUES (?,?,?,?)", (cur.lastrowid, chunk_id, item.item_id, text))
                    vec = np.asarray(vectors[ord_], dtype=np.float32)
                    cur.execute(
                        "INSERT INTO chunk_vectors(chunk_id, item_id, dim, vec) VALUES (?,?,?,?)",
                        (chunk_id, item.item_id, int(vec.shape[0]), vec.tobytes()),
                    )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            finally:
                self._n_chunks = None
            self.vcache.upsert(
                item.item_id, item.platform, normalize_iso(item.last_modified), item.container, item.container_label,
                sorted(set(acl.allowed_principals)), chunk_ids, np.asarray(vectors, dtype=np.float32)[: len(chunk_ids)],
            )

    def _delete_item_rows(self, cur: sqlite3.Cursor, item_id: str) -> None:
        cur.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT rowid FROM chunks WHERE item_id = ?)", (item_id,))
        cur.execute("DELETE FROM chunk_vectors WHERE item_id = ?", (item_id,))
        cur.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))
        cur.execute("DELETE FROM item_principals WHERE item_id = ?", (item_id,))
        cur.execute("DELETE FROM items WHERE item_id = ?", (item_id,))

    def delete_item(self, item_id: str) -> bool:
        with self.lock:
            existed = self.conn.execute("SELECT 1 FROM items WHERE item_id = ?", (item_id,)).fetchone() is not None
            cur = self.conn.cursor()
            cur.execute("BEGIN")
            self._delete_item_rows(cur, item_id)
            self.conn.commit()
            self._n_chunks = None
            self.vcache.delete(item_id)
            return existed

    # ------------------------------------------------------------------ reads
    def _row_to_item(self, row: sqlite3.Row) -> IndexedItem:
        return IndexedItem(
            item_id=row["item_id"],
            platform=row["platform"],
            title=row["title"],
            version=row["version"],
            last_modified=row["last_modified"],
            container=row["container"],
            container_label=row["container_label"],
            url=row["url"],
            author=row["author"],
            links=json.loads(row["links_json"]),
            allowed_principals=json.loads(row["acl_json"]),
            indexed_at=row["indexed_at"],
            dlp=json.loads(row["dlp_json"]) if "dlp_json" in row.keys() and row["dlp_json"] else {},
        )

    def get_item(self, item_id: str) -> IndexedItem | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def get_items(self, item_ids: list[str]) -> dict[str, IndexedItem]:
        if not item_ids:
            return {}
        with self.lock:
            marks = ",".join("?" * len(item_ids))
            rows = self.conn.execute(f"SELECT * FROM items WHERE item_id IN ({marks})", item_ids).fetchall()
        return {row["item_id"]: self._row_to_item(row) for row in rows}

    def get_chunks(self, item_id: str) -> list[IndexedChunk]:
        with self.lock:
            rows = self.conn.execute("SELECT chunk_id, item_id, ord, text, sha256 FROM chunks WHERE item_id = ? ORDER BY ord", (item_id,)).fetchall()
        return [IndexedChunk(row["chunk_id"], row["item_id"], row["ord"], row["text"], row["sha256"]) for row in rows]

    def get_chunk(self, chunk_id: str) -> IndexedChunk | None:
        with self.lock:
            row = self.conn.execute("SELECT chunk_id, item_id, ord, text, sha256 FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
        return IndexedChunk(row["chunk_id"], row["item_id"], row["ord"], row["text"], row["sha256"]) if row else None

    def granting_token(self, item_id: str, principals: list[str]) -> str | None:
        """The first principal (sorted) the user holds that the item's ACL grants, or None."""
        if not principals:
            return None
        with self.lock:
            marks = ",".join("?" * len(principals))
            row = self.conn.execute(
                f"SELECT principal FROM item_principals WHERE item_id = ? AND principal IN ({marks}) ORDER BY principal LIMIT 1",
                [item_id, *principals],
            ).fetchone()
        return row["principal"] if row else None

    # ------------------------------------------------------------------ Gate 1 queries
    @staticmethod
    def _permitted_sql(principals: list[str], alias: str = "i") -> tuple[str, list]:
        if not principals:
            return "0", []
        marks = ",".join("?" * len(principals))
        # An IN-subquery, not a correlated EXISTS: built once per query, then one probe per matching
        # chunk (at 100k chunks, 2.4x faster at the median and 3.6x at p95; same results).
        return f"{alias}.item_id IN (SELECT item_id FROM item_principals WHERE principal IN ({marks}))", list(principals)

    def search_fts(self, query_text: str, principals: list[str], filters: Filters | None = None, limit: int = 20) -> list[Hit]:
        match = self.fts_match(query_text)
        if match is None:
            return []
        filters = filters or Filters()
        permitted, p_params = self._permitted_sql(principals)
        where, f_params = filters.sql()
        sql = (
            "SELECT chunks_fts.chunk_id AS chunk_id, chunks_fts.item_id AS item_id, bm25(chunks_fts) AS rank "
            "FROM chunks_fts JOIN items i ON i.item_id = chunks_fts.item_id "
            f"WHERE chunks_fts MATCH ? AND {permitted} AND {where} "
            "ORDER BY rank LIMIT ?"
        )
        with self.lock:
            rows = self.conn.execute(sql, [match, *p_params, *f_params, limit]).fetchall()
        return [Hit(row["chunk_id"], row["item_id"], -float(row["rank"])) for row in rows]

    def search_vec(self, query_vec: np.ndarray, principals: list[str], filters: Filters | None = None, limit: int = 20, min_similarity: float = 0.12) -> list[Hit]:
        """Gate 1 for vectors: only rows whose item is permitted by the asker's tokens are scored."""
        filters = filters or Filters()
        with self.lock:
            hits = self.vcache.search(query_vec, principals, limit, min_similarity, filters.platform, filters.since, filters.container)
        return [Hit(h.chunk_id, h.item_id, h.score) for h in hits]

    def shadow_denied(self, query_text: str, query_vec: np.ndarray | None, principals: list[str], filters: Filters | None = None, limit: int = 8, min_similarity: float = 0.25) -> list[IndexedItem]:
        """Audit-only: items that match the query but that the principal set does not permit.

        Returns item metadata (ids, never text) so the audit entry can record deny:not_member
        for documents the retriever never surfaced. This runs after the permitted query and
        its results never enter the retrieval path.

        The keyword arm matches on the question's substantive terms only (no stopwords, no
        generic nouns such as "report"), so an unrelated restricted document is not logged as
        "denied" for every query that happens to say "report". Both arms run for every query,
        permitted or not, so a denial costs the same as a miss.
        """
        filters = filters or Filters()
        permitted, p_params = self._permitted_sql(principals)
        where, f_params = filters.sql()
        ids: list[str] = []
        match = self.fts_match(" ".join(sorted(content_terms(query_text))))
        with self.lock:
            if match is not None:
                rows = self.conn.execute(
                    "SELECT chunks_fts.item_id AS item_id, bm25(chunks_fts) AS rank FROM chunks_fts JOIN items i ON i.item_id = chunks_fts.item_id "
                    f"WHERE chunks_fts MATCH ? AND NOT ({permitted}) AND {where} ORDER BY rank LIMIT ?",
                    [match, *p_params, *f_params, limit * 4],
                ).fetchall()
                ids += list(dict.fromkeys(row["item_id"] for row in rows))[:limit]
            if query_vec is not None:
                for hit in self.vcache.search_denied(query_vec, principals, limit // 2 or 1, min_similarity, filters.platform, filters.since, filters.container):
                    ids.append(hit.item_id)
        unique = list(dict.fromkeys(ids))
        items = self.get_items(unique)
        return [items[i] for i in unique if i in items]

    # ------------------------------------------------------------------ meta + reset
    def meta_get(self, key: str) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def meta_set(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO index_meta(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
            self.conn.commit()

    def clear(self) -> None:
        """Drop every indexed item and cursor (the audit log is untouched)."""
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN")
            for table in ("chunks_fts", "chunk_vectors", "chunks", "item_principals", "items", "sync_state"):
                cur.execute(f"DELETE FROM {table}")
            self.conn.commit()
            self._n_chunks = None
            self.vcache.clear()

    # ------------------------------------------------------------------ sync state
    def get_cursor(self, platform: str) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT cursor FROM sync_state WHERE platform = ?", (platform,)).fetchone()
        return row["cursor"] if row else None

    def set_cursor(self, platform: str, cursor: str, items_synced: int) -> None:
        now = normalize_iso(datetime.now(timezone.utc).isoformat())
        with self.lock:
            self.conn.execute(
                "INSERT INTO sync_state(platform, cursor, last_run, items_synced) VALUES (?,?,?,?) "
                "ON CONFLICT(platform) DO UPDATE SET cursor = excluded.cursor, last_run = excluded.last_run, items_synced = sync_state.items_synced + excluded.items_synced",
                (platform, cursor, now, items_synced),
            )
            self.conn.commit()

    def sync_status(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT platform, cursor, last_run, items_synced FROM sync_state ORDER BY platform").fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict:
        with self.lock:
            items = self.conn.execute("SELECT platform, count(*) AS n FROM items GROUP BY platform").fetchall()
            chunks = self.conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
        return {"items": {row["platform"]: row["n"] for row in items}, "chunks": chunks}

    def item_ids_in_container(self, container: str) -> list[str]:
        with self.lock:
            rows = self.conn.execute("SELECT item_id FROM items WHERE container = ? ORDER BY item_id", [container]).fetchall()
        return [row["item_id"] for row in rows]

    def list_items(self) -> list[IndexedItem]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM items ORDER BY platform, item_id").fetchall()
        return [self._row_to_item(row) for row in rows]
