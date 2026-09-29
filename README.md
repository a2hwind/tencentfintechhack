# Internal Brain

**Company-wide answers, scoped to your permissions, fully auditable.**

A context-aware enterprise knowledge system over Confluence, Jira, Slack and Google Drive for the Tencent Cloud AI Singapore Hackathon 2026 (FinTech track, Aspire's "The Internal Brain" challenge). Not a chatbot bolted onto search: permission is a retrieval-time predicate evaluated against the asker's live entitlements, the LLM only ever sees permitted text, and every decision lands in a hash-chained audit log before the answer is returned.

- **Two gates.** Gate 1 filters by principal tokens *inside* the index query, keyword and vector alike, so top-k is computed only over permitted chunks. Gate 2 re-verifies each surviving candidate at the source platform, live, and refreshes anything that changed since the last sync.
- **Uniform denials.** A restricted document and a non-existent one produce byte-identical responses in the same time; the reason goes to the audit log, never to the asker.
- **Tamper-evident, live audit.** Every query, source open, permission event, sync, alert and audit read is an entry in a SHA-256 hash chain with Ed25519-signed checkpoints, streamed live to the compliance console. `python -m internal_brain.audit.verify` finds an edited row or a gap.
- **Grounded, glass-box answers.** A citation and an evidence quote per sentence; a click opens the exact passage, re-checked through both gates. `NO_ANSWER` is the only alternative to a supported answer.
- **Fintech guardrails.** Card numbers, NRICs, bank accounts and secrets are masked at ingestion, so no prompt ever holds them; insider-threat alerts (probing restricted spaces, bursts of blocked questions, retries after revocation) are computed from the trail. Control map: [docs/compliance.md](docs/compliance.md) (MAS TRM, PDPA and NRIC guidance, PCI DSS, IMDA).
- **Real Slack.** `SLACK_MODE=real` connects a workspace through a bot token, with Gate 2 checking membership live and Slack Events arriving signed at `/webhooks/slack`.
- **Evidence, not claims.** 350 adversarial queries: zero leaks ([leak-eval](docs/evidence/leak-eval.md)); denied and missing take the same time ([side-channels](docs/evidence/side-channels.md)); Gate 1 equals a brute-force oracle on 900 queries at up to 100k chunks and 1,000 users ([scale](docs/evidence/scale.md)); 28 golden questions with zero leaks and 100% correct refusals ([quality](docs/evidence/quality.md)).

Start with [docs/architecture.md](docs/architecture.md) (components, trust boundaries, permission model, trade-offs), then [docs/scenarios/](docs/scenarios/README.md) (one worked example per handbook scenario with its audit entry). [docs/submission.md](docs/submission.md) is the drafted submission form; read [AGENTS.md](AGENTS.md) before opening the repo in CodeBuddy.

## Quick start

Backend (Python 3.11+). No API key is needed: without one the planner and answerer are deterministic stubs, so everything runs offline.

```bash
pip install -e ".[dev]"
cp .env.example .env              # optional: models, Slack, knobs
uvicorn internal_brain.api.app:app --port 8000 --env-file .env   # or: make dev
# http://localhost:8000/docs
```

UI (Node 22):

```bash
cd ui && npm install && npm run dev
# http://localhost:3000            chat          /presenter  split-screen demo with a guided run
# /compare  two askers side by side  /audit  compliance console  /admin  permissions and demo controls
```

Ask a question as `jdoe` (identity is an `X-User-Id` header in the demo; production swaps in the IdP session):

```bash
curl -s localhost:8000/ask -H 'X-User-Id: jdoe' -H 'Content-Type: application/json' \
  -d '{"question": "What is the status of the database migration and were there blockers raised in Slack last week?"}' | jq
```

**Real models.** Set `LLM_BASE_URL`, `LLM_API_KEY` and the model names in `.env`: Tencent Cloud TokenHub (`https://tokenhub-intl.tencentcloudmaas.com/v1`, models `hy3` or `hy4-preview`) or Hunyuan (`https://api.hunyuan.cloud.tencent.com/v1`, `hunyuan-turbos-latest`; `EMBEDDINGS=hunyuan` for its embedding model). `python scripts/smoke_llm.py` checks an endpoint (chat, JSON mode, embeddings) and runs the scenarios through it. Changing the embedder rebuilds the index on the next start.

**Real Slack.** Create an internal Slack app with the scopes listed in `scripts/slack_setup.py`, invite the bot to the channels the Brain may index, then:

```bash
export SLACK_MODE=real SLACK_BOT_TOKEN=xoxb-... SLACK_SIGNING_SECRET=...
python scripts/slack_setup.py check                 # token, scopes, readable channels
python scripts/slack_setup.py map                   # prints SLACK_USER_MAP (Brain user -> Slack id, by email)
python scripts/slack_setup.py seed --prefix ib-     # optional, a TEST workspace: the Company A channels and threads
```

Point Event Subscriptions at `https://<host>/webhooks/slack` (behind Caddy: `/api/webhooks/slack`). Confluence, Jira and Drive stay mocked.

**Tests and evidence.**

```bash
pytest                              # or: make test (about a minute)
python scripts/run_scenarios.py     # docs/scenarios/        (make scenarios)
python scripts/leak_eval.py         # docs/evidence/ leak + side channels (make evidence)
python scripts/quality_eval.py      # docs/evidence/quality.md (make quality)
python scripts/benchmark.py         # docs/evidence/scale.md, ~3 min (make benchmark)
```

Docker (API + UI + Caddy on one instance; a real `SITE_ADDRESS` turns on HTTPS):

```bash
docker compose up -d --build
SITE_ADDRESS=brain.example.com docker compose up -d --build
```

## The demo, in seven minutes

Open `/presenter`: the asker's answer on the left, the compliance console's live chain on the right, the trust-boundary diagram lighting up stage by stage. The beats panel runs each step (narration and "point here" cues are in `ui/lib/demoBeats.ts`).

| Beat | What happens | What to point at |
|---|---|---|
| 0 Reset | Fixture reloaded, index rebuilt, a fresh alert session | The two panes and the live chain |
| 1 Scenario 1 (jdoe) | Migration status and last week's Slack blockers: Jira, Drive and Slack in one cited answer | Gate 1 denials in red: found, never shown to the model; click a citation for the exact passage |
| 2 Scenario 3 (ctr-lee) | `/compare`: the restricted Q3 breach report against a report that does not exist | "Byte-identical responses", same size and hash |
| 3 Probing | The contractor keeps asking | A high-severity `container_probe` alert on the live chain; the contractor saw nothing |
| 4 Scenario 4 | Admin removes jdoe from #db-migration; jdoe asks again | The thread is gone; rule `revoked`; the old citation now opens as unavailable |
| 5 Missed webhook | The same removal with the event lost | Gate 2 denies what the warm cache let through; `gate1_rule` records the stale token |
| 6 Scenario 2 | Sync paused, a failover step added to the runbook | Gate 2 refreshes to v8; step 4 is in the answer |
| 7 Stitch | Root cause of last quarter's outage and its follow-ups | One live check per document, at each platform |
| 8 DLP | "What card was charged twice?" | `••••1111`, `•••••567D`: the model never saw the full values |
| 9 Scenario 5 (compliance) | "Everything jdoe accessed in the payment gateway space in the last 30 days" | A validated filter, not a model reading the log; Verify chain is green |
| 10 Close | Replay the trace on the trust-boundary diagram | The model sits outside the boundary |

The tamper beat, from a terminal:

```bash
python -m internal_brain.audit.verify --db data/brain.db      # OK
python scripts/tamper_demo.py --db data/brain.db edit          # edit the newest answer in place
python -m internal_brain.audit.verify --db data/brain.db      # chain broken at seq N
python scripts/tamper_demo.py --db data/brain.db delete        # delete a row
python -m internal_brain.audit.verify --db data/brain.db      # gap after seq N
python scripts/tamper_demo.py --db data/brain.db restore
```

## Repository layout

```
internal_brain/
  models.py        the shared contracts: adapter types, chunk record, plan, decision, audit entry, /ask
  config.py        settings from environment variables (.env.example lists them all)
  adapters/        base.py (the five-method interface), confluence, jira, slack (mock), gdrive,
                   slack_real.py (Web API, bot token), slack_events.py (Events API signing + translation)
  mocks/           store.py (Company A with real permission semantics), one FastAPI app per platform,
                   fixtures/company_a.yaml
  core/            identity, entitlements, events, sync, dlp, index + vector_cache (Gate 1), planner,
                   retrieve, verify_live (Gate 2), assemble, answer, guard, evidence, pipeline, llm
  audit/           log.py (chain, checkpoints, live stream), verify.py (CLI), queries.py (compliance
                   views + NL front), alerts.py (insider-threat and data-at-rest rules)
  evals/           leak bank, golden Q&A (quality.py), synthetic companies and the Gate 1 oracle (synthetic.py, scale.py)
  api/             brain.py (wiring), app.py, routes: /ask, /sources, /admin/*, /audit/*, /webhooks/slack
ui/                Next.js: chat with evidence drawer, presenter, compare, compliance console, admin panel
tests/             scenarios, audit chain, permissions, guardrails, glass box, LLM path, real Slack (strict fake),
                   scale (randomized oracle), quality floors, vector cache, demo operations, leak bank
docs/              architecture.md, compliance.md, submission.md, scenarios/, evidence/, screenshots/, codebuddy/
scripts/           run_scenarios, leak_eval, quality_eval, benchmark, smoke_llm, slack_setup, screenshots, cover, tamper_demo
```

## API surface

| Route | Role | Purpose |
|---|---|---|
| `POST /ask` | any user | one grounded answer (sentences with citations and evidence) or the uniform no-result; `X-Audit-Seq` header |
| `GET /sources/{doc}` | any user | open a cited document, re-checked through both gates and audited; unavailable is one uniform shape |
| `GET /me`, `GET /users` | | the asker's live principals; the demo user list |
| `GET /admin/state`, `GET /admin/index` | admin | mock platform state, sync status, indexed items with their ACLs |
| `POST /admin/slack/...`, `/admin/confluence/...`, `/admin/jira/...`, `/admin/gdrive/...`, `/admin/groups/...`, `DELETE /admin/items/{id}` | admin | mutate the mocks; `notify=false` simulates a missed webhook (Slack controls are off in real mode) |
| `POST /admin/sync/pause`, `resume`, `run`; `POST /admin/reset` | admin | poller controls; reset the demo to the fixture (audit log untouched) |
| `GET /audit/entries`, `/audit/entries/{seq}` | compliance | filtered entries; a full entry with decisions, chunks sent to the model, stage timings, hashes |
| `GET /audit/views/user-access`, `doc-access`, `denials`, `GET /audit/nl?q=` | compliance | canned views and the natural-language front |
| `GET /audit/alerts`, `GET /audit/stream` | compliance | insider-threat and data-at-rest alerts; the live chain as server-sent events |
| `GET /audit/verify`, `GET /audit/head` | compliance | walk the chain; current head and checkpoints |
| `POST /webhooks/slack` | Slack | Events API (signed; SLACK_MODE=real) |
| `/mock/{confluence,jira,slack,gdrive}/...` | | the mock platforms' own APIs (the adapters talk to these) |

Every read of `/audit/*` is itself an audit entry.

## Swapping in a real platform

`adapters/base.py` defines the five methods (`changes_since`, `get_item`, `get_acl`, `principals_for`, `can_read`). Slack is done (`adapters/slack_real.py`, tested against a strict fake of the Web API). Google Drive (API v3: the changes feed, `permissions.list`, and a live `files.get` check) is the next cheapest; Confluence ACL inheritance is the slowest to enumerate, which is why Gate 2 re-verifies per item rather than recomputing whole principal sets on every query.

## Team split (three streams, four shared contracts)

Stream A: platforms and permissions (mocks, adapters, entitlement resolver, sync, Gate 2). Stream B: retrieval and answer (index, planner, fusion, assembly, prompt, guard). Stream C: audit, UI and story (audit chain, console, UI, diagrams, submission pack). The four contracts — the adapter interface, the chunk record, the audit entry and the `/ask` response — are pydantic models in `internal_brain/models.py` and were written before any feature code.

Handbook rule from day one: every pull request carries a CodeBuddy screenshot or session log in `docs/codebuddy/`.
