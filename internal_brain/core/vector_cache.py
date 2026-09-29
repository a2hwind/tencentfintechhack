"""Permission-aware in-memory vector index: Gate 1 for the embedding retriever.

Rows are chunk vectors in one float32 matrix; every row points at its item's slot. Every
principal token has a posting list of item slots. A query ORs the posting lists of the asker's
tokens into an item bitmap, applies the metadata filters (platform, time window, container),
and scores only the rows whose item is permitted. Unpermitted vectors are never scored, so
they can neither crowd out top-k nor leak through a score, and the cost of the filter is
proportional to the asker's entitlements, not to the corpus.

SQLite stays the durable store (chunk_vectors, item_principals); the cache is rebuilt from it
at start-up and kept current by every upsert and delete. At production scale the same shape
maps onto a vector database with a metadata filter on allowed principals (Tencent Cloud
VectorDB), behind the same Index methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

PLATFORM_CODES = {"confluence": 1, "jira": 2, "slack": 3, "gdrive": 4}


def iso_to_epoch(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


@dataclass
class VectorHit:
    chunk_id: str
    item_id: str
    score: float


class VectorCache:
    def __init__(self) -> None:
        self.dim: int | None = None
        self._vectors = np.zeros((0, 1), dtype=np.float32)
        self._row_slot = np.zeros(0, dtype=np.int64)
        self._row_alive = np.zeros(0, dtype=bool)
        self._row_chunk: list[str] = []
        self.n_rows = 0
        self.dead_rows = 0
        # item slots
        self._slot_of: dict[str, int] = {}
        self._slot_item: list[str | None] = []
        self._slot_rows: list[list[int]] = []
        self._slot_principals: list[list[str]] = []
        self._slot_platform = np.zeros(0, dtype=np.int8)
        self._slot_modified = np.zeros(0, dtype=np.float64)
        self._slot_alive = np.zeros(0, dtype=bool)
        self._slot_container: list[str] = []
        self._slot_label: list[str] = []
        self._free_slots: list[int] = []
        # principal -> set of slots, with a lazily built array per principal
        self._postings: dict[str, set[int]] = {}
        self._posting_arrays: dict[str, np.ndarray] = {}

    # ------------------------------------------------------------------ capacity
    def _ensure_rows(self, extra: int, dim: int) -> None:
        if self.dim is None:
            self.dim = dim
            self._vectors = np.zeros((max(64, extra), dim), dtype=np.float32)
            self._row_slot = np.zeros(max(64, extra), dtype=np.int64)
            self._row_alive = np.zeros(max(64, extra), dtype=bool)
        if dim != self.dim:
            raise ValueError(f"vector dimension changed from {self.dim} to {dim}; clear the index first")
        need = self.n_rows + extra
        if need <= self._vectors.shape[0]:
            return
        cap = max(need, self._vectors.shape[0] * 2)
        vectors = np.zeros((cap, self.dim), dtype=np.float32)
        vectors[: self.n_rows] = self._vectors[: self.n_rows]
        row_slot = np.zeros(cap, dtype=np.int64)
        row_slot[: self.n_rows] = self._row_slot[: self.n_rows]
        row_alive = np.zeros(cap, dtype=bool)
        row_alive[: self.n_rows] = self._row_alive[: self.n_rows]
        self._vectors, self._row_slot, self._row_alive = vectors, row_slot, row_alive

    def _new_slot(self) -> int:
        if self._free_slots:
            return self._free_slots.pop()
        slot = len(self._slot_item)
        self._slot_item.append(None)
        self._slot_rows.append([])
        self._slot_principals.append([])
        self._slot_container.append("")
        self._slot_label.append("")
        grow = max(64, len(self._slot_platform) * 2) if slot >= len(self._slot_platform) else len(self._slot_platform)
        if slot >= len(self._slot_platform):
            self._slot_platform = np.concatenate([self._slot_platform, np.zeros(grow - len(self._slot_platform) + 1, dtype=np.int8)])
            self._slot_modified = np.concatenate([self._slot_modified, np.zeros(grow - len(self._slot_modified) + 1, dtype=np.float64)])
            self._slot_alive = np.concatenate([self._slot_alive, np.zeros(grow - len(self._slot_alive) + 1, dtype=bool)])
        return slot

    # ------------------------------------------------------------------ writes
    def upsert(
        self,
        item_id: str,
        platform: str,
        last_modified: str,
        container: str | None,
        container_label: str | None,
        principals: list[str],
        chunk_ids: list[str],
        vectors: np.ndarray,
    ) -> None:
        self.delete(item_id)
        vectors = np.asarray(vectors, dtype=np.float32)
        if len(chunk_ids) == 0:
            return
        if vectors.ndim != 2 or vectors.shape[0] != len(chunk_ids):
            raise ValueError("one vector per chunk")
        self._ensure_rows(len(chunk_ids), int(vectors.shape[1]))
        slot = self._new_slot()
        self._slot_of[item_id] = slot
        self._slot_item[slot] = item_id
        start = self.n_rows
        rows = list(range(start, start + len(chunk_ids)))
        self._vectors[start : start + len(chunk_ids)] = vectors
        self._row_slot[start : start + len(chunk_ids)] = slot
        self._row_alive[start : start + len(chunk_ids)] = True
        self._row_chunk.extend(chunk_ids)
        self.n_rows += len(chunk_ids)
        self._slot_rows[slot] = rows
        self._slot_platform[slot] = PLATFORM_CODES.get(platform, 0)
        self._slot_modified[slot] = iso_to_epoch(last_modified)
        self._slot_alive[slot] = True
        self._slot_container[slot] = (container or "").lower()
        self._slot_label[slot] = (container_label or "").lower()
        unique = sorted(set(principals))
        self._slot_principals[slot] = unique
        for token in unique:
            self._postings.setdefault(token, set()).add(slot)
            self._posting_arrays.pop(token, None)

    def delete(self, item_id: str) -> bool:
        slot = self._slot_of.pop(item_id, None)
        if slot is None:
            return False
        for row in self._slot_rows[slot]:
            self._row_alive[row] = False
        self.dead_rows += len(self._slot_rows[slot])
        for token in self._slot_principals[slot]:
            posting = self._postings.get(token)
            if posting is not None:
                posting.discard(slot)
                if not posting:
                    del self._postings[token]
            self._posting_arrays.pop(token, None)
        self._slot_item[slot] = None
        self._slot_rows[slot] = []
        self._slot_principals[slot] = []
        self._slot_alive[slot] = False
        self._free_slots.append(slot)
        if self.dead_rows > 1024 and self.dead_rows > self.n_rows // 2:
            self.compact()
        return True

    def clear(self) -> None:
        self.__init__()  # type: ignore[misc]

    def compact(self) -> None:
        """Drop dead rows (after many re-indexes): rows are renumbered, slots and postings are kept."""
        alive = np.flatnonzero(self._row_alive[: self.n_rows])
        remap = {int(old): new for new, old in enumerate(alive)}
        self._vectors = self._vectors[alive].copy() if len(alive) else np.zeros((64, self.dim or 1), dtype=np.float32)
        self._row_slot = self._row_slot[alive].copy()
        self._row_chunk = [self._row_chunk[int(i)] for i in alive]
        self.n_rows = len(alive)
        self._row_alive = np.ones(self.n_rows, dtype=bool)
        self.dead_rows = 0
        for slot, rows in enumerate(self._slot_rows):
            self._slot_rows[slot] = [remap[r] for r in rows if r in remap]

    # ------------------------------------------------------------------ masks
    def _posting(self, token: str) -> np.ndarray:
        array = self._posting_arrays.get(token)
        if array is None:
            array = np.fromiter(self._postings.get(token, ()), dtype=np.int64)
            self._posting_arrays[token] = array
        return array

    def permitted_slots(self, principals: list[str]) -> np.ndarray:
        """The item bitmap for a principal set: the OR of the tokens' posting lists."""
        mask = np.zeros(len(self._slot_item), dtype=bool)
        for token in set(principals):
            if token in self._postings:
                mask[self._posting(token)] = True
        return mask

    def filter_slots(self, platform: str | None = None, since: str | None = None, container: str | None = None) -> np.ndarray:
        n = len(self._slot_item)
        mask = self._slot_alive[:n].copy()
        if platform:
            mask &= self._slot_platform[:n] == PLATFORM_CODES.get(platform, -1)
        if since:
            mask &= self._slot_modified[:n] >= iso_to_epoch(since)
        if container:
            hint = container.lower().lstrip("#")
            ok = np.fromiter(
                ((label == hint or label == "#" + hint or cont.endswith(":" + hint)) for label, cont in zip(self._slot_label, self._slot_container)),
                dtype=bool,
                count=n,
            )
            mask &= ok
        return mask

    # ------------------------------------------------------------------ queries
    def _top(self, slot_mask: np.ndarray, query: np.ndarray, k: int, min_similarity: float) -> list[VectorHit]:
        if self.n_rows == 0 or not slot_mask.any():
            return []
        row_mask = self._row_alive[: self.n_rows] & slot_mask[self._row_slot[: self.n_rows]]
        rows = np.flatnonzero(row_mask)
        if len(rows) == 0:
            return []
        scores = self._vectors[rows] @ np.asarray(query, dtype=np.float32)
        if len(rows) > k:
            top = np.argpartition(-scores, k)[:k]
            top = top[np.argsort(-scores[top], kind="stable")]
        else:
            top = np.argsort(-scores, kind="stable")
        hits = []
        for i in top:
            score = float(scores[int(i)])
            if score <= min_similarity:
                continue
            row = int(rows[int(i)])
            hits.append(VectorHit(self._row_chunk[row], self._slot_item[int(self._row_slot[row])] or "", score))
        return hits

    def search(self, query: np.ndarray, principals: list[str], k: int = 20, min_similarity: float = 0.12, platform: str | None = None, since: str | None = None, container: str | None = None) -> list[VectorHit]:
        if not principals:
            return []
        mask = self.permitted_slots(principals) & self.filter_slots(platform, since, container)
        return self._top(mask, query, k, min_similarity)

    def search_denied(self, query: np.ndarray, principals: list[str], k: int = 4, min_similarity: float = 0.25, platform: str | None = None, since: str | None = None, container: str | None = None) -> list[VectorHit]:
        """Audit-only: the best-matching items the principal set does NOT permit (ids and scores, never text)."""
        mask = ~self.permitted_slots(principals) & self.filter_slots(platform, since, container)
        return self._top(mask, query, k, min_similarity)

    def stats(self) -> dict:
        return {"rows": self.n_rows - self.dead_rows, "items": len(self._slot_of), "principals": len(self._postings), "dim": self.dim}
