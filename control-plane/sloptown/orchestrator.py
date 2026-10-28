"""Container orchestration: per-project networks, volumes, dev and runtime containers.

Talks to Podman's libpod REST API (the same API podman-py wraps) with httpx, so
it is async and can use spec fields podman-py does not expose (volume SubPath,
volume import, userns=auto). `FakeOrchestrator` keeps everything in memory and
is used when `orchestrator_enabled` is false (tests, studio-only dev).

Every project container is hardened the same way: all capabilities dropped,
no-new-privileges, read-only root, memory/CPU/pids limits, userns=auto, an
internal network with no default route, and optionally gVisor (`oci_runtime`).
"""

import asyncio
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from . import peers
from .config import get_settings
from .tokens import dev_token, edge_secret_for, runtime_token

log = logging.getLogger(__name__)

API = "/v5.0.0/libpod"


@dataclass
class SlotSpec:
    container_name: str
    volume_name: str
    network_name: str
    image_tag: str
    token_seed: str


class BuildFailed(Exception):
    """`vibe-build` exited non-zero while publishing; `log` is the tail of its output."""

    def __init__(self, log: str) -> None:
        super().__init__("vibe-build failed")
        self.log = log


class OrchestratorError(Exception):
    pass


class Orchestrator(Protocol):
    async def image_id(self, image: str) -> str: ...
    async def create_pool_slot(self) -> SlotSpec: ...
    async def discard(self, container: str, volume: str, network: str) -> None: ...
    async def rename(self, old: str, new: str) -> None: ...
    async def ensure_running(self, container: str) -> None: ...
    async def stop(self, container: str) -> None: ...
    async def reconnect_platform(self) -> int: ...
    async def address(self, container: str, network: str) -> str: ...
    async def container_ips(self, containers: list[str]) -> set[str]: ...
    async def publish(self, project_id: str, network: str, volume: str, seed: str, sha: str, tar: bytes) -> str: ...
    async def unpublish(self, project_id: str) -> None: ...
    async def container_image(self, container: str) -> str | None: ...
    async def replace_dev(self, project_id: str, container: str, network: str, volume: str, seed: str) -> str: ...
    async def roll_runtime(self, project_id: str, network: str, volume: str, seed: str) -> bool: ...


# Bump when the container spec (container or network settings) changes: unclaimed
# slots are discarded and rebuilt, published apps rolled and idle editors replaced,
# like on a new image.
# v2: project networks get DNS (the libpod API defaults dns_enabled to false).
# v3: a per-Vibe edge secret in the environment.
SLOT_SPEC = 3


def slot_tag(image_id: str) -> str:
    """What a Vibe container is compared on: its image, SLOT_SPEC, the model settings
    baked into its environment and how it is isolated (user namespace, OCI runtime).
    Any change recycles pool slots, rolls published apps and replaces idle editors."""
    s = get_settings()
    models = hashlib.sha256(f"{s.agent_model}|{s.agent_small_model}|{s.app_ai_model}".encode()).hexdigest()[:8]
    isolation = f"{'userns' if s.userns_auto else 'hostns'}-{s.oci_runtime or 'default'}"
    return f"{image_id}#spec{SLOT_SPEC}#{models}#{isolation}"


def image_of(tag: str | None) -> str | None:
    return tag.split("#", 1)[0] if tag else tag


def dev_container_name(project_id: str) -> str:
    return f"vibe-dev-{project_id}"


def runtime_container_name(project_id: str) -> str:
    return f"vibe-run-{project_id}"


def container_env(*, token: str, seed: str, mode: str, project_id: str = "") -> dict[str, str]:
    s = get_settings()
    egress = s.egress_url_for_containers
    host = urlparse(egress).hostname or "sloptown-egress"
    proxy = f"http://vibe:{token}@{host}:{s.egress_proxy_port}"
    no_proxy = f"{host},localhost,127.0.0.1"
    # Vite (dev) must accept preview-<slug>.<apps domain> and point its HMR socket at the edge's public port.
    hmr_port = s.apps_port.lstrip(":") or ("443" if s.apps_scheme == "https" else "80")
    return {
        "VIBE_AGENT_MODEL": s.agent_model,
        "VIBE_AGENT_SMALL_MODEL": s.agent_small_model,
        "VIBE_AI_MODEL": s.app_ai_model,
        "VIBE_APPS_DOMAIN": s.apps_domain,
        "VIBE_HMR_CLIENT_PORT": hmr_port,
        "VIBE_PROJECT_ID": project_id,
        "VIBE_PLATFORM_URL": egress,
        "VIBE_PROJECT_TOKEN": token,
        "VIBE_EDGE_SECRET": edge_secret_for(seed),
        "VIBE_MODE": mode,
        "OPENAI_BASE_URL": egress + "/v1",
        "OPENAI_API_KEY": token,
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "http_proxy": proxy,
        "https_proxy": proxy,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }


def _demux(raw: bytes) -> str:
    """Decode a docker-style multiplexed log stream (8-byte frame headers); fall back to raw text."""
    out, i = [], 0
    while i + 8 <= len(raw) and raw[i] in (0, 1, 2) and raw[i + 1 : i + 4] == b"\0\0\0":
        size = int.from_bytes(raw[i + 4 : i + 8], "big")
        out.append(raw[i + 8 : i + 8 + size])
        i += 8 + size
    if i < len(raw):
        out.append(raw[i:])
    return b"".join(out).decode("utf-8", "replace")


def _memory_bytes(value: str) -> int:
    units = {"k": 1024, "m": 1024**2, "g": 1024**3}
    value = value.strip().lower()
    if value[-1] in units:
        return int(float(value[:-1]) * units[value[-1]])
    return int(value)


def hardened_spec(name: str, image: str, env: dict[str, str], labels: dict[str, str], network: str) -> dict[str, Any]:
    s = get_settings()
    spec: dict[str, Any] = {
        "name": name,
        "image": image,
        "env": env,
        "labels": labels,
        "read_only_filesystem": True,
        "read_write_tmpfs": True,  # tmpfs on /tmp, /run, /var/tmp
        "cap_drop": ["ALL"],
        "no_new_privileges": True,
        "netns": {"nsmode": "bridge"},
        "Networks": {network: {}},
        "resource_limits": {
            "memory": {"limit": _memory_bytes(s.container_memory)},
            "cpu": {"quota": int(s.container_cpus * 100_000), "period": 100_000},
            "pids": {"limit": s.container_pids},
        },
        "restart_policy": "always",
    }
    if s.userns_auto:
        # Both are needed over the API (the CLI's --userns=auto sets both). Needs a
        # `containers` range in /etc/subuid and /etc/subgid (deploy/ bootstrap).
        spec["userns"] = {"nsmode": "auto"}
        spec["idmappings"] = {"AutoUserNs": True}
    if s.oci_runtime:
        spec["oci_runtime"] = s.oci_runtime
    return spec


class PodmanOrchestrator:
    def __init__(self, url: str | None = None) -> None:
        url = url or get_settings().podman_url
        if url.startswith("unix://"):
            self._client = httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(uds=url[len("unix://") :]), base_url="http://podman", timeout=120
            )
        else:
            self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=120)

    async def _req(self, method: str, path: str, *, ok: tuple[int, ...] = (200, 201, 204), **kw: Any) -> httpx.Response:
        r = await self._client.request(method, API + path, **kw)
        if r.status_code not in ok:
            raise OrchestratorError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r

    async def image_id(self, image: str) -> str:
        r = await self._req("GET", f"/images/{image}/json")
        return r.json()["Id"]

    async def _connect_platform(self, network: str) -> None:
        """Attach edge, egress and the control plane so they can reach the project's containers."""
        s = get_settings()
        for c in (s.edge_container, s.egress_container, s.cp_container):
            if not c:
                continue
            r = await self._client.post(f"{API}/networks/{network}/connect", json={"container": c})
            if r.status_code not in (200, 204):
                log.warning("could not connect %s to %s: %s %s", c, network, r.status_code, r.text[:200])
        peers.invalidate()  # edge and egress have new addresses on this network

    async def reconnect_platform(self) -> int:
        """Attach edge/egress/cp to every project network they're missing from. Returns connections made.

        Recreating a platform container (deploy, restart, image roll) drops its extra
        network attachments, which would cut every existing Vibe off from the platform.
        """
        s = get_settings()
        r = await self._req("GET", "/networks/json", params={"filters": json.dumps({"label": ["sloptown=1"]})})
        project_nets = {n["name"] for n in r.json() if n["name"].startswith("vibe-net-")}
        made = 0
        for c in (s.edge_container, s.egress_container, s.cp_container):
            if not c:
                continue
            info = await self._client.get(f"{API}/containers/{c}/json")
            if info.status_code != 200:
                continue
            have = set((info.json().get("NetworkSettings", {}).get("Networks") or {}).keys())
            for net in sorted(project_nets - have):
                cr = await self._client.post(f"{API}/networks/{net}/connect", json={"container": c})
                if cr.status_code in (200, 204):
                    made += 1
                    peers.invalidate()
                elif cr.status_code != 404:  # 404: the slot was discarded meanwhile
                    log.warning("reconnect %s to %s: %s %s", c, net, cr.status_code, cr.text[:200])
        return made

    async def _create_dev(self, name: str, network: str, volume: str, seed: str, project_id: str = "", start: bool = True) -> None:
        s = get_settings()
        spec = hardened_spec(
            name,
            s.dev_image,
            container_env(token=dev_token(seed), seed=seed, mode="dev", project_id=project_id),
            {"sloptown": "1", "sloptown.kind": "dev"},
            network,
        )
        # "U" chowns the volume to the container's (auto) user namespace on start.
        spec["volumes"] = [{"Name": volume, "Dest": "/workspace", "Options": ["U"]}]
        # Not "always": podman-restart.service starts every "always" container at boot,
        # idle-stopped editors included, and hundreds of them at once exhaust the host.
        # Editors are woken on access (ensure_running), so they only restart on a crash.
        spec["restart_policy"] = "on-failure"
        await self._req("POST", "/containers/create", json=spec)
        if start:
            await self._req("POST", f"/containers/{name}/start", ok=(204, 304))

    async def create_pool_slot(self) -> SlotSpec:
        s = get_settings()
        suffix = secrets.token_hex(4)
        name, vol, net = f"vibe-pool-{suffix}", f"vibe-vol-pool-{suffix}", f"vibe-net-pool-{suffix}"
        seed = secrets.token_hex(16)
        image_tag = slot_tag(await self.image_id(s.dev_image))
        await self._req(
            "POST",
            "/networks/create",
            # "vb*" interface names let the host nftables rules match project networks.
            # dns_enabled: containers reach sloptown-egress by name (aardvark-dns on this network only).
            json={
                "name": net,
                "internal": True,
                "dns_enabled": True,
                "network_interface": f"vb{suffix}",
                "labels": {"sloptown": "1"},
            },
        )
        try:
            await self._req("POST", "/volumes/create", json={"Name": vol, "Label": {"sloptown": "1"}})
            await self._connect_platform(net)
            await self._create_dev(name, net, vol, seed)
            # A slot is only offered once agentd answers and the template is seeded;
            # otherwise the teacher who claims it gets 503s for the first seconds.
            if not await self._healthy(name, net, timeout=120, port=4097, path="/health"):
                raise OrchestratorError(f"{name} did not become healthy")
        except Exception:
            await self.discard(name, vol, net)
            raise
        return SlotSpec(name, vol, net, image_tag, seed)

    async def discard(self, container: str, volume: str, network: str) -> None:
        await self._client.delete(f"{API}/containers/{container}", params={"force": "true", "t": 5})
        await self._client.delete(f"{API}/volumes/{volume}", params={"force": "true"})
        await self._remove_network(network)

    async def _remove_network(self, network: str) -> None:
        """Detach the platform containers, then remove the network.

        Never `force`: podman's forced network removal also removes every container
        still attached, which would include sloptown-edge, -egress and -cp.
        """
        s = get_settings()
        known = [c for c in (s.edge_container, s.egress_container, s.cp_container) if c]
        for attempt in range(3):
            # Whatever is attached now: the platform containers, and anything reconnect_loop
            # attached between our disconnect and the delete.
            info = await self._client.get(f"{API}/networks/{network}/json")
            if info.status_code == 404:
                return
            attached = [c.get("name") or cid for cid, c in (info.json().get("containers") or {}).items()]
            for c in dict.fromkeys(known + attached):
                await self._client.post(f"{API}/networks/{network}/disconnect", json={"Container": c})
            r = await self._client.delete(f"{API}/networks/{network}")
            if r.status_code in (200, 204, 404):
                return
            await asyncio.sleep(0.5 * (attempt + 1))
        log.warning("could not remove network %s: %s %s", network, r.status_code, r.text[:200])

    async def rename(self, old: str, new: str) -> None:
        await self._req("POST", f"/containers/{old}/rename", params={"name": new})

    async def ensure_running(self, container: str) -> None:
        await self._req("POST", f"/containers/{container}/start", ok=(204, 304))

    async def stop(self, container: str) -> None:
        await self._req("POST", f"/containers/{container}/stop", params={"t": 10}, ok=(204, 304, 404))

    async def container_ips(self, containers: list[str]) -> set[str]:
        """Every address of the named containers, on all their networks (missing ones skipped)."""
        ips: set[str] = set()
        for c in containers:
            r = await self._client.get(f"{API}/containers/{c}/json")
            if r.status_code != 200:
                continue
            for net in (r.json().get("NetworkSettings", {}).get("Networks") or {}).values():
                for key in ("IPAddress", "GlobalIPv6Address"):
                    if net.get(key):
                        ips.add(net[key])
        return ips

    async def address(self, container: str, network: str) -> str:
        r = await self._req("GET", f"/containers/{container}/json")
        nets = r.json().get("NetworkSettings", {}).get("Networks", {})
        ip = nets.get(network, {}).get("IPAddress")
        if not ip:
            raise OrchestratorError(f"{container} has no address on {network}")
        return ip

    async def publish(self, project_id: str, network: str, volume: str, seed: str, sha: str, tar: bytes) -> str:
        name = runtime_container_name(project_id)
        pub_vol = f"vibe-pub-{project_id}-{sha[:7]}"
        old = await self._client.get(f"{API}/containers/{name}/json")
        old_vol = None
        if old.status_code == 200:
            for m in old.json().get("Mounts", []):
                if m.get("Destination") == "/srv/app":
                    old_vol = m.get("Name")
        if pub_vol != old_vol:
            await self._client.delete(f"{API}/volumes/{pub_vol}", params={"force": "true"})
            await self._req("POST", "/volumes/create", json={"Name": pub_vol, "Label": {"sloptown": "1"}})
            await self._req(
                "POST", f"/volumes/{pub_vol}/import", content=tar, headers={"Content-Type": "application/x-tar"}
            )
            try:
                await self._build(project_id, pub_vol)
            except BaseException:
                await self._client.delete(f"{API}/volumes/{pub_vol}", params={"force": "true"})
                raise
        # The runtime mounts <volume>/.data as /data; podman refuses a missing subpath.
        await self.exec(dev_container_name(project_id), ["mkdir", "-p", "/workspace/.data"])
        await self._client.delete(f"{API}/containers/{name}", params={"force": "true", "t": 10})
        await self._create_runtime(name, project_id, network, volume, pub_vol, seed, sha)
        if old_vol and old_vol != pub_vol:
            await self._client.delete(f"{API}/volumes/{old_vol}", params={"force": "true"})
        return name

    async def _build(self, project_id: str, pub_vol: str) -> None:
        """Build web/dist inside the snapshot volume with a one-shot, network-less vibe-dev container."""
        s = get_settings()
        name = f"vibe-build-{project_id}"
        spec = hardened_spec(name, s.dev_image, {}, {"sloptown": "1", "sloptown.kind": "build"}, "")
        spec.pop("Networks")
        spec["netns"] = {"nsmode": "none"}
        spec["restart_policy"] = "no"
        spec["entrypoint"] = ["vibe-build"]  # not the dev entrypoint: no agent, no project token
        spec["command"] = ["/srv/app"]
        spec["volumes"] = [{"Name": pub_vol, "Dest": "/srv/app", "Options": ["U"]}]
        await self._client.delete(f"{API}/containers/{name}", params={"force": "true"})
        await self._req("POST", "/containers/create", json=spec)
        try:
            await self._req("POST", f"/containers/{name}/start", ok=(204, 304))
            r = await self._req("POST", f"/containers/{name}/wait", params={"condition": "exited"}, timeout=600)
            body = r.json()
            code = body if isinstance(body, int) else body.get("StatusCode", 1)
            if code:
                logs = await self._client.get(
                    f"{API}/containers/{name}/logs", params={"stdout": "true", "stderr": "true", "tail": "60"}
                )
                raise BuildFailed(_demux(logs.content)[-4000:])
        finally:
            await self._client.delete(f"{API}/containers/{name}", params={"force": "true"})

    async def exec(self, container: str, cmd: list[str]) -> None:
        r = await self._req("POST", f"/containers/{container}/exec", json={"Cmd": cmd, "AttachStdout": True, "AttachStderr": True})
        exec_id = r.json()["Id"]
        await self._req("POST", f"/exec/{exec_id}/start", json={"Detach": False})
        code = (await self._req("GET", f"/exec/{exec_id}/json")).json().get("ExitCode")
        if code:
            raise OrchestratorError(f"{cmd!r} in {container} exited {code}")

    async def _create_runtime(self, name: str, project_id: str, network: str, volume: str, pub_vol: str, seed: str, sha: str) -> None:
        s = get_settings()
        spec = hardened_spec(
            name,
            s.runtime_image,
            container_env(token=runtime_token(project_id, seed), seed=seed, mode="runtime", project_id=project_id),
            {
                "sloptown": "1",
                "sloptown.kind": "runtime",
                "sloptown.project": project_id,
                "sloptown.sha": sha,
                "sloptown.tag": slot_tag(await self.image_id(s.runtime_image)),
            },
            network,
        )
        spec["volumes"] = [
            {"Name": pub_vol, "Dest": "/srv/app", "Options": ["ro"]},
            {"Name": volume, "Dest": "/data", "Options": ["U"], "SubPath": ".data"},
        ]
        await self._req("POST", "/containers/create", json=spec)
        await self._req("POST", f"/containers/{name}/start", ok=(204, 304))

    async def unpublish(self, project_id: str) -> None:
        await self._client.delete(f"{API}/containers/{runtime_container_name(project_id)}", params={"force": "true"})

    async def container_image(self, container: str) -> str | None:
        r = await self._client.get(f"{API}/containers/{container}/json")
        return r.json().get("Image") if r.status_code == 200 else None

    async def replace_dev(self, project_id: str, container: str, network: str, volume: str, seed: str) -> str:
        """Recreate an editor container on the current dev image, keeping its volume and network.

        A stopped editor stays stopped (ensure_running wakes it on access): starting every
        idle-stopped editor during a rollover is how ~100 of them once filled the host's memory.
        """
        old = await self._client.get(f"{API}/containers/{container}/json")
        running = old.status_code == 200 and bool((old.json().get("State") or {}).get("Running"))
        await self._client.delete(f"{API}/containers/{container}", params={"force": "true", "t": 10})
        await self._create_dev(container, network, volume, seed, project_id, start=running)
        if running and not await self._healthy(container, network, timeout=120, port=4097, path="/health"):
            log.warning("%s replaced but agentd is not healthy yet", container)
        return slot_tag(await self.image_id(get_settings().dev_image))

    async def roll_runtime(self, project_id: str, network: str, volume: str, seed: str) -> bool:
        """Start the published app on the current runtime image next to the old one; swap once healthy."""
        s = get_settings()
        name = runtime_container_name(project_id)
        old = await self._client.get(f"{API}/containers/{name}/json")
        if old.status_code != 200:
            return False
        info = old.json()
        if (info.get("Config", {}).get("Labels") or {}).get("sloptown.tag") == slot_tag(await self.image_id(s.runtime_image)):
            return False
        pub_vol = next((m["Name"] for m in info.get("Mounts", []) if m.get("Destination") == "/srv/app"), None)
        sha = (info.get("Config", {}).get("Labels") or {}).get("sloptown.sha", "")
        if not pub_vol:
            raise OrchestratorError(f"{name} has no published volume")
        nxt = f"{name}-next"
        await self._client.delete(f"{API}/containers/{nxt}", params={"force": "true"})
        await self._create_runtime(nxt, project_id, network, volume, pub_vol, seed, sha)
        if not await self._healthy(nxt, network):
            await self._client.delete(f"{API}/containers/{nxt}", params={"force": "true"})
            raise OrchestratorError(f"{nxt} did not become healthy; kept the old container")
        await self._client.delete(f"{API}/containers/{name}", params={"force": "true", "t": 10})
        await self.rename(nxt, name)
        return True

    async def _healthy(
        self, container: str, network: str, timeout: float = 60, port: int = 8000, path: str = "/healthz"
    ) -> bool:
        """Poll http://<container>:<port><path> until 200 (and, for agentd, a seeded workspace)."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                ip = await self.address(container, network)
                async with httpx.AsyncClient(timeout=3) as c:
                    r = await c.get(f"http://{ip}:{port}{path}")
                    if r.status_code == 200 and (path != "/health" or r.json().get("head")):
                        return True
            except (OrchestratorError, httpx.HTTPError):
                pass
            await asyncio.sleep(1)
        return False


class FakeOrchestrator:
    """In-memory stand-in. Addresses are `fake-<container>` so tests can mock them with respx."""

    def __init__(self) -> None:
        self.containers: dict[str, dict[str, Any]] = {}
        self.published: dict[str, dict[str, Any]] = {}
        self.platform_ips: dict[str, str] = {}
        self.image = "sha256:fake-image-1"
        self.runtime_image = "sha256:fake-runtime-1"
        self.build_error: str | None = None  # set to make the next publish fail its build
        self._n = 0

    async def image_id(self, image: str) -> str:
        return self.image

    async def create_pool_slot(self) -> SlotSpec:
        self._n += 1
        suffix = f"{self._n:04d}{secrets.token_hex(2)}"
        spec = SlotSpec(f"vibe-pool-{suffix}", f"vibe-vol-pool-{suffix}", f"vibe-net-pool-{suffix}", slot_tag(self.image), secrets.token_hex(8))
        self.containers[spec.container_name] = {"running": True, "network": spec.network_name}
        return spec

    async def discard(self, container: str, volume: str, network: str) -> None:
        self.containers.pop(container, None)

    async def rename(self, old: str, new: str) -> None:
        self.containers[new] = self.containers.pop(old)

    async def ensure_running(self, container: str) -> None:
        if container in self.containers:
            self.containers[container]["running"] = True

    async def stop(self, container: str) -> None:
        if container in self.containers:
            self.containers[container]["running"] = False

    async def reconnect_platform(self) -> int:
        return 0

    async def address(self, container: str, network: str) -> str:
        return f"fake-{container}"

    async def container_ips(self, containers: list[str]) -> set[str]:
        return {self.platform_ips[c] for c in containers if c in self.platform_ips}

    async def publish(self, project_id: str, network: str, volume: str, seed: str, sha: str, tar: bytes) -> str:
        if self.build_error is not None:
            raise BuildFailed(self.build_error)
        name = runtime_container_name(project_id)
        self.published[project_id] = {"sha": sha, "size": len(tar)}
        self.containers[name] = {"running": True, "network": network, "image": self.runtime_image, "tag": slot_tag(self.runtime_image)}
        return name

    async def unpublish(self, project_id: str) -> None:
        self.published.pop(project_id, None)
        self.containers.pop(runtime_container_name(project_id), None)

    async def container_image(self, container: str) -> str | None:
        c = self.containers.get(container)
        return c.get("image", self.image) if c else None

    async def replace_dev(self, project_id: str, container: str, network: str, volume: str, seed: str) -> str:
        self.containers[container] = {"running": True, "network": network, "image": self.image}
        return slot_tag(self.image)

    async def roll_runtime(self, project_id: str, network: str, volume: str, seed: str) -> bool:
        c = self.containers.get(runtime_container_name(project_id))
        if c is None or c.get("tag") == slot_tag(self.runtime_image):
            return False
        c["image"], c["tag"] = self.runtime_image, slot_tag(self.runtime_image)
        return True


_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PodmanOrchestrator() if get_settings().orchestrator_enabled else FakeOrchestrator()
    return _orchestrator


def set_orchestrator(o: Orchestrator | None) -> None:
    global _orchestrator
    _orchestrator = o
