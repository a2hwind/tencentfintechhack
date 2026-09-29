"""A strict fake of the Slack Web API, backed by the Company A fixture, for testing SlackWebAdapter
without a workspace. It behaves like Slack where the adapter could get it wrong:

  - bearer token required (not_authed / invalid_auth), scopes enforced (missing_scope), and the
    granted scopes returned in the x-oauth-scopes header
  - the bot reads only channels it is a member of (not_in_channel), and private channels it is
    not in do not exist for it (channel_not_found), in every method
  - history is listed by the root message's ts, newest first; replies are not in history
  - cursor pagination with a small page size, opaque cursors (invalid_cursor on garbage)
  - HTTP 429 with Retry-After when a method is rate limited (`ratelimit(method, times)`)
  - Slack's error shape: HTTP 200 with {"ok": false, "error": ...}
  - no impersonation: any non-Slack parameter or an X-Act-As header is recorded as a violation

Membership, posts and deletions go through the fixture store (notify=False: no event reaches
the Brain unless a test delivers one to /webhooks/slack).
"""

from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from internal_brain.mocks.store import CompanyStore, Message, Thread, slack_ts, utc_now

TOKEN = "xoxb-test-internal-brain"
READ_SCOPES = ["channels:read", "groups:read", "channels:history", "groups:history", "users:read", "users:read.email", "team:read"]
WRITE_SCOPES = ["channels:manage", "groups:write", "chat:write", "chat:write.customize"]
BOT_USER = "U0BRAIN"

PARAMS: dict[str, set[str]] = {
    "auth.test": set(),
    "team.info": {"team"},
    "conversations.list": {"types", "exclude_archived", "limit", "cursor", "team_id"},
    "conversations.info": {"channel", "include_num_members", "include_locale"},
    "conversations.members": {"channel", "limit", "cursor"},
    "conversations.history": {"channel", "oldest", "latest", "inclusive", "limit", "cursor", "include_all_metadata"},
    "conversations.replies": {"channel", "ts", "oldest", "latest", "inclusive", "limit", "cursor", "include_all_metadata"},
    "users.info": {"user", "include_locale"},
    "users.conversations": {"user", "types", "exclude_archived", "limit", "cursor", "team_id"},
    "users.lookupByEmail": {"email"},
    "conversations.create": {"name", "is_private", "team_id"},
    "conversations.invite": {"channel", "users", "force"},
    "chat.postMessage": {"channel", "text", "thread_ts", "username", "icon_emoji", "icon_url", "unfurl_links", "unfurl_media", "mrkdwn", "reply_broadcast"},
}


class FakeSlack:
    def __init__(self, store: CompanyStore, token: str = TOKEN, bot_channels: set[str] | None = None, page_size: int = 2, scopes: list[str] | None = None):
        self.store = store
        self.token = token
        self.page_size = page_size
        self.scopes = list(scopes if scopes is not None else READ_SCOPES + WRITE_SCOPES)
        self.bot_channels = set(bot_channels) if bot_channels is not None else {c.id for c in store.channels.values() if not c.is_dm}
        self.deactivated: set[str] = set()
        self.rate_limits: dict[str, int] = {}
        self.calls: list[tuple[str, dict]] = []
        self.violations: list[str] = []
        self.app = self._build()

    # ------------------------------------------------------------------ test controls
    def ratelimit(self, method: str, times: int = 1) -> None:
        self.rate_limits[method] = self.rate_limits.get(method, 0) + times

    def count(self, method: str) -> int:
        return sum(1 for m, _ in self.calls if m == method)

    def slack_id(self, user_id: str) -> str:
        return self.store.platform_id(user_id, "slack") or user_id

    def thread_id(self, ref: str) -> str:
        thread = next(t for t in self.store.threads.values() if t.ref == ref)
        return f"slack:{thread.channel}:{thread.ts}"

    def remove_member(self, channel: str, user_id: str) -> None:
        self.store.slack_set_membership(channel, user_id, False, notify=False)

    def add_member(self, channel: str, user_id: str) -> None:
        self.store.slack_set_membership(channel, user_id, True, notify=False)

    def reply(self, ref: str, user_id: str, text: str) -> str:
        thread = next(t for t in self.store.threads.values() if t.ref == ref)
        self.store.slack_post(thread.channel, user_id, text, thread_ts=thread.ts, notify=False)
        return thread.messages[-1].ts

    def delete_thread(self, ref: str) -> str:
        thread = next(t for t in self.store.threads.values() if t.ref == ref)
        self.store.delete_item(f"slack:{thread.channel}:{thread.ts}", notify=False)
        return thread.ts

    # ------------------------------------------------------------------ rendering
    def _user_obj(self, uid: str) -> dict | None:
        user = self.store.user_by_platform_id("slack", uid)
        if user is None:
            return None
        profile: dict[str, Any] = {"real_name": user.name, "display_name": user.name.split()[0]}
        if "users:read.email" in self.scopes:
            profile["email"] = user.email
        return {
            "id": uid, "team_id": self.store.workspace, "name": user.id, "real_name": user.name, "deleted": uid in self.deactivated,
            "is_bot": False, "is_restricted": user.slack_guest, "is_ultra_restricted": False, "is_admin": False, "profile": profile,
        }

    def _visible(self, channel_id: str) -> bool:
        c = self.store.channels.get(channel_id)
        if c is None or c.is_dm:
            return False  # the bot is in no DM
        return not c.private or channel_id in self.bot_channels

    def _channel_obj(self, channel_id: str) -> dict:
        c = self.store.channels[channel_id]
        return {
            "id": c.id, "name": c.name, "is_channel": not c.private, "is_group": c.private, "is_im": False, "is_mpim": False,
            "is_private": c.private, "is_archived": False, "is_general": False, "is_member": c.id in self.bot_channels,
            "created": 1_700_000_000, "creator": "U0MLIM", "num_members": len(c.members),
        }

    def _message(self, t: Thread, m: Message, root: bool) -> dict:
        out: dict[str, Any] = {"type": "message", "user": self.slack_id(m.user), "text": m.text, "ts": m.ts, "team": self.store.workspace}
        if m.user.startswith("bot:"):
            out.update({"user": BOT_USER, "bot_id": "B0BRAIN", "username": m.user[4:]})
        if len(t.messages) > 1:
            out["thread_ts"] = t.ts
            if root:
                out.update({
                    "reply_count": len(t.messages) - 1,
                    "reply_users_count": len({x.user for x in t.messages[1:]}),
                    "latest_reply": t.messages[-1].ts,
                    "reply_users": sorted({self.slack_id(x.user) for x in t.messages[1:]}),
                    "is_locked": False,
                    "subscribed": False,
                })
            else:
                out["parent_user_id"] = self.slack_id(t.messages[0].user)
        return out

    def _page(self, rows: list, params: dict) -> tuple[list, str] | dict:
        try:
            limit = int(params.get("limit") or 100)
        except ValueError:
            return {"ok": False, "error": "invalid_limit"}
        size = max(1, min(limit, self.page_size, 1000))
        offset = 0
        if params.get("cursor"):
            try:
                offset = int(base64.b64decode(params["cursor"]).decode().split(":", 1)[1])
            except Exception:  # noqa: BLE001
                return {"ok": False, "error": "invalid_cursor"}
        chunk = rows[offset : offset + size]
        more = offset + size < len(rows)
        return chunk, (base64.b64encode(f"next:{offset + size}".encode()).decode() if more else "")

    # ------------------------------------------------------------------ the API
    def _build(self) -> FastAPI:
        app = FastAPI(title="Fake Slack Web API", docs_url=None, redoc_url=None)
        fake = self

        def ok(**data) -> dict:
            return {"ok": True, **data}

        def err(code: str, **extra) -> dict:
            return {"ok": False, "error": code, **extra}

        def need(scope: str) -> dict | None:
            return None if scope in fake.scopes else err("missing_scope", needed=scope, provided=",".join(fake.scopes))

        def paged(key: str, rows: list, params: dict) -> dict:
            result = fake._page(rows, params)
            if isinstance(result, dict):
                return result
            chunk, cursor = result
            return ok(**{key: chunk, "has_more": bool(cursor), "response_metadata": {"next_cursor": cursor}})

        def dispatch(method: str, p: dict) -> dict:
            s = fake.store
            if method == "auth.test":
                return ok(url="https://company-a.slack.com/", team=s.company["name"], user="internal-brain", team_id=s.workspace, user_id=BOT_USER, bot_id="B0BRAIN", is_enterprise_install=False)
            if method == "team.info":
                return need("team:read") or ok(team={"id": s.workspace, "name": s.company["name"], "domain": "company-a"})
            if method == "conversations.list":
                types = set((p.get("types") or "public_channel").split(","))
                if "private_channel" in types and (e := need("groups:read")):
                    return e
                rows = [fake._channel_obj(c.id) for c in s.channels.values() if not c.is_dm and fake._visible(c.id) and ("private_channel" if c.private else "public_channel") in types]
                return need("channels:read") or paged("channels", rows, p)
            if method in ("conversations.info", "conversations.members", "conversations.history", "conversations.replies"):
                channel = p.get("channel", "")
                if not fake._visible(channel):
                    return err("channel_not_found")
                c = s.channels[channel]
                scope = ("groups:" if c.private else "channels:") + ("read" if method in ("conversations.info", "conversations.members") else "history")
                if e := need(scope):
                    return e
                if method == "conversations.info":
                    return ok(channel=fake._channel_obj(channel))
                if method == "conversations.members":
                    return paged("members", [fake.slack_id(u) for u in c.members], p)
                if channel not in fake.bot_channels:
                    return err("not_in_channel")
                threads = [t for t in s.threads.values() if t.channel == channel and t.deleted_at is None]
                if method == "conversations.history":
                    oldest, latest = float(p.get("oldest") or 0), float(p.get("latest") or 9e12)
                    roots = sorted((t for t in threads if oldest < float(t.ts) <= latest), key=lambda t: float(t.ts), reverse=True)
                    return paged("messages", [fake._message(t, t.messages[0], True) for t in roots], p)
                thread = next((t for t in threads if t.ts == p.get("ts")), None)
                if thread is None:
                    return err("thread_not_found")
                rows = [fake._message(thread, m, i == 0) for i, m in enumerate(thread.messages)]
                return paged("messages", rows, p)
            if method == "users.info":
                if e := need("users:read"):
                    return e
                user = fake._user_obj(p.get("user", ""))
                return ok(user=user) if user else err("user_not_found")
            if method == "users.lookupByEmail":
                if e := need("users:read.email"):
                    return e
                user = next((u for u in s.users.values() if u.email.lower() == (p.get("email") or "").lower()), None)
                uid = user and user.platform_ids.get("slack")
                return ok(user=fake._user_obj(uid)) if uid else err("users_not_found")
            if method == "users.conversations":
                user = s.user_by_platform_id("slack", p.get("user", ""))
                if user is None:
                    return err("user_not_found")
                types = set((p.get("types") or "public_channel").split(","))
                rows = [
                    fake._channel_obj(c.id)
                    for c in s.channels.values()
                    if not c.is_dm and user.id in c.members and fake._visible(c.id) and ("private_channel" if c.private else "public_channel") in types
                ]
                return need("channels:read") or paged("channels", rows, p)
            if method == "conversations.create":
                private = str(p.get("is_private", "false")).lower() == "true"
                if e := need("groups:write" if private else "channels:manage"):
                    return e
                name = p.get("name", "")
                if any(c.name == name for c in s.channels.values()):
                    return err("name_taken")
                from internal_brain.mocks.store import Channel

                cid = f"C9{len(s.channels):04d}"
                s.channels[cid] = Channel(id=cid, name=name, private=private, members=[])
                fake.bot_channels.add(cid)
                return ok(channel=fake._channel_obj(cid))
            if method == "conversations.invite":
                channel = p.get("channel", "")
                if not fake._visible(channel):
                    return err("channel_not_found")
                for uid in (p.get("users") or "").split(","):
                    user = s.user_by_platform_id("slack", uid.strip())
                    if user is None:
                        return err("user_not_found")
                    if user.id not in s.channels[channel].members:
                        s.channels[channel].members.append(user.id)
                return ok(channel=fake._channel_obj(channel))
            if method == "chat.postMessage":
                if e := need("chat:write"):
                    return e
                if p.get("username") and (e := need("chat:write.customize")):
                    return e
                channel = p.get("channel", "")
                if not fake._visible(channel) or channel not in fake.bot_channels:
                    return err("not_in_channel")
                now = utc_now()
                author = "bot:" + (p.get("username") or "internal-brain")
                if p.get("thread_ts"):
                    thread = s.threads.get(f"{channel}:{p['thread_ts']}")
                    if thread is None:
                        return err("thread_not_found")
                    msg = Message(ts=slack_ts(now, len(s.threads) * 100 + len(thread.messages)), user=author, text=p.get("text", ""), created=now)
                    thread.messages.append(msg)
                else:
                    msg = Message(ts=slack_ts(now, (len(s.threads) + 1) * 100), user=author, text=p.get("text", ""), created=now)
                    thread = Thread(channel=channel, ts=msg.ts, messages=[msg])
                    s.threads[thread.item_key] = thread
                return ok(channel=channel, ts=msg.ts, message={"text": msg.text, "ts": msg.ts, "bot_id": "B0BRAIN"})
            return err("unknown_method")

        @app.api_route("/api/{method}", methods=["GET", "POST"])
        async def api(method: str, request: Request):
            params: dict[str, Any] = dict(request.query_params)
            content_type = request.headers.get("content-type", "")
            if request.method == "POST":
                if content_type.startswith("application/x-www-form-urlencoded"):
                    params.update(dict(await request.form()))
                elif content_type.startswith("application/json"):
                    body = await request.body()
                    params.update(json.loads(body) if body else {})
            fake.calls.append((method, dict(params)))
            if request.headers.get("x-act-as"):
                fake.violations.append(f"{method}: X-Act-As header (Slack has no impersonation)")
            unknown = set(params) - PARAMS.get(method, set()) - {"token"}
            if method in PARAMS and unknown:
                fake.violations.append(f"{method}: unknown parameters {sorted(unknown)}")
            headers = {"x-oauth-scopes": ",".join(fake.scopes)}
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer "):
                return JSONResponse({"ok": False, "error": "not_authed"}, headers=headers)
            if auth[len("Bearer "):] != fake.token:
                return JSONResponse({"ok": False, "error": "invalid_auth"}, headers=headers)
            if fake.rate_limits.get(method, 0) > 0:
                fake.rate_limits[method] -= 1
                return JSONResponse({"ok": False, "error": "ratelimited"}, status_code=429, headers={**headers, "Retry-After": "1"})
            return JSONResponse(dispatch(method, params), headers=headers)

        return app
