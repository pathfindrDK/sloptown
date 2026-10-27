"""Project activity tracking and stopping idle editor containers.

`touch(pid)` is called on anything that means "someone is working on this Vibe":
preview traffic through the edge, any call into the dev container (files,
history, chat) and opening the project's SSE stream. Writes to
`projects.last_active_at` are debounced to one per TOUCH_EVERY per project.

`idle_stop_loop` stops vibe-dev containers with no activity for IDLE_STOP, no
running turn and nobody subscribed to the project's event stream. A stopped
container is started again on the next access (`containers.dev_host` and the
preview forward_auth call `ensure_running`), so stopping is invisible apart
from a few seconds of start-up.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, select, update

from .audit import audit
from .db import sessionmaker
from .events import hub
from .models import Project
from .orchestrator import OrchestratorError, get_orchestrator

log = logging.getLogger(__name__)

TOUCH_EVERY = 60.0  # seconds between DB writes per project
IDLE_STOP = timedelta(minutes=30)
IDLE_INTERVAL = 120.0

_last_write: dict[str, float] = {}
_pending: set[asyncio.Task[None]] = set()


async def _write(pid: str) -> None:
    async with sessionmaker()() as db:
        await db.execute(
            update(Project)
            .where(Project.id == pid)
            .values(
                last_active_at=datetime.now(UTC),
                status=case((Project.status == "stopped", "ready"), else_=Project.status),
            )
        )
        await db.commit()


def touch(pid: str) -> None:
    """Record activity on a project. Cheap; safe to call on every request."""
    now = time.monotonic()
    if now - _last_write.get(pid, 0.0) < TOUCH_EVERY:
        return
    _last_write[pid] = now
    task = asyncio.get_running_loop().create_task(_write(pid))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def stop_idle_editors(now: datetime | None = None) -> int:
    """Stop idle dev containers. Returns how many were stopped."""
    from .threads import turns

    now = now or datetime.now(UTC)
    orch = get_orchestrator()
    stopped = 0
    async with sessionmaker()() as db:
        rows = (
            await db.execute(
                select(Project).where(
                    Project.container_name.is_not(None),
                    Project.status != "stopped",
                    Project.last_active_at < now - IDLE_STOP,
                )
            )
        ).scalars().all()
        for p in rows:
            if turns.running_turn(p.id) or hub.subscribers(f"project:{p.id}"):
                continue
            async with turns.lock(p.id):
                if turns.running_turn(p.id):
                    continue
                try:
                    await orch.stop(p.container_name or "")
                except OrchestratorError as e:
                    log.warning("idle stop failed for %s: %s", p.id, e)
                    continue
                p.status = "stopped"
                _last_write.pop(p.id, None)  # next touch must flip status back right away
                audit(db, "project.editor_stopped", actor_sub=None, actor_kind="system", project_id=p.id,
                      details={"idleSince": p.last_active_at.isoformat()})
                await db.commit()
                stopped += 1
    return stopped


async def idle_stop_loop() -> None:
    while True:
        await asyncio.sleep(IDLE_INTERVAL)
        try:
            await stop_idle_editors()
        except Exception:
            log.exception("idle stop failed")
