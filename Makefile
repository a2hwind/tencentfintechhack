.PHONY: install dev ui test scenarios evidence quality benchmark screenshots cover verify tamper-edit tamper-delete tamper-restore slack-check docker

PY ?= python3

install:            ## backend + UI dependencies
	pip install -e ".[dev]"
	cd ui && npm install

dev:                ## API with auto-reload on :8000, reading .env if present (stubs unless LLM_API_KEY is set)
	uvicorn internal_brain.api.app:app --reload --port 8000 --timeout-graceful-shutdown 5 $(if $(wildcard .env),--env-file .env,)

ui:                 ## Next.js dev server on :3000
	cd ui && npm run dev

test:               ## every test: scenarios, audit chain, permissions, guardrails, glass box, real Slack, scale, quality, leak bank
	pytest -q

scenarios:          ## regenerate docs/scenarios/*.md from a real run
	$(PY) scripts/run_scenarios.py

evidence:           ## adversarial leak eval + side-channel timing -> docs/evidence/
	$(PY) scripts/leak_eval.py

quality:            ## golden Q&A: answer quality under permissions -> docs/evidence/quality.md
	$(PY) scripts/quality_eval.py

benchmark:          ## Gate 1 vs a brute-force oracle at 1k/10k/100k chunks, 1,000 users (~3 min) -> docs/evidence/scale.md
	$(PY) scripts/benchmark.py

slack-check:        ## check SLACK_BOT_TOKEN, scopes and the channels the bot can read
	$(PY) scripts/slack_setup.py check

screenshots:        ## UI screenshots -> docs/screenshots/ (needs `cd ui && npm run build`)
	$(PY) scripts/screenshots.py

cover:              ## 16:9 cover image -> docs/cover-*.png
	$(PY) scripts/cover.py

verify:             ## walk the audit chain of the running demo's database
	$(PY) -m internal_brain.audit.verify --db data/brain.db

tamper-edit:        ## demo beat: edit the newest answer in place, then `make verify`
	$(PY) scripts/tamper_demo.py --db data/brain.db edit

tamper-delete:      ## demo beat: delete a row, then `make verify`
	$(PY) scripts/tamper_demo.py --db data/brain.db delete

tamper-restore:     ## put the pre-tamper database back
	$(PY) scripts/tamper_demo.py --db data/brain.db restore

docker:             ## API + UI + Caddy
	docker compose up -d --build

help:
	@awk -F':.*## ' '/^[a-z-]+:.*## /{printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
