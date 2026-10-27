"""PodmanOrchestrator against a real Podman socket (opt-in: SLOPTOWN_PODMAN_IT=1).

Mocks can't catch these, and each one broke the dev stack once:
- project networks must have DNS (the libpod API defaults dns_enabled to false),
- discarding a slot must never remove platform containers attached to its network,
- recreated platform containers must be reattached to existing project networks.

Run (needs the socket mounted and the dev image built by deploy/dev-up.sh):
  podman run --rm --network host -v $PWD:/src:Z -v /run/podman/podman.sock:/run/podman/podman.sock \
    --security-opt label=disable -v sloptown-uv-cache:/root/.cache/uv -w /src \
    -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e SLOPTOWN_PODMAN_IT=1 \
    ghcr.io/astral-sh/uv:python3.13-bookworm-slim uv run pytest -q tests/test_podman_integration.py
"""

import os
import uuid

import httpx
import pytest

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(os.environ.get("SLOPTOWN_PODMAN_IT") != "1", reason="set SLOPTOWN_PODMAN_IT=1"),
]

SOCK = "/run/podman/podman.sock"
API = "http://podman/v5.0.0/libpod"
DEV_IMAGE = os.environ.get("SLOPTOWN_IT_DEV_IMAGE", "localhost/sloptown/vibe-dev:dev")
STUB_IMAGE = "ghcr.io/astral-sh/uv:python3.13-bookworm-slim"


@pytest.fixture
async def podman(monkeypatch):
    from sloptown.config import get_settings
    from sloptown.orchestrator import PodmanOrchestrator

    tag = uuid.uuid4().hex[:6]
    stub = f"it-platform-{tag}"
    s = get_settings()
    monkeypatch.setattr(s, "podman_url", f"unix://{SOCK}")
    monkeypatch.setattr(s, "dev_image", DEV_IMAGE)
    monkeypatch.setattr(s, "userns_auto", False)
    monkeypatch.setattr(s, "edge_container", stub)
    monkeypatch.setattr(s, "egress_container", "")
    monkeypatch.setattr(s, "cp_container", "")
    raw = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=SOCK), base_url="http://podman", timeout=60)
    r = await raw.post(f"{API}/containers/create", json={"name": stub, "image": STUB_IMAGE, "command": ["sleep", "600"]})
    assert r.status_code == 201, r.text
    await raw.post(f"{API}/containers/{stub}/start")
    orch = PodmanOrchestrator()
    try:
        yield orch, raw, stub
    finally:
        await raw.delete(f"{API}/containers/{stub}", params={"force": "true", "t": 0})
        await raw.aclose()


async def _networks_of(raw: httpx.AsyncClient, container: str) -> set[str]:
    r = await raw.get(f"{API}/containers/{container}/json")
    return set((r.json().get("NetworkSettings", {}).get("Networks") or {}).keys())


async def test_slot_network_has_dns_and_discard_spares_platform(podman):
    orch, raw, stub = podman
    spec = await orch.create_pool_slot()
    try:
        net = (await raw.get(f"{API}/networks/{spec.network_name}/json")).json()
        assert net["dns_enabled"] is True and net["internal"] is True
        # podman-restart.service must not start every editor at boot
        info = (await raw.get(f"{API}/containers/{spec.container_name}/json")).json()
        assert info["HostConfig"]["RestartPolicy"]["Name"] == "on-failure"
        assert spec.network_name in await _networks_of(raw, stub)
        # the dev container resolves the platform container by name
        r = await raw.post(f"{API}/containers/{spec.container_name}/exec",
                           json={"Cmd": ["getent", "hosts", stub], "AttachStdout": True})
        exec_id = r.json()["Id"]
        await raw.post(f"{API}/exec/{exec_id}/start", json={"Detach": False})
        assert (await raw.get(f"{API}/exec/{exec_id}/json")).json()["ExitCode"] == 0
    finally:
        await orch.discard(spec.container_name, spec.volume_name, spec.network_name)

    assert (await raw.get(f"{API}/containers/{stub}/json")).status_code == 200, "platform container survived"
    assert (await raw.get(f"{API}/networks/{spec.network_name}/json")).status_code == 404
    assert (await raw.get(f"{API}/containers/{spec.container_name}/json")).status_code == 404


async def test_reconnect_platform_reattaches(podman):
    orch, raw, stub = podman
    spec = await orch.create_pool_slot()
    try:
        await raw.post(f"{API}/networks/{spec.network_name}/disconnect", json={"Container": stub})
        assert spec.network_name not in await _networks_of(raw, stub)
        assert await orch.reconnect_platform() >= 1
        assert spec.network_name in await _networks_of(raw, stub)
        assert await orch.reconnect_platform() == 0 or spec.network_name in await _networks_of(raw, stub)
    finally:
        await orch.discard(spec.container_name, spec.volume_name, spec.network_name)
