"""Internal endpoints for the host's image pipeline, and the idle-editor image rollover.

`POST /internal/images/rolled` (X-Sloptown-Internal, never routed by the edge):
recycle stale pool slots, roll published runtimes one at a time (new container
health-checked before the old one goes), and count editor containers still on
an old image. Those are replaced by `editor_rollover_loop` once idle: no running
turn and no activity for EDITOR_IDLE.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select

from .audit import audit
from .auth import Db
from .config import get_settings
from .db import sessionmaker
from .errors import ApiError
from .models import Project
from .orchestrator import OrchestratorError, get_orchestrator, slot_tag
from .pool import discard_stale
from .tokens import consteq

log = logging.getLogger(__name__)
router = APIRouter(prefix="/internal")

EDITOR_IDLE = timedelta(minutes=15)
ROLLOVER_INTERVAL = 60.0


def require_internal(request: Request) -> None:
    secret = request.headers.get("x-sloptown-internal", "")
    if not secret or not consteq(secret, get_settings().internal_secret):
        raise ApiError(403, "forbidden", "Kun internt")
    # The egress proxy adds the same secret to a Vibe's /platform/* calls (with
    # X-Vibe-Project); those must never reach an internal endpoint, whatever the path.
    if "x-vibe-project" in request.headers:
        raise ApiError(403, "forbidden", "Kun internt")


class Rolled(BaseModel):
    devImage: str | None = None
    runtimeImage: str | None = None
    tag: str | None = None


async def editors_on_old_image(db: Any) -> list[Project]:
    current = await get_orchestrator().image_id(get_settings().dev_image)
    rows = (await db.execute(select(Project).where(Project.container_name.is_not(None)))).scalars().all()
    # Image *and* spec: a new edge secret or isolation setting replaces editors too.
    return [p for p in rows if p.image_tag != slot_tag(current)]


@router.post("/images/rolled")
async def images_rolled(body: Rolled, request: Request, db: Db) -> dict[str, Any]:
    require_internal(request)
    orch = get_orchestrator()
    recycled = await discard_stale()
    rolled, failed = 0, []
    published = (await db.execute(select(Project).where(Project.published_sha.is_not(None)))).scalars().all()
    for p in published:  # one at a time on purpose
        try:
            if await orch.roll_runtime(p.id, p.network_name or "", p.volume_name or "", p.token_seed or ""):
                rolled += 1
        except OrchestratorError as e:
            log.warning("runtime roll failed for %s: %s", p.id, e)
            failed.append(p.id)
    pending = len(await editors_on_old_image(db))
    audit(db, "images.rolled", actor_sub=None, actor_kind="system",
          details={**body.model_dump(), "poolRecycled": recycled, "runtimesRolled": rolled, "failed": failed, "editorsPending": pending})
    await db.commit()
    return {"poolRecycled": recycled, "runtimesRolled": rolled, "runtimesFailed": failed, "editorsPending": pending}


async def rollover_idle_editors(now: datetime | None = None) -> int:
    """Replace idle editor containers that run an old dev image. Returns how many were replaced."""
    from .threads import turns

    now = now or datetime.now(UTC)
    orch = get_orchestrator()
    replaced = 0
    async with sessionmaker()() as db:
        for p in await editors_on_old_image(db):
            if turns.running_turn(p.id) or now - p.last_active_at < EDITOR_IDLE:
                continue
            async with turns.lock(p.id):
                if turns.running_turn(p.id):
                    continue
                try:
                    p.image_tag = await orch.replace_dev(p.id, p.container_name or "", p.network_name or "", p.volume_name or "", p.token_seed or "")
                except OrchestratorError as e:
                    log.warning("editor rollover failed for %s: %s", p.id, e)
                    continue
                audit(db, "project.editor_replaced", actor_sub=None, actor_kind="system", project_id=p.id, details={"image": p.image_tag})
                await db.commit()
                replaced += 1
    return replaced


async def editor_rollover_loop() -> None:
    while True:
        await asyncio.sleep(ROLLOVER_INTERVAL)
        try:
            await rollover_idle_editors()
        except Exception:
            log.exception("editor rollover failed")
