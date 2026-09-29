#!/usr/bin/env python3
"""Golden Q&A: answer quality under permissions -> docs/evidence/quality.md and quality.json.

    python scripts/quality_eval.py                     # the deterministic stub (what CI checks)
    LLM_BASE_URL=... LLM_API_KEY=... python scripts/quality_eval.py --out docs/evidence/quality-llm.md

Exit code 1 on any leak, or when a metric falls below its CI floor.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from internal_brain.api.app import create_app  # noqa: E402
from internal_brain.config import Settings  # noqa: E402
from internal_brain.evals.quality import FLOORS, render_report, run_all, summarize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "evidence" / "quality.md")
    args = parser.parse_args()
    settings = Settings(data_dir=Path(tempfile.mkdtemp(prefix="brain-quality-")), sync_interval_s=0)
    app = create_app(settings, initial_sync=True, poller=False)
    with TestClient(app) as client:
        brain = client.app.state.brain
        results = run_all(client, brain.store)
        meta = {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "answerer": brain.answerer.mode if brain.answerer.mode != "llm" else f"{settings.llm_model_answer} at {settings.llm_base_url}",
            "planner": brain.planner.mode,
            "verifier": settings.verifier,
            "floors": FLOORS,
        }
    summary = summarize(results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_report(results, summary, meta), encoding="utf-8")
    args.out.with_suffix(".json").write_text(json.dumps({"meta": meta, "summary": summary, "cases": [
        {"id": r.case.id, "user": r.case.user, "category": r.case.category, "expect": r.case.expect, "no_result": r.no_result,
         "facts": [r.facts_found, r.facts_total], "citation_groups": [r.cite_groups_ok, r.cite_groups_total], "cited": r.cited,
         "grounded": [r.grounded, r.sentences], "leaks": r.leaks, "answer": r.answer}
        for r in results
    ]}, indent=2), encoding="utf-8")
    print(f"{summary['cases']} cases: answered {summary['answered_when_expected']:.0%} when expected, no-result {summary['no_result_when_expected']:.0%} when expected, "
          f"fact recall {summary['fact_recall']:.0%}, citation recall {summary['citation_recall']:.0%}, precision {summary['citation_precision']:.0%}, "
          f"grounded {summary['grounded_sentences']:.0%}, leaks {summary['leaks']}  -> {args.out}")
    below = [k for k, floor in FLOORS.items() if summary[k] < floor]
    if summary["leaks"] or below:
        print(f"FAIL: leaks={summary['leaks']} below floor={below}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
