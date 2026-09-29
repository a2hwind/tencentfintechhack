"""Golden Q&A (internal_brain/evals/quality.py) against the deterministic stub, held to floors."""

from __future__ import annotations

from internal_brain.evals.quality import CASES, FLOORS, run_all, summarize


def test_golden_answers_meet_the_floors(brain):
    results = run_all(brain.c, brain.brain.store)
    summary = summarize(results)
    assert summary["leaks"] == 0, [(r.case.id, r.leaks) for r in results if r.leaks]
    wrong = [r.case.id for r in results if not r.outcome_ok]
    for metric, floor in FLOORS.items():
        assert summary[metric] >= floor, (metric, summary[metric], floor, wrong)


def test_every_case_is_well_formed():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))
    for case in CASES:
        assert case.expect in ("answer", "no_result")
        if case.expect == "answer":
            assert case.facts and case.cite, case.id
        else:
            assert not case.facts and not case.cite, case.id
