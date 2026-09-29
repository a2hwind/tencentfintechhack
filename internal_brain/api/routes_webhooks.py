"""POST /webhooks/slack: the Slack Events API request URL (SLACK_MODE=real with SLACK_SIGNING_SECRET).

Point the Slack app's Event Subscriptions at https://<your host>/webhooks/slack and subscribe the
bot to: member_joined_channel, member_left_channel, message.channels, message.groups,
channel_left, group_left, channel_deleted, group_deleted, channel_rename, group_rename, user_change.

Unsigned, mis-signed or stale (over five minutes) requests get 401 and change nothing. Events
are acknowledged at once; the re-syncs they trigger run in the background, well inside Slack's
three-second budget.
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..adapters.slack_events import translate, verify_signature
from ..adapters.slack_real import SlackWebAdapter
from ..core.events import Event, now_iso
from .brain import Brain
from .deps import get_brain

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])
SEEN_EVENT_IDS = 5000


@router.post("/slack")
async def slack_events(request: Request, brain: Brain = Depends(get_brain)):
    secret = brain.settings.slack_signing_secret
    if not brain.slack_real or not secret:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    body = await request.body()
    if not verify_signature(secret, request.headers.get("X-Slack-Request-Timestamp"), body, request.headers.get("X-Slack-Signature")):
        return JSONResponse({"ok": False, "error": "invalid_signature"}, status_code=401)
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    if payload.get("type") != "event_callback":
        return {"ok": True, "ignored": payload.get("type")}

    adapter = brain.adapters["slack"]
    team = getattr(adapter, "team", None) or {}
    if team.get("id") and payload.get("team_id") and payload["team_id"] != team["id"]:
        return {"ok": True, "ignored": "other_workspace"}
    event_id = str(payload.get("event_id") or "")
    if event_id:
        if event_id in brain.slack_event_ids:
            return {"ok": True, "duplicate": True}
        brain.slack_event_ids[event_id] = time.time()
        while len(brain.slack_event_ids) > SEEN_EVENT_IDS:
            brain.slack_event_ids.popitem(last=False)

    event = payload.get("event") or {}
    t = translate(event)
    brain.slack_last_event = now_iso()
    if isinstance(adapter, SlackWebAdapter):
        for channel in t.forget_channels:
            adapter.forget_channel(channel)
        for user in t.forget_users:
            adapter.forget_user(user)
    if t.permission is not None:
        brain.audit.record_permission_event("slack-events-api", {**t.permission, "event_id": event_id or None})
    for ev in t.events:
        brain.bus.publish(ev)
    if t.remove_container:
        for item_id in brain.index.item_ids_in_container(t.remove_container):
            brain.bus.publish(Event("item_deleted", "slack", {"item_id": item_id}))
    if t.resync_container:
        for item_id in brain.index.item_ids_in_container(t.resync_container):
            brain.bus.publish(Event("content_changed", "slack", {"item_id": item_id}))
    log.info("slack event %s %s -> %s", event_id, event.get("type"), t.summary())
    return {"ok": True, **t.summary()}
