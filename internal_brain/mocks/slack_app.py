"""Mock Slack Web API: the subset the adapter calls.

Permission semantics: channel membership for private channels and DMs; any full
workspace member can read a public channel; guests read only their channels (see
CompanyStore.slack_can_read). Slack reports errors as `{"ok": false, "error": ...}`
with HTTP 200, and so does this mock. `X-Act-As: <user id>` evaluates as that user.
"""

from __future__ import annotations

from fastapi import FastAPI, Query, Request

from .store import CompanyStore, Thread, iso


def create_slack_app(store: CompanyStore) -> FastAPI:
    app = FastAPI(title="Mock Slack", docs_url=None, redoc_url=None)

    def err(code: str) -> dict:
        return {"ok": False, "error": code}

    def render_message(m) -> dict:
        return {"type": "message", "user": store.platform_id(m.user, "slack") or m.user, "text": m.text, "ts": m.ts}

    def render_thread(t: Thread) -> dict:
        return {
            "channel": t.channel,
            "ts": t.ts,
            "latest_reply": t.messages[-1].ts,
            "reply_count": len(t.messages) - 1,
            "last_modified": iso(t.last_modified),
            "messages": [render_message(m) for m in t.messages],
            "x_links": t.links,
        }

    @app.get("/api/conversations.list")
    def conversations_list(types: str = "public_channel,private_channel,im"):
        wanted = set(types.split(","))
        channels = []
        for c in store.channels.values():
            kind = "im" if c.is_dm else ("private_channel" if c.private else "public_channel")
            if kind in wanted:
                channels.append({"id": c.id, "name": c.name, "is_private": c.private, "is_im": c.is_dm})
        return {"ok": True, "channels": channels}

    @app.get("/api/conversations.info")
    def conversations_info(channel: str):
        c = store.channels.get(channel)
        if c is None:
            return err("channel_not_found")
        return {"ok": True, "channel": {"id": c.id, "name": c.name, "is_private": c.private, "is_im": c.is_dm, "num_members": len(c.members)}}

    @app.get("/api/conversations.members")
    def conversations_members(channel: str):
        c = store.channels.get(channel)
        if c is None:
            return err("channel_not_found")
        return {"ok": True, "members": [store.platform_id(u, "slack") or u for u in c.members]}

    @app.get("/api/conversations.history")
    def conversations_history(channel: str, oldest: str = "0", limit: int = Query(200, le=1000)):
        """Thread roots in a channel whose thread changed after `oldest` (a ts)."""
        c = store.channels.get(channel)
        if c is None:
            return err("channel_not_found")
        oldest_f = float(oldest or 0)
        messages = []
        deleted = []
        for t in store.threads.values():
            if t.channel != channel:
                continue
            if t.deleted_at is not None:
                if t.deleted_at.timestamp() > oldest_f:
                    deleted.append({"ts": t.ts, "deleted": iso(t.deleted_at)})
                continue
            if t.last_modified.timestamp() > oldest_f:
                root = render_message(t.messages[0])
                root.update({"thread_ts": t.ts, "reply_count": len(t.messages) - 1, "latest_reply": t.messages[-1].ts, "x_last_modified": iso(t.last_modified), "x_version": t.version})
                messages.append(root)
        messages.sort(key=lambda m: m["latest_reply"])
        return {"ok": True, "messages": messages[:limit], "x_deleted": deleted, "has_more": False}

    @app.get("/api/conversations.replies")
    def conversations_replies(channel: str, ts: str, request: Request):
        t = store.threads.get(f"{channel}:{ts}")
        if t is None or t.deleted_at is not None:
            return err("thread_not_found")
        act_as = request.headers.get("x-act-as")
        if act_as is not None and not store.slack_can_read(act_as, t):
            return err("not_in_channel")
        return {"ok": True, **render_thread(t)}

    @app.get("/api/users.conversations")
    def users_conversations(user: str, types: str = "public_channel,private_channel,mpim,im"):
        u = store.user_by_platform_id("slack", user)
        if u is None:
            return err("user_not_found")
        wanted = set(types.split(","))
        channels = []
        for c in store.channels.values():
            kind = "im" if c.is_dm else ("private_channel" if c.private else "public_channel")
            if kind in wanted and u.id in c.members:
                channels.append({"id": c.id, "name": c.name, "is_private": c.private, "is_im": c.is_dm})
        return {"ok": True, "channels": channels}

    @app.get("/api/users.info")
    def users_info(user: str):
        u = store.user_by_platform_id("slack", user)
        if u is None:
            return err("user_not_found")
        return {"ok": True, "user": {"id": user, "name": u.id, "real_name": u.name, "is_restricted": u.slack_guest, "is_ultra_restricted": u.slack_guest, "team_id": store.workspace}}

    @app.get("/api/team.info")
    def team_info():
        return {"ok": True, "team": {"id": store.workspace, "name": store.company["name"], "domain": store.domain.split(".")[0]}}

    return app
