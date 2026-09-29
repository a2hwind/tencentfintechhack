"""SLACK_MODE=real: the Slack Web API adapter and the Events API webhook, against a strict fake of
Slack (tests/fake_slack.py) holding the Company A workspace. No impersonation, real pagination,
rate limits, signed events."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import CANARIES, SCENARIO_1, BrainClient, decisions_for, denied_docs
from fake_slack import TOKEN, FakeSlack

from internal_brain.adapters.slack_events import sign, translate, verify_signature
from internal_brain.adapters.slack_real import SlackWebAdapter, extract_links
from internal_brain.api.app import create_app
from internal_brain.config import Settings
from internal_brain.core.identity import IdentityDirectory
from internal_brain.mocks.store import CompanyStore

SECRET = "test-signing-secret-4f1e"
TEAM = "T0COMPA"


def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
        data_dir=tmp_path / "data",
        sync_interval_s=0,
        slack_mode="real",
        slack_bot_token=TOKEN,
        slack_signing_secret=SECRET,
        slack_membership_ttl_s=0,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def fake() -> FakeSlack:
    return FakeSlack(CompanyStore.from_yaml(Settings().fixture_path))


@pytest.fixture
def real(tmp_path, fake):
    app = create_app(_settings(tmp_path), initial_sync=True, poller=False, transports={"slack": httpx.ASGITransport(app=fake.app)})
    with TestClient(app) as client:
        yield BrainClient(client)


def deliver(b: BrainClient, event: dict, event_id: str, *, ts: str | None = None, secret: str = SECRET, team: str = TEAM) -> httpx.Response:
    payload = {"token": "unused", "team_id": team, "api_app_id": "A0BRAIN", "event": event, "type": "event_callback", "event_id": event_id, "event_time": int(time.time())}
    body = json.dumps(payload).encode()
    stamp = str(int(time.time())) if ts is None else ts
    headers = {"X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": sign(secret, stamp, body), "Content-Type": "application/json"}
    response = b.c.post("/webhooks/slack", content=body, headers=headers)
    b.c.portal.call(b.brain.sync.drain)  # let the re-syncs the event triggered land
    return response


def _slack_cited(body) -> list[str]:
    return [c["doc"] for c in body["citations"] if c["doc"].startswith("slack:")]


# ---------------------------------------------------------------------------
# Connector: sync, pagination, rendering, strictness
# ---------------------------------------------------------------------------
def test_real_mode_indexes_the_bots_channels_through_pagination(real, fake):
    health = real.c.get("/health").json()
    assert health["connectors"]["slack"] == "real" and health["slack"]["team"]["id"] == TEAM and health["slack"]["webhook"] is True
    indexed = {i.item_id for i in real.brain.index.list_items() if i.platform == "slack"}
    expected = {f"slack:{t.channel}:{t.ts}" for t in fake.store.threads.values() if not fake.store.channels[t.channel].is_dm}
    assert indexed == expected, "every thread of every channel the bot is in, and no DM"
    paged = {m for m, p in fake.calls if p.get("cursor")}
    assert {"conversations.list", "conversations.replies"} <= paged, "cursors were followed"
    assert fake.violations == [], "only real Slack parameters, no impersonation header"
    assert real.c.get("/mock/slack/api/team.info").status_code == 404, "the Slack mock is not mounted in real mode"

    blockers = real.brain.index.get_item(fake.thread_id("db-blockers"))
    text = "\n".join(c.text for c in real.brain.index.get_chunks(blockers.item_id))
    assert "Tan Jun:" in text and blockers.container_label == "#db-migration" and blockers.version == 4
    assert "Tr0ub4dor&3" not in text, "DLP masks the pasted password at ingestion"
    assert "jira:DBM-45" in blockers.links
    assert "confluence:7201" in real.brain.index.get_item(fake.thread_id("auth-design")).links
    assert blockers.url.startswith("https://company-a.slack.com/archives/C0DBM/p")


def test_scenario_1_and_the_leads_channel_in_real_mode(real, fake):
    body, seq, _ = real.ask("jdoe", SCENARIO_1)
    cited = _slack_cited(body)
    assert fake.thread_id("db-blockers") in cited
    assert not any(c.startswith("slack:C0DBML:") for c in cited), "jdoe is not in the leads channel"
    for canary in CANARIES:
        assert canary not in body["answer"]
    d = decisions_for(real.entry(seq), fake.thread_id("db-blockers"))[0]
    assert d["gate1"] == "allow" and d["gate2"] == "allow" and d["rule"] == "slack:channel:C0DBM"


def test_missed_webhook_revocation_is_caught_by_gate2(real, fake):
    real.ask("jdoe", SCENARIO_1)  # warm the entitlement cache
    fake.remove_member("C0DBM", "jdoe")  # in Slack, with no event delivered
    body, seq, _ = real.ask("jdoe", SCENARIO_1)
    assert not any(c.startswith("slack:C0DBM:") for c in _slack_cited(body))
    d = decisions_for(real.entry(seq), fake.thread_id("db-blockers"))[0]
    assert (d["gate1"], d["gate2"], d["rule"], d["gate1_rule"]) == ("allow", "deny", "revoked", "slack:channel:C0DBM")


def test_gate2_shares_one_membership_lookup_per_channel(real, fake):
    real.brain.adapters["slack"]._members.ttl = 2.0  # the default (the other tests use 0: no reuse)
    fake.calls.clear()
    real.ask("jdoe", SCENARIO_1)
    per_channel: dict[str, int] = {}
    for method, params in fake.calls:
        if method == "conversations.members" and not params.get("cursor"):
            per_channel[params["channel"]] = per_channel.get(params["channel"], 0) + 1
    assert per_channel and max(per_channel.values()) == 1, per_channel
    assert "C0PAY" not in per_channel, "a public channel needs no member list for a full member"


def test_rate_limits_are_retried_after_retry_after_then_fail_closed(real, fake):
    adapter: SlackWebAdapter = real.brain.adapters["slack"]
    waits: list[float] = []

    async def no_sleep(seconds: float) -> None:
        waits.append(seconds)

    adapter._sleep = no_sleep
    thread = fake.thread_id("db-progress")
    fake.ratelimit("conversations.replies", 2)
    view = real.c.get(f"/sources/{thread}", headers={"X-User-Id": "jdoe"}).json()
    assert view["available"] and waits == [1.0, 1.0] and adapter.stats["rate_limited"] >= 2

    fake.ratelimit("conversations.replies", 10)
    blocked = real.c.get(f"/sources/{thread}", headers={"X-User-Id": "jdoe"})
    assert blocked.json()["available"] is False
    d = real.entry(int(blocked.headers["X-Audit-Seq"]))["decisions"][0]
    assert d["gate2"] == "deny" and d["rule"] == "unverifiable", "never served on a check Slack did not answer"
    fake.rate_limits.clear()


def test_bot_not_invited_means_not_indexed(tmp_path):
    fake = FakeSlack(CompanyStore.from_yaml(Settings().fixture_path), bot_channels={"C0DBM", "C0INC"})
    app = create_app(_settings(tmp_path), initial_sync=True, poller=False, transports={"slack": httpx.ASGITransport(app=fake.app)})
    with TestClient(app) as client:
        channels = {i.container for i in client.app.state.brain.index.list_items() if i.platform == "slack"}
        assert channels == {"slack:channel:C0DBM", "slack:channel:C0INC"}, "public channels too: only where the bot was invited"
        assert not any(m == "conversations.history" and p.get("channel") == "C0PAY" for m, p in fake.calls)


def test_guests_get_no_workspace_token_and_deactivated_users_get_nothing(real, fake):
    adapter: SlackWebAdapter = real.brain.adapters["slack"]
    principals = real.c.portal.call(adapter.principals_for, "U0CTRLEE")
    assert "slack:channel:C0VEND" in principals and not any(p.startswith("slack:workspace:") for p in principals)
    body, _, _ = real.ask("ctr-lee", "What did the team decide about access token lifetime in the auth design discussion?")
    assert not any(c.startswith("slack:C0AUTH:") for c in _slack_cited(body)), "a guest cannot read public channels they are not in"

    fake.deactivated.add("U0JDOE")
    r = deliver(real, {"type": "user_change", "user": {"id": "U0JDOE", "deleted": True, "team_id": TEAM}}, "Ev-deact")
    assert r.status_code == 200
    body, _, _ = real.ask("jdoe", SCENARIO_1)
    assert _slack_cited(body) == [], "a deactivated account reads nothing in Slack"
    events = real.compliance("/audit/entries", kind="permission_event")["entries"]
    assert any((e.get("event") or {}).get("action") == "deactivated" for e in events)


def test_admin_mock_slack_controls_are_off_in_real_mode(real):
    r = real.c.post("/admin/slack/channels/C0DBM/members", json={"user_id": "jdoe", "member": False}, headers={"X-User-Id": "admin"})
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Events API webhook
# ---------------------------------------------------------------------------
def test_webhook_rejects_unsigned_stale_and_forged_requests(real):
    body = json.dumps({"type": "url_verification", "challenge": "abc123", "token": "x"}).encode()
    now = str(int(time.time()))
    assert real.c.post("/webhooks/slack", content=body).status_code == 401
    stale = str(int(time.time()) - 600)
    assert real.c.post("/webhooks/slack", content=body, headers={"X-Slack-Request-Timestamp": stale, "X-Slack-Signature": sign(SECRET, stale, body)}).status_code == 401
    assert real.c.post("/webhooks/slack", content=body, headers={"X-Slack-Request-Timestamp": now, "X-Slack-Signature": sign("wrong-secret", now, body)}).status_code == 401
    ok = real.c.post("/webhooks/slack", content=body, headers={"X-Slack-Request-Timestamp": now, "X-Slack-Signature": sign(SECRET, now, body)})
    assert ok.status_code == 200 and ok.json() == {"challenge": "abc123"}


def test_webhook_is_off_in_mock_mode(client):
    assert client.post("/webhooks/slack", content=b"{}").status_code == 404


def test_member_left_event_revokes_at_gate1_and_is_audited(real, fake):
    body, _, _ = real.ask("jdoe", SCENARIO_1)
    assert fake.thread_id("db-blockers") in _slack_cited(body)
    fake.remove_member("C0DBM", "jdoe")
    r = deliver(real, {"type": "member_left_channel", "user": "U0JDOE", "channel": "C0DBM", "channel_type": "G", "team": TEAM}, "Ev-left")
    assert r.status_code == 200
    after, seq, _ = real.ask("jdoe", SCENARIO_1)
    assert not any(c.startswith("slack:C0DBM:") for c in _slack_cited(after))
    assert denied_docs(real.entry(seq)).get(fake.thread_id("db-blockers")) == "revoked"
    d = decisions_for(real.entry(seq), fake.thread_id("db-blockers"))[0]
    assert d["gate1"] == "deny", "the event reached the entitlement cache: Gate 1 already knew"
    events = real.compliance("/audit/entries", kind="permission_event")["entries"]
    assert any(e["actor"] == "slack-events-api" and e["event"]["channel"] == "C0DBM" for e in events)


def test_message_events_resync_and_tombstone_threads(real, fake):
    thread = fake.thread_id("db-blockers")
    before = real.brain.index.get_item(thread).version
    reply_ts = fake.reply("db-blockers", "mlim", "Update: the replica volume resize landed early and replica lag is down to 2 minutes.")
    event = {"type": "message", "channel": "C0DBM", "user": "U0MLIM", "text": "Update...", "ts": reply_ts, "thread_ts": thread.split(":")[2], "channel_type": "group"}
    assert deliver(real, event, "Ev-reply").json()["published"] == [f"content_changed:{thread}"]
    item = real.brain.index.get_item(thread)
    assert item.version == before + 1
    assert any("resize landed early" in c.text for c in real.brain.index.get_chunks(thread))

    # Slack retries until acknowledged: a repeat of the same event id is a no-op
    again = deliver(real, event, "Ev-reply")
    assert again.json() == {"ok": True, "duplicate": True}

    root_ts = fake.delete_thread("db-progress")
    deleted = {"type": "message", "subtype": "message_deleted", "channel": "C0DBM", "deleted_ts": root_ts, "hidden": True, "previous_message": {"type": "message", "ts": root_ts, "user": "U0JDOE"}}
    deliver(real, deleted, "Ev-del")
    assert real.brain.index.get_item(f"slack:C0DBM:{root_ts}") is None


def test_removing_the_bot_disconnects_the_channel(real, fake):
    pay = {i.item_id for i in real.brain.index.list_items() if i.container == "slack:channel:C0PAY"}
    assert pay
    fake.bot_channels.discard("C0PAY")
    deliver(real, {"type": "channel_left", "channel": "C0PAY", "actor_id": "U0MLIM"}, "Ev-botleft")
    assert all(real.brain.index.get_item(i) is None for i in pay)

    # the same without an event: the next poll tombstones what the bot can no longer read
    inc = {i.item_id for i in real.brain.index.list_items() if i.container == "slack:channel:C0INC"}
    fake.bot_channels.discard("C0INC")
    real.admin("POST", "/admin/sync/run")
    assert inc and all(real.brain.index.get_item(i) is None for i in inc)


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
def test_signature_helpers():
    body = b'{"type":"event_callback"}'
    ts = str(int(time.time()))
    assert verify_signature(SECRET, ts, body, sign(SECRET, ts, body))
    assert not verify_signature(SECRET, ts, body + b" ", sign(SECRET, ts, body))
    assert not verify_signature(SECRET, "not-a-number", body, sign(SECRET, "not-a-number", body))
    assert not verify_signature("", ts, body, sign("", ts, body))


def test_event_translation():
    t = translate({"type": "message", "subtype": "message_changed", "channel": "C1", "message": {"ts": "2.0", "thread_ts": "1.0"}})
    assert [(e.kind, e.payload["item_id"]) for e in t.events] == [("content_changed", "slack:C1:1.0")]
    t = translate({"type": "message", "subtype": "message_deleted", "channel": "C1", "deleted_ts": "2.0", "previous_message": {"ts": "2.0", "thread_ts": "1.0"}})
    assert [(e.kind, e.payload["item_id"]) for e in t.events] == [("content_changed", "slack:C1:1.0")], "a reply was deleted: re-sync"
    t = translate({"type": "message", "subtype": "channel_join", "channel": "C1", "ts": "3.0"})
    assert t.events == [] and t.ignored == "message/channel_join"
    t = translate({"type": "group_rename", "channel": {"id": "G1", "name": "new"}})
    assert t.resync_container == "slack:channel:G1"
    assert translate({"type": "reaction_added"}).ignored == "reaction_added"


def test_links_and_rendering():
    assert extract_links("see https://wiki.company-a.com/pages/7201 and DBM-45, also <https://docs.google.com/document/d/1AbCdEfGhIjKlMn/edit|the plan>") == [
        "jira:DBM-45", "confluence:7201", "gdrive:1AbCdEfGhIjKlMn"
    ]

    class Stub(SlackWebAdapter):
        async def _name(self, user_id, fallback=None):  # type: ignore[override]
            return {"U1": "Mei Lim"}.get(user_id, user_id)

    adapter = Stub(httpx.AsyncClient(), token="x")
    text = asyncio.run(adapter.render("<@U1> see <#C0DBM|db-migration> &amp; <https://x.example/a|the doc> <!here> 1 &lt; 2"))
    assert text == "@Mei Lim see #db-migration & the doc (https://x.example/a) @here 1 < 2"


def test_user_map_replaces_fixture_slack_ids(tmp_path, fake):
    settings = _settings(tmp_path, slack_user_map={"jdoe": "U0JDOE", "mlim": "U0MLIM"})
    app = create_app(settings, initial_sync=False, poller=False, transports={"slack": httpx.ASGITransport(app=fake.app)})
    directory = app.state.brain.directory
    assert directory.platform_ids_for("jdoe")["slack"] == "U0JDOE"
    assert "slack" not in directory.platform_ids_for("jr-tan"), "unmapped: no Slack identity, no Slack access"
    assert directory.user_for("slack", "U0JRTAN") is None


def test_real_mode_needs_a_token(tmp_path):
    with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
        create_app(Settings(data_dir=tmp_path / "d", slack_mode="real", slack_bot_token=""), initial_sync=False, poller=False)


def test_setup_script_checks_maps_and_seeds_a_workspace(fake):
    import importlib.util

    spec = importlib.util.spec_from_file_location("slack_setup", Path(__file__).resolve().parents[1] / "scripts" / "slack_setup.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)  # type: ignore[union-attr]
    store = CompanyStore.from_yaml(Settings().fixture_path)

    async def run():
        adapter = SlackWebAdapter(httpx.AsyncClient(base_url="https://slack.com/api", transport=httpx.ASGITransport(app=fake.app)), TOKEN)
        report = await setup.check(adapter)
        mapping, missing = await setup.build_map(adapter, IdentityDirectory.from_fixture(Settings().fixture_path), {})

        async def no_pause(_: float) -> None:
            return None

        seeded = await setup.seed(adapter, store, prefix="ib-", user_map=mapping, pause=no_pause)
        new_threads = []
        for c in seeded["channels"]:
            new_threads += [m async for m in adapter.paginate("conversations.history", "messages", channel=c["id"])]
        await adapter.client.aclose()
        return report, mapping, missing, seeded, new_threads

    report, mapping, missing, seeded, new_threads = asyncio.run(run())
    assert report["ok"] and report["missing_read_scopes"] == [] and len(report["channels_readable"]) == 7
    assert mapping["jdoe"] == "U0JDOE" and mapping["ctr-lee"] == "U0CTRLEE"
    assert {c["name"] for c in seeded["channels"]} >= {"ib-payments", "ib-db-migration", "ib-security-private"}
    dbm = next(c for c in seeded["channels"] if c["name"] == "ib-db-migration")
    assert dbm["private"] and set(dbm["invited"]) == {"U0JDOE", "U0JRTAN", "U0MLIM"}
    fixture_threads = [t for t in store.threads.values() if not store.channels[t.channel].is_dm]
    assert seeded["threads"] == len(fixture_threads) == len(new_threads)
    assert any(m.get("username") == "Tan Jun" for m in new_threads), "posted under the author's name"
    assert fake.violations == []
