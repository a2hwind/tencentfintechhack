#!/usr/bin/env python3
"""Run the adversarial leak evaluation and the side-channel timing check; write docs/evidence/.

    python scripts/leak_eval.py                # all users, full bank, 40 timing runs per arm
    python scripts/leak_eval.py --runs 100     # more timing samples

Exit code 1 if any leak, unpermitted citation, unpermitted chunk or non-uniform denial is found.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from internal_brain.api.app import create_app  # noqa: E402
from internal_brain.config import Settings  # noqa: E402
from internal_brain.evals.leak import TIMING_DENIED, TIMING_NOMATCH, render_leak_report, render_side_channel_report, run_bank, timing_experiment  # noqa: E402

OUT = ROOT / "docs" / "evidence"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=40, help="timing runs per arm")
    parser.add_argument("--users", default=None, help="comma-separated subset of users")
    args = parser.parse_args()

    for var in ("LLM_API_KEY", "LLM_BASE_URL"):
        if os.environ.get(var):
            print(f"note: {var} is set; the eval will exercise the real model")
    data_dir = Path(tempfile.mkdtemp(prefix="brain-leak-eval-"))
    app = create_app(Settings(data_dir=data_dir, sync_interval_s=0), initial_sync=True, poller=False)
    OUT.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        brain = client.app.state.brain
        meta = client.get("/health").json()
        users = args.users.split(",") if args.users else [u.id for u in brain.directory.list()]
        results = run_bank(client, brain.store, users)
        timing = timing_experiment(client, runs=args.runs)
        r1 = client.post("/ask", json={"question": TIMING_DENIED[1]}, headers={"X-User-Id": TIMING_DENIED[0]})
        r2 = client.post("/ask", json={"question": TIMING_NOMATCH[1]}, headers={"X-User-Id": TIMING_NOMATCH[0]})
        identical = r1.content == r2.content

    leak_md = render_leak_report(results, users, meta)
    side_md = render_side_channel_report(timing, identical, meta)
    (OUT / "leak-eval.md").write_text(leak_md, encoding="utf-8")
    (OUT / "side-channels.md").write_text(side_md, encoding="utf-8")
    failures = [r for r in results if not r.clean]
    print(f"{len(results)} queries · {len(failures)} failures · wrote docs/evidence/leak-eval.md and side-channels.md")
    for arm, stats in timing.items():
        print(f"  {arm:9s} median {stats['median_ms']:.1f} ms  p95 {stats['p95_ms']:.1f} ms")
    for r in failures[:20]:
        print(f"  FAIL {r.user} · {r.question} → {r.leaked_canaries} {r.unpermitted_citations} {r.unpermitted_chunks} uniform={r.uniform_ok}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
