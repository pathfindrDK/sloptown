"""One user namespace per project: PodmanOrchestrator against an in-memory libpod API.

The editor and the published app both write the project volume's `.data`, and "U"
chowns it to whichever container starts last. With two auto namespaces a publish
took `.data` from the editor (its API died with "attempt to write a readonly
database", the preview answered 500). These tests pin the specs PodmanOrchestrator
sends: every container of a project after the first reuses that first one's mapping.
The real chown behaviour is covered by test_podman_integration.py.
"""

import itertools
import json
import re

import httpx
import pytest
import respx

from sloptown.orchestrator import PodmanOrchestrator, dev_container_name, runtime_container_name

BASE = "http://podman"
PID = "p1"
DEV, RUN = dev_container_name(PID), runtime_container_name(PID)


class FakeLibpod:
    """Just enough of /v5.0.0/libpod for publish, replace_dev and roll_runtime."""

    def __init__(self) -> None:
        self.containers: dict[str, dict] = {}
        self.created: list[dict] = []
        self._ranges = itertools.count(1)

    def mapping(self, spec: dict) -> dict:
        ids = spec.get("idmappings") or {}
        if ids.get("AutoUserNs"):
            base = 2147483647 + next(self._ranges) * 1024
            return {"UidMap": [f"0:{base}:1024"], "GidMap": [f"0:{base}:1024"]}
        if ids.get("UIDMap"):
            fmt = lambda es: [f"{e['container_id']}:{e['host_id']}:{e['size']}" for e in es]  # noqa: E731
            return {"UidMap": fmt(ids["UIDMap"]), "GidMap": fmt(ids["GIDMap"])}
        return {}

    def add(self, name: str, spec: dict, running: bool = True) -> None:
        self.containers[name] = {"spec": spec, "ids": self.mapping(spec), "running": running}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = re.sub(r"^/v[\d.]+/libpod", "", request.url.path)
        m, name = request.method, None
        if hit := re.match(r"^/containers/([^/]+)(/.*)?$", path):
            name, rest = hit.group(1), hit.group(2) or ""
            if name == "create" and m == "POST":
                spec = json.loads(request.content)
                if spec["name"] in self.containers:
                    return httpx.Response(409)
                self.created.append(spec)
                self.add(spec["name"], spec, running=False)
                return httpx.Response(201, json={"Id": spec["name"]})
            c = self.containers.get(name)
            if m == "DELETE":
                self.containers.pop(name, None)
                return httpx.Response(200 if c else 404)
            if c is None:
                return httpx.Response(404)
            if rest == "/json":
                spec = c["spec"]
                labels = spec.get("labels") or {}
                mounts = [{"Name": v["Name"], "Destination": v["Dest"]} for v in spec.get("volumes") or []]
                return httpx.Response(200, json={
                    "State": {"Running": c["running"]}, "Config": {"Labels": labels}, "Mounts": mounts,
                    "HostConfig": {"IDMappings": c["ids"]},
                })
            if rest == "/start":
                c["running"] = True
                return httpx.Response(204)
            if rest == "/rename":
                self.containers[request.url.params["name"]] = self.containers.pop(name)
                return httpx.Response(204)
            if rest == "/exec":
                return httpx.Response(201, json={"Id": "e1"})
        if path.startswith("/exec/"):
            return httpx.Response(200, json={"ExitCode": 0}) if m == "GET" else httpx.Response(200)
        if path.startswith("/images/"):
            return httpx.Response(200, json={"Id": "sha256:img"})
        if path.startswith("/volumes/"):
            return httpx.Response(201 if m == "POST" else 204)
        return httpx.Response(500, text=f"unmocked {m} {path}")


@pytest.fixture
def libpod(monkeypatch):
    from sloptown.config import get_settings

    monkeypatch.setattr(get_settings(), "userns_auto", True)
    fake = FakeLibpod()
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.route().mock(side_effect=fake)
        yield fake


def auto_dev(fake: FakeLibpod, name: str = DEV) -> None:
    fake.add(name, {"name": name, "idmappings": {"AutoUserNs": True}, "volumes": [{"Name": "vol", "Dest": "/workspace"}]})


async def test_publish_runs_the_app_in_the_editors_namespace(libpod):
    auto_dev(libpod)
    orch = PodmanOrchestrator(BASE)
    await orch.publish(PID, "net", "vol", "seed", "a" * 40, b"tar", build=False)
    spec = libpod.created[-1]
    assert spec["name"] == RUN
    assert spec["userns"] == {"nsmode": "private"}
    assert libpod.containers[RUN]["ids"] == libpod.containers[DEV]["ids"]
    data = next(v for v in spec["volumes"] if v["Dest"] == "/data")
    assert data["SubPath"] == ".data" and "U" in data["Options"]


async def test_replace_dev_keeps_the_projects_namespace(libpod):
    auto_dev(libpod)
    before = libpod.containers[DEV]["ids"]
    orch = PodmanOrchestrator(BASE)
    await orch.replace_dev(PID, DEV, "net", "vol", "seed")
    assert libpod.containers[DEV]["ids"] == before
    assert f"{DEV}-next" not in libpod.containers
    assert libpod.created[-1]["userns"] == {"nsmode": "private"}


async def test_replace_dev_without_an_editor_adopts_the_published_apps_namespace(libpod):
    libpod.add(RUN, {"name": RUN, "idmappings": {"AutoUserNs": True}})
    orch = PodmanOrchestrator(BASE)
    await orch.replace_dev(PID, DEV, "net", "vol", "seed")
    assert libpod.containers[DEV]["ids"] == libpod.containers[RUN]["ids"]


async def test_roll_runtime_moves_a_split_runtime_onto_the_editors_namespace(libpod, monkeypatch):
    """The repair path for projects published before this fix (SLOT_SPEC 4 rolls them all)."""
    auto_dev(libpod)
    libpod.add(RUN, {
        "name": RUN, "idmappings": {"AutoUserNs": True},
        "labels": {"sloptown.tag": "old", "sloptown.sha": "a" * 40},
        "volumes": [{"Name": "vibe-pub-p1-aaaaaaa", "Dest": "/srv/app"}, {"Name": "vol", "Dest": "/data"}],
    })
    assert libpod.containers[RUN]["ids"] != libpod.containers[DEV]["ids"]
    orch = PodmanOrchestrator(BASE)

    async def healthy(*a, **k):
        return True

    monkeypatch.setattr(orch, "_healthy", healthy)
    assert await orch.roll_runtime(PID, "net", "vol", "seed") is True
    assert libpod.containers[RUN]["ids"] == libpod.containers[DEV]["ids"]


async def test_pool_slots_and_hostns_keep_their_modes(libpod, monkeypatch):
    from sloptown.config import get_settings
    from sloptown.orchestrator import hardened_spec

    ids = {"UidMap": ["0:2147500000:1024"], "GidMap": ["0:2147500000:1024"]}
    assert hardened_spec("n", "img", {}, {}, "net")["idmappings"] == {"AutoUserNs": True}
    spec = hardened_spec("n", "img", {}, {}, "net", ids)
    assert spec["idmappings"]["UIDMap"] == [{"container_id": 0, "host_id": 2147500000, "size": 1024}]
    monkeypatch.setattr(get_settings(), "userns_auto", False)
    assert "userns" not in hardened_spec("n", "img", {}, {}, "net", ids)
