## What

<!-- one paragraph: what changed and why -->

## Checks

- [ ] `pytest` passes locally (every handbook scenario, tamper detection, the adversarial leak bank)
- [ ] `cd ui && npm run build` passes if the UI changed
- [ ] `python scripts/run_scenarios.py` regenerated `docs/scenarios/` if answer or audit behaviour changed
- [ ] **CodeBuddy / WorkBuddy proof**: a screenshot or session log for this change is in `docs/codebuddy/` and `docs/codebuddy/sessions.md` has a row for it
- [ ] Any new trade-off is stated in `docs/architecture.md`

## Scenario touched

<!-- 1 unified query · 2 freshness · 3 negative case · 4 live revocation · 5 audit inquiry · none -->
