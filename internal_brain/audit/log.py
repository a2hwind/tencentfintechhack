"""Append-only, hash-chained audit log with signed checkpoints.

    entry_hash = SHA-256(canonical_json(entry without prev_hash/entry_hash) || prev_hash)

Every entry commits to the previous one, and seq is contiguous, so editing any field
breaks every later link and deleting a row leaves a gap nobody can produce. Every N
entries the current entry_hash is signed with an Ed25519 key kept outside the
application database (a separate key file in the demo, a KMS in production): an
attacker with write access to the database can rewrite rows and recompute hashes but
cannot forge the checkpoint signatures.

One entry per query with the per-document decisions nested inside; separate entries
for permission events, sync events and reads of the audit log itself. Entries are
written before the answer is returned, never after.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from ..models import AuditActor, AuditEntry, Decision, GuardStats, SentChunk

GENESIS_HASH = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_entries (
    seq        INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    actor_id   TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_entries_actor_ts ON audit_entries(actor_id, ts);
CREATE INDEX IF NOT EXISTS audit_entries_kind_ts ON audit_entries(kind, ts);
CREATE TABLE IF NOT EXISTS audit_decisions (
    seq       INTEGER NOT NULL,
    ts        TEXT NOT NULL,
    actor_id  TEXT NOT NULL,
    doc       TEXT NOT NULL,
    platform  TEXT NOT NULL,
    container TEXT,
    gate1     TEXT NOT NULL,
    gate2     TEXT NOT NULL,
    rule      TEXT NOT NULL,
    decision  TEXT NOT NULL,
    refreshed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS audit_decisions_actor ON audit_decisions(actor_id, ts);
CREATE INDEX IF NOT EXISTS audit_decisions_doc ON audit_decisions(doc, ts);
CREATE INDEX IF NOT EXISTS audit_decisions_container ON audit_decisions(container, ts);
CREATE INDEX IF NOT EXISTS audit_decisions_decision ON audit_decisions(decision, ts);
CREATE TABLE IF NOT EXISTS audit_checkpoints (
    id         INTEGER PRIMARY KEY,
    seq        INTEGER NOT NULL,
    entry_hash TEXT NOT NULL,
    ts         TEXT NOT NULL,
    key_id     TEXT NOT NULL,
    signature  TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_entry(body: dict[str, Any], prev_hash: str) -> str:
    return hashlib.sha256((canonical_json(body) + prev_hash).encode("utf-8")).hexdigest()


def checkpoint_message(seq: int, entry_hash: str) -> bytes:
    return f"internal-brain-checkpoint:{seq}:{entry_hash}".encode("utf-8")


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
class SigningKeys:
    """Ed25519 key pair stored outside the application database."""

    def __init__(self, key_dir: Path):
        self.key_dir = Path(key_dir)
        self.key_dir.mkdir(parents=True, exist_ok=True)
        self.private_path = self.key_dir / "audit_signing.key"
        self.public_path = self.key_dir / "audit_public.key"
        if self.private_path.exists():
            self.private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(self.private_path.read_text().strip()))
        else:
            self.private = Ed25519PrivateKey.generate()
            raw = self.private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
            self.private_path.write_text(raw.hex())
            try:
                self.private_path.chmod(0o600)
            except OSError:
                pass
        self.public = self.private.public_key()
        self.public_path.write_text(self.public_hex)

    @property
    def public_hex(self) -> str:
        return self.public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    @property
    def key_id(self) -> str:
        return hashlib.sha256(bytes.fromhex(self.public_hex)).hexdigest()[:16]

    def sign(self, seq: int, entry_hash: str) -> str:
        return self.private.sign(checkpoint_message(seq, entry_hash)).hex()


def load_public_key(path_or_hex: str | Path) -> Ed25519PublicKey:
    text = Path(path_or_hex).read_text().strip() if Path(str(path_or_hex)).exists() else str(path_or_hex).strip()
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(text))


# ---------------------------------------------------------------------------
# Verification report
# ---------------------------------------------------------------------------
@dataclass
class VerifyReport:
    ok: bool
    entries: int
    checkpoints: int
    checkpoints_verified: int
    first_broken_seq: int | None = None
    gap_after_seq: int | None = None
    bad_checkpoint_seq: int | None = None
    head_hash: str | None = None
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "entries": self.entries,
            "checkpoints": self.checkpoints,
            "checkpoints_verified": self.checkpoints_verified,
            "first_broken_seq": self.first_broken_seq,
            "gap_after_seq": self.gap_after_seq,
            "bad_checkpoint_seq": self.bad_checkpoint_seq,
            "head_hash": self.head_hash,
            "problems": self.problems,
        }


def verify_chain(conn: sqlite3.Connection, public_key: Ed25519PublicKey | None) -> VerifyReport:
    """Walk the chain from genesis: recompute every hash, check every link, every seq, every signature."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT seq, entry_json, prev_hash, entry_hash FROM audit_entries ORDER BY seq").fetchall()
    report = VerifyReport(ok=True, entries=len(rows), checkpoints=0, checkpoints_verified=0)
    prev_hash = GENESIS_HASH
    expected_seq = 1
    hashes: dict[int, str] = {}
    for row in rows:
        seq = row["seq"]
        if seq != expected_seq:
            report.ok = False
            report.gap_after_seq = expected_seq - 1
            report.problems.append(f"gap after seq {expected_seq - 1}: next row is seq {seq}")
            break
        body = json.loads(row["entry_json"])
        body.pop("prev_hash", None)
        body.pop("entry_hash", None)
        recomputed = hash_entry(body, prev_hash)
        if row["prev_hash"] != prev_hash or row["entry_hash"] != recomputed:
            report.ok = False
            report.first_broken_seq = seq
            report.problems.append(f"chain broken at seq {seq}")
            break
        hashes[seq] = row["entry_hash"]
        prev_hash = row["entry_hash"]
        expected_seq += 1
    report.head_hash = prev_hash if rows else None
    chain_ok = report.ok

    checkpoints = conn.execute("SELECT seq, entry_hash, key_id, signature FROM audit_checkpoints ORDER BY seq").fetchall()
    report.checkpoints = len(checkpoints)
    for cp in checkpoints:
        if public_key is None:
            continue
        try:
            public_key.verify(bytes.fromhex(cp["signature"]), checkpoint_message(cp["seq"], cp["entry_hash"]))
        except (InvalidSignature, ValueError):
            report.ok = False
            report.bad_checkpoint_seq = report.bad_checkpoint_seq or cp["seq"]
            report.problems.append(f"checkpoint signature invalid at seq {cp['seq']}")
            continue
        if cp["seq"] in hashes:
            if hashes[cp["seq"]] != cp["entry_hash"]:
                report.ok = False
                report.bad_checkpoint_seq = report.bad_checkpoint_seq or cp["seq"]
                report.problems.append(f"checkpoint at seq {cp['seq']} does not match the chain (rows were rewritten)")
                continue
            report.checkpoints_verified += 1
        elif chain_ok:
            report.ok = False
            report.bad_checkpoint_seq = report.bad_checkpoint_seq or cp["seq"]
            report.problems.append(f"checkpoint at seq {cp['seq']} points past the end of the chain (rows were deleted)")
        # else: the chain is already broken before this checkpoint; nothing more to learn from it
    return report


# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------
class AuditLog:
    def __init__(self, conn: sqlite3.Connection, key_dir: Path, checkpoint_every: int = 5, lock: threading.RLock | None = None):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.lock = lock or threading.RLock()
        self.keys = SigningKeys(key_dir)
        self.checkpoint_every = max(1, checkpoint_every)
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # ------------------------------------------------------------------ live tail
    def subscribe(self, maxsize: int = 1000) -> asyncio.Queue:
        """A queue that receives every entry appended from now on (for the live console stream)."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers = [(loop, q) for loop, q in self._subscribers if q is not queue]

    @staticmethod
    def _offer(queue: asyncio.Queue, entry: AuditEntry) -> None:
        if queue.full():  # a slow consumer drops the oldest entry, never blocks the writer
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(entry)

    def _notify(self, entry: AuditEntry) -> None:
        alive = []
        for loop, queue in self._subscribers:
            if loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(self._offer, queue, entry)
                alive.append((loop, queue))
            except RuntimeError:
                continue
        self._subscribers = alive

    # ------------------------------------------------------------------ append
    def append(self, kind: str, actor: AuditActor, **fields: Any) -> AuditEntry:
        with self.lock:
            row = self.conn.execute("SELECT seq, entry_hash FROM audit_entries ORDER BY seq DESC LIMIT 1").fetchone()
            seq = (row["seq"] + 1) if row else 1
            prev_hash = row["entry_hash"] if row else GENESIS_HASH
            entry = AuditEntry(seq=seq, ts=now_iso(), kind=kind, actor=actor, **fields)  # type: ignore[arg-type]
            body = entry.model_dump(mode="json", exclude={"prev_hash", "entry_hash"})
            entry.prev_hash = prev_hash
            entry.entry_hash = hash_entry(body, prev_hash)
            stored = dict(body, prev_hash=prev_hash, entry_hash=entry.entry_hash)
            cur = self.conn.cursor()
            cur.execute("BEGIN")
            cur.execute(
                "INSERT INTO audit_entries(seq, ts, kind, actor_id, entry_json, prev_hash, entry_hash) VALUES (?,?,?,?,?,?,?)",
                (seq, entry.ts, kind, actor.id, canonical_json(stored), prev_hash, entry.entry_hash),
            )
            if entry.decisions:
                cur.executemany(
                    "INSERT INTO audit_decisions(seq, ts, actor_id, doc, platform, container, gate1, gate2, rule, decision, refreshed) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (seq, entry.ts, actor.id, d.doc, d.platform, d.container, d.gate1, d.gate2, d.rule, "allow" if (d.gate1 == "allow" and d.gate2 == "allow") else "deny", int(d.refreshed))
                        for d in entry.decisions
                    ],
                )
            if seq % self.checkpoint_every == 0:
                cur.execute(
                    "INSERT INTO audit_checkpoints(seq, entry_hash, ts, key_id, signature) VALUES (?,?,?,?,?)",
                    (seq, entry.entry_hash, entry.ts, self.keys.key_id, self.keys.sign(seq, entry.entry_hash)),
                )
            self.conn.commit()
        if self._subscribers:
            self._notify(entry)
        return entry

    # convenience writers ------------------------------------------------------
    def record_query(
        self,
        actor: AuditActor,
        query: str,
        plan: dict,
        decisions: list[Decision],
        sent_to_model: list[SentChunk],
        answer: str,
        guard: GuardStats,
        outcome: str,
        latency_ms: int,
        model: str,
        timings_ms: dict[str, float] | None = None,
    ) -> AuditEntry:
        return self.append(
            "query",
            actor,
            query=query,
            plan=plan,
            decisions=decisions,
            sent_to_model=sent_to_model,
            answer=answer,
            guard=guard,
            outcome=outcome,
            latency_ms=latency_ms,
            model=model,
            timings_ms=timings_ms,
        )

    def record_permission_event(self, actor_id: str, event: dict) -> AuditEntry:
        return self.append("permission_event", AuditActor(id=actor_id), event=event)

    def record_content_event(self, actor_id: str, event: dict) -> AuditEntry:
        return self.append("content_event", AuditActor(id=actor_id), event=event)

    def record_sync(self, summary: dict) -> AuditEntry:
        return self.append("sync_event", AuditActor(id="sync-worker"), event=summary)

    def record_audit_read(self, actor_id: str, what: dict) -> AuditEntry:
        return self.append("audit_read", AuditActor(id=actor_id), event=what)

    def record_source_open(self, actor: AuditActor, decision: Decision, event: dict) -> AuditEntry:
        return self.append("source_open", actor, decisions=[decision], event=event)

    def entries_after(self, seq: int, limit: int = 50, include_reads: bool = False) -> list[AuditEntry]:
        with self.lock:
            if include_reads:
                rows = self.conn.execute("SELECT entry_json FROM audit_entries WHERE seq > ? ORDER BY seq LIMIT ?", (seq, limit)).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT entry_json FROM audit_entries WHERE seq > ? AND kind != 'audit_read' ORDER BY seq LIMIT ?", (seq, limit)
                ).fetchall()
        return [AuditEntry.model_validate(json.loads(r["entry_json"])) for r in rows]

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> AuditEntry:
        return AuditEntry.model_validate(json.loads(row["entry_json"]))

    def get(self, seq: int) -> AuditEntry | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM audit_entries WHERE seq = ?", (seq,)).fetchone()
        return self._row_to_entry(row) if row else None

    def head(self) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT seq, ts, entry_hash FROM audit_entries ORDER BY seq DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def query(
        self,
        actor: str | None = None,
        kind: str | None = None,
        platform: str | None = None,
        container: str | None = None,
        doc: str | None = None,
        decision: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        clauses = ["1=1"]
        params: list[Any] = []
        if actor:
            clauses.append("e.actor_id = ?")
            params.append(actor)
        if kind:
            clauses.append("e.kind = ?")
            params.append(kind)
        if since:
            clauses.append("e.ts >= ?")
            params.append(since)
        if until:
            clauses.append("e.ts <= ?")
            params.append(until)
        if platform or container or doc or decision:
            sub = ["d.seq = e.seq"]
            if platform:
                sub.append("d.platform = ?")
                params.append(platform)
            if container:
                sub.append("d.container = ?")
                params.append(container)
            if doc:
                sub.append("d.doc = ?")
                params.append(doc)
            if decision:
                sub.append("d.decision = ?")
                params.append(decision)
            clauses.append(f"EXISTS (SELECT 1 FROM audit_decisions d WHERE {' AND '.join(sub)})")
        sql = f"SELECT * FROM audit_entries e WHERE {' AND '.join(clauses)} ORDER BY e.seq DESC LIMIT ? OFFSET ?"
        with self.lock:
            rows = self.conn.execute(sql, [*params, limit, offset]).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def decisions(self, actor: str | None = None, doc: str | None = None, container: str | None = None, decision: str | None = None, since: str | None = None, limit: int = 500) -> list[dict]:
        clauses = ["1=1"]
        params: list[Any] = []
        if actor:
            clauses.append("actor_id = ?")
            params.append(actor)
        if doc:
            clauses.append("doc = ?")
            params.append(doc)
        if container:
            clauses.append("container = ?")
            params.append(container)
        if decision:
            clauses.append("decision = ?")
            params.append(decision)
        if since:
            clauses.append("ts >= ?")
            params.append(since)
        where = " AND ".join(f"d.{c}" if c != "1=1" else c for c in clauses)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT d.*, e.kind AS kind FROM audit_decisions d JOIN audit_entries e ON e.seq = d.seq WHERE {where} ORDER BY d.seq DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def last_decisions(self, actor_id: str, docs: list[str]) -> dict[str, str]:
        """Most recent decision (allow/deny) this actor received for each of the given documents."""
        if not docs:
            return {}
        marks = ",".join("?" * len(docs))
        with self.lock:
            rows = self.conn.execute(
                f"SELECT doc, decision FROM audit_decisions WHERE actor_id = ? AND doc IN ({marks}) ORDER BY seq ASC", [actor_id, *docs]
            ).fetchall()
        latest: dict[str, str] = {}
        for row in rows:  # ascending seq: the last write wins
            latest[row["doc"]] = row["decision"]
        return latest

    def last_decision(self, actor_id: str, doc: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM audit_decisions WHERE actor_id = ? AND doc = ? ORDER BY seq DESC LIMIT 1", (actor_id, doc)
            ).fetchone()
        return dict(row) if row else None

    def count(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT count(*) FROM audit_entries").fetchone()[0])

    def checkpoints(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT seq, entry_hash, ts, key_id, signature FROM audit_checkpoints ORDER BY seq").fetchall()
        return [dict(row) for row in rows]

    def verify(self) -> VerifyReport:
        with self.lock:
            return verify_chain(self.conn, self.keys.public)
