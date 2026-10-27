"""Threads and turns: OpenCode event mapping, one turn per project + one queued, commit, stop, budget block."""

import asyncio
import json
from datetime import UTC, datetime

import httpx

from .a_support import bdb, db, dev_base, env, pytestmark  # noqa: F401

SID = "ses_1"


def ev(type_, **props):
    return f"data: {json.dumps({'type': type_, 'properties': props})}\n\n".encode()


class FakeOpenCode:
    """Mocks opencode serve + agentd for one project. Each prompt's event stream waits on a gate."""

    def __init__(self, env, pid):
        self.gates: list[asyncio.Event] = []
        self.prompts: list[dict] = []
        self.aborted = asyncio.Event()
        oc, ad = dev_base(pid, 4096), dev_base(pid, 4097)
        env.mock.post(f"{oc}/session").respond(json={"id": SID, "title": "x"})
        env.mock.patch(f"{oc}/session/{SID}").respond(json={"id": SID})
        env.mock.get(f"{oc}/event").mock(side_effect=self._events)
        env.mock.post(f"{oc}/session/{SID}/prompt_async").mock(side_effect=self._prompt)
        env.mock.post(f"{oc}/session/{SID}/abort").mock(side_effect=self._abort)
        self.commit = env.mock.post(f"{ad}/commit").respond(json={"sha": "c0ffee1", "files": ["web/src/App.tsx"]})
        env.mock.get(f"{oc}/session/{SID}/message").respond(json=[
            {"info": {"id": "m1", "sessionID": SID, "role": "user", "time": {"created": 1_700_000_000_000}},
             "parts": [{"id": "p1", "type": "text", "text": "Lav en quiz", "metadata": {"vibeAuthor": "Mette"}}]},
            {"info": {"id": "m2", "sessionID": SID, "role": "assistant", "time": {"created": 1_700_000_001_000}},
             "parts": [{"id": "p0", "type": "step-start"},
                       {"id": "p2", "type": "tool", "tool": "write", "callID": "c",
                        "state": {"status": "completed", "input": {"filePath": "a"}, "output": "ok", "title": "web/src/App.tsx", "metadata": {}}},
                       {"id": "p3", "type": "text", "text": "Færdig"}]},
        ])

    def _prompt(self, request):
        self.prompts.append(json.loads(request.content))
        return httpx.Response(204)

    def _abort(self, request):
        self.aborted.set()
        return httpx.Response(200, json=True)

    def _events(self, request):
        gate = asyncio.Event()
        self.gates.append(gate)
        aborted = self.aborted

        async def stream():
            yield ev("server.connected")
            yield ev("message.updated", info={"id": "other", "sessionID": "ses_other", "role": "assistant"})
            yield ev("message.updated", info={"id": "a1", "sessionID": SID, "role": "assistant", "time": {"created": 1}})
            yield ev("message.part.updated", part={"id": "t1", "messageID": "a1", "sessionID": SID, "type": "text", "text": "Jeg"})
            yield ev("message.part.updated", part={"id": "s1", "messageID": "a1", "sessionID": SID, "type": "step-start"})
            yield ev("message.part.updated", part={"id": "k1", "messageID": "a1", "sessionID": SID, "type": "tool", "tool": "edit", "callID": "c1",
                                                   "state": {"status": "running", "input": {}, "title": "App.tsx", "time": {"start": 1}}})
            done, _ = await asyncio.wait([asyncio.ensure_future(gate.wait()), asyncio.ensure_future(aborted.wait())],
                                         return_when=asyncio.FIRST_COMPLETED)
            if aborted.is_set():
                yield ev("session.error", sessionID=SID, error={"name": "MessageAbortedError", "data": {}})
            yield ev("message.part.updated", part={"id": "t1", "messageID": "a1", "sessionID": SID, "type": "text", "text": "Jeg er færdig"})
            yield ev("session.idle", sessionID=SID)

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream())


async def until(pred, timeout=3.0):
    end = asyncio.get_event_loop().time() + timeout
    while not pred():
        assert asyncio.get_event_loop().time() < end, "timed out"
        await asyncio.sleep(0.01)


async def setup(env, pid="t1"):
    owner = await env.user("s-t")
    await env.project(pid, "s-t", owner)
    oc = FakeOpenCode(env, pid)
    thread = (await env.client.post(f"/api/projects/{pid}/threads", json={"title": "Quiz"}, headers=owner)).json()
    return owner, oc, thread


async def test_turn_queue_commit_and_events(env):
    from sloptown.models import Project, Turn
    from sloptown.threads import turns
    from sqlalchemy import select

    owner, oc, thread = await setup(env)
    tid = thread["id"]
    r = await env.client.post(f"/api/threads/{tid}/messages", json={"text": "Lav en quiz"}, headers=owner)
    assert r.status_code == 202 and r.json()["queued"] is False
    await until(lambda: len(oc.gates) == 1 and env.events("message.part"))

    proj = await env.db.get(Project, "t1")
    await env.db.refresh(proj)
    assert proj.active_turn_sub == "s-t"
    assert oc.prompts[0]["parts"][0]["metadata"]["vibeAuthor"] == "Mette"

    r = await env.client.post(f"/api/threads/{tid}/messages", json={"text": "Gør den blå"}, headers=owner)
    assert r.json()["queued"] is True
    threads = (await env.client.get("/api/projects/t1/threads", headers=owner)).json()
    assert threads[0]["status"] == "running"
    r = await env.client.post(f"/api/threads/{tid}/messages", json={"text": "En til"}, headers=owner)
    assert r.status_code == 409 and r.json()["error"] == "queue_full"

    oc.gates[0].set()
    await until(lambda: len(oc.gates) == 2)
    oc.gates[1].set()
    await turns.wait_idle("t1")

    parts = env.events("message.part")
    assert {"type": "tool", "tool": "edit", "title": "App.tsx", "status": "running", "input": {}} in [p["part"] for p in parts]
    texts = [p for p in parts if p["part"]["type"] == "text"]
    assert all(p["index"] == 0 for p in texts) and texts[-1]["part"]["text"] == "Jeg er færdig"
    assert not any(m["id"] == "other" for m in env.events("message.created"))
    finished = env.events("turn.finished")
    assert [f["status"] for f in finished] == ["done", "done"] and finished[0]["commitSha"] == "c0ffee1"
    assert env.events("files.changed")[0]["paths"] == ["web/src/App.tsx"]
    body = json.loads(oc.commit.calls[0].request.content)
    assert body["trailers"] == {"Vibe-Thread": tid, "Vibe-Author": "Mette"} and body["message"] == "Lav en quiz"

    await env.db.refresh(proj)
    assert proj.active_turn_sub is None
    statuses = (await env.db.execute(select(Turn.status).order_by(Turn.created_at))).scalars().all()
    assert statuses == ["done", "done"]


async def test_stop_running_turn(env):
    from sloptown.threads import turns

    owner, oc, thread = await setup(env)
    await env.client.post(f"/api/threads/{thread['id']}/messages", json={"text": "Lav noget"}, headers=owner)
    await until(lambda: len(oc.gates) == 1)
    r = await env.client.post(f"/api/threads/{thread['id']}/stop", headers=owner)
    assert r.json() == {"stopped": True}
    await turns.wait_idle("t1")
    assert env.events("turn.finished")[-1]["status"] == "stopped"


async def test_budget_blocked(env, monkeypatch):
    from sloptown import budgets

    owner, oc, thread = await setup(env)
    retry = datetime(2026, 11, 2, tzinfo=UTC)

    async def blocked(db, sub, now=None):
        return False, retry

    monkeypatch.setattr(budgets, "check_build_allowed", blocked)
    r = await env.client.post(f"/api/threads/{thread['id']}/messages", json={"text": "x"}, headers=owner)
    assert r.status_code == 429 and r.json()["error"] == "budget_blocked" and r.json()["retryAt"].startswith("2026-11-02")


async def test_messages_mapping_and_permissions(env):
    owner, oc, thread = await setup(env)
    msgs = (await env.client.get(f"/api/threads/{thread['id']}/messages", headers=owner)).json()
    assert msgs[0]["role"] == "user" and msgs[0]["authorName"] == "Mette"
    assert msgs[1]["parts"] == [
        {"type": "tool", "tool": "write", "title": "web/src/App.tsx", "status": "done", "input": {"filePath": "a"}, "output": "ok"},
        {"type": "text", "text": "Færdig"},
    ]
    await env.share("t1", "s-v", "viewer")
    viewer = await env.user("s-v", role="elev")
    r = await env.client.post(f"/api/threads/{thread['id']}/messages", json={"text": "x"}, headers=viewer)
    assert r.status_code == 403


async def test_rename_archive_and_images(env):
    owner, oc, thread = await setup(env)
    r = await env.client.patch(f"/api/threads/{thread['id']}", json={"title": "Farver", "archived": True}, headers=owner)
    assert r.json()["title"] == "Farver" and r.json()["archived"] is True
    assert (await env.client.get("/api/projects/t1/threads", headers=owner)).json() == []
    assert len((await env.client.get("/api/projects/t1/threads?archived=1", headers=owner)).json()) == 1
    r = await env.client.post(f"/api/threads/{thread['id']}/messages", json={"text": "x"}, headers=owner)
    assert r.status_code == 409
    t2 = (await env.client.post("/api/projects/t1/threads", json={}, headers=owner)).json()
    bad = {"text": "se", "images": [{"mime": "image/png", "dataUrl": "https://x/y.png"}]}
    assert (await env.client.post(f"/api/threads/{t2['id']}/messages", json=bad, headers=owner)).status_code == 400


async def test_restore_blocked_while_running(env):
    from sloptown.threads import turns

    owner, oc, thread = await setup(env)
    env.mock.post(f"{dev_base('t1', 4097)}/restore").respond(json={"sha": "d00d", "files": ["a"], "message": "Gendan"})
    await env.client.post(f"/api/threads/{thread['id']}/messages", json={"text": "x"}, headers=owner)
    await until(lambda: len(oc.gates) == 1)
    r = await env.client.post("/api/projects/t1/restore", json={"sha": "abcdef1"}, headers=owner)
    assert r.status_code == 409
    oc.gates[0].set()
    await turns.wait_idle("t1")
    r = await env.client.post("/api/projects/t1/restore", json={"sha": "abcdef1"}, headers=owner)
    assert r.status_code == 200 and r.json()["sha"] == "d00d"
