"""Request dependencies: the brain, the signed-in identity, role checks."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request

from ..models import Identity
from .brain import Brain


def get_brain(request: Request) -> Brain:
    return request.app.state.brain


def current_identity(brain: Brain = Depends(get_brain), x_user_id: str | None = Header(default=None, alias="X-User-Id")) -> Identity:
    """Demo identity: an X-User-Id header resolved against the directory.

    In production this is the session established by the IdP (OIDC); the rest of the
    system is unchanged because everything downstream consumes an Identity.
    """
    if not x_user_id:
        raise HTTPException(status_code=401, detail="missing identity (X-User-Id)")
    identity = brain.directory.get(x_user_id)
    if identity is None:
        raise HTTPException(status_code=401, detail="unknown identity")
    return identity


def require_role(role: str):
    def dependency(identity: Identity = Depends(current_identity)) -> Identity:
        if not identity.has_role(role):
            raise HTTPException(status_code=403, detail=f"requires the {role} role")
        return identity

    return dependency
