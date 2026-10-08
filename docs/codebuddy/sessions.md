# CodeBuddy sessions

| # | Date | Screenshot | What was asked | What changed | PR / commit |
|---|---|---|---|---|---|
| 1 | 2026-10-09 | `01-codebuddy-prompt-and-diagnosis.jpg` | Fix the CI failure on a clean install (`tests/test_slack_real.py` errors: python-multipart missing), then run the full suite in Docker | CodeBuddy read `pyproject.toml` and `tests/fake_slack.py` and found the cause: the Slack fake calls `request.form()` (line 288), which needs python-multipart | Commit "Tests: add python-multipart (CodeBuddy)" |
| 2 | 2026-10-09 | `02-codebuddy-fix-and-docker.jpg` | Same session | Added `python-multipart>=0.0.18` to the `dev` extras with a comment (`pyproject.toml`, +5 −1); started Docker Desktop when the first test run could not reach the daemon | Same commit |
| 3 | 2026-10-09 | `03-codebuddy-tests-102-passed.jpg` | Same session | Full suite in a clean Python 3.12 container: 102 passed, exit code 0 | Same commit |
