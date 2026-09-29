"""Wiring: one object that owns every component, built from Settings.

Mock platforms are FastAPI apps reached through an in-process ASGI transport, so the
adapters speak HTTP to them exactly as they would to the real services. SLACK_MODE=real
swaps the Slack mock for SlackWebAdapter on https://slack.com/api (bot token), with Slack
Events arriving at POST /webhooks/slack; the other three platforms stay mocked.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from collections import OrderedDict
from datetime import datetime, timezone

import httpx

from ..adapters import ConfluenceAdapter, GoogleDriveAdapter, JiraAdapter, SlackAdapter
from ..adapters.base import AdapterError
from ..adapters.slack_real import SlackWebAdapter
from ..audit.alerts import AlertEngine
from ..audit.log import AuditLog
from ..config import Settings
from ..core.answer import Answerer
from ..core.embeddings import make_embedder
from ..core.entitlements import EntitlementResolver
from ..core.events import Event, EventBus
from ..core.identity import IdentityDirectory
from ..core.index import Index
from ..core.llm import LLMClient
from ..core.pipeline import AskPipeline
from ..core.planner import Planner
from ..core.retrieve import Retriever
from ..core.sync import SyncWorker
from ..core.verify_live import LiveVerifier
from ..mocks.confluence_app import create_confluence_app
from ..mocks.gdrive_app import create_gdrive_app
from ..mocks.jira_app import create_jira_app
from ..mocks.slack_app import create_slack_app
from ..mocks.store import CompanyStore


log = logging.getLogger(__name__)

# Bump when the ingestion pipeline changes what is stored (2 = DLP masking at ingestion,
# 3 = exact Confluence/Jira projections): an index built by an older pipeline is cleared and
# rebuilt on the next start.
INGEST_VERSION = "3"


class Brain:
    def __init__(self, settings: Settings, transports: dict[str, httpx.AsyncBaseTransport] | None = None):
        """`transports` replaces the network for a real connector (tests point Slack at a fake)."""
        self.settings = settings
        self.started_at: str | None = None
        # Set on the first shutdown signal (see app.py): open event streams end so the server can exit.
        self.closing = asyncio.Event()
        self.slack_real = settings.slack_mode == "real"
        if settings.slack_mode not in ("mock", "real"):
            raise ValueError(f"SLACK_MODE must be mock or real, not {settings.slack_mode!r}")
        if self.slack_real and not settings.slack_bot_token:
            raise ValueError("SLACK_MODE=real needs SLACK_BOT_TOKEN (a bot token, xoxb-...)")

        # identity + mock platforms
        self.directory = IdentityDirectory.from_fixture(settings.fixture_path)
        self.store = CompanyStore.from_yaml(settings.fixture_path)
        self.mock_apps = {
            "confluence": create_confluence_app(self.store),
            "jira": create_jira_app(self.store),
            "slack": create_slack_app(self.store),
            "gdrive": create_gdrive_app(self.store),
        }
        if self.slack_real:
            del self.mock_apps["slack"]
        self.clients = {
            name: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://mock-{name}", timeout=settings.gate2_timeout_s + 1.0)
            for name, app in self.mock_apps.items()
        }
        self.adapters = {
            "confluence": ConfluenceAdapter(self.clients["confluence"], self.store.base_urls["confluence"]),
            "jira": JiraAdapter(self.clients["jira"], self.store.base_urls["jira"]),
            "gdrive": GoogleDriveAdapter(self.clients["gdrive"], self.store.base_urls["gdrive"]),
        }
        if self.slack_real:
            self.clients["slack"] = httpx.AsyncClient(base_url=settings.slack_api_base, transport=(transports or {}).get("slack"), timeout=10.0)
            self.adapters["slack"] = SlackWebAdapter(
                self.clients["slack"],
                settings.slack_bot_token,
                history_days=settings.slack_history_days,
                lookback_days=settings.slack_lookback_days,
                membership_ttl_s=settings.slack_membership_ttl_s,
            )
            if settings.slack_user_map:
                self.directory.set_platform_ids("slack", settings.slack_user_map)
        else:
            self.adapters["slack"] = SlackAdapter(self.clients["slack"], self.store.base_urls["slack"])
        # Slack Events API: event ids already handled (Slack retries until it gets a 2xx)
        self.slack_event_ids: OrderedDict[str, float] = OrderedDict()
        self.slack_last_event: str | None = None

        # storage: index and audit share one SQLite file (and one lock)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(settings.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.index = Index(conn=self.conn, lock=self.lock)
        self.audit = AuditLog(self.conn, settings.audit_key_dir, checkpoint_every=settings.checkpoint_every, lock=self.lock)

        # control plane
        self.bus = EventBus()
        self.store.subscribe(lambda ev: self.bus.publish(Event(ev.kind, ev.platform, dict(ev.payload), ev.ts)))
        self.embedder = make_embedder(
            settings.embeddings,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.embedding_model,
            min_similarity=settings.embedding_min_similarity,
            timeout_s=settings.llm_timeout_s,
        )
        self.entitlements = EntitlementResolver(self.adapters, self.directory, self.bus, ttl_s=settings.entitlement_ttl_s)
        self.sync = SyncWorker(self.adapters, self.index, self.embedder, self.bus, interval_s=settings.sync_interval_s, audit=self.audit)
        self.llm = LLMClient(settings.llm_base_url, settings.llm_api_key, settings.llm_timeout_s, extra_body=settings.llm_extra_body)
        self.planner = Planner(self.llm, settings.llm_model_planner)
        self.retriever = Retriever(self.index, self.embedder, settings)
        self.verifier = LiveVerifier(self.adapters, self.sync, timeout_s=settings.gate2_timeout_s)
        self.answerer = Answerer(self.llm, settings.llm_model_answer)
        self.alerts = AlertEngine(self.audit)
        self.sync.on_sensitive = lambda item, counts: self.alerts.on_sensitive_data(
            item.item_id, item.title, item.container, counts, item.version, where=item.container_label
        )
        self.pipeline = AskPipeline(
            settings, self.entitlements, self.planner, self.retriever, self.verifier, self.answerer, self.audit, self.llm, alerts=self.alerts
        )

    async def start(self, initial_sync: bool = True, poller: bool = True) -> None:
        self.closing = asyncio.Event()
        self.started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        # Vectors are only comparable within one embedding model: a different model means a fresh index.
        previous = self.index.meta_get("embedder")
        if previous is not None and previous != self.embedder.name:
            log.warning("embedder changed (%s -> %s): clearing the index for a full re-sync", previous, self.embedder.name)
            self.index.clear()
        ingest = self.index.meta_get("ingest_version")
        if ingest != INGEST_VERSION and self.index.stats()["chunks"] > 0:
            log.warning("ingestion pipeline changed (%s -> %s): clearing the index for a full re-sync", ingest, INGEST_VERSION)
            self.index.clear()
        self.index.meta_set("embedder", self.embedder.name)
        self.index.meta_set("ingest_version", INGEST_VERSION)
        if self.slack_real:
            try:
                team = await self.adapters["slack"].team_info()
                log.info("slack: connected to workspace %s (%s)", team.get("name"), team.get("id"))
            except AdapterError as exc:
                log.error("slack: auth.test failed (%s); Slack will be skipped until the token works", exc)
        if initial_sync:
            await self.sync.sync_all(reason="startup")
        if poller and self.settings.sync_interval_s > 0:
            self.sync.start()

    async def stop(self) -> None:
        self.closing.set()
        await self.sync.stop()
        await self.llm.aclose()
        for client in self.clients.values():
            await client.aclose()
        with self.lock:
            self.conn.commit()

    def info(self) -> dict:
        return {
            "version": "0.1.0",
            "started_at": self.started_at,
            "planner": self.planner.mode,
            "answerer": self.answerer.mode,
            "embeddings": self.embedder.name,
            "verifier": self.settings.verifier,
            "no_result_min_latency_ms": self.settings.no_result_min_latency_ms,
            "sync": self.sync.status(),
            "audit": {"entries": self.audit.count(), "head": self.audit.head(), "key_id": self.audit.keys.key_id, "checkpoint_every": self.audit.checkpoint_every},
            "dlp": {"ingest_version": INGEST_VERSION},
            "connectors": {platform: getattr(adapter, "mode", "mock") for platform, adapter in self.adapters.items()},
            "slack": self.slack_status(),
            "vector_index": self.index.vcache.stats(),
            "entitlement_cache": self.entitlements.stats,
        }

    def slack_status(self) -> dict:
        adapter = self.adapters["slack"]
        if not self.slack_real:
            return {"mode": "mock"}
        return {
            **adapter.status(),  # type: ignore[attr-defined]
            "webhook": bool(self.settings.slack_signing_secret),
            "last_event": self.slack_last_event,
            "mapped_users": sorted(u for u, pids in self.directory.platform_ids.items() if "slack" in pids),
        }
