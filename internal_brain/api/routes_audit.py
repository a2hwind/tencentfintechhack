"""/audit/*: the compliance console's API. Requires the compliance role; every read is
itself an audit entry, because the log is the most sensitive dataset in the system."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..audit.queries import AuditFilter, denials, doc_access, entries_with_decisions, nl_to_filter, user_access
from ..models import AuditEntry, Identity
from .brain import Brain
from .deps import get_brain, require_role

router = APIRouter(prefix="/audit", tags=["audit"])

compliance = require_role("compliance")


def _log_read(brain: Brain, identity: Identity, what: dict) -> int:
    return brain.audit.record_audit_read(identity.id, what).seq


@router.get("/entries")
def entries(
    actor: str | None = None,
    kind: str | None = None,
    platform: str | None = None,
    container: str | None = None,
    doc: str | None = None,
    decision: str | None = Query(default=None, pattern="^(allow|deny)$"),
    days: int | None = Query(default=None, ge=1, le=3650),
    limit: int = Query(default=50, ge=1, le=500),
    identity: Identity = Depends(compliance),
    brain: Brain = Depends(get_brain),
):
    f = AuditFilter(actor=actor, kind=kind, platform=platform, container=container, doc=doc, decision=decision, days=days)
    rows = entries_with_decisions(brain.audit, f, limit=limit)
    read_seq = _log_read(brain, identity, {"view": "entries", "filter": f.model_dump(exclude_none=True), "returned": len(rows)})
    return {"filter": f.model_dump(exclude_none=True), "entries": rows, "read_logged_as": read_seq}


@router.get("/entries/{seq}")
def entry(seq: int, identity: Identity = Depends(compliance), brain: Brain = Depends(get_brain)):
    e = brain.audit.get(seq)
    if e is None:
        raise HTTPException(status_code=404, detail="no such entry")
    read_seq = _log_read(brain, identity, {"view": "entry", "seq": seq})
    return {"entry": e.model_dump(mode="json"), "read_logged_as": read_seq}


@router.get("/views/user-access")
def view_user_access(actor: str, days: int = Query(default=30, ge=1, le=3650), container: str | None = None, identity: Identity = Depends(compliance), brain: Brain = Depends(get_brain)):
    result = user_access(brain.audit, actor, days=days, container=container)
    result["read_logged_as"] = _log_read(brain, identity, {"view": "user-access", "actor": actor, "days": days, "container": container})
    return result


@router.get("/views/doc-access")
def view_doc_access(doc: str, days: int | None = Query(default=None, ge=1, le=3650), identity: Identity = Depends(compliance), brain: Brain = Depends(get_brain)):
    result = doc_access(brain.audit, doc, days=days)
    result["read_logged_as"] = _log_read(brain, identity, {"view": "doc-access", "doc": doc, "days": days})
    return result


@router.get("/views/denials")
def view_denials(actor: str, days: int | None = Query(default=None, ge=1, le=3650), identity: Identity = Depends(compliance), brain: Brain = Depends(get_brain)):
    result = denials(brain.audit, actor, days=days)
    result["read_logged_as"] = _log_read(brain, identity, {"view": "denials", "actor": actor, "days": days})
    return result


@router.get("/nl")
def natural_language(q: str = Query(min_length=3, max_length=500), identity: Identity = Depends(compliance), brain: Brain = Depends(get_brain)):
    """Question -> schema-validated filter -> indexed query. No model reads the log."""
    containers = _known_containers(brain)
    f = nl_to_filter(q, [u.id for u in brain.directory.list()], containers)
    rows = entries_with_decisions(brain.audit, f, limit=100)
    read_seq = _log_read(brain, identity, {"view": "nl", "question": q, "filter": f.model_dump(exclude_none=True), "returned": len(rows)})
    return {"question": q, "filter": f.model_dump(exclude_none=True), "entries": rows, "read_logged_as": read_seq}


@router.get("/verify")
def verify(identity: Identity = Depends(compliance), brain: Brain = Depends(get_brain)):
    report = brain.audit.verify()
    _log_read(brain, identity, {"view": "verify", "ok": report.ok, "entries": report.entries})
    return report.as_dict()


@router.get("/alerts")
def alerts(
    days: int | None = Query(default=None, ge=1, le=3650),
    limit: int = Query(default=100, ge=1, le=500),
    min_severity: str | None = Query(default=None, pattern="^(low|medium|high)$"),
    identity: Identity = Depends(compliance),
    brain: Brain = Depends(get_brain),
):
    """Insider-threat and data-hygiene alerts raised by the deterministic rules over the trail."""
    rows = brain.alerts.list(days=days, limit=limit, min_severity=min_severity)
    read_seq = _log_read(brain, identity, {"view": "alerts", "returned": len(rows)})
    return {"alerts": rows, "read_logged_as": read_seq}


def summarize(entry: AuditEntry) -> dict:
    """The live-tail shape: enough to render a row; the full entry is one GET /audit/entries/{seq} away."""
    allow = sum(1 for d in entry.decisions if d.gate1 == "allow" and d.gate2 == "allow")
    return {
        "seq": entry.seq,
        "ts": entry.ts,
        "kind": entry.kind,
        "actor": entry.actor.id,
        "query": entry.query,
        "outcome": entry.outcome,
        "allow": allow,
        "deny": len(entry.decisions) - allow,
        "refreshed": any(d.refreshed for d in entry.decisions),
        "revoked": any(d.rule == "revoked" for d in entry.decisions),
        "latency_ms": entry.latency_ms,
        "redactions": entry.guard.redactions if entry.guard else {},
        "citations_rejected": entry.guard.citations_rejected if entry.guard else 0,
        "event": entry.event,
        "prev_hash": entry.prev_hash,
        "entry_hash": entry.entry_hash,
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


class _Closing(Exception):
    pass


async def _next_entry(queue: asyncio.Queue, closing: asyncio.Event, timeout: float) -> AuditEntry:
    """The next appended entry; asyncio.TimeoutError after `timeout`; _Closing when the server shuts down."""
    get = asyncio.ensure_future(queue.get())
    stop = asyncio.ensure_future(closing.wait())
    try:
        done, _ = await asyncio.wait({get, stop}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (get, stop):
            if not task.done():
                task.cancel()
    if get in done:
        return get.result()
    if stop in done:
        raise _Closing
    raise asyncio.TimeoutError


@router.get("/stream")
async def stream(
    request: Request,
    since: int = Query(default=0, ge=0),
    include_reads: bool = False,
    limit: int | None = Query(default=None, ge=1, le=10000),
    timeout_s: float | None = Query(default=None, gt=0, le=3600),
    identity: Identity = Depends(compliance),
    brain: Brain = Depends(get_brain),
):
    """Server-sent events: the backlog after `since`, then every new entry as it is appended.

    `limit` closes the stream after that many entries and `timeout_s` after that many seconds
    (long-polling clients and tests); by default it stays open.

    Opening the stream is itself one audit entry. Reads of the log are hidden unless
    include_reads=true, so a console watching the chain does not watch itself.
    """
    read_seq = _log_read(brain, identity, {"view": "stream", "since": since, "include_reads": include_reads})
    queue = brain.audit.subscribe()  # subscribe before reading the backlog: nothing falls in between

    async def events():
        sent = 0
        last = since
        deadline = asyncio.get_running_loop().time() + timeout_s if timeout_s else None
        try:
            yield _sse("hello", {"read_logged_as": read_seq, "head": brain.audit.head()})
            for entry in brain.audit.entries_after(since, limit=200, include_reads=include_reads):
                last = entry.seq
                yield _sse("entry", summarize(entry))
                sent += 1
                if limit and sent >= limit:
                    return
            while True:
                if brain.closing.is_set() or await request.is_disconnected():
                    return
                wait = 10.0
                if deadline is not None:
                    wait = min(wait, deadline - asyncio.get_running_loop().time())
                    if wait <= 0:
                        return
                try:
                    entry = await _next_entry(queue, brain.closing, wait)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                except _Closing:
                    yield _sse("bye", {"reason": "server shutting down"})
                    return
                if entry.seq <= last or (entry.kind == "audit_read" and not include_reads):
                    continue
                last = entry.seq
                yield _sse("entry", summarize(entry))
                sent += 1
                if limit and sent >= limit:
                    return
        finally:
            brain.audit.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/head")
def head(identity: Identity = Depends(compliance), brain: Brain = Depends(get_brain)):
    return {"head": brain.audit.head(), "entries": brain.audit.count(), "checkpoints": brain.audit.checkpoints()[-5:], "key_id": brain.audit.keys.key_id, "public_key": brain.audit.keys.public_hex}


@router.get("/containers")
def containers(identity: Identity = Depends(compliance), brain: Brain = Depends(get_brain)):
    return _known_containers(brain)


def _known_containers(brain: Brain) -> list[dict]:
    seen: dict[str, dict] = {}
    for item in brain.index.list_items():
        if not item.container or item.container in seen:
            continue
        aliases = [item.container_label or ""]
        platform, _, rest = item.container.partition(":")
        kind, _, ident = rest.partition(":")
        aliases += [ident, f"{ident} {kind}", f"{platform} {kind} {ident}"]
        if platform == "confluence":
            space = brain.store.spaces.get(ident)
            if space:
                aliases += [space.name, f"{space.name} space"]
        if platform == "jira":
            project = brain.store.projects.get(ident)
            if project:
                aliases += [project.name, f"{project.name} project"]
        if platform == "slack":
            channel = brain.store.channels.get(ident)
            if channel:
                aliases += [f"#{channel.name}", channel.name]
        if platform == "gdrive":
            drive = brain.store.drives.get(ident)
            if drive:
                aliases += [drive.name, f"{drive.name} drive"]
        seen[item.container] = {"container": item.container, "platform": platform, "label": item.container_label, "aliases": [a for a in aliases if a]}
    return list(seen.values())
