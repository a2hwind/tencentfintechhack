"""VectorCache: the permission-aware in-memory vector index behind Gate 1's embedding retriever."""

from __future__ import annotations

import numpy as np
import pytest

from internal_brain.core.vector_cache import VectorCache


def unit(*values: float) -> np.ndarray:
    v = np.asarray(values, dtype=np.float32)
    return v / np.linalg.norm(v)


def filled() -> VectorCache:
    cache = VectorCache()
    cache.upsert("confluence:1", "confluence", "2026-09-01T00:00:00Z", "confluence:space:ENG", "ENG", ["confluence:space:ENG"], ["confluence:1#0", "confluence:1#1"], np.stack([unit(1, 0, 0), unit(0.9, 0.1, 0)]))
    cache.upsert("slack:C1:1.0", "slack", "2026-09-20T00:00:00Z", "slack:channel:C1", "#db", ["slack:channel:C1"], ["slack:C1:1.0#0"], np.stack([unit(1, 0.05, 0)]))
    cache.upsert("jira:SEC-1", "jira", "2026-01-01T00:00:00Z", "jira:project:SEC", "SEC", ["jira:project:SEC:level:security-only"], ["jira:SEC-1#0"], np.stack([unit(1, 0, 0.01)]))
    return cache


def test_only_permitted_rows_are_scored():
    cache = filled()
    q = unit(1, 0, 0)
    assert [h.item_id for h in cache.search(q, ["confluence:space:ENG"], k=10)] == ["confluence:1", "confluence:1"]
    got = cache.search(q, ["confluence:space:ENG", "slack:channel:C1"], k=10)
    assert {h.item_id for h in got} == {"confluence:1", "slack:C1:1.0"} and "jira:SEC-1" not in {h.item_id for h in got}
    assert cache.search(q, [], k=10) == [] and cache.search(q, ["nobody"], k=10) == []
    # the best-matching restricted row cannot crowd out top-k: k=1 still returns a permitted row
    assert cache.search(q, ["slack:channel:C1"], k=1)[0].item_id == "slack:C1:1.0"


def test_filters_and_the_audit_only_complement():
    cache = filled()
    q = unit(1, 0, 0)
    everyone = ["confluence:space:ENG", "slack:channel:C1", "jira:project:SEC:level:security-only"]
    assert {h.item_id for h in cache.search(q, everyone, k=10, platform="slack")} == {"slack:C1:1.0"}
    assert {h.item_id for h in cache.search(q, everyone, k=10, since="2026-09-10T00:00:00Z")} == {"slack:C1:1.0"}
    assert {h.item_id for h in cache.search(q, everyone, k=10, container="db")} == {"slack:C1:1.0"}
    assert {h.item_id for h in cache.search(q, everyone, k=10, container="ENG")} == {"confluence:1"}
    denied = cache.search_denied(q, ["confluence:space:ENG"], k=10)
    assert {h.item_id for h in denied} == {"slack:C1:1.0", "jira:SEC-1"}, "exactly the complement"


def test_upsert_replaces_and_delete_frees_postings():
    cache = filled()
    cache.upsert("confluence:1", "confluence", "2026-09-02T00:00:00Z", "confluence:space:ENG", "ENG", ["confluence:space:ENG:user:mlim"], ["confluence:1#0"], np.stack([unit(0, 1, 0)]))
    assert cache.search(unit(1, 0, 0), ["confluence:space:ENG"], k=10) == [], "the old ACL is gone with the old rows"
    assert [h.chunk_id for h in cache.search(unit(0, 1, 0), ["confluence:space:ENG:user:mlim"], k=10)] == ["confluence:1#0"]
    assert cache.delete("slack:C1:1.0") and not cache.delete("slack:C1:1.0")
    assert "slack:channel:C1" not in cache._postings
    assert cache.stats() == {"rows": 2, "items": 2, "principals": 2, "dim": 3}


def test_compaction_keeps_results():
    cache = VectorCache()
    rng = np.random.default_rng(0)
    vectors = rng.standard_normal((3000, 16)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    for i in range(3000):
        cache.upsert(f"i{i}", "gdrive", "2026-09-01T00:00:00Z", None, None, [f"t{i % 7}"], [f"i{i}#0"], vectors[i : i + 1])
    q = vectors[5]
    before = [(h.chunk_id, round(h.score, 5)) for h in cache.search(q, ["t0", "t3"], k=15, min_similarity=-1)]
    for i in range(3000):
        if i % 3:
            cache.delete(f"i{i}")  # two thirds dead: compaction runs
    assert cache.n_rows < 3000 and cache.n_rows - cache.dead_rows == 1000, "dead rows were compacted away"
    after = [(h.chunk_id, round(h.score, 5)) for h in cache.search(q, ["t0", "t3"], k=15, min_similarity=-1)]
    assert after[:3] == [row for row in before if int(row[0][1:].split("#")[0]) % 3 == 0][:3]
    permitted = [i for i in range(0, 3000, 3) if i % 7 in (0, 3)]
    brute = sorted(((float(vectors[i] @ q), f"i{i}#0") for i in permitted), reverse=True)[:15]
    assert [c for _, c in brute] == [c for c, _ in after]


def test_dimension_change_is_refused():
    cache = filled()
    with pytest.raises(ValueError, match="dimension"):
        cache.upsert("x", "jira", "2026-09-01T00:00:00Z", None, None, ["t"], ["x#0"], np.ones((1, 4), dtype=np.float32))
