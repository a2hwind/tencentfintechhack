#!/usr/bin/env python3
"""Connect the Brain to a real Slack workspace (SLACK_MODE=real).

    export SLACK_BOT_TOKEN=xoxb-...
    python scripts/slack_setup.py check                  # token, scopes, workspace, channels the bot can read
    python scripts/slack_setup.py map                    # SLACK_USER_MAP from each Brain user's email
    python scripts/slack_setup.py map --email jdoe=alice@yourco.com --email mlim=mei@yourco.com
    python scripts/slack_setup.py seed --prefix ib-      # a TEST workspace: create the Company A channels, post its threads

Create the app at https://api.slack.com/apps (from scratch, in your workspace; an internal app
keeps Slack's standard rate-limit tiers). Bot token scopes:
    read (required):  channels:read groups:read channels:history groups:history users:read
    map:              users:read.email
    seed (optional):  channels:manage groups:write chat:write chat:write.customize
Install it, invite the bot to the channels the Brain may index (/invite @your-app), then run:
    SLACK_MODE=real SLACK_BOT_TOKEN=xoxb-... SLACK_USER_MAP=... uvicorn internal_brain.api.app:app
For events, set SLACK_SIGNING_SECRET and point Event Subscriptions at https://<host>/webhooks/slack
with bot events: member_joined_channel member_left_channel message.channels message.groups
channel_left group_left channel_deleted group_deleted channel_rename group_rename user_change.
Direct messages are out of scope: a bot token cannot read people's DMs, and the Brain should not.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from internal_brain.adapters.base import AdapterError  # noqa: E402
from internal_brain.adapters.slack_real import SlackApiError, SlackWebAdapter  # noqa: E402
from internal_brain.config import Settings  # noqa: E402
from internal_brain.core.identity import IdentityDirectory  # noqa: E402
from internal_brain.mocks.store import CompanyStore  # noqa: E402

READ_SCOPES = ["channels:read", "groups:read", "channels:history", "groups:history", "users:read"]
SEED_SCOPES = ["channels:manage", "groups:write", "chat:write", "chat:write.customize"]
SENSITIVE_REFS = {"payments-double-charge"}  # the DLP demo thread: synthetic test card number and NRIC


async def check(adapter: SlackWebAdapter, history_days: float = 90) -> dict:
    response = await adapter.client.post("/auth.test", headers={"Authorization": f"Bearer {adapter.token}"})
    auth = response.json()
    if not auth.get("ok"):
        return {"ok": False, "error": auth.get("error")}
    granted = [s for s in response.headers.get("x-oauth-scopes", "").split(",") if s]
    channels = [c async for c in adapter.paginate("conversations.list", "channels", types="public_channel,private_channel", exclude_archived=False)]
    readable = []
    oldest = f"{time.time() - history_days * 86400:.6f}"
    for c in channels:
        if not c.get("is_member"):
            continue
        n = 0
        async for _ in adapter.paginate("conversations.history", "messages", channel=c["id"], oldest=oldest):
            n += 1
        readable.append({"id": c["id"], "name": c["name"], "private": bool(c.get("is_private")), "messages": n})
    return {
        "ok": True,
        "team": {"id": auth.get("team_id"), "name": auth.get("team"), "url": auth.get("url")},
        "bot_user": auth.get("user_id"),
        "scopes": granted,
        "missing_read_scopes": [s for s in READ_SCOPES if s not in granted],
        "channels_visible": len(channels),
        "channels_readable": readable,
    }


async def build_map(adapter: SlackWebAdapter, directory: IdentityDirectory, emails: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    found: dict[str, str] = {}
    missing: list[str] = []
    for identity in directory.list():
        email = emails.get(identity.id, identity.email)
        try:
            user = (await adapter.call("users.lookupByEmail", email=email))["user"]
            found[identity.id] = user["id"]
        except SlackApiError as exc:
            missing.append(f"{identity.id} <{email}>: {exc.error}")
    return found, missing


async def seed(
    adapter: SlackWebAdapter,
    store: CompanyStore,
    prefix: str = "ib-",
    user_map: dict[str, str] | None = None,
    skip_sensitive: bool = False,
    pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
    dry_run: bool = False,
) -> dict:
    """Create the fixture's channels (prefixed) and post its threads as the bot, each message
    under its author's display name. Real members are invited where SLACK_USER_MAP names them."""
    user_map = user_map or {}
    existing = {c["name"]: c async for c in adapter.paginate("conversations.list", "channels", types="public_channel,private_channel", exclude_archived=True)}
    created: dict[str, str] = {}
    report: dict = {"channels": [], "threads": 0, "messages": 0, "skipped": []}
    for channel in store.channels.values():
        if channel.is_dm:
            report["skipped"].append(f"DM {channel.id}: direct messages are out of scope")
            continue
        name = f"{prefix}{channel.name}"[:80]
        if dry_run:
            created[channel.id] = f"(new #{name})"
            report["channels"].append({"fixture": channel.id, "name": name, "private": channel.private, "invited": []})
            continue
        if name in existing:
            if not existing[name].get("is_member"):
                report["skipped"].append(f"#{name} exists and the bot is not in it: /invite the bot, then re-run")
                continue
            cid = existing[name]["id"]
        else:
            cid = (await adapter.call("conversations.create", name=name, is_private=channel.private))["channel"]["id"]
        created[channel.id] = cid
        invite = sorted({user_map[u] for u in channel.members if u in user_map})
        if invite:
            try:
                await adapter.call("conversations.invite", channel=cid, users=",".join(invite))
            except SlackApiError as exc:
                if exc.error not in ("already_in_channel", "cant_invite_self"):
                    report["skipped"].append(f"invite to #{name}: {exc.error}")
        report["channels"].append({"fixture": channel.id, "name": name, "id": cid, "private": channel.private, "invited": invite})

    threads = sorted(store.threads.values(), key=lambda t: float(t.ts))
    for thread in threads:
        if thread.channel not in created or (skip_sensitive and thread.ref in SENSITIVE_REFS):
            continue
        root_ts: str | None = None
        for message in thread.messages:
            author = store.users[message.user].name if message.user in store.users else message.user
            if not dry_run:
                sent = await adapter.call(
                    "chat.postMessage", channel=created[thread.channel], text=message.text, username=author, icon_emoji=":bust_in_silhouette:", thread_ts=root_ts,
                )
                root_ts = root_ts or sent["ts"]
                await pause(1.1)  # chat.postMessage: about one message per second per channel
            report["messages"] += 1
        report["threads"] += 1
    return report


def _adapter() -> SlackWebAdapter:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token.startswith("xox"):
        sys.exit("Set SLACK_BOT_TOKEN to the app's bot token (xoxb-...).")
    base = os.environ.get("SLACK_API_BASE", "https://slack.com/api")
    return SlackWebAdapter(httpx.AsyncClient(base_url=base, timeout=15.0), token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="token, scopes, workspace and the channels the bot can read")
    m = sub.add_parser("map", help="print SLACK_USER_MAP from users.lookupByEmail")
    m.add_argument("--email", action="append", default=[], metavar="USER=EMAIL", help="use this email for a Brain user (repeatable)")
    s = sub.add_parser("seed", help="create the Company A channels in a TEST workspace and post the fixture threads")
    s.add_argument("--prefix", default="ib-")
    s.add_argument("--skip-sensitive", action="store_true", help="leave out the DLP demo thread (synthetic test card number and NRIC)")
    s.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    settings = Settings()
    adapter = _adapter()

    async def run() -> int:
        try:
            if args.command == "check":
                report = await check(adapter, settings.slack_history_days)
                if not report["ok"]:
                    print(f"auth.test failed: {report['error']}")
                    return 1
                print(f"workspace {report['team']['name']} ({report['team']['id']}), bot user {report['bot_user']}")
                print(f"scopes: {', '.join(report['scopes']) or '(none reported)'}")
                if report["missing_read_scopes"]:
                    print(f"  missing for SLACK_MODE=real: {', '.join(report['missing_read_scopes'])}")
                print(f"channels the bot can read ({len(report['channels_readable'])} of {report['channels_visible']} visible):")
                for c in report["channels_readable"]:
                    print(f"  #{c['name']:<28} {'private' if c['private'] else 'public ':<8} {c['messages']} top-level messages in the history window")
                if not report["channels_readable"]:
                    print("  none yet: /invite the bot to the channels the Brain may index")
                return 0 if not report["missing_read_scopes"] else 1
            if args.command == "map":
                emails = dict(pair.split("=", 1) for pair in args.email if "=" in pair)
                found, missing = await build_map(adapter, IdentityDirectory.from_fixture(settings.fixture_path), emails)
                print("SLACK_USER_MAP=" + ",".join(f"{k}={v}" for k, v in sorted(found.items())))
                for line in missing:
                    print(f"  not found: {line}")
                return 0
            report = await seed(
                adapter, CompanyStore.from_yaml(settings.fixture_path), prefix=args.prefix, user_map=settings.slack_user_map,
                skip_sensitive=args.skip_sensitive, dry_run=args.dry_run,
            )
            for c in report["channels"]:
                print(f"  #{c['name']:<28} {'private' if c['private'] else 'public':<8} invited {len(c['invited'])}")
            print(f"{report['threads']} threads, {report['messages']} messages{' (dry run)' if args.dry_run else ''}")
            for line in report["skipped"]:
                print(f"  skipped: {line}")
            return 0
        except SlackApiError as exc:
            print(f"Slack said {exc.error}" + (f"; the app needs the {exc.needed} scope" if exc.needed else ""))
            return 1
        except (AdapterError, httpx.HTTPError) as exc:
            print(f"Could not reach Slack at {adapter.client.base_url}: {exc.__class__.__name__}: {exc}")
            return 1
        finally:
            await adapter.client.aclose()

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
