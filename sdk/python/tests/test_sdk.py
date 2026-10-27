import asyncio
import json
import sqlite3

import httpx
import pytest
import respx
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import miira_vibe
from miira_vibe import BudgetExceeded, User, ai, current_user, members, require_role, setup
from miira_vibe.runtime import create_app

PLATFORM = "http://egress.test:8200"
EDGE = {"x-vibe-edge-secret": "s3cret", "x-vibe-user": "abc123", "x-vibe-name": "Ali%20Yilmaz",
        "x-vibe-role": "viewer", "x-vibe-classes": "7.A,Kor%20%C3%A6"}


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_EDGE_SECRET", "s3cret")
    monkeypatch.setenv("VIBE_MODE", "runtime")
    monkeypatch.setenv("VIBE_PLATFORM_URL", PLATFORM)
    monkeypatch.setenv("VIBE_PROJECT_TOKEN", "tok")
    monkeypatch.setenv("OPENAI_BASE_URL", PLATFORM + "/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "tok")
    monkeypatch.setenv("VIBE_DB_PATH", str(tmp_path / "app.sqlite"))
    members.invalidate()
    yield


def app_with_routes() -> FastAPI:
    app = setup(FastAPI())

    @app.get("/who")
    async def who(user: User = Depends(current_user)):
        return user.to_dict()

    @app.get("/edit")
    async def edit(user: User = Depends(require_role("editor"))):
        return {"ok": True}

    return app


MEMBERS = {"version": "v1", "members": [
    {"id": "abc123", "name": "Ali Yilmaz", "role": "viewer", "classes": ["7.A"], "avatarHue": 10},
    {"id": "own1", "name": "Mette Hansen", "role": "owner", "classes": [], "avatarHue": 200},
]}


def test_identity_from_edge_headers():
    with TestClient(app_with_routes()) as c:
        assert c.get("/who", headers=EDGE).json() == {
            "id": "abc123", "name": "Ali Yilmaz", "role": "viewer", "classes": ["7.A", "Kor æ"]}
        assert c.get("/edit", headers=EDGE).status_code == 403
        assert c.get("/edit", headers={**EDGE, "x-vibe-role": "owner"}).status_code == 200


def test_untrusted_headers_rejected_in_runtime():
    with TestClient(app_with_routes()) as c:
        assert c.get("/who", headers={**EDGE, "x-vibe-edge-secret": "wrong"}).status_code == 401
        assert c.get("/who").status_code == 401


def test_dev_mode_falls_back_to_dev_identity(monkeypatch):
    monkeypatch.setenv("VIBE_MODE", "dev")
    monkeypatch.setenv("VIBE_DEV_ROLE", "viewer")
    with TestClient(app_with_routes()) as c:
        assert c.get("/who").json()["id"] == "dev-viewer"
        # forged headers without the secret still don't win
        assert c.get("/who", headers={**EDGE, "x-vibe-edge-secret": "x"}).json()["id"] == "dev-viewer"


def test_require_role_rejects_unknown():
    with pytest.raises(ValueError):
        require_role("admin")  # type: ignore[arg-type]


@respx.mock
async def test_members_list_get_and_sync(tmp_path):
    route = respx.get(f"{PLATFORM}/platform/members").mock(return_value=httpx.Response(200, json=MEMBERS))
    assert [m.id for m in await members.list()] == ["abc123", "own1"]
    assert (await members.get("own1")).name == "Mette Hansen"
    assert route.calls.last.request.headers["authorization"] == "Bearer tok"
    assert route.call_count == 1  # cached

    assert await members.sync() == 2
    conn = sqlite3.connect(tmp_path / "app.sqlite")
    assert conn.execute("select count(*) from members where active=1").fetchone()[0] == 2

    members.invalidate()
    route.mock(return_value=httpx.Response(200, json={"version": "v2", "members": MEMBERS["members"][1:]}))
    assert await members.sync() == 1
    rows = dict(conn.execute("select id, active from members").fetchall())
    assert rows == {"abc123": 0, "own1": 1}


async def test_dev_members_without_platform(monkeypatch):
    monkeypatch.setenv("VIBE_MODE", "dev")
    monkeypatch.delenv("VIBE_PLATFORM_URL")
    assert any(m.role == "owner" for m in await members.list())


@respx.mock
async def test_ai_chat_and_budget():
    route = respx.post(f"{PLATFORM}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "Hej!"}}]}))
    assert await ai.chat("hej", user=User("abc123", "Ali", "viewer")) == "Hej!"
    req = route.calls.last.request
    assert req.headers["x-vibe-user"] == "abc123" and req.headers["authorization"] == "Bearer tok"
    assert json.loads(req.content)["messages"] == [{"role": "user", "content": "hej"}]

    route.mock(return_value=httpx.Response(429, json={"error": {"type": "budget_exceeded", "retry_at": "2026-11-02T00:00:00+01:00"}}))
    with pytest.raises(BudgetExceeded) as e:
        await ai.chat("hej")
    assert e.value.retry_at.isoformat() == "2026-11-02T00:00:00+01:00"


@respx.mock
async def test_ai_stream():
    sse = (
        'data: {"choices":[{"delta":{"content":"He"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"j"}}]}\n\n'
        'data: {"choices":[],"usage":{"total_tokens":3}}\n\n'
        "data: [DONE]\n\n"
    )
    route = respx.post(f"{PLATFORM}/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"}))
    assert "".join([d async for d in ai.stream("hej")]) == "Hej"
    assert json.loads(route.calls.last.request.content)["stream_options"] == {"include_usage": True}


@respx.mock
def test_router_endpoints():
    respx.get(f"{PLATFORM}/platform/members").mock(return_value=httpx.Response(200, json=MEMBERS))
    chat = respx.post(f"{PLATFORM}/v1/chat/completions")
    with TestClient(app_with_routes()) as c:
        assert c.get("/_vibe/me", headers=EDGE).json()["id"] == "abc123"
        assert c.get("/_vibe/members", headers=EDGE).json()["version"] == "v1"
        chat.mock(return_value=httpx.Response(200, json={"choices": [{"message": {"content": "svar"}}]}))
        assert c.post("/_vibe/ai/chat", headers=EDGE, json={"messages": [{"role": "user", "content": "x"}]}).json() == {"text": "svar"}
        chat.mock(return_value=httpx.Response(429, json={"error": {"type": "budget_exceeded", "retry_at": None}}))
        r = c.post("/_vibe/ai/chat", headers=EDGE, json={"messages": [], "stream": True})
        assert r.status_code == 429 and r.json()["error"] == "budget_exceeded"
        assert c.get("/_vibe/me").status_code == 401


async def test_watcher_dispatches_changes(monkeypatch):
    seen: list[str] = []

    @miira_vibe.on_members_changed
    async def cb(version: str) -> None:
        seen.append(version)

    body = 'event: ping\ndata: {}\n\nevent: members.changed\ndata: {"version": "v9"}\n\n'
    with respx.mock:
        respx.get(f"{PLATFORM}/platform/members/events").mock(
            return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}))
        sub = members.subscribe()
        nxt = asyncio.ensure_future(anext(sub))
        task = asyncio.create_task(members.watch_forever())
        assert await asyncio.wait_for(nxt, 2) == "v9"
        task.cancel()
        await sub.aclose()
    assert seen and seen[0] == "v9"
    members._callbacks.remove(cb)


def test_runtime_serves_api_spa_and_health(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "helper.py").write_text("GREETING = 'hej'\n")
    (tmp_path / "api" / "main.py").write_text(
        "from fastapi import FastAPI\nfrom helper import GREETING\nfrom miira_vibe import setup\n"
        "app = setup(FastAPI())\n@app.get('/hello')\ndef hello():\n    return {'msg': GREETING}\n")
    dist = tmp_path / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>")
    (dist / "assets" / "a.js").write_text("1")
    with TestClient(create_app(tmp_path)) as c:
        assert c.get("/healthz").json() == {"ok": True, "api": True, "web": True}
        assert c.get("/api/hello").json() == {"msg": "hej"}
        assert c.get("/api/_vibe/me", headers=EDGE).json()["id"] == "abc123"
        assert c.get("/some/client/route").text == "<div id=root></div>"
        assert c.get("/assets/a.js").text == "1"
        assert c.get("/assets/missing.js").status_code == 404


async def test_members_event_stream_closes_cleanly_on_disconnect():
    """A browser going away mid-wait must not crash the stream (aclose on a running generator)."""
    from miira_vibe import router as vibe_router
    from miira_vibe.identity import User

    r = vibe_router.build_router(ai_route=False)
    endpoint = next(rt.endpoint for rt in r.routes if rt.path == "/members/events")
    resp = await endpoint(user=User(id="m1", name="Mette", role="owner", classes=[]))
    it = resp.body_iterator
    assert "ready" in await anext(it)
    waiting = asyncio.create_task(anext(it))  # blocked until a change or the next ping
    await asyncio.sleep(0.05)
    waiting.cancel()  # what Starlette does when the client disconnects
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await it.aclose()
    assert not members._subscribers, "the subscription is released"
