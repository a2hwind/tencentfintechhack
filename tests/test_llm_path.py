"""The real model code paths, exercised against a fake OpenAI-compatible server: planner JSON,
answer prompt with <doc> blocks, the guard's handling of model misbehaviour, the LLM verifier,
and embeddings through /embeddings (the Hunyuan option)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import CANARIES, SCENARIO_1, BrainClient

from internal_brain.api.app import create_app
from internal_brain.config import Settings
from fake_llm import calls, serve


@pytest.fixture(scope="module")
def fake_llm():
    server = serve()
    yield server
    server.stop()


def _settings(tmp_path, fake_llm, **overrides) -> Settings:
    base = dict(
        data_dir=tmp_path / "data",
        sync_interval_s=0,
        llm_base_url=fake_llm.base_url,
        llm_api_key="test-key",
        llm_model_planner="fake-planner",
        llm_model_answer="fake-answer",
        verifier="llm",
        embeddings="hunyuan",
        embedding_model="fake-embed",
        embedding_min_similarity=0.12,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def llm_brain(tmp_path, fake_llm):
    app = create_app(_settings(tmp_path, fake_llm), initial_sync=True, poller=False)
    with TestClient(app) as client:
        yield BrainClient(client)


def test_health_reports_model_modes(llm_brain):
    health = llm_brain.c.get("/health").json()
    assert health["planner"] == "llm" and health["answerer"] == "llm"
    assert health["embeddings"] == "openai:fake-embed"
    assert health["sync"]["index"]["chunks"] > 0, "embeddings came from /embeddings during the initial sync"


def test_model_plan_and_grounded_answer(llm_brain):
    calls.clear()
    body, seq, _ = llm_brain.ask("jdoe", SCENARIO_1)
    entry = llm_brain.entry(seq)
    assert entry["plan"]["fallback"] is False and entry["plan"]["intent"] == "status"
    assert {sq["platform"] for sq in entry["plan"]["subqueries"]} == {"confluence", "jira", "slack", "gdrive"}
    assert entry["model"] == "fake-answer"
    assert not body["no_result"] and body["citations"]
    cited = [c["doc"] for c in body["citations"]]
    assert all(c in {s["chunk"].split("#")[0] for s in entry["sent_to_model"]} for c in cited)
    assert not any(c.startswith("slack:C0DBML:") for c in cited)
    for canary in CANARIES:
        assert canary not in body["answer"]
    models = {c["model"] for c in calls}
    assert "fake-planner" in models and "fake-answer" in models


def test_planner_falls_back_to_all_platforms_on_bad_model_output(llm_brain):
    for marker in ("INVALID_PLAN", "NOT_JSON"):
        body, seq, _ = llm_brain.ask("jdoe", f"{SCENARIO_1} {marker}")
        entry = llm_brain.entry(seq)
        assert entry["plan"]["fallback"] is True and len(entry["plan"]["subqueries"]) == 4
        assert not body["no_result"], "the fallback plan still answers"


def test_guard_strips_foreign_citations_and_uncited_claims_from_the_model(llm_brain):
    body, seq, _ = llm_brain.ask("jdoe", f"{SCENARIO_1} FOREIGN_CITE UNCITED")
    entry = llm_brain.entry(seq)
    assert entry["guard"]["citations_rejected"] >= 1 and entry["guard"]["claims_stripped"] >= 1
    assert "confluence:9001" not in body["answer"] and "hunter2" not in body["answer"]
    assert "confluence:9001" not in [c["doc"] for c in body["citations"]]
    assert any("citation_rejected" in e for e in entry["guard"]["events"])


def test_llm_verifier_strips_unsupported_sentences(llm_brain):
    body, seq, _ = llm_brain.ask("jdoe", f"{SCENARIO_1} HALLUCINATE")
    entry = llm_brain.entry(seq)
    assert entry["guard"]["unsupported_claims"] >= 1
    assert "invented figure" not in body["answer"]
    assert not body["no_result"]


def test_negative_case_is_still_uniform_with_a_model(llm_brain):
    restricted, s1, r1 = llm_brain.ask("ctr-lee", "Where is the Q3 breach report?")
    missing, s2, r2 = llm_brain.ask("ctr-lee", "Where is the Q9 llama report?")
    assert restricted["no_result"] and missing["no_result"] and r1.content == r2.content
    assert llm_brain.entry(s1)["sent_to_model"] == []


def test_switching_embedders_clears_and_rebuilds_the_index(tmp_path, fake_llm):
    app1 = create_app(Settings(data_dir=tmp_path / "data", sync_interval_s=0), initial_sync=True, poller=False)
    with TestClient(app1) as c1:
        first = c1.get("/health").json()
        assert first["embeddings"] == "hash-256" and first["sync"]["index"]["chunks"] > 0
        chunks_before = first["sync"]["index"]["chunks"]
    app2 = create_app(_settings(tmp_path, fake_llm), initial_sync=True, poller=False)
    with TestClient(app2) as c2:
        second = c2.get("/health").json()
        assert second["embeddings"] == "openai:fake-embed"
        assert second["sync"]["index"]["chunks"] == chunks_before, "full re-sync after the embedder changed"
        assert c2.app.state.brain.index.meta_get("embedder") == "openai:fake-embed"
        body, _, _ = BrainClient(c2).ask("jdoe", SCENARIO_1)
        assert not body["no_result"]


def test_provider_without_json_mode_is_detected_once(tmp_path, fake_llm):
    app = create_app(_settings(tmp_path, fake_llm, llm_model_planner="no-json-mode", embeddings="hash", embedding_min_similarity=None), initial_sync=True, poller=False)
    with TestClient(app) as client:
        b = BrainClient(client)
        calls.clear()
        _, seq, _ = b.ask("jdoe", SCENARIO_1)
        assert b.entry(seq)["plan"]["fallback"] is False, "the retry without response_format still yields a valid plan"
        assert client.app.state.brain.llm.json_mode_supported is False
        calls.clear()
        b.ask("jdoe", SCENARIO_1)
        planner_calls = [c for c in calls if c["model"] == "no-json-mode"]
        assert len(planner_calls) == 1 and "response_format" not in planner_calls[0]["keys"], "learned once, not retried every query"


def test_reasoning_blocks_and_fenced_json_are_handled(tmp_path, fake_llm):
    app = create_app(
        _settings(tmp_path, fake_llm, llm_model_planner="thinker", llm_model_answer="thinker", embeddings="hash", embedding_min_similarity=None),
        initial_sync=True,
        poller=False,
    )
    with TestClient(app) as client:
        b = BrainClient(client)
        body, seq, _ = b.ask("jdoe", SCENARIO_1)
        entry = b.entry(seq)
        assert entry["plan"]["fallback"] is False, "fenced JSON after a think block is still parsed"
        assert "<think>" not in body["answer"] and "confluence:9001" not in body["answer"]
        assert entry["guard"]["citations_rejected"] == 0, "the think block never reaches the guard"


def test_hunyuan_requests_disable_enhancement(tmp_path, fake_llm):
    base = fake_llm.base_url.replace("/v1", "/hunyuan/v1")
    app = create_app(_settings(tmp_path, fake_llm, llm_base_url=base, embeddings="hash", embedding_min_similarity=None), initial_sync=True, poller=False)
    with TestClient(app) as client:
        calls.clear()
        BrainClient(client).ask("jdoe", SCENARIO_1)
        assert calls and all(c["body_extra"].get("enable_enhancement") is False for c in calls), "no web search may leak into a grounded answer"
