"""Publish with the pre-publish check: clean code goes out at once; findings start an AI fix
turn, after which the new head is checked again and published, retried, or given up on."""

import io
import json
import tarfile

import httpx
from sqlalchemy import select

from .a_support import bdb, db, dev_base, env, pytestmark  # noqa: F401
from .test_turns import FakeOpenCode, until

LOOP = 'setInterval(() => fetch("/api/arena/state"), 100);\n'
CLEAN = 'setInterval(() => fetch("/api/arena/state"), 3000);\n'


def tar_of(app: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = app.encode()
        info = tarfile.TarInfo("web/src/App.tsx")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeAgentd:
    """/export serves the next scripted tree; /history says which sha is head."""

    def __init__(self, env, pid, head="a" * 40):
        self.trees: list[str] = []
        self.head = head
        self.exports: list[str] = []
        ad = dev_base(pid, 4097)
        env.mock.post(f"{ad}/export").mock(side_effect=self._export)
        env.mock.get(f"{ad}/history").mock(side_effect=lambda _r: httpx.Response(200, json={"commits": [{"sha": self.head}]}))

    def _export(self, request):
        sha = json.loads(request.content)["sha"]
        self.exports.append(sha)
        full = self.head if self.head.startswith(sha) else sha.ljust(40, "0")
        return httpx.Response(200, content=tar_of(self.trees.pop(0)), headers={"x-vibe-sha": full})


async def setup(env, pid="p1"):
    owner = await env.user("s-t")
    await env.project(pid, "s-t", owner)
    return owner, FakeOpenCode(env, pid), FakeAgentd(env, pid)


async def test_clean_code_publishes_at_once(env):
    owner, oc, ad = await setup(env)
    ad.trees = [CLEAN]
    r = await env.client.post("/api/projects/p1/publish", json={"sha": "a" * 7}, headers=owner)
    assert r.status_code == 200 and r.json()["publishedSha"] == "a" * 40
    assert env.orch.published["p1"]["sha"] == "a" * 40
    assert oc.prompts == [] and env.events("publish.checking") == []


async def test_findings_are_fixed_by_the_ai_then_published(env):
    from sloptown.models import AuditLog
    from sloptown.threads import turns

    owner, oc, ad = await setup(env)
    ad.trees = [LOOP, CLEAN]
    r = await env.client.post("/api/projects/p1/publish", json={"sha": "a" * 7}, headers=owner)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "fixing" and body["attempt"] == 1
    assert body["findings"] == [{"rule": "fast-poll", "title": "Appen spørger serveren for tit", "path": "web/src/App.tsx", "line": 1}]
    assert "p1" not in env.orch.published

    threads = (await env.client.get("/api/projects/p1/threads", headers=owner)).json()
    assert [t["title"] for t in threads] == ["Tjek før udgivelse"] and threads[0]["id"] == body["threadId"]
    await until(lambda: len(oc.gates) == 1)
    prompt = oc.prompts[0]["parts"][0]["text"]
    assert prompt.startswith("Tjek før udgivelse") and "web/src/App.tsx:1" in prompt
    assert env.events("publish.checking")[0]["turnId"] == body["turnId"]

    # While the AI works, the Vibe says so and a second publish is refused.
    assert (await env.client.get("/api/projects/p1", headers=owner)).json()["publishPending"] is True
    again = await env.client.post("/api/projects/p1/publish", json={"sha": "a" * 7}, headers=owner)
    assert again.status_code == 409 and again.json()["error"] == "publish_pending"

    ad.head = "b" * 40  # the fix turn's commit
    oc.gates[0].set()
    await turns.wait_idle("p1")
    assert env.events("project.published") == [{"publishedSha": "b" * 40, "url": env.events("project.published")[0]["url"]}]
    assert env.orch.published["p1"]["sha"] == "b" * 40
    assert ad.exports == ["a" * 7, "b" * 40]
    proj = (await env.client.get("/api/projects/p1", headers=owner)).json()
    assert proj["publishedSha"] == "b" * 40 and proj["publishPending"] is False
    actions = (await env.db.execute(select(AuditLog.action).where(AuditLog.project_id == "p1"))).scalars().all()
    assert "project.publish_check" in actions and "project.publish" in actions


async def test_gives_up_after_two_fix_attempts(env):
    from sloptown.threads import turns

    owner, oc, ad = await setup(env)
    ad.trees = [LOOP, LOOP, LOOP]
    assert (await env.client.post("/api/projects/p1/publish", json={"sha": "a" * 7}, headers=owner)).status_code == 202
    await until(lambda: len(oc.gates) == 1)
    oc.gates[0].set()
    await until(lambda: len(oc.gates) == 2)  # the second attempt runs by itself
    assert [c["attempt"] for c in env.events("publish.checking")] == [1, 2]
    oc.gates[1].set()
    await turns.wait_idle("p1")
    failed = env.events("publish.failed")
    assert len(failed) == 1 and failed[0]["error"] == "check_failed" and failed[0]["findings"][0]["rule"] == "fast-poll"
    assert "p1" not in env.orch.published


async def test_a_stopped_fix_stops_the_publish(env):
    from sloptown.threads import turns

    owner, oc, ad = await setup(env)
    ad.trees = [LOOP]
    body = (await env.client.post("/api/projects/p1/publish", json={"sha": "a" * 7}, headers=owner)).json()
    await until(lambda: len(oc.gates) == 1)
    assert (await env.client.post(f"/api/threads/{body['threadId']}/stop", headers=owner)).status_code == 200
    await turns.wait_idle("p1")
    assert env.events("publish.failed")[0]["error"] == "check_stopped"
    assert "p1" not in env.orch.published


async def test_an_older_version_with_findings_is_refused(env):
    owner, oc, ad = await setup(env)
    ad.trees = [LOOP]
    r = await env.client.post("/api/projects/p1/publish", json={"sha": "c" * 7}, headers=owner)
    assert r.status_code == 422 and r.json()["error"] == "check_failed"
    assert r.json()["findings"][0]["rule"] == "fast-poll"
    assert oc.prompts == [] and "p1" not in env.orch.published


async def test_the_fix_turn_goes_ahead_of_waiting_messages(env):
    from sloptown.models import Turn
    from sloptown.threads import turns

    owner, oc, ad = await setup(env)
    thread = (await env.client.post("/api/projects/p1/threads", json={"title": "Quiz"}, headers=owner)).json()
    await env.client.post(f"/api/threads/{thread['id']}/messages", json={"text": "Lav en quiz"}, headers=owner)
    await until(lambda: len(oc.gates) == 1)
    await env.client.post(f"/api/threads/{thread['id']}/messages", json={"text": "Gør den blå"}, headers=owner)

    ad.trees = [LOOP, CLEAN]
    r = await env.client.post("/api/projects/p1/publish", json={"sha": "a" * 7}, headers=owner)
    assert r.status_code == 202
    queue = (await env.client.get("/api/projects/p1/queue", headers=owner)).json()["items"]
    assert [q["text"][:18] for q in queue] == ["Tjek før udgivelse", "Gør den blå"]

    oc.gates[0].set()
    await until(lambda: len(oc.gates) == 2)
    oc.gates[1].set()  # the fix turn; publish follows, then the waiting message
    await until(lambda: len(oc.gates) == 3)
    assert env.orch.published["p1"]["sha"] == "a" * 40
    oc.gates[2].set()
    await turns.wait_idle("p1")
    statuses = (await env.db.execute(select(Turn.publish_attempt).order_by(Turn.started_at))).scalars().all()
    assert statuses == [None, 1, None]
