"""Entitlement resolver: the signed-in identity to the set of principal tokens they hold
on each platform, right now.

60 s TTL cache, invalidated on membership events. Gate 2 makes correctness independent
of the TTL; the TTL only tunes how much Gate 1 filtering is done against a slightly
stale view. A platform that fails to answer contributes no tokens (fail closed), and a
partial resolution is never cached.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from ..adapters.base import AdapterError, PlatformAdapter
from ..models import Identity, PrincipalSet
from .events import Event, EventBus, now_iso
from .identity import IdentityDirectory

log = logging.getLogger(__name__)


@dataclass
class _CacheEntry:
    principals: PrincipalSet
    expires_at: float


class EntitlementResolver:
    def __init__(self, adapters: dict[str, PlatformAdapter], directory: IdentityDirectory, bus: EventBus, ttl_s: float = 60.0):
        self.adapters = adapters
        self.directory = directory
        self.ttl_s = ttl_s
        self._cache: dict[str, _CacheEntry] = {}
        self.listen_to_events = True
        self.stats = {"hits": 0, "misses": 0, "invalidations": 0}
        bus.subscribe("membership_changed", self._on_membership)

    # ----------------------------------------------------------------- events
    def _on_membership(self, event: Event) -> None:
        if not self.listen_to_events:
            return
        pid = str(event.payload.get("user", ""))
        user_id = self.directory.user_for(event.platform, pid)
        if user_id:
            self.invalidate(user_id)
        else:
            # Unknown mapping: invalidate everyone rather than risk a stale grant.
            self._cache.clear()
            self.stats["invalidations"] += 1

    def invalidate(self, user_id: str | None = None) -> None:
        if user_id is None:
            self._cache.clear()
        else:
            self._cache.pop(user_id, None)
        self.stats["invalidations"] += 1

    # ----------------------------------------------------------------- resolution
    async def principals_for(self, identity: Identity, use_cache: bool = True) -> PrincipalSet:
        cached = self._cache.get(identity.id)
        if use_cache and cached and cached.expires_at > time.monotonic():
            self.stats["hits"] += 1
            return cached.principals
        self.stats["misses"] += 1

        platform_ids = self.directory.platform_ids_for(identity.id)
        tokens: list[str] = []
        complete = True

        async def one(platform: str, pid: str) -> list[str]:
            adapter = self.adapters.get(platform)
            if adapter is None:
                return []
            try:
                return await adapter.principals_for(pid)
            except AdapterError as exc:
                log.warning("principals_for(%s, %s) failed: %s", platform, pid, exc)
                raise

        results = await asyncio.gather(*(one(p, pid) for p, pid in platform_ids.items()), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                complete = False
                continue
            tokens.extend(result)

        principals = PrincipalSet(user_id=identity.id, tokens=sorted(set(tokens)), platform_user_ids=platform_ids, resolved_at=now_iso())
        if complete:
            self._cache[identity.id] = _CacheEntry(principals=principals, expires_at=time.monotonic() + self.ttl_s)
        return principals
