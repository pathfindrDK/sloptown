"""Members cache, /platform/* (internal auth, sub stripping) and the access-changed webhook."""

import json
import time

import httpx

from .a_support import bdb, db, env, pytestmark  # noqa: F401

INTERNAL = {"X-Sloptown-Internal": "internal-test"}


async def setup(env, pid="m1"):
    owner = await env.user("s-t")
    await env.project(pid, "s-t", owner)
    env.miira.share(pid, "s-elev", "viewer", name="Ali")
    return owner


async def test_platform_members_strip_sub(env):
    await setup(env)
    from sloptown.members import invalidate
    await invalidate(env.db, "m1")
    r = await env.client.get("/platform/members", headers={**INTERNAL, "X-Vibe-Project": "m1", "X-Vibe-Token-Kind": "runtime"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["members"]) == 2
    assert all("sub" not in m for m in body["members"])
    assert "s-elev" not in json.dumps(body)


async def test_platform_requires_internal_secret(env):
    await setup(env)
    r = await env.client.get("/platform/members", headers={"X-Vibe-Project": "m1"})
    assert r.status_code == 403
    r = await env.client.get("/platform/members", headers={"X-Sloptown-Internal": "wrong", "X-Vibe-Project": "m1"})
    assert r.status_code == 403


async def test_whoami_dev_only(env):
    await setup(env)
    h = {**INTERNAL, "X-Vibe-Project": "m1"}
    assert (await env.client.get("/platform/whoami", headers={**h, "X-Vibe-Token-Kind": "runtime"})).status_code == 403
    r = await env.client.get("/platform/whoami", headers={**h, "X-Vibe-Token-Kind": "dev"})
    assert r.json()["role"] == "owner" and "sub" not in r.json()


async def test_members_cache_ttl_and_stale_fallback(env):
    await setup(env)
    from sloptown.members import get_members
    calls = env.miira.calls
    await get_members(env.db, "m1")
    await get_members(env.db, "m1")
    assert env.miira.calls == calls  # served from the cache filled at create
    env.mock.get("http://miira.test/api/vibe/m1/members").respond(500)
    payload = await get_members(env.db, "m1", force=True)
    assert payload["members"]  # stale cache instead of an error


def signed(body: dict, ts: int | None = None, secret: str = "dev-webhook-secret"):
    from sloptown.hooks import sign

    raw = json.dumps(body).encode()
    t = str(ts or int(time.time()))
    return raw, {"X-Miira-Timestamp": t, "X-Miira-Signature": sign(secret, t, raw), "Content-Type": "application/json"}


async def test_webhook_rejects_bad_signature_and_old_timestamp(env):
    raw, h = signed({"fileId": "m1"}, secret="wrong")
    assert (await env.client.post("/hooks/miira/access-changed", content=raw, headers=h)).status_code == 401
    raw, h = signed({"fileId": "m1"}, ts=int(time.time()) - 3600)
    assert (await env.client.post("/hooks/miira/access-changed", content=raw, headers=h)).status_code == 401


async def test_webhook_refreshes_and_notifies(env):
    await setup(env)
    from sloptown.models import MembersCache

    env.miira.share("m1", "s-new", "viewer", name="Ny")
    raw, h = signed({"fileId": "M1", "event": "access-changed"})
    r = await env.client.post("/hooks/miira/access-changed", content=raw, headers=h)
    assert r.json() == {"ok": True, "known": True}
    row = await env.db.get(MembersCache, "m1")
    await env.db.refresh(row)
    assert any(m["sub"] == "s-new" for m in row.payload["members"])
    channels = [c for (c, e, _d) in env.published if e == "members.changed"]
    assert "members:m1" in channels and "project:m1" in channels

    raw, h = signed({"fileId": "unknown"})
    assert (await env.client.post("/hooks/miira/access-changed", content=raw, headers=h)).json()["known"] is False


async def test_members_sse_receives_change(env):
    import asyncio

    from sloptown.events import hub

    await setup(env)
    got = []

    async def listen():
        async for event, data in hub.subscribe("members:m1"):
            got.append((event, data))
            return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.01)
    raw, h = signed({"fileId": "m1"})
    await env.client.post("/hooks/miira/access-changed", content=raw, headers=h)
    await asyncio.wait_for(task, 2)
    assert got[0][0] == "members.changed"
    _ = httpx  # keep import for parity with other modules


async def test_event_streams_do_not_hold_db_connections(env):
    """Every vibe container keeps /platform/members/events open; each must not pin a DB connection."""
    from sloptown.db import engine, sessionmaker
    from sloptown.models import Project
    from sloptown.platform import InternalCaller, member_events

    owner = await env.user("s-t")
    await env.project("st1", "s-t", owner)
    pool = engine().pool
    baseline = pool.checkedout()
    streams = []
    for _ in range(20):  # more than pool size + overflow (15)
        db = sessionmaker()()
        project = await db.get(Project, "st1")  # the token check takes a connection
        streams.append(await member_events(InternalCaller(project, "dev"), db))
    assert pool.checkedout() <= baseline, "streams must give their connection back"
    r = await env.client.get("/api/me", headers=owner)
    assert r.status_code == 200
