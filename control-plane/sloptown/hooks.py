"""Inbound webhooks from Miira: `POST /hooks/miira/access-changed` (docs/miira-integration.md §7).

Verifies the HMAC signature over `timestamp + "." + body` and a ±5 minute
timestamp window, refetches the member list and tells running apps
(`members:<pid>`) and open studios (`project:<pid>`) that it changed.
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Request

from .audit import audit
from .auth import Db
from .config import get_settings
from .errors import ApiError
from .events import hub
from .members import MembersUnavailable, get_members, invalidate
from .models import Project

log = logging.getLogger(__name__)
router = APIRouter()

MAX_SKEW = 300


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


@router.post("/hooks/miira/access-changed")
async def access_changed(request: Request, db: Db) -> dict[str, Any]:
    body = await request.body()
    ts = request.headers.get("x-miira-timestamp", "")
    sig = request.headers.get("x-miira-signature", "")
    if not ts.isdigit() or abs(time.time() - int(ts)) > MAX_SKEW:
        raise ApiError(401, "stale_webhook", "Timestamp mangler eller er for gammelt")
    if not hmac.compare_digest(sig, sign(get_settings().miira_webhook_secret, ts, body)):
        raise ApiError(401, "bad_signature", "Ugyldig signatur")
    try:
        file_id = str(json.loads(body)["fileId"]).lower()
    except (ValueError, KeyError, TypeError):
        raise ApiError(400, "bad_body", "fileId mangler")
    project = await db.get(Project, file_id)
    if project is None:
        return {"ok": True, "known": False}
    await invalidate(db, file_id)
    version = None
    try:
        version = (await get_members(db, file_id, force=True)).get("version")
    except MembersUnavailable:
        log.warning("members refetch after webhook failed for %s", file_id)
    audit(db, "members.changed", actor_sub=None, actor_kind="system", project_id=file_id, details={"version": version})
    await db.commit()
    hub.publish(f"members:{file_id}", "members.changed", {"version": version})
    hub.publish(f"project:{file_id}", "members.changed", {"version": version})
    return {"ok": True, "known": True}
