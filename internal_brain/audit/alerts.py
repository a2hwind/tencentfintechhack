"""Insider-threat and data-hygiene alerts, computed deterministically from the audit trail.

No model reads the log. Each rule is a small query over recent audit entries, run right
after the entry that could trigger it is written; a new alert is itself appended to the hash
chain, so alerts are timestamped and tamper-evident like everything else, and they reach the
compliance console through the same live stream.

Rules (thresholds are constructor arguments):
  blocked_burst           repeated questions that came back empty because of a denial
  container_probe         repeated blocked questions aimed at one container the asker cannot see
  retry_after_revocation  asking again for a document the asker has just lost access to
  source_probe            opening sources that are not available (e.g. guessing document ids)
  model_citation_rejected the model cited something it was not given (hallucination or injection)
  sensitive_data_at_rest  card numbers, NRICs, bank accounts or secrets sitting in a source platform

A "blocked" question is one whose outcome was the uniform no-result while at least one
candidate document was denied: the asker went looking for something they cannot see. Denials
that merely accompany an answered question (a restricted document that also matched) do not
count, which keeps ordinary use quiet.

The behavioural rules look only at the trail after the most recent demo reset (itself an audit
entry), so every rehearsal starts from a clean slate and an alert suppressed as a duplicate in
one run fires again in the next. sensitive_data_at_rest is about the data, not a session: it is
raised once per document version, ever.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Callable

from ..core.dlp import KIND_LABELS, severity as dlp_severity
from ..models import AuditActor, AuditEntry
from .log import AuditLog

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class AlertEngine:
    def __init__(
        self,
        audit: AuditLog,
        blocked_burst: int = 3,
        burst_window_min: int = 10,
        probe_threshold: int = 2,
        probe_window_min: int = 30,
        source_probe: int = 3,
        clock: Callable[[], datetime] | None = None,
    ):
        self.audit = audit
        self.blocked_burst = blocked_burst
        self.burst_window = timedelta(minutes=burst_window_min)
        self.probe_threshold = probe_threshold
        self.probe_window = timedelta(minutes=probe_window_min)
        self.source_probe = source_probe
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.enabled = True

    # ------------------------------------------------------------------ data access
    def session_start(self) -> int:
        """Seq of the most recent demo reset (0 if none): the behavioural rules look only after it."""
        with self.audit.lock:
            row = self.audit.conn.execute(
                "SELECT MAX(seq) FROM audit_entries WHERE kind = 'content_event' AND entry_json LIKE ?", ['%"action":"demo_reset"%']
            ).fetchone()
        return int(row[0] or 0)

    def _entries(self, actor_id: str, kinds: tuple[str, ...], since: datetime) -> list[AuditEntry]:
        marks = ",".join("?" * len(kinds))
        with self.audit.lock:
            rows = self.audit.conn.execute(
                f"SELECT entry_json FROM audit_entries WHERE actor_id = ? AND kind IN ({marks}) AND ts >= ? AND seq > ? ORDER BY seq",
                [actor_id, *kinds, _iso(since), self.session_start()],
            ).fetchall()
        return [AuditEntry.model_validate(json.loads(r["entry_json"])) for r in rows]

    def _recent_alert_keys(self, since: datetime | None, after_seq: int = 0) -> set[str]:
        with self.audit.lock:
            if since is None:
                rows = self.audit.conn.execute("SELECT entry_json FROM audit_entries WHERE kind = 'alert' AND seq > ?", [after_seq]).fetchall()
            else:
                rows = self.audit.conn.execute(
                    "SELECT entry_json FROM audit_entries WHERE kind = 'alert' AND ts >= ? AND seq > ?", [_iso(since), after_seq]
                ).fetchall()
        keys = set()
        for r in rows:
            event = json.loads(r["entry_json"]).get("event") or {}
            if event.get("key"):
                keys.add(event["key"])
        return keys

    def _raise(
        self, rule: str, severity: str, key: str, title: str, detail: str, subject: dict, evidence: list[int], dedupe_since: datetime | None,
        per_session: bool = True, **extra,
    ) -> AuditEntry | None:
        if key in self._recent_alert_keys(dedupe_since, self.session_start() if per_session else 0):
            return None
        event = {
            "rule": rule,
            "severity": severity,
            "title": title,
            "detail": detail,
            "subject": subject,
            "evidence_seqs": sorted(set(evidence))[-20:],
            "key": key,
            **extra,
        }
        return self.audit.append("alert", AuditActor(id="alert-engine"), event=event)

    @staticmethod
    def _blocked(entry: AuditEntry) -> bool:
        return entry.outcome == "no_result" and any(d.gate1 == "deny" or d.gate2 == "deny" for d in entry.decisions)

    # ------------------------------------------------------------------ triggers
    def after_query(self, actor_id: str) -> list[AuditEntry]:
        if not self.enabled:
            return []
        now = self.clock()
        raised: list[AuditEntry] = []
        window_start = now - max(self.burst_window, self.probe_window)
        queries = self._entries(actor_id, ("query",), window_start)
        if not queries:
            return raised
        latest = queries[-1]

        # model_citation_rejected: one alert per offending query
        if latest.guard and latest.guard.citations_rejected > 0:
            alert = self._raise(
                "model_citation_rejected",
                "low",
                key=f"model_citation_rejected:{latest.seq}",
                title="Model cited a document it was not given",
                detail=(
                    f"The answer to {actor_id}'s question cited {latest.guard.citations_rejected} document(s) outside the assembled context. "
                    "The guard removed those sentences. Review the cited documents for embedded instructions."
                ),
                subject={"type": "user", "id": actor_id},
                evidence=[latest.seq],
                dedupe_since=None,
            )
            if alert:
                raised.append(alert)

        # blocked_burst
        burst_start = _iso(now - self.burst_window)
        blocked_recent = [e for e in queries if e.ts >= burst_start and self._blocked(e)]
        if len(blocked_recent) >= self.blocked_burst:
            severity = "high" if len(blocked_recent) >= 2 * self.blocked_burst else "medium"
            alert = self._raise(
                "blocked_burst",
                severity,
                key=f"blocked_burst:{actor_id}:{severity}",
                title=f"{actor_id}: {len(blocked_recent)} questions for restricted content in {int(self.burst_window.total_seconds() // 60)} min",
                detail="Each of these questions matched documents the asker cannot see and came back empty. The asker saw only the uniform no-result message.",
                subject={"type": "user", "id": actor_id},
                evidence=[e.seq for e in blocked_recent],
                dedupe_since=now - self.burst_window,
            )
            if alert:
                raised.append(alert)

        # container_probe: blocked questions aimed at one container the asker has no allowed document in
        probe_start = _iso(now - self.probe_window)
        in_window = [e for e in queries if e.ts >= probe_start]
        allowed_containers = {d.container for e in in_window for d in e.decisions if d.gate1 == "allow" and d.gate2 == "allow" and d.container}
        per_container: dict[str, list[int]] = {}
        for e in in_window:
            if not self._blocked(e):
                continue
            for container in {d.container for d in e.decisions if (d.gate1 == "deny" or d.gate2 == "deny") and d.container}:
                per_container.setdefault(container, []).append(e.seq)
        probed = {c: sorted(set(seqs)) for c, seqs in per_container.items() if c not in allowed_containers and len(set(seqs)) >= self.probe_threshold}
        if probed:
            seqs = sorted({seq for group in probed.values() for seq in group})
            questions = [e.query for e in in_window if e.seq in seqs and e.query]
            containers = sorted(probed)
            alert = self._raise(
                "container_probe",
                "high",
                key=f"container_probe:{actor_id}",  # one alert per asker per window, however many containers
                title=f"{actor_id} is probing restricted content: {', '.join(containers[:3])}{' …' if len(containers) > 3 else ''}",
                detail=(
                    f"{len(seqs)} questions in {int(self.probe_window.total_seconds() // 60)} min matched only documents that {actor_id} has no access to. "
                    f"Questions: {'; '.join(repr(q) for q in questions[:3])}. Each got the uniform no-result message."
                ),
                subject={"type": "user", "id": actor_id},
                evidence=seqs,
                dedupe_since=now - self.probe_window,
                containers=containers,
            )
            if alert:
                raised.append(alert)

        # retry_after_revocation
        revoked_at: dict[str, int] = {}
        for e in in_window:
            for d in e.decisions:
                if d.rule == "revoked" and d.doc not in revoked_at:
                    revoked_at[d.doc] = e.seq
        for doc, first_seq in revoked_at.items():
            retries = [e.seq for e in in_window if e.seq > first_seq and any(d.doc == doc and (d.gate1 == "deny" or d.gate2 == "deny") for d in e.decisions)]
            if retries:
                alert = self._raise(
                    "retry_after_revocation",
                    "low",
                    key=f"retry_after_revocation:{actor_id}:{doc}",
                    title=f"{actor_id} keeps reaching for {doc} after losing access",
                    detail="Access to this document was revoked, and later questions matched it again. Usually harmless; worth a look if it continues.",
                    subject={"type": "user", "id": actor_id},
                    evidence=[first_seq, *retries],
                    dedupe_since=now - self.probe_window,
                    doc=doc,
                )
                if alert:
                    raised.append(alert)
        return raised

    def after_source_open(self, actor_id: str) -> list[AuditEntry]:
        if not self.enabled:
            return []
        now = self.clock()
        opens = self._entries(actor_id, ("source_open",), now - self.burst_window)
        unavailable = [e for e in opens if e.event and e.event.get("available") is False]
        if len(unavailable) < self.source_probe:
            return []
        alert = self._raise(
            "source_probe",
            "medium",
            key=f"source_probe:{actor_id}",
            title=f"{actor_id} opened {len(unavailable)} sources that are not available to them",
            detail="Repeated attempts to open documents outside the asker's access, for example by guessing document ids. Each attempt got the uniform unavailable response.",
            subject={"type": "user", "id": actor_id},
            evidence=[e.seq for e in unavailable],
            dedupe_since=now - self.burst_window,
        )
        return [alert] if alert else []

    def on_sensitive_data(self, doc: str, title: str, container: str | None, counts: dict[str, int], version: int, where: str | None = None) -> AuditEntry | None:
        """A source document holds card numbers, national IDs or secrets. Values are never recorded."""
        if not self.enabled or not counts:
            return None
        kinds = ", ".join(f"{n} {KIND_LABELS.get(k, k)}{'s' if n > 1 else ''}" for k, n in sorted(counts.items()))
        labels = [KIND_LABELS.get(k, k) for k in sorted(counts)]
        what = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
        return self._raise(
            "sensitive_data_at_rest",
            dlp_severity(counts),
            key=f"sensitive_data_at_rest:{doc}:{version}",
            title=f"{what[0].upper() + what[1:]} stored in {where or container or doc}",
            detail=(
                f"'{title}' contains {kinds}. The Brain indexed and will display only masked values; "
                "the full values are still in the source platform and should be removed there."
            ),
            subject={"type": "document", "id": doc},
            evidence=[],
            dedupe_since=None,
            per_session=False,
            doc=doc,
            container=container,
            counts=counts,
            version=version,
        )

    # ------------------------------------------------------------------ reads
    def list(self, days: int | None = None, limit: int = 100, min_severity: str | None = None) -> list[dict]:
        since = _iso(self.clock() - timedelta(days=days)) if days else "0000"
        with self.audit.lock:
            rows = self.audit.conn.execute(
                "SELECT entry_json FROM audit_entries WHERE kind = 'alert' AND ts >= ? ORDER BY seq DESC LIMIT ?", [since, limit]
            ).fetchall()
        out = []
        floor = SEVERITY_ORDER.get(min_severity or "low", 0)
        for r in rows:
            entry = json.loads(r["entry_json"])
            event = entry.get("event") or {}
            if SEVERITY_ORDER.get(event.get("severity", "low"), 0) < floor:
                continue
            out.append({"seq": entry["seq"], "ts": entry["ts"], "entry_hash": entry["entry_hash"], **event})
        return out
