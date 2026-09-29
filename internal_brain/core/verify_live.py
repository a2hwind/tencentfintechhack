"""Gate 2: live re-verify and read-through refresh.

For the candidates that survive ranking, the adapter asks the source platform
can_read(user, item) right now, under the asker's identity. Anything revoked since the
last sync, deleted (404) or unverifiable (adapter error or timeout) is dropped and
logged as a denial. This fails closed: the system never serves on a stale permission
when the live check is unavailable.

The same call returns the item's current version. If it is newer than the indexed copy,
the item is fetched, re-chunked in memory and used for this answer, and the index is
updated as a side effect (read-through refresh: zero staleness for what is served).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..adapters.base import AdapterError, PlatformAdapter
from ..models import Decision, PrincipalSet, ReadCheck
from .chunking import sha256_text
from .index import IndexedItem
from .retrieve import Candidate, ChunkHit
from .sync import SyncWorker

log = logging.getLogger(__name__)


@dataclass
class Verification:
    kept: list[Candidate]
    decisions: list[Decision]


class LiveVerifier:
    def __init__(self, adapters: dict[str, PlatformAdapter], sync: SyncWorker, timeout_s: float = 2.0):
        self.adapters = adapters
        self.sync = sync
        self.timeout_s = timeout_s

    async def check(self, platform: str, item_id: str, principals: PrincipalSet) -> ReadCheck:
        """can_read(user, item) at the source, right now, under the asker's identity. Fails closed."""
        adapter = self.adapters.get(platform)
        pid = principals.platform_user_ids.get(platform)
        if adapter is None or pid is None:
            return ReadCheck(allowed=False, reason="not_member")
        try:
            return await asyncio.wait_for(adapter.can_read(pid, item_id), timeout=self.timeout_s)
        except (asyncio.TimeoutError, AdapterError) as exc:
            log.warning("gate2 unverifiable %s: %s", item_id, exc)
            return ReadCheck(allowed=False, reason="unverifiable")
        except Exception as exc:  # any other failure is still a failure to verify
            log.warning("gate2 error %s: %s", item_id, exc)
            return ReadCheck(allowed=False, reason="unverifiable")

    async def _check(self, candidate: Candidate, principals: PrincipalSet) -> ReadCheck:
        return await self.check(candidate.item.platform, candidate.item.item_id, principals)

    async def _refresh(self, candidate: Candidate) -> Candidate | None:
        result = await self.sync.fetch_and_index(candidate.item.item_id)
        if result is None:
            return None
        item, chunks = result
        fresh = self.sync.index.get_item(item.item_id) or candidate.item
        return Candidate(
            item=fresh if isinstance(fresh, IndexedItem) else candidate.item,
            chunks=[ChunkHit(f"{item.item_id}#{i}", text, sha256_text(text), candidate.score) for i, text in enumerate(chunks)],
            score=candidate.score,
            rule=candidate.rule,
            source=candidate.source,
            expanded_from=candidate.expanded_from,
        )

    async def verify(self, candidates: list[Candidate], principals: PrincipalSet) -> Verification:
        checks = await asyncio.gather(*(self._check(c, principals) for c in candidates))
        kept: list[Candidate] = []
        decisions: list[Decision] = []
        for candidate, check in zip(candidates, checks):
            item = candidate.item
            if not check.allowed:
                rule = "revoked" if check.reason == "not_member" else check.reason
                decisions.append(Decision(doc=item.item_id, platform=item.platform, gate1="allow", gate2="deny", rule=rule, gate1_rule=candidate.rule, version=item.version, source=candidate.source, container=item.container))  # type: ignore[arg-type]
                continue
            refreshed = False
            served = candidate
            if check.version is not None and check.version > item.version:
                fresh = await self._refresh(candidate)
                if fresh is None:
                    decisions.append(Decision(doc=item.item_id, platform=item.platform, gate1="allow", gate2="deny", rule="unverifiable", gate1_rule=candidate.rule, version=item.version, source=candidate.source, container=item.container))  # type: ignore[arg-type]
                    continue
                served = fresh
                refreshed = True
            kept.append(served)
            decisions.append(
                Decision(
                    doc=item.item_id,
                    platform=item.platform,  # type: ignore[arg-type]
                    gate1="allow",
                    gate2="allow",
                    rule=candidate.rule,
                    gate1_rule=candidate.rule,
                    version=check.version if check.version is not None else item.version,
                    refreshed=refreshed,
                    source=candidate.source,
                    container=item.container,
                )
            )
        return Verification(kept=kept, decisions=decisions)
