"""Permission changes on the document side and on the user side, with and without the webhook,
plus deletion tombstones. Each change must be reflected in the next answer and in the audit."""

from __future__ import annotations

from conftest import decisions_for, denied_docs

ARCH_Q = "What does the payment gateway do during failover?"
AUTH_Q = "What was decided about auth service access tokens?"
CERT_Q = "How is the upstream certificate rotation being automated?"
VENDOR_Q = "What does the vendor portal API spec say about webhook retries?"
LAG_Q = "Why is the migration dry run blocked by replica lag?"


def _cited(body):
    return [c["doc"] for c in body["citations"]]


def test_page_restriction_change_rides_the_sync_path(brain):
    before, _, _ = brain.ask("jdoe", ARCH_Q)
    assert "confluence:8813" in _cited(before)

    # Restrict the architecture page to the security lead; the content event re-indexes the ACL.
    brain.admin("POST", "/admin/confluence/pages/8813/restrictions", json={"users": ["sec-ho"], "groups": [], "notify": True})
    assert brain.brain.index.get_item("confluence:8813").allowed_principals == ["confluence:space:PAYGW:user:sec-ho"]

    after, seq, _ = brain.ask("jdoe", ARCH_Q)
    assert "confluence:8813" not in _cited(after)
    assert denied_docs(brain.entry(seq)).get("confluence:8813") == "revoked"
    still, _, _ = brain.ask("sec-ho", ARCH_Q)
    assert "confluence:8813" in _cited(still)


def test_page_restriction_change_without_webhook_is_caught_by_gate2(brain):
    brain.ask("jdoe", ARCH_Q)
    brain.admin("POST", "/admin/confluence/pages/8813/restrictions", json={"users": ["sec-ho"], "groups": [], "notify": False})
    assert brain.brain.index.get_item("confluence:8813").allowed_principals == ["confluence:space:PAYGW"], "index still stale"
    after, seq, _ = brain.ask("jdoe", ARCH_Q)
    assert "confluence:8813" not in _cited(after)
    d = decisions_for(brain.entry(seq), "confluence:8813")[0]
    assert d["gate1"] == "allow" and d["gate2"] == "deny" and d["rule"] == "revoked"


def test_jira_security_level_removes_the_issue_for_non_members(brain):
    before, _, _ = brain.ask("jdoe", CERT_Q)
    assert "jira:PAY-231" in _cited(before)
    brain.admin("POST", "/admin/jira/issues/PAY-231/security-level", json={"level": "Security only", "notify": True})
    assert brain.brain.index.get_item("jira:PAY-231").allowed_principals == ["jira:project:PAY:level:security-only"]
    after, seq, _ = brain.ask("jdoe", CERT_Q)
    assert "jira:PAY-231" not in _cited(after)
    assert denied_docs(brain.entry(seq)).get("jira:PAY-231") == "revoked"
    sec, _, _ = brain.ask("sec-ho", CERT_Q)
    assert "jira:PAY-231" in _cited(sec)


def test_group_membership_change_propagates_to_confluence_and_drive(brain):
    before, _, _ = brain.ask("jr-tan", AUTH_Q)
    assert "confluence:7201" in _cited(before)
    brain.admin("POST", "/admin/groups/eng/members", json={"user_id": "jr-tan", "member": False, "notify": True})
    after, seq, _ = brain.ask("jr-tan", AUTH_Q)
    assert "confluence:7201" not in _cited(after)
    entry = brain.entry(seq)
    assert "confluence:space:ENG" not in entry["actor"]["principals"]
    assert "gdrive:group:eng" not in entry["actor"]["principals"]
    assert denied_docs(entry).get("confluence:7201") == "revoked"


def test_deletion_tombstone(brain):
    before, _, _ = brain.ask("jdoe", LAG_Q)
    assert "jira:DBM-45" in _cited(before)
    # deleted at the source, webhook missed: Gate 2 sees the 404 immediately
    brain.admin("DELETE", "/admin/items/jira:DBM-45", params={"notify": "false"})
    assert brain.brain.index.get_item("jira:DBM-45") is not None
    after, seq, _ = brain.ask("jdoe", LAG_Q)
    assert "jira:DBM-45" not in _cited(after)
    d = decisions_for(brain.entry(seq), "jira:DBM-45")[0]
    assert d["gate2"] == "deny" and d["rule"] == "deleted"
    # the next poll writes the tombstone
    brain.admin("POST", "/admin/sync/run")
    assert brain.brain.index.get_item("jira:DBM-45") is None


def test_drive_share_then_unshare_for_the_contractor(brain):
    plan_q = "What is the cutover plan for the orders table migration?"
    before, _, _ = brain.ask("ctr-lee", plan_q)
    assert before["no_result"], "the cutover plan lives in the Engineering drive: not the contractor's"

    brain.admin("POST", "/admin/gdrive/files/1cutoverplan/share", json={"user_id": "ctr-lee", "share": True, "notify": True})
    assert "gdrive:user:lee@vendorworks.example" in brain.brain.index.get_item("gdrive:1cutoverplan").allowed_principals
    shared, seq_shared, _ = brain.ask("ctr-lee", plan_q)
    assert "gdrive:1cutoverplan" in _cited(shared)
    assert {p["doc"]: p["rule"] for p in shared["provenance"]}["gdrive:1cutoverplan"] == "gdrive:user:lee@vendorworks.example"

    brain.admin("POST", "/admin/gdrive/files/1cutoverplan/share", json={"user_id": "ctr-lee", "share": False, "notify": True})
    after, seq, _ = brain.ask("ctr-lee", plan_q)
    assert after["no_result"]
    assert denied_docs(brain.entry(seq)).get("gdrive:1cutoverplan") == "revoked"


def test_demo_reset_restores_the_fixture_and_keeps_the_audit(brain):
    brain.admin("POST", "/admin/slack/channels/C0DBM/members", json={"user_id": "jdoe", "member": False, "notify": True})
    brain.admin("POST", "/admin/sync/pause")
    brain.admin("POST", "/admin/confluence/pages/8812/edit", json={"append": "Step 4 (failover): promote the standby.", "notify": True})
    entries_before = brain.brain.audit.count()

    result = brain.admin("POST", "/admin/reset")
    assert result["reset"] and result["sync"]["paused"] is False
    assert "jdoe" in brain.brain.store.channels["C0DBM"].members
    assert brain.brain.store.pages["8812"].version == 7
    assert brain.brain.index.get_item("confluence:8812").version == 7
    assert brain.brain.audit.count() > entries_before, "append-only: nothing removed, the reset is logged"
    assert brain.brain.audit.verify().ok
    body, _, _ = brain.ask("jdoe", "What's the status of the database migration and were there blockers raised in Slack last week?")
    assert any(c["doc"].startswith("slack:C0DBM:") for c in body["citations"])
