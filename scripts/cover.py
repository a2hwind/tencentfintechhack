#!/usr/bin/env python3
"""Render the 16:9 cover image for the submission (docs/cover-1920x1080.png and docs/cover-380x216.png).

    python scripts/cover.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs"

HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { width: 1920px; height: 1080px; overflow: hidden; font-family: -apple-system, "Segoe UI", Inter, Helvetica, Arial, sans-serif;
         background: radial-gradient(1200px 700px at 78% 30%, #123c4a 0%, #0b1f2a 55%, #071118 100%); color: #eaf2f5; }
  .wrap { position: absolute; inset: 0; padding: 90px 120px 130px; display: grid; grid-template-columns: 1.05fr 0.95fr; gap: 80px; }
  .left { display: flex; flex-direction: column; justify-content: center; }
  .kicker { font-size: 26px; letter-spacing: 0.18em; text-transform: uppercase; color: #7ed0c3; font-weight: 600; }
  h1 { font-size: 132px; line-height: 1.0; font-weight: 800; margin: 26px 0 30px; letter-spacing: -0.02em; }
  .blurb { font-size: 44px; line-height: 1.25; color: #cfe3e8; max-width: 820px; }
  .pillars { display: flex; gap: 18px; margin-top: 56px; flex-wrap: wrap; }
  .pill { border: 2px solid rgba(126, 208, 195, 0.55); border-radius: 999px; padding: 14px 26px; font-size: 26px; color: #dff5f0; background: rgba(126,208,195,0.08); }
  .right { display: flex; align-items: center; justify-content: center; }
  .flow { width: 100%; display: flex; flex-direction: column; gap: 16px; }
  .box { border-radius: 18px; padding: 22px 30px; font-size: 30px; font-weight: 600; border: 2px solid rgba(234,242,245,0.16); background: rgba(255,255,255,0.05); }
  .box small { display: block; font-size: 22px; font-weight: 400; color: #a9c4cc; margin-top: 6px; }
  .gate { border-color: #7ed0c3; background: rgba(126,208,195,0.14); }
  .deny { border-color: #ff9c8a; background: rgba(255,156,138,0.10); }
  .arrow { text-align: center; color: #7ed0c3; font-size: 30px; line-height: 0.6; }
  .foot { position: absolute; left: 120px; right: 120px; bottom: 56px; display: flex; justify-content: space-between; font-size: 24px; color: #a9c4cc; }
</style></head><body>
<div class="wrap">
  <div class="left">
    <div class="kicker">FinTech track · Aspire · The Internal Brain</div>
    <h1>Internal Brain</h1>
    <div class="blurb">Company-wide answers, scoped to your permissions, fully auditable.</div>
    <div class="pillars">
      <span class="pill">Two gates before the model</span>
      <span class="pill">Denials indistinguishable from misses</span>
      <span class="pill">Hash-chained, signed audit trail</span>
      <span class="pill">Exact at 100k chunks, 0 leaks</span>
    </div>
  </div>
  <div class="right">
    <div class="flow">
      <div class="box">Question<small>resolved to the asker's live entitlements first</small></div>
      <div class="arrow">▼</div>
      <div class="box gate">Gate 1 · filtered inside the index query<small>top-k over permitted chunks only</small></div>
      <div class="arrow">▼</div>
      <div class="box gate">Gate 2 · re-verified live at Confluence · Jira · Slack · Drive<small>revoked, deleted, unverifiable → dropped; changed → refreshed</small></div>
      <div class="arrow">▼</div>
      <div class="box">Grounded answer · one citation per sentence<small>guard strips anything uncited or unsupported</small></div>
      <div class="arrow">▼</div>
      <div class="box deny">Audit entry written before the answer returns<small>SHA-256 chain · Ed25519 checkpoints · every decision, every chunk</small></div>
    </div>
  </div>
</div>
<div class="foot"><span>Tencent Cloud AI Singapore Hackathon 2026</span><span>Confluence · Jira · Slack · Google Drive · Hunyuan</span></div>
</body></html>"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.set_content(HTML)
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "cover-1920x1080.png"))
        small = browser.new_page(viewport={"width": 1900, "height": 1080}, device_scale_factor=0.2)  # 380 x 216, the form's recommended size
        small.set_content(HTML)
        small.wait_for_timeout(300)
        small.screenshot(path=str(OUT / "cover-380x216.png"))
        browser.close()
    print("wrote docs/cover-1920x1080.png and docs/cover-380x216.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
