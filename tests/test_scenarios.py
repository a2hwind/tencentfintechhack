"""One test per handbook scenario, plus the cross-platform stitch and the contractor's positive path.

These are what make the live demo safe to run: every beat of the demo script is asserted here.
"""

from __future__ import annotations

import json

from conftest import CANARIES, CROSS_PLATFORM, SCENARIO_1, SCENARIO_2, SCENARIO_3, SCENARIO_3_CONTROL, allowed_docs, decisions_for, denied_docs

LEADS_THREAD_PREFIX = "slack:C0DBML:"
DBM_THREAD_PREFIX = "slack:C0DBM:"


def _docs_with_prefix(ids, prefix):
    return [d for d in ids if d.startswith(prefix)]


# ---------------------------------------------------------------------------
# Scenario 1: unified query
# ---------------------------------------------------------------------------
def test_scenario1_unified_query(brain):
    body, seq, _ = brain.ask("jdoe", SCENARIO_1)
    cited = [c["doc"] for c in body["citations"]]

    assert not body["no_result"]
    assert "jira:DBM-42" in cited and "jira:DBM-45" in cited, cited
    assert _docs_with_prefix(cited, DBM_THREAD_PREFIX), "the #db-migration blocker thread should be cited"
    assert not _docs_with_prefix(cited, LEADS_THREAD_PREFIX), "the private leads thread must never be cited"
    for canary in CANARIES:
        assert canary not in body["answer"]

    entry = brain.entry(seq)
    denied = denied_docs(entry)
    leads = _docs_with_prefix(denied, LEADS_THREAD_PREFIX)
    assert leads and denied[leads[0]] == "not_member", denied
    # nothing from the leads thread reached the model
    sent = [s["chunk"] for s in entry["sent_to_model"]]
    assert not _docs_with_prefix(sent, LEADS_THREAD_PREFIX)
    # provenance names the entitlement that granted each cited doc
    rules = {p["doc"]: p["rule"] for p in body["provenance"]}
    assert rules["jira:DBM-42"] == "jira:project:DBM:role:developers"
    assert rules[_docs_with_prefix(cited, DBM_THREAD_PREFIX)[0]] == "slack:channel:C0DBM"
    assert entry["plan"]["intent"] == "status"
    assert entry["outcome"] == "answered"


# ---------------------------------------------------------------------------
# Scenario 2: freshness via read-through refresh with the poller paused
# ---------------------------------------------------------------------------
def test_scenario2_freshness_readthrough(brain):
    before = brain.brain.index.get_item("confluence:8812")
    assert before is not None and before.version == 7

    brain.admin("POST", "/admin/sync/pause")
    step4 = "Step 4 (failover): promote the standby gateway in ap-southeast-1b and shift the DNS weight to 100% on the standby; roll back when the primary passes health checks."
    result = brain.admin("POST", "/admin/confluence/pages/8812/edit", json={"append": step4, "notify": True})
    assert result["version"] == 8
    assert result["indexed_version"] == 7, "with the poller paused the index must still hold the morning's version"

    body, seq, _ = brain.ask("jdoe", SCENARIO_2)
    assert "confluence:8812" in [c["doc"] for c in body["citations"]]
    assert "standby gateway" in body["answer"] and "Step 4" in body["answer"], body["answer"]

    entry = brain.entry(seq)
    decision = decisions_for(entry, "confluence:8812")[0]
    assert decision["gate2"] == "allow" and decision["refreshed"] is True and decision["version"] == 8
    after = brain.brain.index.get_item("confluence:8812")
    assert after.version == 8, "read-through refresh updates the index as a side effect"
    assert any(s["chunk"].startswith("confluence:8812#") for s in entry["sent_to_model"])


# ---------------------------------------------------------------------------
# Scenario 3: negative case with no metadata side-channel
# ---------------------------------------------------------------------------
def test_scenario3_negative_case_identical_response(brain):
    restricted, seq_restricted, r1 = brain.ask("ctr-lee", SCENARIO_3)
    missing, seq_missing, r2 = brain.ask("ctr-lee", SCENARIO_3_CONTROL)

    assert restricted["no_result"] and missing["no_result"]
    assert r1.content == r2.content, "a restricted document and a non-existent one must produce byte-identical bodies"
    assert restricted["citations"] == [] and restricted["provenance"] == []
    for canary in CANARIES:
        assert canary not in r1.text

    denied = denied_docs(brain.entry(seq_restricted))
    assert denied.get("confluence:9001") == "not_member", denied
    control = brain.entry(seq_missing)
    assert not control["decisions"], "the control query matched nothing: no_match, not a denial"
    assert control["sent_to_model"] == []
    assert brain.entry(seq_restricted)["sent_to_model"] == []


def test_contractor_gets_answers_on_what_they_may_see(brain):
    body, seq, _ = brain.ask("ctr-lee", "What changed in the vendor portal API spec?")
    cited = [c["doc"] for c in body["citations"]]
    assert "gdrive:1vendorspec" in cited or any(d.startswith("slack:C0VEND:") for d in cited), cited
    for canary in CANARIES:
        assert canary not in body["answer"]
    # the link-shared onboarding checklist is not granted to the contractor (stated trade-off)
    assert "gdrive:1onboarding" not in cited


# ---------------------------------------------------------------------------
# Scenario 4: live revocation, via the membership event and via Gate 2 when the webhook is missed
# ---------------------------------------------------------------------------
def test_scenario4_live_revocation_event(brain):
    first, seq1, _ = brain.ask("jdoe", SCENARIO_1)
    thread = _docs_with_prefix([c["doc"] for c in first["citations"]], DBM_THREAD_PREFIX)[0]

    brain.admin("POST", "/admin/slack/channels/C0DBM/members", json={"user_id": "jdoe", "member": False, "notify": True})

    second, seq2, _ = brain.ask("jdoe", SCENARIO_1)
    cited = [c["doc"] for c in second["citations"]]
    assert not _docs_with_prefix(cited, DBM_THREAD_PREFIX), "the channel's threads vanish from the next answer"
    assert "jira:DBM-42" in cited, "Jira access is untouched"

    entry = brain.entry(seq2)
    assert denied_docs(entry).get(thread) == "revoked", denied_docs(entry)
    assert "slack:channel:C0DBM" not in entry["actor"]["principals"]
    assert not any(s["chunk"].startswith(DBM_THREAD_PREFIX) for s in entry["sent_to_model"])
    # the permission change itself is on the chain
    events = brain.compliance("/audit/entries", kind="permission_event")["entries"]
    assert any(e["event"]["channel"] == "C0DBM" and e["event"]["user"] == "jdoe" for e in events)


def test_scenario4_gate2_catches_missed_webhook(brain):
    first, _, _ = brain.ask("jdoe", SCENARIO_1)
    thread = _docs_with_prefix([c["doc"] for c in first["citations"]], DBM_THREAD_PREFIX)[0]

    # notify=false: no membership event, the 60 s entitlement cache still carries slack:channel:C0DBM
    brain.admin("POST", "/admin/slack/channels/C0DBM/members", json={"user_id": "jdoe", "member": False, "notify": False})

    second, seq2, _ = brain.ask("jdoe", SCENARIO_1)
    cited = [c["doc"] for c in second["citations"]]
    assert not _docs_with_prefix(cited, DBM_THREAD_PREFIX)

    entry = brain.entry(seq2)
    assert "slack:channel:C0DBM" in entry["actor"]["principals"], "the stale cache still granted Gate 1"
    decision = decisions_for(entry, thread)[0]
    assert decision["gate1"] == "allow" and decision["gate2"] == "deny" and decision["rule"] == "revoked"
    assert not any(s["chunk"].startswith(DBM_THREAD_PREFIX) for s in entry["sent_to_model"])


# ---------------------------------------------------------------------------
# Scenario 5: audit inquiry
# ---------------------------------------------------------------------------
def test_scenario5_audit_inquiry(brain):
    brain.ask("jdoe", SCENARIO_2)
    brain.ask("jdoe", SCENARIO_1)
    brain.ask("ctr-lee", SCENARIO_3)

    view = brain.compliance("/audit/views/user-access", actor="jdoe", container="confluence:space:PAYGW", days=30)
    assert view["queries"], "jdoe's runbook query must be reconstructible"
    q = view["queries"][0]
    assert q["query"] == SCENARIO_2
    assert all(d["container"] == "confluence:space:PAYGW" for d in q["decisions"])
    assert any(d["doc"] == "confluence:8812" and d["gate1"] == "allow" and d["gate2"] == "allow" for d in q["decisions"])
    assert q["sent_to_model"] and q["ts"] and q["entry_hash"]
    assert any(d["doc"] == "confluence:8812" for d in view["documents"])
    assert "read_logged_as" in view

    nl = brain.compliance("/audit/nl", q="Everything jdoe accessed related to the payment gateway Confluence space in the last 30 days")
    assert nl["filter"] == {"actor": "jdoe", "container": "confluence:space:PAYGW", "platform": "confluence", "days": 30}, nl["filter"]
    assert nl["entries"] and nl["entries"][0]["actor"] == "jdoe"

    who = brain.compliance("/audit/views/doc-access", doc="confluence:9001")
    assert any(row["actor_id"] == "ctr-lee" and row["decision"] == "deny" for row in who["accesses"])

    denials = brain.compliance("/audit/views/denials", actor="ctr-lee")
    assert any(row["doc"] == "confluence:9001" and row["rule"] == "not_member" for row in denials["denials"])

    report = brain.compliance("/audit/verify")
    assert report["ok"] is True and report["entries"] > 0


# ---------------------------------------------------------------------------
# Cross-platform stitch: Drive postmortem -> Jira tickets through link expansion
# ---------------------------------------------------------------------------
def test_cross_platform_link_expansion(brain):
    body, seq, _ = brain.ask("jdoe", CROSS_PLATFORM)
    cited = [c["doc"] for c in body["citations"]]
    assert "gdrive:1postmortemQ2" in cited, cited
    assert "jira:PAY-231" in cited and "jira:PAY-232" in cited, cited
    # the security-level ticket in the same project never surfaces for jdoe
    assert "jira:PAY-260" not in cited
    assert "CANARY-PAY-260" not in json.dumps(body)


def test_link_expansion_passes_gate1_per_item(brain):
    """From the postmortem alone, one hop reaches both follow-up tickets; each linked item passes
    Gate 1 on its own, so a reader of the postmortem without the PAY project gets neither."""
    import asyncio

    from internal_brain.core.retrieve import Candidate, ChunkHit

    b = brain.brain
    item = b.index.get_item("gdrive:1postmortemQ2")
    seed = Candidate(item=item, chunks=[ChunkHit(c.chunk_id, c.text, c.sha256, 1.0) for c in b.index.get_chunks(item.item_id)], score=1.0, rule="gdrive:group:eng")

    jdoe = asyncio.run(b.entitlements.principals_for(b.directory.get("jdoe"), use_cache=False))
    extra, denied = b.retriever.expand_links([seed], jdoe)
    assert {c.item.item_id for c in extra} >= {"jira:PAY-231", "jira:PAY-232"}
    assert all(c.source == "expansion" and c.expanded_from == "gdrive:1postmortemQ2" for c in extra)
    assert not denied

    tan = asyncio.run(b.entitlements.principals_for(b.directory.get("jr-tan"), use_cache=False))
    extra, denied = b.retriever.expand_links([seed], tan)
    assert not extra, "jr-tan reads the postmortem but is not in the PAY project"
    assert {d.doc for d in denied} == {"jira:PAY-231", "jira:PAY-232"}
    assert all(d.gate1 == "deny" and d.source == "expansion" for d in denied)


def test_expansion_respects_gate1(brain):
    """The SEC report links to the forensics file; neither is visible to jdoe, so nothing is expanded or hinted."""
    body, seq, _ = brain.ask("jdoe", SCENARIO_3)
    cited = [c["doc"] for c in body["citations"]]
    assert "confluence:9001" not in cited and "gdrive:1forensicsQ3" not in cited
    assert "no related document" not in body["answer"].lower()
    entry = brain.entry(seq)
    for doc in ("confluence:9001", "gdrive:1forensicsQ3"):
        for d in decisions_for(entry, doc):
            assert d["gate1"] == "deny"
    assert not any(s["chunk"].startswith(("confluence:9001", "gdrive:1forensicsQ3")) for s in entry["sent_to_model"])


def test_unknown_identity_is_rejected(client):
    assert client.post("/ask", json={"question": "hello"}).status_code == 401
    assert client.post("/ask", json={"question": "hello"}, headers={"X-User-Id": "nobody"}).status_code == 401
