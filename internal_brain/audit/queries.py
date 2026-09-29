"""Compliance queries over the indexed audit columns.

Three canned views (what did user X access, who retrieved document Y, all denials for
user X) plus a small natural-language front: the planner pattern (question to a
schema-validated filter), never a model reading the log's contents.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from .log import AuditLog


class AuditFilter(BaseModel):
    actor: str | None = None
    doc: str | None = None
    container: str | None = None
    platform: str | None = None
    decision: str | None = Field(default=None, pattern="^(allow|deny)$")
    days: int | None = Field(default=None, ge=1, le=3650)
    kind: str | None = None

    def since(self) -> str | None:
        if not self.days:
            return None
        return (datetime.now(timezone.utc) - timedelta(days=self.days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def entries_with_decisions(log: AuditLog, f: AuditFilter, limit: int = 100) -> list[dict]:
    entries = log.query(actor=f.actor, kind=f.kind, platform=f.platform, container=f.container, doc=f.doc, decision=f.decision, since=f.since(), limit=limit)
    out = []
    for e in entries:
        decisions = [d.model_dump() for d in e.decisions]
        if f.container:
            decisions = [d for d in decisions if d.get("container") == f.container]
        if f.doc:
            decisions = [d for d in decisions if d.get("doc") == f.doc]
        if f.platform:
            decisions = [d for d in decisions if d.get("platform") == f.platform]
        if f.decision:
            decisions = [d for d in decisions if ("allow" if (d["gate1"] == "allow" and d["gate2"] == "allow") else "deny") == f.decision]
        out.append(
            {
                "seq": e.seq,
                "ts": e.ts,
                "kind": e.kind,
                "actor": e.actor.id,
                "query": e.query,
                "outcome": e.outcome,
                "decisions": decisions,
                "sent_to_model": [s.chunk for s in e.sent_to_model],
                "event": e.event,
                "entry_hash": e.entry_hash,
            }
        )
    return out


def user_access(log: AuditLog, actor: str, days: int = 30, container: str | None = None) -> dict:
    f = AuditFilter(actor=actor, days=days, container=container, kind="query")
    entries = entries_with_decisions(log, f)
    docs: dict[str, dict] = {}
    for e in entries:
        for d in e["decisions"]:
            allowed = d["gate1"] == "allow" and d["gate2"] == "allow"
            slot = docs.setdefault(d["doc"], {"doc": d["doc"], "platform": d["platform"], "container": d.get("container"), "allowed": 0, "denied": 0, "last_seen": e["ts"]})
            slot["allowed" if allowed else "denied"] += 1
    return {"actor": actor, "days": days, "container": container, "queries": entries, "documents": sorted(docs.values(), key=lambda d: d["doc"])}


def doc_access(log: AuditLog, doc: str, days: int | None = None) -> dict:
    f = AuditFilter(doc=doc, days=days)
    rows = log.decisions(doc=doc, since=f.since())
    return {"doc": doc, "accesses": rows}


def denials(log: AuditLog, actor: str, days: int | None = None) -> dict:
    f = AuditFilter(actor=actor, days=days)
    rows = log.decisions(actor=actor, decision="deny", since=f.since())
    return {"actor": actor, "denials": rows}


# ---------------------------------------------------------------------------
# Natural-language front (stub planner; an LLM planner returns the same AuditFilter)
# ---------------------------------------------------------------------------
DAYS_RE = re.compile(r"last (\d+) days|past (\d+) days")
WORD_WINDOWS = [(re.compile(r"\b(last|past) week\b"), 7), (re.compile(r"\b(last|past) month\b"), 30), (re.compile(r"\b(last|past) quarter\b"), 90), (re.compile(r"\btoday\b"), 1)]


def nl_to_filter(question: str, known_actors: list[str], known_containers: list[dict[str, Any]]) -> AuditFilter:
    """Map a compliance question to a filter using the directory and the index's containers.

    known_containers: [{"container": "confluence:space:PAYGW", "label": "PAYGW", "aliases": ["payment gateway", ...]}]
    """
    q = re.sub(r"[-_]", " ", question.lower())  # "payment-gateway" and "payment gateway" are the same words
    f = AuditFilter()
    for actor in sorted(known_actors, key=len, reverse=True):
        if re.search(rf"\b{re.escape(re.sub(r'[-_]', ' ', actor.lower()))}\b", q):
            f.actor = actor
            break
    m = DAYS_RE.search(q)
    if m:
        f.days = int(m.group(1) or m.group(2))
    else:
        for pattern, days in WORD_WINDOWS:
            if pattern.search(q):
                f.days = days
                break
    for c in known_containers:
        names = [c.get("label", ""), *c.get("aliases", [])]
        if any(n and re.search(rf"\b{re.escape(re.sub(r'[-_]', ' ', str(n).lower().lstrip('#')))}\b", q) for n in names):
            f.container = c["container"]
            break
    for platform in ("confluence", "jira", "slack", "gdrive", "drive"):
        if re.search(rf"\b{platform}\b", q):
            f.platform = "gdrive" if platform == "drive" else platform
            break
    if re.search(r"\bdenied|denials|blocked|refused\b", q):
        f.decision = "deny"
    return f
