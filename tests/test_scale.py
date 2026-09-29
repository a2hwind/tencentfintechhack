"""Randomized evidence that permission filtering is exact, on synthetic companies with every ACL
shape (internal_brain/evals/synthetic.py). scripts/benchmark.py runs the same checks at 100k chunks.

1. Projection: for every (user, item), the platform's native rule == (the user's tokens intersect
   the item's ACL tokens), with tokens produced by the production adapters.
2. Gate 1: every permission-filtered query (keywords and vectors, with platform, time and
   container filters) returns exactly the brute-force top k over the documents the asker may see.
"""

from __future__ import annotations

import asyncio

import pytest

from internal_brain.evals.scale import build_sync, check_gate1
from internal_brain.evals.synthetic import SyntheticEmbedder, check_projection, generate_company
from internal_brain.mocks.store import CompanyStore

SEEDS = range(12)


def _shapes(store: CompanyStore) -> set[str]:
    """Which ACL shapes a generated company actually contains."""
    shapes: set[str] = set()
    for page in store.pages.values():
        restricted = [p for p in store.confluence_chain(page) if p.restrictions and not p.restrictions.empty()]
        if restricted:
            shapes.add("confluence:restriction")
            if restricted[0] is not page:
                shapes.add("confluence:inherited-restriction")
            if len(restricted) > 1:
                shapes.add("confluence:intersected-restrictions")
            space = store.spaces[page.space]
            if any(not store.user_in(u, space.read) for u in restricted[0].restrictions.users):
                shapes.add("confluence:grantee-without-space-access")
    for project in store.projects.values():
        in_roles = {u for members in project.roles.values() for u in members}
        if any(u not in in_roles for members in project.security_levels.values() for u in members):
            shapes.add("jira:level-member-without-browse")
    if any(i.security_level for i in store.issues.values()):
        shapes.add("jira:security-level")
    if any(c.is_dm for c in store.channels.values()):
        shapes.add("slack:dm")
    if any(c.private and not c.is_dm for c in store.channels.values()):
        shapes.add("slack:private")
    if any(u.slack_guest for u in store.users.values()):
        shapes.add("slack:guest")
    if any(f.anyone_with_link for f in store.files.values()):
        shapes.add("gdrive:anyone-with-link")
    if any(len(store.gdrive_folder_chain(f)) > 1 for f in store.files.values()):
        shapes.add("gdrive:nested-folders")
    if any(not f.permissions.empty() for f in store.files.values()):
        shapes.add("gdrive:file-share")
    return shapes


def test_projection_is_exact_on_random_companies():
    pairs = allowed = 0
    shapes: set[str] = set()
    for seed in SEEDS:
        store = CompanyStore(generate_company(users=40, items=160, seed=seed))
        shapes |= _shapes(store)
        report = asyncio.run(check_projection(store))
        assert report.over_grants == [], f"seed {seed}: Gate 1 would over-grant {report.over_grants[:3]}"
        assert report.under_grants == [], f"seed {seed}: Gate 1 would under-grant {report.under_grants[:3]}"
        pairs += report.pairs
        allowed += report.allowed
    assert pairs >= 70_000 and 0.2 < allowed / pairs < 0.6, (pairs, allowed)
    assert shapes >= {
        "confluence:restriction", "confluence:inherited-restriction", "confluence:intersected-restrictions",
        "confluence:grantee-without-space-access", "jira:security-level", "jira:level-member-without-browse",
        "slack:dm", "slack:private", "slack:guest", "gdrive:anyone-with-link", "gdrive:nested-folders", "gdrive:file-share",
    }, shapes


@pytest.mark.parametrize("seed", [3, 11])
def test_gate1_equals_the_bruteforce_oracle(seed):
    built = build_sync(generate_company(users=80, items=400, seed=seed), SyntheticEmbedder(seed=seed))
    report = check_gate1(built, n_queries=150, seed=seed)
    assert report.fts_mismatches == [], report.fts_mismatches[:2]
    assert report.vec_mismatches == [], report.vec_mismatches[:2]
    assert report.unpermitted_returned == 0
    # the naive alternative (top k over everything, then drop what the asker cannot see) loses results
    assert report.postfilter_short > 0 and sum(report.postfilter_recall) / len(report.postfilter_recall) < 0.95
