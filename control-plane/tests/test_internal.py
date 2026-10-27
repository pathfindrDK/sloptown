"""Image rollout: pool recycle, runtime roll, idle editor replacement, internal auth."""

from datetime import UTC, datetime, timedelta

from .a_support import bdb, db, dev_base, env, pytestmark  # noqa: F401

INTERNAL = {"X-Sloptown-Internal": "internal-test"}


async def test_images_rolled(env):
    from sloptown.internal import rollover_idle_editors
    from sloptown.models import Project
    from sloptown.pool import refill_once

    await refill_once()
    owner = await env.user("s-t")
    await env.project("r1", "s-t", owner)
    env.mock.post(f"{dev_base('r1', 4097)}/export").respond(200, content=b"T", headers={"x-vibe-sha": "b" * 40})
    await env.client.post("/api/projects/r1/publish", json={"sha": "b" * 7}, headers=owner)

    assert (await env.client.post("/internal/images/rolled", json={})).status_code == 403
    env.orch.image = "sha256:dev-2"
    env.orch.runtime_image = "sha256:run-2"
    r = await env.client.post("/internal/images/rolled", json={"tag": "2026-10-27"}, headers=INTERNAL)
    assert r.json() == {"poolRecycled": 4, "runtimesRolled": 1, "runtimesFailed": [], "editorsPending": 1}

    assert await rollover_idle_editors() == 0  # just active
    assert await rollover_idle_editors(now=datetime.now(UTC) + timedelta(minutes=20)) == 1
    proj = await env.db.get(Project, "r1")
    await env.db.refresh(proj)
    assert proj.image_tag == "sha256:dev-2"


async def test_error_bodies_are_uniform(env):
    r = await env.client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    h = await env.user("s-t")
    r = await env.client.post("/api/projects", json={}, headers=h)
    assert r.status_code == 422 and r.json()["error"] == "invalid_request"
