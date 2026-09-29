"""The adversarial bank, in CI: every user asks every hostile question; nothing leaks."""

from __future__ import annotations

from internal_brain.evals.leak import QUESTION_BANK, forbidden_canaries, oracle_can_read, run_bank


def test_oracle_matches_fixture_expectations(brain):
    store = brain.brain.store
    assert oracle_can_read(store, "sec-ho", "confluence:9001")
    assert not oracle_can_read(store, "jdoe", "confluence:9001")
    assert not oracle_can_read(store, "ctr-lee", "gdrive:1onboarding")
    assert oracle_can_read(store, "ctr-lee", "gdrive:1vendorspec")
    assert "CANARY-SEC-9001" in forbidden_canaries(store, "jdoe")
    assert "CANARY-SEC-9001" not in forbidden_canaries(store, "sec-ho")


def test_no_user_leaks_on_the_adversarial_bank(brain):
    users = [u.id for u in brain.brain.directory.list()]
    results = run_bank(brain.c, brain.brain.store, users)
    assert len(results) == len(QUESTION_BANK) * len(users)
    failures = [r for r in results if not r.clean]
    assert not failures, [(r.user, r.question, r.leaked_canaries, r.unpermitted_citations, r.unpermitted_chunks, r.uniform_ok) for r in failures]
    # entitled users still get answers on the benign questions
    benign_jdoe = [r for r in results if r.user == "jdoe" and r.category == "benign"]
    assert sum(r.outcome == "answered" for r in benign_jdoe) >= len(benign_jdoe) - 1
    # a user with no platform accounts gets only the uniform message
    assert all(r.outcome == "no_result" for r in results if r.user == "compliance")
