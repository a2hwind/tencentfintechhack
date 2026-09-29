"""Test harness: a fresh Internal Brain per test, mocks included, no network, no LLM key."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Never pick up a developer's LLM key: every test must run against the deterministic stubs.
for var in ("LLM_API_KEY", "LLM_BASE_URL", "VERIFIER"):
    os.environ.pop(var, None)

from internal_brain.api.app import create_app  # noqa: E402
from internal_brain.config import Settings  # noqa: E402

SCENARIO_1 = "What's the status of the database migration and were there blockers raised in Slack last week?"
SCENARIO_2 = "What's the latest runbook for the payment-service incident?"
SCENARIO_3 = "Where is the Q3 breach report?"
SCENARIO_3_CONTROL = "Where is the Q9 llama report?"
CROSS_PLATFORM = "Root cause of the payment outage last quarter and the follow-up tickets"

CANARIES = ("CANARY-LEADS-7731", "CANARY-SEC-9001", "CANARY-VENDOR-7305", "CANARY-VENDOR-7306", "CANARY-PAY-260", "CANARY-SLACK-SEC", "CANARY-DRIVE-SEC")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", sync_interval_s=0, checkpoint_every=3)


@pytest.fixture
def app(settings: Settings):
    return create_app(settings, initial_sync=True, poller=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


class BrainClient:
    """Thin helper over the HTTP API so tests read like the demo script."""

    def __init__(self, client: TestClient):
        self.c = client
        self.brain = client.app.state.brain

    def ask(self, user: str, question: str):
        r = self.c.post("/ask", json={"question": question}, headers={"X-User-Id": user})
        assert r.status_code == 200, r.text
        return r.json(), int(r.headers["X-Audit-Seq"]), r

    def entry(self, seq: int) -> dict:
        r = self.c.get(f"/audit/entries/{seq}", headers={"X-User-Id": "compliance"})
        assert r.status_code == 200, r.text
        return r.json()["entry"]

    def admin(self, method: str, path: str, **kwargs):
        r = self.c.request(method, path, headers={"X-User-Id": "admin"}, **kwargs)
        assert r.status_code == 200, r.text
        return r.json()

    def compliance(self, path: str, **params):
        r = self.c.get(path, params=params, headers={"X-User-Id": "compliance"})
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def brain(client) -> BrainClient:
    return BrainClient(client)


def decisions_for(entry: dict, doc: str) -> list[dict]:
    return [d for d in entry["decisions"] if d["doc"] == doc]


def denied_docs(entry: dict) -> dict[str, str]:
    return {d["doc"]: d["rule"] for d in entry["decisions"] if d["gate1"] == "deny" or d["gate2"] == "deny"}


def allowed_docs(entry: dict) -> dict[str, str]:
    return {d["doc"]: d["rule"] for d in entry["decisions"] if d["gate1"] == "allow" and d["gate2"] == "allow"}
