"""Slack adapter. Items are threads (root message plus replies).

Native rule: channel membership; a public channel is readable by every full workspace
member; a DM is its participants. Projection: private channel -> channel token; public
channel -> channel token plus workspace token; DM -> participant user tokens.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Acl, Item, ItemRef, ReadCheck
from .base import AdapterError, HttpAdapter


def _ts_to_iso(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_to_ts(value: str | None) -> str:
    if not value:
        return "0"
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return f"{dt.timestamp():.6f}"


class SlackAdapter(HttpAdapter):
    platform = "slack"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._names: dict[str, str] = {}

    async def _ok(self, path: str, params: dict | None = None, act_as: str | None = None) -> dict:
        data = await self._get_json(path, params=params, act_as=act_as)
        if not data.get("ok", False):
            raise AdapterError(f"slack: {path} -> {data.get('error')}")
        return data

    async def _name(self, user_id: str) -> str:
        if user_id not in self._names:
            try:
                user = (await self._ok("/api/users.info", params={"user": user_id}))["user"]
                self._names[user_id] = user.get("real_name") or user.get("name") or user_id
            except AdapterError:
                self._names[user_id] = user_id
        return self._names[user_id]

    async def changes_since(self, cursor: str | None) -> tuple[list[ItemRef], str]:
        oldest = _iso_to_ts(cursor)
        channels = (await self._ok("/api/conversations.list", params={"types": "public_channel,private_channel,im"}))["channels"]
        refs: list[ItemRef] = []
        newest = cursor or "1970-01-01T00:00:00Z"
        for channel in channels:
            history = await self._ok("/api/conversations.history", params={"channel": channel["id"], "oldest": oldest, "limit": 500})
            for root in history.get("messages", []):
                when = root.get("x_last_modified") or _ts_to_iso(root.get("latest_reply", root["ts"]))
                refs.append(
                    ItemRef(
                        item_id=f"slack:{channel['id']}:{root['ts']}",
                        platform="slack",
                        version=int(root.get("x_version", root.get("reply_count", 0) + 1)),
                        last_modified=when,
                    )
                )
                newest = max(newest, when)
            for gone in history.get("x_deleted", []):
                refs.append(ItemRef(item_id=f"slack:{channel['id']}:{gone['ts']}", platform="slack", version=0, last_modified=gone["deleted"], deleted=True))
                newest = max(newest, gone["deleted"])
        return refs, newest

    @staticmethod
    def _split(item_id: str) -> tuple[str, str]:
        _, channel, ts = item_id.split(":", 2)
        return channel, ts

    async def get_item(self, item_id: str) -> Item:
        channel_id, ts = self._split(item_id)
        thread = await self._ok("/api/conversations.replies", params={"channel": channel_id, "ts": ts})
        info = (await self._ok("/api/conversations.info", params={"channel": channel_id}))["channel"]
        messages = thread["messages"]
        first = messages[0]["text"]
        label = "a direct message" if info.get("is_im") else f"#{info['name']}"
        started = _ts_to_iso(messages[0]["ts"])[:10]
        lines = [f"Slack thread in {label}, started {started}.", ""]
        for m in messages:
            lines.append(f"{await self._name(m['user'])}: {m['text']}")
        title_label = "DM" if info.get("is_im") else f"#{info['name']}"
        return Item(
            item_id=item_id,
            platform="slack",
            title=f"{title_label}: {first[:70]}{'...' if len(first) > 70 else ''}",
            text="\n".join(lines),
            version=len(messages),
            last_modified=thread.get("last_modified") or _ts_to_iso(thread.get("latest_reply", ts)),
            author=messages[0]["user"],
            url=f"{self.web_base_url}/archives/{channel_id}/p{ts.replace('.', '')}" if self.web_base_url else None,
            container=f"slack:channel:{channel_id}",
            container_label="DM" if info.get("is_im") else f"#{info['name']}",
            links=list(thread.get("x_links", [])),
        )

    async def get_acl(self, item_id: str) -> Acl:
        channel_id, ts = self._split(item_id)
        info = (await self._ok("/api/conversations.info", params={"channel": channel_id}))["channel"]
        if info.get("is_im"):
            members = (await self._ok("/api/conversations.members", params={"channel": channel_id}))["members"]
            tokens = [f"slack:user:{m}" for m in members]
        elif info.get("is_private"):
            tokens = [f"slack:channel:{channel_id}"]
        else:
            team = (await self._ok("/api/team.info"))["team"]
            tokens = [f"slack:channel:{channel_id}", f"slack:workspace:{team['id']}"]
        thread = await self._ok("/api/conversations.replies", params={"channel": channel_id, "ts": ts})
        return Acl(item_id=item_id, allowed_principals=tokens, version=len(thread["messages"]))

    async def principals_for(self, platform_user_id: str) -> list[str]:
        user = (await self._ok("/api/users.info", params={"user": platform_user_id}))["user"]
        conversations = (await self._ok("/api/users.conversations", params={"user": platform_user_id, "types": "public_channel,private_channel"}))["channels"]
        tokens = [f"slack:user:{platform_user_id}"]
        tokens += [f"slack:channel:{c['id']}" for c in conversations]
        if not user.get("is_restricted") and not user.get("is_ultra_restricted"):
            tokens.append(f"slack:workspace:{user['team_id']}")
        return tokens

    async def can_read(self, platform_user_id: str, item_id: str) -> ReadCheck:
        channel_id, ts = self._split(item_id)
        data = await self._get_json("/api/conversations.replies", params={"channel": channel_id, "ts": ts}, act_as=platform_user_id)
        if data.get("ok"):
            messages = data["messages"]
            return ReadCheck(allowed=True, reason="ok", version=len(messages), last_modified=data.get("last_modified") or _ts_to_iso(data.get("latest_reply", ts)))
        error = data.get("error")
        if error in ("not_in_channel", "channel_not_found", "access_denied"):
            return ReadCheck(allowed=False, reason="not_member")
        if error in ("thread_not_found", "message_not_found"):
            return ReadCheck(allowed=False, reason="deleted")
        raise AdapterError(f"slack: can_read {item_id} -> {error}")
