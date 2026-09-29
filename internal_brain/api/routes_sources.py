"""GET /sources/{doc}: open a cited document's passages, re-checked at open.

Clicking a citation is a new access, so it passes both gates again: Gate 1 (the index ACL
against the asker's live principals) and Gate 2 (the source platform, right now). A document
that changed since it was indexed is refreshed first. Every open is an audit entry with a
per-document decision, so "who retrieved document Y" includes opens, and repeated unavailable
opens feed the source-probe alert. Unavailable is one uniform response, time-floored like the
no-result answer, whatever the reason (restricted, revoked, deleted, unknown, unverifiable).
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..models import PLATFORMS, SOURCE_UNAVAILABLE_MESSAGE, AuditActor, Decision, Identity, SourceChunk, SourceView
from ..core.events import now_iso
from .brain import Brain
from .deps import current_identity, get_brain

router = APIRouter(tags=["sources"])


@router.get("/sources/{doc_id:path}", response_model=SourceView)
async def open_source(doc_id: str, identity: Identity = Depends(current_identity), brain: Brain = Depends(get_brain)):
    started = time.perf_counter()
    principals = await brain.entitlements.principals_for(identity)
    actor = AuditActor(id=identity.id, principals=principals.tokens)
    platform = doc_id.split(":", 1)[0]
    item = brain.index.get_item(doc_id) if platform in PLATFORMS else None

    view: SourceView | None = None
    decision: Decision | None = None
    reason = "no_match"
    if item is not None:
        rule = brain.index.granting_token(doc_id, principals.tokens)
        if rule is None:
            reason = "not_member"
            decision = Decision(doc=doc_id, platform=item.platform, gate1="deny", gate2="skipped", rule="not_member", version=item.version, container=item.container)  # type: ignore[arg-type]
        else:
            check = await brain.verifier.check(item.platform, doc_id, principals)
            refreshed = False
            if check.allowed and check.version is not None and check.version > item.version:
                fresh = await brain.sync.fetch_and_index(doc_id)
                if fresh is None:
                    check = check.model_copy(update={"allowed": False, "reason": "unverifiable"})
                else:
                    item = brain.index.get_item(doc_id) or item
                    refreshed = True
            if not check.allowed:
                reason = "revoked" if check.reason == "not_member" else check.reason
                decision = Decision(doc=doc_id, platform=item.platform, gate1="allow", gate2="deny", rule=reason, gate1_rule=rule, version=item.version, container=item.container)  # type: ignore[arg-type]
            else:
                reason = "ok"
                decision = Decision(
                    doc=doc_id, platform=item.platform, gate1="allow", gate2="allow", rule=rule, gate1_rule=rule,  # type: ignore[arg-type]
                    version=check.version or item.version, refreshed=refreshed, container=item.container,
                )
                chunks = brain.index.get_chunks(doc_id)
                view = SourceView(
                    available=True,
                    doc=doc_id,
                    platform=item.platform,  # type: ignore[arg-type]
                    title=item.title,
                    url=item.url,
                    version=item.version,
                    updated=item.last_modified,
                    rule=rule,
                    verified_at=now_iso(),
                    refreshed=refreshed,
                    chunks=[SourceChunk(chunk=c.chunk_id, text=c.text) for c in chunks],
                )

    event = {"doc": doc_id, "available": view is not None, "reason": reason}
    if view is not None:
        event["chunks"] = [c.chunk for c in view.chunks]
    if decision is not None:
        entry = brain.audit.record_source_open(actor, decision, event)
    else:
        entry = brain.audit.append("source_open", actor, event=event)
    brain.alerts.after_source_open(identity.id)

    headers = {"X-Audit-Seq": str(entry.seq), "Cache-Control": "no-store"}
    if view is None:
        remaining = brain.settings.no_result_min_latency_ms / 1000.0 - (time.perf_counter() - started)
        if remaining > 0:
            await asyncio.sleep(remaining)
        return JSONResponse(SourceView(available=False, message=SOURCE_UNAVAILABLE_MESSAGE).model_dump(mode="json"), headers=headers)
    return JSONResponse(view.model_dump(mode="json"), headers=headers)
