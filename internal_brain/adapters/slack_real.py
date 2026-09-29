"""Slack Web API adapter for a real workspace (SLACK_MODE=real). Items are threads.

Same five methods as every adapter, spoken to https://slack.com/api with a bot token
(xoxb-...). Scopes: channels:read, groups:read, channels:history, groups:history,
users:read (plus users:read.email for scripts/slack_setup.py map).

Scope of what is indexed: the channels the bot has been invited to. Inviting the bot is how a
workspace admin connects a channel, and removing it disconnects the channel (its threads are
tombstoned on the next sync, or at once via the channel_left event). Direct messages are out of
scope: a bot token cannot read people's DMs, and the Brain should not.

Permission model, projected to the same tokens as the mock:
  private channel -> slack:channel:<id>
  public channel  -> slack:channel:<id> + slack:workspace:<team>  (any full member can read it)
  guests (is_restricted / is_ultra_restricted) and deactivated users get no workspace token

Gate 2 without impersonation: a bot token cannot act as a user, so can_read asks Slack the
authoritative questions directly: is the user active and a full member (users.info), are they
in the channel (conversations.members), does the thread still exist and how many messages does
it have (conversations.replies, limit 1). Concurrent checks for one channel share a single
members call, and its answer is reused for `membership_ttl_s` (2 s by default): a revocation
is honoured within that window with no webhook at all.

Rate limits: HTTP 429 is retried after the Retry-After header (up to `max_retries`, never
longer than `max_wait_s`); a Gate 2 check that would wait longer than its own 2 s budget fails
closed as unverifiable. Internal (customer-built) apps keep the standard tiers: Slack's 2025
limits on conversations.history and .replies apply to commercially distributed apps.

Pagination: every list call follows response_metadata.next_cursor to the end.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx

from ..models import Acl, Item, ItemRef, ReadCheck
from .base import EPOCH, AdapterError

SKIP_SUBTYPES = {
    "channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name", "channel_archive",
    "channel_unarchive", "group_join", "group_leave", "group_topic", "group_purpose", "group_name",
    "group_archive", "group_unarchive", "bot_add", "bot_remove", "pinned_item", "unpinned_item", "tombstone",
}
USER_MENTION = re.compile(r"<@([UW][A-Z0-9]+)(?:\|([^>]+))?>")
CHANNEL_MENTION = re.compile(r"<#([CG][A-Z0-9]+)(?:\|([^>]*))?>")
SPECIAL_MENTION = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")
LINK = re.compile(r"<((?:https?|mailto):[^|>]+)(?:\|([^>]+))?>")
JIRA_KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")
CONFLUENCE_PAGE = re.compile(r"/pages/(?:viewpage\.action\?pageId=)?(\d+)")
DRIVE_FILE = re.compile(r"(?:drive|docs)\.google\.com/(?:file|document|spreadsheets|presentation)/d/([A-Za-z0-9_-]{10,})")


class SlackApiError(AdapterError):
    def __init__(self, method: str, error: str, needed: str | None = None):
        super().__init__(f"slack: {method} -> {error}" + (f" (needs {needed})" if needed else ""))
        self.method = method
        self.error = error
        self.needed = needed


def ts_to_iso(ts: str | float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_to_epoch(value: str | None) -> float:
    if not value:
        return 0.0
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _latest_ts(message: dict) -> float:
    """The newest activity a message carries: its own ts, its thread's latest reply, its last edit."""
    return max(float(message.get("ts") or 0), float(message.get("latest_reply") or 0), float((message.get("edited") or {}).get("ts") or 0))


def extract_links(text: str) -> list[str]:
    """Explicit references to other indexed systems. False positives are harmless: link
    expansion only follows ids that are in the index, each through Gate 1 on its own."""
    links: list[str] = []
    for key in JIRA_KEY.findall(text):
        links.append(f"jira:{key}")
    for page in CONFLUENCE_PAGE.findall(text):
        links.append(f"confluence:{page}")
    for file_id in DRIVE_FILE.findall(text):
        links.append(f"gdrive:{file_id}")
    return list(dict.fromkeys(links))


class _Coalescer:
    """Concurrent calls for one key share one request; the answer is reused for `ttl` seconds."""

    def __init__(self, ttl: float, clock: Callable[[], float]):
        self.ttl = ttl
        self.clock = clock
        self._done: dict[str, tuple[float, Any]] = {}
        self._inflight: dict[str, asyncio.Future] = {}

    async def get(self, key: str, fetch: Callable[[], Awaitable[Any]]) -> Any:
        hit = self._done.get(key)
        if hit is not None and self.clock() - hit[0] < self.ttl:
            return hit[1]
        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)
        future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await fetch()
        except asyncio.CancelledError:
            # The caller that owned the request gave up (e.g. its Gate 2 budget ran out): the
            # others waiting on it fail closed as unverifiable instead of being cancelled too.
            future.set_exception(AdapterError(f"slack: shared lookup {key} was cancelled"))
            future.exception()
            raise
        except Exception as exc:
            future.set_exception(exc)
            future.exception()  # mark retrieved: waiters re-raise it, nobody else needs to
            raise
        else:
            future.set_result(value)
            self._done[key] = (self.clock(), value)
            return value
        finally:
            self._inflight.pop(key, None)

    def forget(self, key: str | None = None) -> None:
        if key is None:
            self._done.clear()
        else:
            self._done.pop(key, None)


class SlackWebAdapter:
    platform = "slack"
    mode = "real"

    def __init__(
        self,
        client: httpx.AsyncClient,
        token: str,
        history_days: float = 90,
        lookback_days: float = 7,
        membership_ttl_s: float = 2.0,
        max_retries: int = 3,
        max_wait_s: float = 30.0,
        page_limit: int = 200,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.client = client
        self.token = token
        self.history_days = history_days
        self.lookback_days = lookback_days
        self.max_retries = max_retries
        self.max_wait_s = max_wait_s
        self.page_limit = page_limit
        self._sleep = sleep
        self._clock = clock
        self._members = _Coalescer(membership_ttl_s, clock)
        self._channel_info = _Coalescer(membership_ttl_s, clock)
        self._user_info = _Coalescer(membership_ttl_s, clock)
        self._names: dict[str, str] = {}
        self._threads: dict[str, tuple[float, list[dict]]] = {}  # item_id -> (fetched at, messages), for get_acl after get_item
        self._seen: dict[str, float] = {}  # item_id -> root ts, for tombstones between polls
        self.team: dict[str, Any] | None = None
        self.stats = {"calls": 0, "rate_limited": 0, "waited_s": 0.0, "pages": 0}

    # ------------------------------------------------------------------ transport
    async def call(self, method: str, **params: Any) -> dict:
        """One Web API call: form-encoded POST, bearer token, 429 retried after Retry-After."""
        form = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in params.items() if v is not None}
        for attempt in range(self.max_retries + 1):
            self.stats["calls"] += 1
            try:
                response = await self.client.post(f"/{method}", data=form, headers={"Authorization": f"Bearer {self.token}"})
            except httpx.HTTPError as exc:
                raise AdapterError(f"slack: {method}: {exc.__class__.__name__}: {exc}") from exc
            if response.status_code == 429:
                self.stats["rate_limited"] += 1
                wait = float(response.headers.get("Retry-After", "1") or 1)
                if attempt >= self.max_retries or wait > self.max_wait_s:
                    raise SlackApiError(method, "ratelimited")
                self.stats["waited_s"] += wait
                await self._sleep(wait)
                continue
            if response.status_code >= 500:
                if attempt >= self.max_retries:
                    raise AdapterError(f"slack: {method} -> HTTP {response.status_code}")
                await self._sleep(min(2.0 ** attempt, self.max_wait_s))
                continue
            try:
                data = response.json()
            except ValueError as exc:
                raise AdapterError(f"slack: {method} -> non-JSON response ({response.status_code})") from exc
            if not data.get("ok"):
                raise SlackApiError(method, str(data.get("error", "unknown_error")), data.get("needed"))
            return data
        raise SlackApiError(method, "ratelimited")  # pragma: no cover

    async def paginate(self, method: str, key: str, **params: Any) -> AsyncIterator[dict]:
        cursor: str | None = None
        while True:
            data = await self.call(method, **params, limit=self.page_limit, cursor=cursor)
            self.stats["pages"] += 1
            for row in data.get(key, []):
                yield row
            cursor = (data.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor:
                return

    # ------------------------------------------------------------------ lookups
    async def team_info(self) -> dict:
        if self.team is None:
            auth = await self.call("auth.test")
            self.team = {"id": auth["team_id"], "name": auth.get("team"), "url": (auth.get("url") or "").rstrip("/"), "bot_user_id": auth.get("user_id")}
        return self.team

    async def _user(self, user_id: str) -> dict | None:
        async def fetch() -> dict | None:
            try:
                return (await self.call("users.info", user=user_id))["user"]
            except SlackApiError as exc:
                if exc.error in ("user_not_found", "user_not_visible"):
                    return None
                raise

        return await self._user_info.get(user_id, fetch)

    async def _name(self, user_id: str | None, fallback: str | None = None) -> str:
        if not user_id:
            return fallback or "unknown"
        if user_id not in self._names:
            user = await self._user(user_id)
            profile = (user or {}).get("profile") or {}
            self._names[user_id] = (user or {}).get("real_name") or profile.get("real_name") or profile.get("display_name") or (user or {}).get("name") or user_id
        return self._names[user_id]

    async def _channel(self, channel_id: str) -> dict:
        return await self._channel_info.get(channel_id, lambda: self._fetch_channel(channel_id))

    async def _fetch_channel(self, channel_id: str) -> dict:
        return (await self.call("conversations.info", channel=channel_id))["channel"]

    async def _channel_members(self, channel_id: str) -> frozenset[str]:
        async def fetch() -> frozenset[str]:
            return frozenset([m async for m in self.paginate("conversations.members", "members", channel=channel_id)])

        return await self._members.get(channel_id, fetch)

    async def _replies(self, channel_id: str, ts: str) -> list[dict]:
        return [m async for m in self.paginate("conversations.replies", "messages", channel=channel_id, ts=ts)]

    def forget_channel(self, channel_id: str) -> None:
        """Drop cached channel facts (a membership or channel event arrived)."""
        self._members.forget(channel_id)
        self._channel_info.forget(channel_id)

    def forget_user(self, user_id: str) -> None:
        self._user_info.forget(user_id)
        self._names.pop(user_id, None)

    async def render(self, text: str) -> str:
        """Slack mrkdwn to plain text: names for mentions, labels and URLs for links, entities decoded."""
        out = text or ""
        names = {uid: await self._name(uid) for uid, label in USER_MENTION.findall(out) if not label}
        out = USER_MENTION.sub(lambda m: "@" + (m.group(2) or names.get(m.group(1), m.group(1))), out)
        out = CHANNEL_MENTION.sub(lambda m: "#" + (m.group(2) or m.group(1)), out)
        out = SPECIAL_MENTION.sub(lambda m: "@" + m.group(1), out)
        out = LINK.sub(lambda m: f"{m.group(2)} ({m.group(1)})" if m.group(2) and m.group(2) != m.group(1) else m.group(1), out)
        return html.unescape(out)

    @staticmethod
    def _split(item_id: str) -> tuple[str, str]:
        _, channel, ts = item_id.split(":", 2)
        return channel, ts

    # ------------------------------------------------------------------ the five methods
    async def changes_since(self, cursor: str | None) -> tuple[list[ItemRef], str]:
        """Threads in the bot's channels. Slack lists history by the root message's ts, so a
        repeat poll re-reads a lookback window to see new replies to recent threads; replies to
        older threads, edits and deletions arrive through the Events API (and Gate 2 checks
        every served thread at the source regardless)."""
        now = time.time()
        since = iso_to_epoch(cursor) - self.lookback_days * 86400 if cursor else now - self.history_days * 86400
        channels = [c async for c in self.paginate("conversations.list", "channels", types="public_channel,private_channel", exclude_archived=False)]
        readable = {c["id"] for c in channels if c.get("is_member")}
        refs: list[ItemRef] = []
        listed: set[str] = set()
        newest = cursor or EPOCH
        for channel_id in sorted(readable):
            async for m in self.paginate("conversations.history", "messages", channel=channel_id, oldest=f"{max(since, 0):.6f}"):
                if m.get("subtype") in SKIP_SUBTYPES or (m.get("thread_ts") and m["thread_ts"] != m["ts"]):
                    continue  # membership noise, and replies broadcast to the channel (their thread is listed)
                item_id = f"slack:{channel_id}:{m['ts']}"
                when = ts_to_iso(_latest_ts(m))
                refs.append(ItemRef(item_id=item_id, platform="slack", version=int(m.get("reply_count") or 0) + 1, last_modified=when))
                listed.add(item_id)
                self._seen[item_id] = float(m["ts"])
                newest = max(newest, when)
        # Tombstones: threads seen before that are gone from a window we just re-read, and every
        # thread of a channel the bot can no longer read (it was removed: the channel is disconnected).
        for item_id, root_ts in list(self._seen.items()):
            if item_id in listed:
                continue
            channel_id, _ = self._split(item_id)
            if channel_id not in readable or root_ts > since:
                refs.append(ItemRef(item_id=item_id, platform="slack", version=0, last_modified=ts_to_iso(now), deleted=True))
                del self._seen[item_id]
        return refs, newest

    async def get_item(self, item_id: str) -> Item:
        channel_id, ts = self._split(item_id)
        messages = [m for m in await self._replies(channel_id, ts) if m.get("subtype") not in SKIP_SUBTYPES]
        if not messages:
            raise AdapterError(f"slack: {item_id} has no messages")
        self._threads[item_id] = (self._clock(), messages)
        info = await self._channel(channel_id)
        team = await self.team_info()
        label = f"#{info.get('name', channel_id)}"
        started = ts_to_iso(messages[0]["ts"])[:10]
        lines = [f"Slack thread in {label}, started {started}.", ""]
        raw = []
        for m in messages:
            name = m.get("username") or await self._name(m.get("user"), fallback=(m.get("bot_profile") or {}).get("name"))
            text = await self.render(m.get("text", ""))
            raw.append(m.get("text", ""))
            lines.append(f"{name}: {text}")
        first = await self.render(messages[0].get("text", ""))
        return Item(
            item_id=item_id,
            platform="slack",
            title=f"{label}: {first[:70]}{'...' if len(first) > 70 else ''}",
            text="\n".join(lines),
            version=len(messages),
            last_modified=ts_to_iso(max(_latest_ts(m) for m in messages)),
            author=messages[0].get("user") or messages[0].get("bot_id") or "unknown",
            url=f"{team['url']}/archives/{channel_id}/p{ts.replace('.', '')}" if team.get("url") else None,
            container=f"slack:channel:{channel_id}",
            container_label=label,
            links=extract_links("\n".join(raw)),
        )

    async def get_acl(self, item_id: str) -> Acl:
        channel_id, ts = self._split(item_id)
        info = await self._channel(channel_id)
        if info.get("is_im") or info.get("is_mpim"):
            tokens = [f"slack:user:{m}" for m in sorted(await self._channel_members(channel_id))]
        elif info.get("is_private"):
            tokens = [f"slack:channel:{channel_id}"]
        else:
            tokens = [f"slack:channel:{channel_id}", f"slack:workspace:{(await self.team_info())['id']}"]
        cached = self._threads.pop(item_id, None)
        messages = cached[1] if cached and self._clock() - cached[0] < 30 else await self._replies(channel_id, ts)
        return Acl(item_id=item_id, allowed_principals=tokens, version=len(messages))

    async def principals_for(self, platform_user_id: str) -> list[str]:
        user = await self._user(platform_user_id)
        if user is None or user.get("deleted") or user.get("is_bot"):
            return []  # unknown, deactivated or a bot: no Slack entitlements at all
        tokens = [f"slack:user:{platform_user_id}"]
        async for c in self.paginate("users.conversations", "channels", user=platform_user_id, types="public_channel,private_channel", exclude_archived=False):
            tokens.append(f"slack:channel:{c['id']}")
        if not user.get("is_restricted") and not user.get("is_ultra_restricted"):
            tokens.append(f"slack:workspace:{user.get('team_id') or (await self.team_info())['id']}")
        return tokens

    async def can_read(self, platform_user_id: str, item_id: str) -> ReadCheck:
        channel_id, ts = self._split(item_id)
        try:
            info = await self._channel(channel_id)
        except SlackApiError as exc:
            if exc.error == "channel_not_found":
                return ReadCheck(allowed=False, reason="deleted")  # deleted, or the bot was removed: disconnected
            raise
        user = await self._user(platform_user_id)
        if user is None or user.get("deleted"):
            return ReadCheck(allowed=False, reason="not_member")
        full_member = not (user.get("is_restricted") or user.get("is_ultra_restricted") or user.get("is_bot"))
        if info.get("is_private") or info.get("is_im") or info.get("is_mpim") or not full_member:
            # membership decides (a public channel is open to every full member without joining)
            if platform_user_id not in await self._channel_members(channel_id):
                return ReadCheck(allowed=False, reason="not_member")
        try:
            head = await self.call("conversations.replies", channel=channel_id, ts=ts, limit=1)
        except SlackApiError as exc:
            if exc.error in ("thread_not_found", "message_not_found"):
                return ReadCheck(allowed=False, reason="deleted")
            raise
        root = (head.get("messages") or [{}])[0]
        if root.get("subtype") == "tombstone" or not root:
            return ReadCheck(allowed=False, reason="deleted")
        return ReadCheck(allowed=True, reason="ok", version=int(root.get("reply_count") or 0) + 1, last_modified=ts_to_iso(_latest_ts(root)))

    def status(self) -> dict:
        return {"mode": self.mode, "team": self.team, "threads_tracked": len(self._seen), **self.stats}
