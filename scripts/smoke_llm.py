#!/usr/bin/env python3
"""Check a real model endpoint before the demo: chat, JSON mode, embeddings, then the five
handbook scenarios end to end with the real planner and answer model.

    export LLM_BASE_URL=https://tokenhub-intl.tencentcloudmaas.com/v1   # Tencent Cloud International (TokenHub)
    export LLM_API_KEY=...
    export LLM_MODEL_PLANNER=hy3 LLM_MODEL_ANSWER=hy3
    python scripts/smoke_llm.py

Prints what works, what does not, and the settings to use. Nothing is written to your data dir.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from internal_brain.config import Settings  # noqa: E402
from internal_brain.core.llm import LLMClient, LLMError, extract_json  # noqa: E402
from internal_brain.core.planner import PLANNER_SYSTEM  # noqa: E402

QUESTIONS = [
    ("jdoe", "What's the status of the database migration and were there blockers raised in Slack last week?"),
    ("jdoe", "What's the latest runbook for the payment-service incident?"),
    ("ctr-lee", "Where is the Q3 breach report?"),
    ("jdoe", "Root cause of the payment outage last quarter and the follow-up tickets"),
    ("jdoe", "What card was charged twice in the payments channel?"),
]


def line(ok: bool, label: str, detail: str = "") -> None:
    print(f"  [{'ok' if ok else '!!'}] {label}{(': ' + detail) if detail else ''}")


async def check_endpoint(settings: Settings) -> dict:
    found: dict = {}
    llm = LLMClient(settings.llm_base_url, settings.llm_api_key, settings.llm_timeout_s, extra_body=settings.llm_extra_body)
    print(f"endpoint {settings.llm_base_url}  planner={settings.llm_model_planner}  answer={settings.llm_model_answer}")
    print(f"  extra body sent with every request: {llm.extra_body or '{}'}")
    try:
        t0 = time.perf_counter()
        reply = await llm.chat(settings.llm_model_answer, "Reply with exactly: pong", "ping", max_tokens=10)
        line("pong" in reply.lower(), "chat", f"{(time.perf_counter() - t0) * 1000:.0f} ms, replied {reply[:40]!r}")
        found["chat"] = True
    except LLMError as exc:
        line(False, "chat", str(exc)[:200])
        found["chat"] = False
        await llm.aclose()
        return found
    try:
        t0 = time.perf_counter()
        raw = await llm.chat(settings.llm_model_planner, PLANNER_SYSTEM, "What's the status of the database migration?", json_mode=True, max_tokens=400)
        plan = extract_json(raw)
        line(True, "planner JSON", f"{(time.perf_counter() - t0) * 1000:.0f} ms, platforms {plan.get('platforms')}")
        line(llm.json_mode_supported is not False, "response_format json_object", "supported" if llm.json_mode_supported else "not supported: the client retries without it automatically")
    except Exception as exc:  # noqa: BLE001
        line(False, "planner JSON", f"{exc} (the planner falls back to all four platforms)")
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
            r = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/embeddings",
                json={"model": settings.embedding_model, "input": ["payment gateway failover"]},
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )
        if r.status_code < 400:
            dim = len(r.json()["data"][0]["embedding"])
            line(True, "embeddings", f"{settings.embedding_model}, {dim} dimensions -> EMBEDDINGS=openai EMBEDDING_MODEL={settings.embedding_model}")
            found["embeddings"] = True
        else:
            line(False, "embeddings", f"{r.status_code}; keep EMBEDDINGS=hash (default) or use EMBEDDINGS=bge-small locally")
            found["embeddings"] = False
    except Exception as exc:  # noqa: BLE001
        line(False, "embeddings", str(exc)[:200])
        found["embeddings"] = False
    await llm.aclose()
    return found


def run_scenarios(settings: Settings) -> None:
    from fastapi.testclient import TestClient

    from internal_brain.api.app import create_app

    print("\nscenarios through the real pipeline (fresh temp data dir):")
    app = create_app(settings, initial_sync=True, poller=False)
    with TestClient(app) as client:
        for user, question in QUESTIONS:
            t0 = time.perf_counter()
            r = client.post("/ask", json={"question": question}, headers={"X-User-Id": user})
            body = r.json()
            seq = r.headers.get("X-Audit-Seq")
            entry = client.get(f"/audit/entries/{seq}", headers={"X-User-Id": "compliance"}).json()["entry"]
            print(f"\n[{user}] {question}  ({(time.perf_counter() - t0) * 1000:.0f} ms, model {entry['model']}, plan fallback={entry['plan']['fallback']})")
            print(f"  {body['answer'][:600]}")
            print(f"  cited: {[c['doc'] for c in body['citations']]}  guard: {entry['guard']}")


def main() -> int:
    if not os.environ.get("LLM_API_KEY") or not os.environ.get("LLM_BASE_URL"):
        print("Set LLM_BASE_URL and LLM_API_KEY first (see .env.example).")
        return 2
    data_dir = Path(tempfile.mkdtemp(prefix="brain-smoke-"))
    settings = Settings(data_dir=data_dir, sync_interval_s=0)
    found = asyncio.run(check_endpoint(settings))
    if not found.get("chat"):
        return 1
    if settings.embeddings in ("hunyuan", "openai") and not found.get("embeddings"):
        settings = Settings(data_dir=data_dir, sync_interval_s=0, embeddings="hash")
        print("  (embeddings unavailable at this endpoint: running the scenarios with EMBEDDINGS=hash)")
    run_scenarios(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
