"""Activity tracking and idle editor stop: stop when idle, skip when watched, restart on access."""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from .a_support import bdb, db, dev_base, env, pytestmark  # noqa: F401

LATER = timedelta(minutes=45)


async def _project(env, pid: str):
    from sloptown.models import Project

    owner = await env.user("s-t")
    await env.project(pid, "s-t", owner)
    return owner, await env.db.get(Project, pid)


async def test_idle_editor_is_stopped_and_restarted_on_access(env):
    from sloptown import activity
    from sloptown.models import Project

    owner, proj = await _project(env, "i1")
    container = proj.container_name

    assert await activity.stop_idle_editors() == 0  # just created, still active
    assert await activity.stop_idle_editors(now=datetime.now(UTC) + LATER) == 1
    assert env.orch.containers[container]["running"] is False
    await env.db.refresh(proj)
    assert proj.status == "stopped"

    # Any call into the dev container starts it again and marks the project ready.
    env.mock.get(f"{dev_base('i1', 4097)}/files").respond(json={"files": []})
    r = await env.client.get("/api/projects/i1/files", headers=owner)
    assert r.status_code == 200
    assert env.orch.containers[container]["running"] is True
    await asyncio.gather(*activity._pending)
    await env.db.refresh(proj)
    assert proj.status == "ready"
    assert (await env.db.get(Project, "i1")).last_active_at > datetime.now(UTC) - timedelta(minutes=1)


async def test_idle_editor_with_running_turn_or_open_stream_is_kept(env):
    from sloptown import activity
    from sloptown.events import hub
    from sloptown.threads import turns

    _, proj = await _project(env, "i2")

    turns._running["i2"] = "turn-x"
    try:
        assert await activity.stop_idle_editors(now=datetime.now(UTC) + LATER) == 0
    finally:
        turns._running.pop("i2", None)

    async def watch() -> None:
        async for _ in hub.subscribe("project:i2"):
            pass

    task = asyncio.create_task(watch())
    await asyncio.sleep(0)
    try:
        assert hub.subscribers("project:i2") == 1
        assert await activity.stop_idle_editors(now=datetime.now(UTC) + LATER) == 0
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert env.orch.containers[proj.container_name]["running"] is True
