"""The glass box: per-stage timings in the audit, evidence quotes for every sentence, sources that
re-check both gates at open, and a live stream of the chain for the console."""

from __future__ import annotations

import json
import threading

from conftest import SCENARIO_1, SCENARIO_2

from internal_brain.models import SOURCE_UNAVAILABLE_MESSAGE


def test_stage_timings_are_recorded(brain):
    _, seq, _ = brain.ask("jdoe", SCENARIO_1)
    timings = brain.entry(seq)["timings_ms"]
    assert set(timings) == {"entitlements", "plan", "gate1", "expand", "gate2", "assemble", "answer", "guard"}
    assert all(v >= 0 for v in timings.values())


def test_every_sentence_carries_evidence_from_its_cited_chunk(brain):
    body, seq, _ = brain.ask("jdoe", SCENARIO_1)
    entry = brain.entry(seq)
    sent = {s["chunk"] for s in entry["sent_to_model"]}
    chunk_text = {c.chunk_id: c.text for doc in body["citations"] for c in brain.brain.index.get_chunks(doc["doc"])}
    assert body["sentences"], "structured sentences accompany every answer"
    for sentence in body["sentences"]:
        assert sentence["citations"]
        assert {e["doc"] for e in sentence["evidence"]} == set(sentence["citations"])
        for ev in sentence["evidence"]:
            assert ev["chunk"] in sent, "evidence only ever comes from what the model was given"
            assert ev["quote"] in chunk_text[ev["chunk"]]
    for citation in body["citations"]:
        assert citation["chunks"] and set(citation["chunks"]) <= sent


def test_no_result_carries_no_sentences(brain):
    body, _, r = brain.ask("ctr-lee", "Where is the Q3 breach report?")
    assert body["no_result"] and body["sentences"] == [] and body["citations"] == []


def test_source_open_rechecks_both_gates_and_is_audited(brain):
    body, _, _ = brain.ask("jdoe", SCENARIO_1)
    thread = next(c["doc"] for c in body["citations"] if c["doc"].startswith("slack:C0DBM:"))
    r = brain.c.get(f"/sources/{thread}", headers={"X-User-Id": "jdoe"})
    view = r.json()
    assert view["available"] and view["rule"] == "slack:channel:C0DBM" and view["chunks"]
    entry = brain.entry(int(r.headers["X-Audit-Seq"]))
    assert entry["kind"] == "source_open" and entry["decisions"][0]["gate2"] == "allow"

    # revoke without the webhook: Gate 1 still passes on the warm cache, Gate 2 denies at open
    brain.admin("POST", "/admin/slack/channels/C0DBM/members", json={"user_id": "jdoe", "member": False, "notify": False})
    revoked = brain.c.get(f"/sources/{thread}", headers={"X-User-Id": "jdoe"})
    assert revoked.json() == {**revoked.json(), "available": False, "message": SOURCE_UNAVAILABLE_MESSAGE, "chunks": []}
    d = brain.entry(int(revoked.headers["X-Audit-Seq"]))["decisions"][0]
    assert d["gate1"] == "allow" and d["gate2"] == "deny" and d["rule"] == "revoked"

    access = brain.compliance("/audit/views/doc-access", doc=thread)["accesses"]
    assert {(row["actor_id"], row["kind"], row["decision"]) for row in access} >= {("jdoe", "source_open", "allow"), ("jdoe", "source_open", "deny")}


def test_unavailable_sources_are_byte_identical(brain):
    restricted = brain.c.get("/sources/confluence:9001", headers={"X-User-Id": "ctr-lee"})
    missing = brain.c.get("/sources/confluence:424242", headers={"X-User-Id": "ctr-lee"})
    bogus = brain.c.get("/sources/not-a-platform:1", headers={"X-User-Id": "ctr-lee"})
    assert restricted.content == missing.content == bogus.content
    assert restricted.json()["available"] is False
    assert "9001" not in restricted.text and "CANARY" not in restricted.text


def test_source_open_refreshes_a_stale_document(brain):
    brain.admin("POST", "/admin/sync/pause")
    brain.admin("POST", "/admin/confluence/pages/8812/edit", json={"append": "Step 4 (failover): promote the standby gateway.", "notify": True})
    r = brain.c.get("/sources/confluence:8812", headers={"X-User-Id": "jdoe"})
    view = r.json()
    assert view["available"] and view["refreshed"] and view["version"] == 8
    assert any("Step 4 (failover)" in c["text"] for c in view["chunks"])


def _read_events(response, want: int) -> list[dict]:
    events, current = [], None
    for line in response.iter_lines():
        if line.startswith("event: "):
            current = line[len("event: "):]
        elif line.startswith("data: ") and current == "entry":
            events.append(json.loads(line[len("data: "):]))
            if len(events) >= want:
                break
    return events


def test_audit_stream_backlog_then_live(brain, client):
    assert client.get("/audit/stream", headers={"X-User-Id": "jdoe"}).status_code == 403
    _, first_seq, _ = brain.ask("jdoe", SCENARIO_2)
    # The test client returns a streamed body once the app finishes, so the live entry is
    # produced from another thread while the stream is open, and `limit` ends the stream.
    timer = threading.Timer(0.5, lambda: brain.ask("ctr-lee", "Where is the Q3 breach report?"))
    timer.start()
    with client.stream("GET", "/audit/stream", params={"since": first_seq - 1, "limit": 2, "timeout_s": 20}, headers={"X-User-Id": "compliance"}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _read_events(response, 2)
    timer.join()
    assert events[0]["seq"] == first_seq and events[0]["kind"] == "query" and events[0]["actor"] == "jdoe"
    assert events[1]["kind"] in ("query", "alert") and events[1]["seq"] > first_seq
    assert all(e["kind"] != "audit_read" for e in events), "the console does not watch itself"
    assert all(e["entry_hash"] and e["prev_hash"] for e in events)
