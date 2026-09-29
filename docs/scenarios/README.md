# Scenario walkthroughs

One worked example per handbook scenario, generated from a real run of the system with the audit entry each produced.

1. [Unified query](scenario-1-unified-query.md)
2. [Data freshness](scenario-2-freshness.md)
3. [Negative case, no metadata side-channel](scenario-3-negative-case.md)
4. [Live revocation](scenario-4-live-revocation.md)
5. [Audit inquiry](scenario-5-audit-inquiry.md)

Plus: [Cross-platform stitch](cross-platform-stitch.md) (Drive postmortem to Jira tickets through link expansion).

Regenerate with `python scripts/run_scenarios.py`. The tests in `tests/test_scenarios.py` assert every beat above.
