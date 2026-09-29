"""Tamper evidence: the chain breaks where a row is edited, gaps where a row is deleted,
and signed checkpoints catch a full rewrite. Also: reads of the log are gated and logged."""

from __future__ import annotations

import json
import sqlite3

from conftest import SCENARIO_1, SCENARIO_2

from internal_brain.audit.log import verify_chain
from internal_brain.audit.verify import main as verify_cli


def _fill(brain, n: int = 4):
    for i in range(n):
        brain.ask("jdoe", SCENARIO_1 if i % 2 == 0 else SCENARIO_2)


def test_audit_chain_detects_tamper(brain, settings):
    _fill(brain)
    log = brain.brain.audit
    ok = log.verify()
    assert ok.ok and ok.entries >= 4 and ok.checkpoints >= 1 and ok.checkpoints_verified == ok.checkpoints

    # 1. edit one answer directly in the database -> chain broken at that seq
    conn = log.conn
    row = conn.execute("SELECT seq, entry_json FROM audit_entries WHERE kind = 'query' ORDER BY seq LIMIT 1").fetchone()
    target = row["seq"]
    doc = json.loads(row["entry_json"])
    doc["answer"] = "the attacker's version of the answer"
    conn.execute("UPDATE audit_entries SET entry_json = ? WHERE seq = ?", (json.dumps(doc, sort_keys=True, separators=(",", ":")), target))
    conn.commit()
    broken = log.verify()
    assert not broken.ok and broken.first_broken_seq == target, broken.as_dict()

    # restore the row, then 2. delete a row -> gap after the previous seq
    conn.execute("UPDATE audit_entries SET entry_json = ? WHERE seq = ?", (row["entry_json"], target))
    conn.commit()
    assert log.verify().ok
    victim = target + 1
    conn.execute("DELETE FROM audit_entries WHERE seq = ?", (victim,))
    conn.commit()
    gap = log.verify()
    assert not gap.ok and gap.gap_after_seq == victim - 1, gap.as_dict()


def test_checkpoint_signatures_catch_a_full_rewrite(brain):
    """An attacker with write access recomputes every hash after an edit; the signed checkpoints still fail."""
    _fill(brain, 6)
    log = brain.brain.audit
    conn = log.conn
    from internal_brain.audit.log import GENESIS_HASH, hash_entry

    rows = conn.execute("SELECT seq, entry_json FROM audit_entries ORDER BY seq").fetchall()
    prev = GENESIS_HASH
    for row in rows:
        doc = json.loads(row["entry_json"])
        if doc["kind"] == "query":
            doc["answer"] = "rewritten"
        doc.pop("prev_hash", None)
        doc.pop("entry_hash", None)
        new_hash = hash_entry(doc, prev)
        doc["prev_hash"], doc["entry_hash"] = prev, new_hash
        conn.execute(
            "UPDATE audit_entries SET entry_json = ?, prev_hash = ?, entry_hash = ? WHERE seq = ?",
            (json.dumps(doc, sort_keys=True, separators=(",", ":")), prev, new_hash, row["seq"]),
        )
        prev = new_hash
    conn.commit()
    report = log.verify()
    assert report.first_broken_seq is None, "the rewrite is internally consistent..."
    assert not report.ok and report.bad_checkpoint_seq is not None, "...but the signed checkpoints no longer match"
    assert any("rows were rewritten" in p for p in report.problems)


def test_verify_cli_reads_the_database(brain, settings, capsys):
    _fill(brain, 3)
    db = str(settings.db_path)
    assert verify_cli(["--db", db]) == 0
    out = capsys.readouterr().out
    assert "audit chain: OK" in out
    # standalone verifier, standalone connection, public key from the key dir
    conn = sqlite3.connect(db)
    from internal_brain.audit.log import load_public_key

    assert verify_chain(conn, load_public_key(settings.audit_key_dir / "audit_public.key")).ok
    conn.execute("DELETE FROM audit_entries WHERE seq = 2")
    conn.commit()
    assert verify_cli(["--db", db, "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["gap_after_seq"] == 1


def test_audit_reads_are_gated_and_logged(brain, client):
    brain.ask("jdoe", SCENARIO_1)
    assert client.get("/audit/entries", headers={"X-User-Id": "jdoe"}).status_code == 403
    assert client.get("/audit/verify", headers={"X-User-Id": "ctr-lee"}).status_code == 403
    before = brain.brain.audit.count()
    page = brain.compliance("/audit/entries", actor="jdoe")
    assert page["entries"] and page["read_logged_as"] == before + 1
    logged = brain.brain.audit.get(page["read_logged_as"])
    assert logged.kind == "audit_read" and logged.actor.id == "compliance" and logged.event["view"] == "entries"


def test_entry_is_written_before_the_response(brain):
    body, seq, _ = brain.ask("jdoe", SCENARIO_1)
    entry = brain.entry(seq)
    assert entry["answer"] == body["answer"], "the log stores exactly what the user saw"
    assert entry["actor"]["id"] == "jdoe" and entry["actor"]["principals"]
    assert entry["prev_hash"] and entry["entry_hash"]
    assert entry["latency_ms"] is not None and entry["model"]
