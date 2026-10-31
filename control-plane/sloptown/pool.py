"""Warm pool of unclaimed vibe-dev containers: atomic claim and background refill.

Claim locks one `ready` row with `FOR UPDATE SKIP LOCKED`, so concurrent
creates never get the same container. Refill runs under a Postgres advisory lock
so only one process creates containers, and discards unclaimed slots whose
image differs from the current `dev_image` (that is how image patches roll out
to the pool).
"""

import asyncio
import logging

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import engine, sessionmaker
from .models import PoolSlot
from .orchestrator import dev_container_name, get_orchestrator, slot_tag

log = logging.getLogger(__name__)

REFILL_LOCK_ID = 0x51_09_70_01
REFILL_INTERVAL = 30.0

_wake = asyncio.Event()


def request_refill() -> None:
    _wake.set()


async def claim(db: AsyncSession, project_id: str) -> PoolSlot:
    """Take a ready slot (or create one on the spot), rename it to the project. Caller commits."""
    orch = get_orchestrator()
    current = slot_tag(await orch.image_id(get_settings().dev_image))
    slot = (
        await db.execute(
            select(PoolSlot)
            .where(PoolSlot.state == "ready", PoolSlot.image_tag == current)
            .order_by(PoolSlot.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if slot is not None and not await orch.wake_slot(slot.container_name, slot.network_name):
        log.warning("pool slot %s did not come up, discarding it", slot.container_name)
        await orch.discard(slot.container_name, slot.volume_name, slot.network_name)
        await db.delete(slot)
        slot = None
    if slot is None:
        log.info("pool empty, creating a container on demand for %s", project_id)
        spec = await orch.create_pool_slot()
        slot = PoolSlot(
            container_name=spec.container_name,
            volume_name=spec.volume_name,
            network_name=spec.network_name,
            image_tag=spec.image_tag,
            token_seed=spec.token_seed,
            state="claimed",
        )
    else:
        slot.state = "claimed"
        await db.delete(slot)
    await orch.rename(slot.container_name, dev_container_name(project_id))
    request_refill()
    return slot


async def _discard_stale(db: AsyncSession, current: str) -> int:
    stale = (
        await db.execute(select(PoolSlot).where(PoolSlot.state == "ready", PoolSlot.image_tag != current))
    ).scalars().all()
    for slot in stale:
        log.info("discarding stale pool slot %s", slot.container_name)
        await get_orchestrator().discard(slot.container_name, slot.volume_name, slot.network_name)
        await db.delete(slot)
    await db.commit()
    return len(stale)


async def discard_stale() -> int:
    """Drop unclaimed slots built from an older dev image (refill then rebuilds them)."""
    async with sessionmaker()() as db:
        n = await _discard_stale(db, slot_tag(await get_orchestrator().image_id(get_settings().dev_image)))
    request_refill()
    return n


async def refill_once() -> int:
    """Discard stale slots and top the pool up. Returns the number of containers created."""
    s = get_settings()
    orch = get_orchestrator()
    created = 0
    # Session-level advisory lock on a dedicated connection; the work session commits
    # several times and may hop connections, so it can't hold the lock itself.
    async with engine().connect() as lock_conn, sessionmaker()() as db:
        got = (await lock_conn.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": REFILL_LOCK_ID})).scalar()
        if not got:
            return 0
        try:
            current = slot_tag(await orch.image_id(s.dev_image))
            await _discard_stale(db, current)
            ready = (
                await db.execute(
                    select(func.count()).select_from(PoolSlot).where(
                        PoolSlot.state == "ready", PoolSlot.image_tag == current
                    )
                )
            ).scalar_one()
            for _ in range(max(0, s.pool_size - ready)):
                spec = await orch.create_pool_slot()
                db.add(
                    PoolSlot(
                        container_name=spec.container_name,
                        volume_name=spec.volume_name,
                        network_name=spec.network_name,
                        image_tag=spec.image_tag,
                        token_seed=spec.token_seed,
                        state="ready",
                    )
                )
                await db.commit()
                created += 1
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": REFILL_LOCK_ID})
    return created


async def refill_loop() -> None:
    while True:
        try:
            await refill_once()
        except Exception:
            log.exception("pool refill failed")
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), timeout=REFILL_INTERVAL)
        except TimeoutError:
            pass
