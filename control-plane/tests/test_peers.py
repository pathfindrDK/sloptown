"""The control plane serves only its platform peers (edge, egress) and loopback (peers.py)."""

from types import SimpleNamespace

import httpx

from .a_support import bdb, db, env, pytestmark  # noqa: F401

EDGE, VIBE = "10.89.0.5", "10.89.66.2"


def client_from(env, ip: str) -> httpx.AsyncClient:
    app = env.client._transport.app
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 40000)), base_url="https://studio.test")


async def test_a_vibe_on_a_shared_network_gets_nothing(env):
    from sloptown import peers

    peers.reset()
    env.orch.platform_ips = {"sloptown-edge": EDGE, "sloptown-egress": "10.89.0.6"}
    try:
        async with client_from(env, VIBE) as vibe:
            assert (await vibe.get("/api/config")).status_code == 404
            assert (await vibe.get("/edge/auth/app", headers={"X-Forwarded-Host": "x.apps.test"})).status_code == 404
        async with client_from(env, EDGE) as edge:
            assert (await edge.get("/api/config")).status_code == 200
    finally:
        peers.reset()


async def test_only_the_edge_names_the_visitor(env):
    from sloptown import peers
    from sloptown.audit import client_ip

    peers.reset()
    env.orch.platform_ips = {"sloptown-edge": EDGE}
    try:
        assert await peers.allowed(EDGE)
        forged = {"x-forwarded-for": "204.168.206.145"}
        assert client_ip(SimpleNamespace(headers=forged, client=SimpleNamespace(host=EDGE))) == "204.168.206.145"
        assert client_ip(SimpleNamespace(headers=forged, client=SimpleNamespace(host=VIBE))) == VIBE
    finally:
        peers.reset()


async def test_a_new_network_address_is_looked_up_at_once(env):
    """The edge joins a Vibe's network and asks for its certificate from the new address
    straight away; that first request must not wait out the refresh window."""
    from sloptown import peers

    peers.reset()
    env.orch.platform_ips = {"sloptown-edge": EDGE}
    try:
        assert await peers.allowed(EDGE)
        env.orch.platform_ips = {"sloptown-edge": "10.89.5.2"}
        assert not await peers.allowed("10.89.5.2")  # inside the window, nothing told us
        peers.invalidate()
        assert await peers.allowed("10.89.5.2")
    finally:
        peers.reset()


async def test_a_burst_from_a_new_address_waits_for_one_refresh(env):
    """Opening a Vibe sends several requests at once from the edge's address on that
    Vibe's network. Those arriving while the first one refreshes must not get a 404."""
    import asyncio

    from sloptown import peers

    peers.reset()
    env.orch.platform_ips = {"sloptown-edge": EDGE}
    real = env.orch.container_ips
    calls = 0

    async def slow(containers):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return await real(containers)

    env.orch.container_ips = slow
    try:
        assert all(await asyncio.gather(*(peers.allowed(EDGE) for _ in range(6))))
        assert calls == 1
    finally:
        env.orch.container_ips = real
        peers.reset()
