# Working on Internal Brain with a coding assistant (CodeBuddy, WorkBuddy or any agent)

Read this first; it is short and it is what keeps changes safe.

## The one rule

Permission is a retrieval-time predicate evaluated against the asker's live entitlements. It is never an ingestion-time filter and never a check on the finished answer. Any change that moves enforcement after retrieval, exposes counts or reasons to the asker, or lets restricted text reach a prompt is wrong even if every test still passes. Read `docs/architecture.md` sections 2, 5 and 7 before touching `core/`.

## Map

| Path | What lives there | Change it when |
|---|---|---|
| `internal_brain/models.py` | the four shared contracts (adapter types, chunk record, audit entry, `/ask`) | only with all three streams agreeing; every stream depends on these |
| `internal_brain/adapters/` | one interface, four platforms; mocks and real behind the same five methods (`slack_real.py` + `slack_events.py` for a real workspace) | adding a platform, making an adapter real |
| `internal_brain/mocks/` | Company A with real permission semantics; `fixtures/company_a.yaml` | changing the demo data (keep the canaries in restricted docs) |
| `internal_brain/core/index.py`, `vector_cache.py` | Gate 1 (the ACL predicate inside the keyword query; the permission bitmap for vectors) | retrieval quality, scale (run `make benchmark`) |
| `internal_brain/core/dlp.py` | card, NRIC, bank-account and secret masking at ingestion and on output | new identifier formats |
| `internal_brain/core/verify_live.py` | Gate 2, read-through refresh, fail-closed | freshness, revocation |
| `internal_brain/core/guard.py` | citation whitelist, uncited-claim stripping, lexical / LLM verifier | hallucination control |
| `internal_brain/audit/` | hash chain, signed checkpoints, `brain-verify`, compliance views, live stream, alert rules | anything the compliance console needs |
| `internal_brain/evals/` | leak bank, golden Q&A, synthetic companies and the Gate 1 oracle | evidence; add a golden case for every answer-quality fix |
| `internal_brain/api/` | wiring (`brain.py`) and routes | new endpoints |
| `ui/` | Next.js chat, compliance console, admin panel; `lib/api.ts` mirrors the API types | UI |
| `tests/` | one test per handbook scenario, tamper detection, ACL projection (fixture and randomized), guardrails, glass box, LLM path (fake server), real Slack (strict fake), quality floors, leak bank | every change |

## Commands

```bash
make install        # pip + npm
make test           # pytest: must stay green
make dev            # API on :8000       make ui  # Next.js on :3000
make scenarios      # regenerate docs/scenarios from a real run
make evidence       # adversarial leak eval + timing -> docs/evidence
make quality        # golden Q&A -> docs/evidence/quality.md
make benchmark      # Gate 1 vs a brute-force oracle at 100k chunks -> docs/evidence/scale.md
make help           # everything else
```

## Conventions

- Python 3.11, type hints everywhere, pydantic models for anything that crosses a boundary, no new dependencies without a line in `pyproject.toml` and a reason in the PR.
- Every platform call at query time runs under the asker's identity; the LLM never gets credentials, tools, or text the asker cannot read.
- A denial is byte-identical to a miss. Never add a hint, a count, or a different message for restricted content. Reasons go to the audit log only.
- The audit entry is written before the response is returned. Never after. Never skip it on error.
- New behaviour gets a test in the file that matches its scenario; if it changes an answer or an audit entry, regenerate `docs/scenarios/`.
- Restricted fixture documents carry `CANARY-*` strings; tests and the leak bank assert those never appear for the wrong user. Keep them when editing the fixture.
- A new ACL shape in any adapter gets a case in `internal_brain/evals/synthetic.py`; `tests/test_scale.py` then checks the projection against the platform's own rule on random companies.
- Card numbers, NRICs and secrets never reach the index in full: extend `core/dlp.py` rather than masking later in the pipeline. Bump `INGEST_VERSION` in `api/brain.py` whenever what the index stores changes.
- Every pull request carries a CodeBuddy/WorkBuddy screenshot or session log in `docs/codebuddy/` (handbook requirement: no proof, no score).

## Good first tasks for a session

- A real Google Drive adapter behind `adapters/base.py` (Drive API v3 changes feed, `permissions.list` with inherited permissions, a live `files.get` for Gate 2); `adapters/slack_real.py` and its strict fake in `tests/fake_slack.py` are the pattern to follow.
- Tencent Cloud VectorDB behind `Index.search_vec` / `shadow_denied` (the permission bitmap becomes a metadata filter on allowed principals).
- A cross-encoder reranker on the fused top 30 in `core/retrieve.py`.
- OIDC session in `api/deps.py` replacing the demo `X-User-Id` header.
