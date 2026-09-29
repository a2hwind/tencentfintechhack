"""POST /ask: one grounded answer, or the uniform no-result. The audit sequence number
travels in a response header so that denied and empty responses have byte-identical bodies."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..models import AskRequest, AskResponse, Identity
from .brain import Brain
from .deps import current_identity, get_brain

router = APIRouter(tags=["ask"])


@router.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, identity: Identity = Depends(current_identity), brain: Brain = Depends(get_brain)):
    response, entry = await brain.pipeline.ask(identity, body.question.strip())
    return JSONResponse(content=response.model_dump(mode="json"), headers={"X-Audit-Seq": str(entry.seq), "Cache-Control": "no-store"})


@router.get("/me")
async def me(identity: Identity = Depends(current_identity), brain: Brain = Depends(get_brain)):
    principals = await brain.entitlements.principals_for(identity)
    return {"identity": identity.model_dump(), "principals": principals.tokens, "platform_user_ids": principals.platform_user_ids, "resolved_at": principals.resolved_at}


@router.get("/users")
def users(brain: Brain = Depends(get_brain)):
    """Demo helper for the user switcher."""
    return [{"id": u.id, "name": u.name, "title": u.title, "roles": u.roles} for u in brain.directory.list()]
