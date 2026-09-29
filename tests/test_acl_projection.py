"""Each platform's native rule becomes principal tokens without losing fidelity."""

from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def _acl(brain, item_id: str) -> list[str]:
    platform = item_id.split(":")[0]
    return sorted(_run(brain.brain.adapters[platform].get_acl(item_id)).allowed_principals)


def _principals(brain, user: str) -> list[str]:
    identity = brain.brain.directory.get(user)
    return _run(brain.brain.entitlements.principals_for(identity, use_cache=False)).tokens


def test_confluence_space_and_restriction_projection(brain):
    assert _acl(brain, "confluence:8812") == ["confluence:space:PAYGW"]
    # a restriction narrows the space: its grantees are compound tokens (grantee AND space)
    assert _acl(brain, "confluence:9001") == ["confluence:space:SEC:group:security-team"]
    # a page restricted to named individuals carries only their tokens...
    assert _acl(brain, "confluence:7305") == ["confluence:space:ENG:user:mlim", "confluence:space:ENG:user:sec-ho"]
    # ...and the child inherits the restriction
    assert _acl(brain, "confluence:7306") == ["confluence:space:ENG:user:mlim", "confluence:space:ENG:user:sec-ho"]
    jdoe = _principals(brain, "jdoe")
    assert {"confluence:space:ENG", "confluence:space:ENG:user:jdoe", "confluence:space:ENG:group:eng"} <= set(jdoe)
    assert not any(t.startswith("confluence:space:SEC") for t in jdoe), "no SEC space access: no SEC tokens of any kind"


def test_restrictions_and_security_levels_only_narrow(brain):
    """Found by the randomized projection test (tests/test_scale.py): a person named in a page
    restriction without access to the space, or in a Jira security level without browse
    permission on the project, must get nothing at Gate 1 (Gate 2 was already denying them)."""
    brain.brain.store.confluence_set_restrictions("9001", users=["jdoe"], groups=["security-team"], notify=False)
    acl = _acl(brain, "confluence:9001")
    assert "confluence:space:SEC:user:jdoe" in acl
    assert not set(_principals(brain, "jdoe")) & set(acl), "named on the page, but cannot see the SEC space"
    brain.brain.store.projects["DBM"].security_levels["Leads"] = ["sec-ho"]  # sec-ho is not in a DBM role
    assert "jira:project:DBM:level:leads" not in _principals(brain, "sec-ho")


def test_jira_role_and_security_level_projection(brain):
    assert _acl(brain, "jira:DBM-42") == ["jira:project:DBM:role:developers"]
    assert _acl(brain, "jira:PAY-260") == ["jira:project:PAY:level:security-only"]
    jdoe = _principals(brain, "jdoe")
    assert "jira:project:PAY:role:developers" in jdoe and "jira:project:PAY:level:security-only" not in jdoe
    assert "jira:project:PAY:level:security-only" in _principals(brain, "sec-ho")


def test_slack_channel_workspace_and_dm_projection(brain):
    threads = {t.channel: f"slack:{t.item_key}" for t in brain.brain.store.threads.values()}
    assert _acl(brain, threads["C0DBM"]) == ["slack:channel:C0DBM"]  # private: members only
    assert _acl(brain, threads["C0PAY"]) == ["slack:channel:C0PAY", "slack:workspace:T0COMPA"]  # public: any full member
    assert _acl(brain, threads["D0JDSH"]) == ["slack:user:U0JDOE", "slack:user:U0SECHO"]  # DM: participants
    lee = _principals(brain, "ctr-lee")
    assert "slack:channel:C0VEND" in lee and "slack:workspace:T0COMPA" not in lee, "a guest gets no workspace token"
    assert not any(t.startswith(("confluence:", "jira:")) for t in lee), "no Confluence or Jira account, no tokens"


def test_drive_inheritance_and_link_sharing_projection(brain):
    assert _acl(brain, "gdrive:1postmortemQ2") == ["gdrive:group:eng", "gdrive:user:jdoe@company-a.com"]
    # folder grant is inherited by the file inside it, on top of the drive membership
    assert set(_acl(brain, "gdrive:1vendorspec")) == {"gdrive:group:eng", "gdrive:user:lee@vendorworks.example"}
    # "anyone with the link" adds no token
    assert _acl(brain, "gdrive:1onboarding") == ["gdrive:group:eng", "gdrive:user:mlim@company-a.com"]
    assert "gdrive:domain:company-a.com" in _principals(brain, "jdoe")
    assert "gdrive:domain:company-a.com" not in _principals(brain, "ctr-lee")


def test_index_stores_acl_as_data(brain):
    item = brain.brain.index.get_item("confluence:9001")
    assert item.allowed_principals == ["confluence:space:SEC:group:security-team"]
    idx = brain.brain.index
    assert idx.granting_token("confluence:9001", ["confluence:space:SEC:group:security-team"]) == "confluence:space:SEC:group:security-team"
    assert idx.granting_token("confluence:9001", ["confluence:space:SEC"]) is None, "space membership alone does not open a restricted page"
    assert idx.search_fts("breach report", [], None, 10) == [], "an empty principal set matches nothing"


def test_can_read_reflects_live_membership(brain):
    slack = brain.brain.adapters["slack"]
    thread = next(f"slack:{t.item_key}" for t in brain.brain.store.threads.values() if t.channel == "C0DBM")
    assert _run(slack.can_read("U0JDOE", thread)).allowed
    brain.brain.store.slack_set_membership("C0DBM", "jdoe", False, notify=False)
    check = _run(slack.can_read("U0JDOE", thread))
    assert not check.allowed and check.reason == "not_member"
    brain.brain.store.delete_item(thread, notify=False)
    assert _run(slack.can_read("U0MLIM", thread)).reason == "deleted"
