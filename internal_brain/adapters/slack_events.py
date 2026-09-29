"""Slack Events API: request signing, and translation of Slack events onto the event bus.

Every request Slack sends carries X-Slack-Request-Timestamp and X-Slack-Signature:
    v0=HMAC-SHA256(signing_secret, "v0:{timestamp}:{raw body}")
A request is accepted only if the signature matches (constant-time compare) and the timestamp
is within five minutes of now, which stops replays. Slack retries unacknowledged events, so the
receiver also drops event_ids it has already handled.

What each event means for the Brain:
  member_joined_channel / member_left_channel   the user's entitlements changed: cache invalidated,
                                                 and the change is an audit entry
  message (new, reply, broadcast)                the thread changed: re-sync it now
  message_changed                                an edit: re-sync the thread
  message_deleted                                root deleted: tombstone; reply deleted: re-sync
  channel_left / group_left                      the bot was removed: the channel is disconnected,
  channel_deleted / group_deleted                 every indexed thread in it is tombstoned
  channel_rename / group_rename                  titles changed: re-sync the channel's threads
  user_change                                    deactivated, or became a guest: entitlements
                                                 invalidated everywhere for that user
Anything else is acknowledged and ignored. Missing an event is never a leak: Gate 2 checks
every served thread at Slack itself.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field

from ..core.events import Event

MAX_SKEW_S = 300


def sign(secret: str, timestamp: str, body: bytes) -> str:
    base = f"v0:{timestamp}:".encode("utf-8") + body
    return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str | None, body: bytes, signature: str | None, now: float | None = None) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        sent = int(timestamp)
    except ValueError:
        return False
    if abs((time.time() if now is None else now) - sent) > MAX_SKEW_S:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


@dataclass
class Translation:
    events: list[Event] = field(default_factory=list)
    permission: dict | None = None  # recorded as a permission_event audit entry
    forget_channels: list[str] = field(default_factory=list)
    forget_users: list[str] = field(default_factory=list)
    remove_container: str | None = None  # tombstone every indexed item in this container
    resync_container: str | None = None  # re-sync every indexed item in this container
    ignored: str | None = None

    def summary(self) -> dict:
        return {
            "published": [f"{e.kind}:{e.payload.get('item_id') or e.payload.get('user') or ''}" for e in self.events],
            "remove_container": self.remove_container,
            "resync_container": self.resync_container,
            "ignored": self.ignored,
        }


def _thread(channel: str, ts: str) -> str:
    return f"slack:{channel}:{ts}"


def translate(event: dict) -> Translation:
    kind = event.get("type")
    channel = event.get("channel") if isinstance(event.get("channel"), str) else (event.get("channel") or {}).get("id")
    t = Translation()

    if kind in ("member_joined_channel", "member_left_channel"):
        user = event.get("user", "")
        t.events.append(Event("membership_changed", "slack", {"user": user, "channel": channel, "action": kind}))
        t.permission = {"platform": "slack", "channel": channel, "user": user, "action": "add" if kind == "member_joined_channel" else "remove", "source": "slack_events_api"}
        t.forget_channels.append(channel or "")
        return t

    if kind == "message":
        subtype = event.get("subtype")
        if subtype == "message_changed":
            message = event.get("message") or {}
            root = message.get("thread_ts") or message.get("ts")
            if channel and root:
                t.events.append(Event("content_changed", "slack", {"item_id": _thread(channel, root)}))
            return t
        if subtype == "message_deleted":
            previous = event.get("previous_message") or {}
            deleted = event.get("deleted_ts") or previous.get("ts")
            root = previous.get("thread_ts") or deleted
            if channel and root:
                kind_out = "item_deleted" if root == deleted else "content_changed"
                t.events.append(Event(kind_out, "slack", {"item_id": _thread(channel, root)}))
            return t
        if subtype in (None, "thread_broadcast", "file_share", "bot_message", "me_message", "message_replied"):
            message = event.get("message") if subtype == "message_replied" else event
            root = (message or {}).get("thread_ts") or (message or {}).get("ts")
            if channel and root:
                t.events.append(Event("content_changed", "slack", {"item_id": _thread(channel, root)}))
            return t
        t.ignored = f"message/{subtype}"
        return t

    if kind in ("channel_left", "group_left", "channel_deleted", "group_deleted"):
        if channel:
            t.remove_container = f"slack:channel:{channel}"
            t.forget_channels.append(channel)
            t.events.append(Event("membership_changed", "slack", {"user": "", "channel": channel, "action": kind}))
            t.permission = {"platform": "slack", "channel": channel, "action": kind, "source": "slack_events_api"}
        return t

    if kind in ("channel_rename", "group_rename"):
        if channel:
            t.resync_container = f"slack:channel:{channel}"
            t.forget_channels.append(channel)
        return t

    if kind == "user_change":
        user = (event.get("user") or {}).get("id") if isinstance(event.get("user"), dict) else event.get("user")
        if user:
            t.forget_users.append(user)
            t.events.append(Event("membership_changed", "slack", {"user": user, "action": "user_change"}))
            profile = event.get("user") if isinstance(event.get("user"), dict) else {}
            if profile.get("deleted") or profile.get("is_restricted") or profile.get("is_ultra_restricted"):
                t.permission = {"platform": "slack", "user": user, "action": "deactivated" if profile.get("deleted") else "guest", "source": "slack_events_api"}
        return t

    t.ignored = str(kind)
    return t
