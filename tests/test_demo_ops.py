"""Operating the demo: repeatable resets, stable ids, rehearsal-safe alerts, a server that exits
cleanly while a compliance console is streaming, and the Gate 1 token kept on Gate 2 denials."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from conftest import SCENARIO_1, decisions_for

ROOT = Path(__file__).resolve().parents[1]
ARCH_Q = "What does the payment gateway do during failover?"


def _alerts(brain, rule: str) -> list[dict]:
    return [a for a in brain.compliance("/audit/alerts", limit=200)["alerts"] if a["rule"] == rule]


def test_gate2_denial_keeps_the_gate1_token(brain):
    brain.ask("jdoe", ARCH_Q)
    brain.admin("POST", "/admin/confluence/pages/8813/restrictions", json={"users": ["sec-ho"], "groups": [], "notify": False})
    _, seq, _ = brain.ask("jdoe", ARCH_Q)
    d = decisions_for(brain.entry(seq), "confluence:8813")[0]
    assert (d["gate1"], d["gate2"], d["rule"]) == ("allow", "deny", "revoked")
    assert d["gate1_rule"] == "confluence:space:PAYGW", "the stale entitlement Gate 1 used is on the record"


def test_reset_keeps_slack_ids_and_old_citations_still_open(brain):
    body, _, _ = brain.ask("jdoe", SCENARIO_1)
    thread = next(c["doc"] for c in body["citations"] if c["doc"].startswith("slack:C0DBM:"))
    brain.admin("POST", "/admin/reset")
    view = brain.c.get(f"/sources/{thread}", headers={"X-User-Id": "jdoe"}).json()
    assert view["available"], "a citation from before the reset still opens"
    again, _, _ = brain.ask("jdoe", SCENARIO_1)
    assert thread in [c["doc"] for c in again["citations"]]


def test_reset_does_not_raise_data_at_rest_alerts_again(brain):
    before = _alerts(brain, "sensitive_data_at_rest")
    assert before
    brain.admin("POST", "/admin/reset")
    brain.admin("POST", "/admin/reset")
    assert len(_alerts(brain, "sensitive_data_at_rest")) == len(before)


def test_each_rehearsal_starts_a_fresh_alert_session(brain):
    probe = ("Where is the Q3 breach report?", "Summarise the Q3 breach incident report.")
    for q in probe:
        brain.ask("ctr-lee", q)
    assert len(_alerts(brain, "container_probe")) == 1
    for q in probe:
        brain.ask("ctr-lee", q)
    assert len(_alerts(brain, "container_probe")) == 1, "a duplicate within the window is suppressed"

    brain.admin("POST", "/admin/reset")
    brain.ask("ctr-lee", probe[0])
    assert len(_alerts(brain, "container_probe")) == 1, "questions from before the reset do not count towards the next run"
    brain.ask("ctr-lee", probe[1])
    assert len(_alerts(brain, "container_probe")) == 2, "the rehearsal's alert fires again after a reset"
    assert brain.brain.audit.verify().ok


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_server_exits_promptly_with_an_open_audit_stream(tmp_path):
    port = _free_port()
    env = {**os.environ, "BRAIN_DATA_DIR": str(tmp_path / "data"), "SYNC_INTERVAL_S": "0", "PYTHONPATH": str(ROOT)}
    env.pop("LLM_API_KEY", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "internal_brain.api.app:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise AssertionError("server did not start")

        with httpx.Client(base_url=base, timeout=10) as client:
            with client.stream("GET", "/audit/stream", headers={"X-User-Id": "compliance"}) as response:
                lines = response.iter_lines()
                assert next(lines).startswith("event: hello")
                proc.send_signal(signal.SIGINT)  # one Ctrl+C
                t0 = time.time()
                rest = list(lines)  # the server ends the stream
                assert any(line.startswith("event: bye") for line in rest)
        assert proc.wait(timeout=10) is not None
        assert time.time() - t0 < 5, "no hang waiting for the stream to finish"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
