"""FastAPI application factory. `uvicorn internal_brain.api.app:app` runs the demo."""

from __future__ import annotations

import asyncio
import logging
import signal
import threading
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import Settings
from .brain import Brain
from .deps import get_brain
from .routes_admin import router as admin_router
from .routes_ask import router as ask_router
from .routes_audit import router as audit_router
from .routes_sources import router as sources_router
from .routes_webhooks import router as webhooks_router

log = logging.getLogger("internal_brain")


def end_streams_on_shutdown_signal(event: asyncio.Event) -> Callable[[], None]:
    """Chain SIGINT/SIGTERM so the first Ctrl+C also ends open /audit/stream responses.

    uvicorn waits for in-flight responses before it runs the lifespan shutdown, and an event
    stream never finishes on its own, so without this the server would hang on exit while a
    console is attached. The server's own handler still runs; returns a function that undoes this.
    """
    if threading.current_thread() is not threading.main_thread():
        return lambda: None  # signals can only be handled on the main thread (e.g. not under TestClient)
    loop = asyncio.get_running_loop()
    installed: dict[int, tuple[Callable, Callable]] = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(sig)
        if not callable(previous):
            continue

        def handler(signum, frame, previous=previous):
            loop.call_soon_threadsafe(event.set)
            previous(signum, frame)

        signal.signal(sig, handler)
        installed[sig] = (handler, previous)

    def restore() -> None:
        for sig, (handler, previous) in installed.items():
            if signal.getsignal(sig) is handler:
                signal.signal(sig, previous)

    return restore


def create_app(settings: Settings | None = None, initial_sync: bool = True, poller: bool = True, transports: dict | None = None) -> FastAPI:
    settings = settings or Settings()
    brain = Brain(settings, transports=transports)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await brain.start(initial_sync=initial_sync, poller=poller)
        restore_signals = end_streams_on_shutdown_signal(brain.closing)
        log.info("internal brain up: planner=%s answerer=%s embeddings=%s", brain.planner.mode, brain.answerer.mode, brain.embedder.name)
        try:
            yield
        finally:
            restore_signals()
            await brain.stop()

    app = FastAPI(
        title="Internal Brain",
        version="0.1.0",
        description="Company-wide answers, scoped to your permissions, fully auditable.",
        lifespan=lifespan,
    )
    app.state.brain = brain
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Audit-Seq"],
    )
    app.include_router(ask_router)
    app.include_router(admin_router)
    app.include_router(audit_router)
    app.include_router(sources_router)
    app.include_router(webhooks_router)

    # The mock platforms are reachable for humans too (the adapters use an in-process transport).
    for name, mock in brain.mock_apps.items():
        app.mount(f"/mock/{name}", mock)

    @app.get("/health", tags=["meta"])
    def health(b: Brain = Depends(get_brain)):
        return {"status": "ok", **b.info()}

    @app.get("/", tags=["meta"])
    def root():
        return {"name": "Internal Brain", "docs": "/docs", "health": "/health", "ask": "POST /ask (header X-User-Id)"}

    return app


app = create_app()
