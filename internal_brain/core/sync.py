"""Sync workers: incremental by cursor, so staleness is bounded by the poll interval.

Each adapter exposes changes_since(cursor). A changed item is re-indexed by replacing
all of its chunks in one transaction, so a document is never half old, half new. The
ACL is part of the item version, so permission edits on the document side ride the
same path. Deletions write tombstones that remove the item's chunks.

Two triggers: the poller (every SYNC_INTERVAL_S) and content events from the bus (the
webhook seam), which sync one item immediately. `paused` disables both, which is how
the demo shows the Gate 2 read-through refresh doing its job on its own.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from typing import Callable

from ..adapters.base import AdapterError, PlatformAdapter
from ..models import Acl, Item, PLATFORMS
from . import dlp
from .chunking import chunk_text
from .embeddings import Embedder
from .events import Event, EventBus
from .index import Index, IndexedItem, normalize_iso

log = logging.getLogger(__name__)


class SyncWorker:
    def __init__(self, adapters: dict[str, PlatformAdapter], index: Index, embedder: Embedder, bus: EventBus, interval_s: float = 60.0, audit=None):
        self.adapters = adapters
        self.index = index
        self.embedder = embedder
        self.bus = bus
        self.interval_s = interval_s
        self.audit = audit
        self.paused = False
        self.runs = 0
        self.last_run: str | None = None
        self.last_summary: dict | None = None
        self._pending: set[asyncio.Task] = set()
        self._task: asyncio.Task | None = None
        # called with (masked item, {kind: count}) when DLP finds sensitive values in a source document
        self.on_sensitive: Callable[[Item, dict[str, int]], None] | None = None
        bus.subscribe("content_changed", self._on_content)
        bus.subscribe("item_deleted", self._on_deleted)

    # ------------------------------------------------------------------ indexing one item
    def index_item(self, item: Item, acl: Acl) -> list[str]:
        """Mask sensitive values, chunk, embed, and replace the item's rows in one transaction.

        The index never holds a full card number, NRIC, bank account or secret, so no prompt can.
        """
        text = dlp.mask(item.text)
        title = dlp.mask(item.title)
        counts = dict(text.counts)  # every adapter repeats the title inside the text: count values once
        masked = item.model_copy(update={"text": text.text, "title": title.text})
        chunks = chunk_text(masked.text)
        vectors = self.embedder.embed(chunks)
        self.index.upsert_item(masked, acl, chunks, vectors, dlp_counts=counts)
        if counts and self.on_sensitive is not None:
            try:
                self.on_sensitive(masked, counts)
            except Exception as exc:  # an alerting failure must never break ingestion
                log.warning("sensitive-data hook failed for %s: %s", item.item_id, exc)
        return chunks

    async def fetch_and_index(self, item_id: str) -> tuple[Item, list[str]] | None:
        """Fetch content and ACL under the connector credential and write them to the index."""
        platform = item_id.split(":", 1)[0]
        adapter = self.adapters.get(platform)
        if adapter is None:
            return None
        try:
            item = await adapter.get_item(item_id)
            acl = await adapter.get_acl(item_id)
        except AdapterError as exc:
            log.warning("fetch %s failed: %s", item_id, exc)
            return None
        chunks = self.index_item(item, acl)
        return item, chunks

    # ------------------------------------------------------------------ events (webhook seam)
    def _schedule(self, coro) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        task = loop.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def _on_content(self, event: Event) -> None:
        if self.paused:
            return
        item_id = event.payload.get("item_id")
        if item_id:
            self._schedule(self._sync_one_logged(item_id, reason="webhook"))

    def _on_deleted(self, event: Event) -> None:
        if self.paused:
            return
        item_id = event.payload.get("item_id")
        if item_id:
            removed = self.index.delete_item(item_id)
            self._record({"platform": event.platform, "reason": "webhook", "updated": 0, "deleted": int(removed), "items": [item_id]})

    async def _sync_one_logged(self, item_id: str, reason: str) -> None:
        result = await self.fetch_and_index(item_id)
        if result is not None:
            self._record({"platform": item_id.split(":", 1)[0], "reason": reason, "updated": 1, "deleted": 0, "items": [item_id]})

    async def drain(self) -> None:
        """Wait for event-triggered syncs to land (admin routes call this before responding)."""
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    # ------------------------------------------------------------------ polling
    async def sync_platform(self, platform: str, reason: str = "poll") -> dict:
        adapter = self.adapters[platform]
        cursor = self.index.get_cursor(platform)
        refs, new_cursor = await adapter.changes_since(cursor)
        updated: list[str] = []
        deleted: list[str] = []
        for ref in refs:
            if ref.deleted:
                if self.index.delete_item(ref.item_id):
                    deleted.append(ref.item_id)
                continue
            existing = self.index.get_item(ref.item_id)
            if existing and existing.version == ref.version and existing.last_modified == normalize_iso(ref.last_modified):
                continue
            if await self.fetch_and_index(ref.item_id) is not None:
                updated.append(ref.item_id)
        self.index.set_cursor(platform, new_cursor, len(updated) + len(deleted))
        summary = {"platform": platform, "reason": reason, "updated": len(updated), "deleted": len(deleted), "items": (updated + deleted)[:50], "cursor": new_cursor}
        if updated or deleted or cursor is None:
            self._record(summary)
        return summary

    async def sync_all(self, reason: str = "poll") -> dict:
        results = {}
        for platform in PLATFORMS:
            if platform not in self.adapters:
                continue
            try:
                results[platform] = await self.sync_platform(platform, reason=reason)
            except AdapterError as exc:
                log.warning("sync %s failed: %s", platform, exc)
                results[platform] = {"platform": platform, "error": str(exc)}
        self.runs += 1
        self.last_run = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.last_summary = results
        return results

    def _record(self, summary: dict) -> None:
        if self.audit is not None:
            self.audit.record_sync(summary)

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.interval_s)
                if not self.paused:
                    await self.sync_all(reason="poll")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep the poller alive
                log.exception("sync loop error: %s", exc)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self.run_forever())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.drain()

    def status(self) -> dict:
        return {
            "paused": self.paused,
            "interval_s": self.interval_s,
            "runs": self.runs,
            "last_run": self.last_run,
            "pending_events": len(self._pending),
            "platforms": self.index.sync_status(),
            "index": self.index.stats(),
        }
