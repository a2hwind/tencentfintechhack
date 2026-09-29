#!/usr/bin/env python3
"""Capture UI screenshots for the submission pack (docs/screenshots/).

Starts the API (fresh temp data dir) and the built UI, drives each screen with Playwright, and
writes PNGs. Requires `npm run build` in ui/ and `pip install playwright`.

    python scripts/screenshots.py
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
API_PORT = int(os.environ.get("SHOT_API_PORT", "8000"))  # NEXT_PUBLIC_API_URL is baked in at build time
UI_PORT = int(os.environ.get("SHOT_UI_PORT", "3000"))
SCENARIO_1 = "What's the status of the database migration and were there blockers raised in Slack last week?"


def wait_for(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not come up")


def latest_query_seq(actor: str) -> int:
    """The newest query entry by `actor` (read as compliance: the read itself is logged)."""
    request = urllib.request.Request(f"http://localhost:{API_PORT}/audit/entries?actor={actor}&kind=query&limit=1", headers={"X-User-Id": "compliance"})
    return int(json.loads(urllib.request.urlopen(request, timeout=5).read())["entries"][0]["seq"])


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    data_dir = Path(tempfile.mkdtemp(prefix="brain-shots-"))
    env = dict(os.environ, BRAIN_DATA_DIR=str(data_dir), SYNC_INTERVAL_S="60", CORS_ORIGINS=f"http://localhost:{UI_PORT}")
    env.pop("LLM_API_KEY", None)
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "internal_brain.api.app:app", "--port", str(API_PORT), "--timeout-graceful-shutdown", "3"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ui_env = dict(os.environ, PORT=str(UI_PORT), NEXT_PUBLIC_API_URL=f"http://localhost:{API_PORT}", HOSTNAME="127.0.0.1")
    standalone = ROOT / "ui" / ".next" / "standalone" / "server.js"
    if standalone.exists():
        static_src, static_dst = ROOT / "ui" / ".next" / "static", ROOT / "ui" / ".next" / "standalone" / ".next" / "static"
        if not static_dst.exists():
            shutil.copytree(static_src, static_dst)
        ui = subprocess.Popen(["node", str(standalone)], cwd=standalone.parent, env=ui_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        ui = subprocess.Popen(["npx", "next", "start", "-p", str(UI_PORT)], cwd=ROOT / "ui", env=ui_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(f"http://localhost:{API_PORT}/health")
        wait_for(f"http://localhost:{UI_PORT}/")
        base = f"http://localhost:{UI_PORT}"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 960}, color_scheme="light")

            def act_as(user: str) -> None:
                page.goto(base + "/")
                page.wait_for_selector("select[aria-label='Acting user']:not([disabled])")
                page.select_option("select[aria-label='Acting user']", user)
                page.wait_for_timeout(600)

            def ask(question: str) -> None:
                page.fill("textarea", question)
                page.keyboard.press("Enter")
                page.wait_for_selector(".answer-text", timeout=30000)
                page.wait_for_timeout(800)

            # 1. chat as jdoe: scenario 1, sentence-by-sentence citations
            act_as("jdoe")
            ask(SCENARIO_1)
            page.screenshot(path=str(OUT / "01-chat-scenario-1.png"), full_page=True)

            # 2. the evidence drawer: the exact passage behind a citation, re-checked live
            page.locator(".answer-text .cite").first.click()
            page.wait_for_selector(".drawer mark", timeout=15000)
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / "05-evidence-drawer.png"))
            page.keyboard.press("Escape")

            # 3. chat as ctr-lee: the negative case
            act_as("ctr-lee")
            ask("Where is the Q3 breach report?")
            page.screenshot(path=str(OUT / "02-chat-negative-case.png"), full_page=True)

            # 4. compare: restricted vs non-existent, byte-identical
            page.set_viewport_size({"width": 1600, "height": 1000})
            page.goto(base + "/compare?preset=restricted-vs-missing&run=1")
            page.wait_for_selector("text=Byte-identical", timeout=30000)
            page.wait_for_timeout(800)
            page.screenshot(path=str(OUT / "06-compare-restricted-vs-missing.png"), full_page=True)

            # 5. presenter: the asker's answer next to the live audit chain and the trust boundary
            page.set_viewport_size({"width": 1920, "height": 1080})
            page.goto(base + "/presenter")
            page.wait_for_selector(".stream-state.ok", timeout=30000)
            if page.locator(".beats").count() == 0:
                page.click("button:has-text('Guided demo')")
            page.wait_for_selector(".beats")
            page.click("button[aria-label^='Run beat 1:']")
            page.wait_for_function(
                "() => { const b = document.querySelector(\"button[aria-label^='Run beat 1:']\"); const li = b && b.closest('li'); return !!li && li.classList.contains('done'); }",
                timeout=60000,
            )
            page.wait_for_function("() => document.querySelectorAll('.steps .step').length > 0 && document.querySelectorAll('.steps .step.pending, .steps .step.current').length === 0", timeout=20000)
            page.wait_for_timeout(800)
            page.screenshot(path=str(OUT / "07-presenter.png"))

            # 6. audit console as compliance, the scenario-1 entry open
            seq = latest_query_seq("jdoe")
            page.set_viewport_size({"width": 1440, "height": 960})
            act_as("compliance")
            page.goto(base + f"/audit?seq={seq}")
            page.wait_for_selector(".detail-panel", timeout=15000)
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT / "03-audit-console.png"), full_page=True)

            # 7. admin panel
            act_as("admin")
            page.goto(base + "/admin")
            page.wait_for_timeout(2500)
            page.screenshot(path=str(OUT / "04-admin-panel.png"), full_page=True)
            browser.close()
        print(f"wrote {len(list(OUT.glob('*.png')))} screenshots to {OUT}")
        return 0
    finally:
        for proc in (ui, api):
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
