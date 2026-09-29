"""/admin/*: demo-only mutations of the mock platforms, sync controls, and state views.

Every mutation goes to the mock platform (which owns the semantics), emits the event
the real platform would send as a webhook (unless notify=false, which simulates a
missed webhook), and writes a permission or content entry to the audit log.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..models import Identity
from ..mocks.store import NotFound
from .brain import Brain
from .deps import get_brain, require_role

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_role("admin"))])


class MembershipChange(BaseModel):
    user_id: str
    member: bool = True
    notify: bool = True  # false simulates a missed webhook: the entitlement cache stays warm, Gate 2 must catch it


class PageEdit(BaseModel):
    append: str | None = Field(default=None, max_length=5000)
    body: str | None = Field(default=None, max_length=20000)
    notify: bool = True


class Restrictions(BaseModel):
    users: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    notify: bool = True


class CommentIn(BaseModel):
    author: str
    text: str = Field(max_length=5000)
    notify: bool = True


class SecurityLevel(BaseModel):
    level: str | None = None
    notify: bool = True


class ShareChange(BaseModel):
    user_id: str
    share: bool = True
    notify: bool = True


class FileEdit(BaseModel):
    append: str | None = Field(default=None, max_length=5000)
    body: str | None = Field(default=None, max_length=20000)
    notify: bool = True


class GroupChange(BaseModel):
    user_id: str
    member: bool = True
    notify: bool = True


class SlackPost(BaseModel):
    channel_id: str
    user_id: str
    text: str = Field(max_length=4000)
    thread_ts: str | None = None
    notify: bool = True


async def _settle(brain: Brain) -> None:
    """Let event-triggered syncs land before responding, so the next query sees them."""
    await brain.sync.drain()


def _mock_slack(brain: Brain) -> None:
    if brain.slack_real:
        raise HTTPException(status_code=409, detail="Slack is connected to a real workspace: change membership and post messages in Slack itself.")


# ------------------------------------------------------------------ views
@router.get("/state")
def state(brain: Brain = Depends(get_brain)):
    return {"company": brain.store.snapshot(), "sync": brain.sync.status(), "entitlement_cache": brain.entitlements.stats, "events": [e.__dict__ for e in brain.bus.history[-50:]]}


@router.get("/index")
def index_items(brain: Brain = Depends(get_brain)):
    return [
        {
            "item_id": i.item_id,
            "platform": i.platform,
            "title": i.title,
            "version": i.version,
            "last_modified": i.last_modified,
            "container": i.container,
            "allowed_principals": i.allowed_principals,
            "links": i.links,
            "chunks": len(brain.index.get_chunks(i.item_id)),
        }
        for i in brain.index.list_items()
    ]


# ------------------------------------------------------------------ Slack
@router.post("/slack/channels/{channel_id}/members")
async def slack_membership(channel_id: str, change: MembershipChange, identity: Identity = Depends(require_role("admin")), brain: Brain = Depends(get_brain)):
    _mock_slack(brain)
    try:
        channel = brain.store.slack_set_membership(channel_id, change.user_id, change.member, notify=change.notify)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    brain.audit.record_permission_event(identity.id, {"platform": "slack", "channel": channel_id, "user": change.user_id, "action": "add" if change.member else "remove", "notified": change.notify})
    await _settle(brain)
    return {"channel": channel.id, "name": channel.name, "members": channel.members, "event_delivered": change.notify}


@router.post("/slack/messages")
async def slack_post(post: SlackPost, identity: Identity = Depends(require_role("admin")), brain: Brain = Depends(get_brain)):
    _mock_slack(brain)
    try:
        thread = brain.store.slack_post(post.channel_id, post.user_id, post.text, post.thread_ts, notify=post.notify)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    brain.audit.record_content_event(identity.id, {"platform": "slack", "item_id": f"slack:{thread.item_key}", "action": "post", "notified": post.notify})
    await _settle(brain)
    return {"item_id": f"slack:{thread.item_key}", "version": thread.version}


# ------------------------------------------------------------------ Confluence
@router.post("/confluence/pages/{page_id}/edit")
async def confluence_edit(page_id: str, edit: PageEdit, identity: Identity = Depends(require_role("admin")), brain: Brain = Depends(get_brain)):
    if not edit.append and edit.body is None:
        raise HTTPException(status_code=422, detail="append or body required")
    try:
        page = brain.store.confluence_edit_page(page_id, body=edit.body, append=edit.append, author=identity.id, notify=edit.notify)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    brain.audit.record_content_event(identity.id, {"platform": "confluence", "item_id": f"confluence:{page_id}", "action": "edit", "version": page.version, "notified": edit.notify})
    await _settle(brain)
    indexed = brain.index.get_item(f"confluence:{page_id}")
    return {"item_id": f"confluence:{page_id}", "version": page.version, "indexed_version": indexed.version if indexed else None, "event_delivered": edit.notify}


@router.post("/confluence/pages/{page_id}/restrictions")
async def confluence_restrictions(page_id: str, r: Restrictions, identity: Identity = Depends(require_role("admin")), brain: Brain = Depends(get_brain)):
    try:
        page = brain.store.confluence_set_restrictions(page_id, r.users, r.groups, notify=r.notify)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    brain.audit.record_permission_event(identity.id, {"platform": "confluence", "item_id": f"confluence:{page_id}", "action": "restrict", "users": r.users, "groups": r.groups, "version": page.version, "notified": r.notify})
    await _settle(brain)
    return {"item_id": f"confluence:{page_id}", "version": page.version, "restrictions": {"users": r.users, "groups": r.groups}}


# ------------------------------------------------------------------ Jira
@router.post("/jira/issues/{key}/comment")
async def jira_comment(key: str, c: CommentIn, identity: Identity = Depends(require_role("admin")), brain: Brain = Depends(get_brain)):
    try:
        issue = brain.store.jira_add_comment(key, c.author, c.text, notify=c.notify)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    brain.audit.record_content_event(identity.id, {"platform": "jira", "item_id": f"jira:{key}", "action": "comment", "version": issue.version, "notified": c.notify})
    await _settle(brain)
    return {"item_id": f"jira:{key}", "version": issue.version}


@router.post("/jira/issues/{key}/security-level")
async def jira_security(key: str, s: SecurityLevel, identity: Identity = Depends(require_role("admin")), brain: Brain = Depends(get_brain)):
    try:
        issue = brain.store.jira_set_security_level(key, s.level, notify=s.notify)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    brain.audit.record_permission_event(identity.id, {"platform": "jira", "item_id": f"jira:{key}", "action": "security_level", "level": s.level, "version": issue.version, "notified": s.notify})
    await _settle(brain)
    return {"item_id": f"jira:{key}", "version": issue.version, "security_level": issue.security_level}


# ------------------------------------------------------------------ Drive
@router.post("/gdrive/files/{file_id}/share")
async def gdrive_share(file_id: str, s: ShareChange, identity: Identity = Depends(require_role("admin")), brain: Brain = Depends(get_brain)):
    try:
        f = brain.store.gdrive_share(file_id, s.user_id, s.share, notify=s.notify)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    brain.audit.record_permission_event(identity.id, {"platform": "gdrive", "item_id": f"gdrive:{file_id}", "action": "share" if s.share else "unshare", "user": s.user_id, "version": f.version, "notified": s.notify})
    await _settle(brain)
    return {"item_id": f"gdrive:{file_id}", "version": f.version, "shared_with": f.permissions.users}


@router.post("/gdrive/files/{file_id}/edit")
async def gdrive_edit(file_id: str, e: FileEdit, identity: Identity = Depends(require_role("admin")), brain: Brain = Depends(get_brain)):
    if not e.append and e.body is None:
        raise HTTPException(status_code=422, detail="append or body required")
    try:
        f = brain.store.gdrive_update(file_id, body=e.body, append=e.append, notify=e.notify)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    brain.audit.record_content_event(identity.id, {"platform": "gdrive", "item_id": f"gdrive:{file_id}", "action": "edit", "version": f.version, "notified": e.notify})
    await _settle(brain)
    return {"item_id": f"gdrive:{file_id}", "version": f.version}


# ------------------------------------------------------------------ directory groups, deletion
@router.post("/groups/{group}/members")
async def group_membership(group: str, g: GroupChange, identity: Identity = Depends(require_role("admin")), brain: Brain = Depends(get_brain)):
    if g.user_id not in brain.store.users:
        raise HTTPException(status_code=404, detail="unknown user")
    members = brain.store.group_set(group, g.user_id, g.member, notify=g.notify)
    brain.audit.record_permission_event(identity.id, {"platform": "directory", "group": group, "user": g.user_id, "action": "add" if g.member else "remove", "notified": g.notify})
    await _settle(brain)
    return {"group": group, "members": members}


@router.delete("/items/{item_id:path}")
async def delete_item(item_id: str, notify: bool = True, identity: Identity = Depends(require_role("admin")), brain: Brain = Depends(get_brain)):
    try:
        brain.store.delete_item(item_id, notify=notify)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    brain.audit.record_content_event(identity.id, {"item_id": item_id, "action": "delete", "notified": notify})
    await _settle(brain)
    return {"item_id": item_id, "deleted": True, "indexed": brain.index.get_item(item_id) is not None}


# ------------------------------------------------------------------ sync controls
@router.get("/sync/status")
def sync_status(brain: Brain = Depends(get_brain)):
    return brain.sync.status()


@router.post("/sync/pause")
def sync_pause(brain: Brain = Depends(get_brain)):
    brain.sync.paused = True
    return brain.sync.status()


@router.post("/sync/resume")
def sync_resume(brain: Brain = Depends(get_brain)):
    brain.sync.paused = False
    return brain.sync.status()


@router.post("/sync/run")
async def sync_run(brain: Brain = Depends(get_brain)):
    summary = await brain.sync.sync_all(reason="manual")
    return {"summary": summary, "status": brain.sync.status()}


@router.post("/entitlements/invalidate")
def entitlements_invalidate(brain: Brain = Depends(get_brain)):
    brain.entitlements.invalidate()
    return brain.entitlements.stats


@router.post("/reset")
async def reset_demo(identity: Identity = Depends(require_role("admin")), brain: Brain = Depends(get_brain)):
    """Back to the fixture: mock platforms reloaded, index rebuilt, caches cleared, sync resumed.

    The audit log is append-only and is never touched; the reset itself is an entry.
    """
    await brain.sync.drain()
    brain.store.reload()
    brain.index.clear()
    brain.entitlements.invalidate()
    brain.sync.paused = False
    summary = await brain.sync.sync_all(reason="reset")
    entry = brain.audit.record_content_event(identity.id, {"action": "demo_reset", "platforms": list(summary)})
    return {"reset": True, "audit_seq": entry.seq, "sync": brain.sync.status()}
