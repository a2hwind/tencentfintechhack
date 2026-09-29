"""In-process event bus: the seam where platform webhooks arrive.

A real deployment receives Slack Events API, Jira/Confluence webhooks and Drive push
notifications at an HTTP endpoint and publishes them here. In the demo the mock
platforms publish directly. Subscribers: the entitlement resolver (membership events
invalidate its cache), the sync worker (content events trigger an immediate sync),
and the audit log (permission events are entries too).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Event:
    kind: str  # membership_changed | content_changed | item_deleted
    platform: str
    payload: dict[str, Any]
    ts: str = field(default_factory=now_iso)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[Event], None]]] = {}
        self.history: list[Event] = []

    def subscribe(self, kind: str, handler: Callable[[Event], None]) -> None:
        self._subscribers.setdefault(kind, []).append(handler)

    def publish(self, event: Event) -> None:
        self.history.append(event)
        for handler in list(self._subscribers.get(event.kind, [])) + list(self._subscribers.get("*", [])):
            handler(event)
