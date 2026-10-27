"""Egress proxy: auth, inference metering (stream + non-stream), nerf/block, platform relay,
throttles, and the raw CONNECT proxy."""

import asyncio
import base64
import json

import httpx
import pytest_asyncio
import respx
from sqlalchemy import select

from sloptown.egress import app as egress
from sloptown.egress.config import EgressConfig, host_allowed
from sloptown.egress.connect import start_connect_proxy
from sloptown.egress.limits import Limiter
from sloptown.models import UsageEvent

from .b_support import bdb, db, enable_budgets, make_project, make_user, pytestmark  # noqa: F401

OPENAI = "https://openai.test/v1"


@pytest_asyncio.fixture(loop_scope="session")
async def setup(db):
    await make_user(db, "owner")
    await make_user(db, "turn-author")
    await make_project(db, "p1", "owner", dev_token="dev-tok", run_token="run-tok", active_turn_sub="turn-author")
    await db.commit()
    egress.configure(EgressConfig())
    egress.state.client = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=egress.app), base_url="http://egress") as c:
        yield c


def auth(tok: str = "dev-tok") -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def usage_rows(db) -> list[UsageEvent]:
    db.expire_all()
    return list((await db.execute(select(UsageEvent).order_by(UsageEvent.id))).scalars())


async def test_rejects_unknown_token(setup):
    r = await setup.post("/v1/chat/completions", json={"model": "x"}, headers=auth("nope"))
    assert r.status_code == 401
    r = await setup.post("/v1/chat/completions", json={"model": "x"})
    assert r.status_code == 401



async def test_token_valid_as_soon_as_pool_slot_is_claimed(setup, db):
    """A rejected token (unclaimed pool container) must not stay rejected once its project exists."""
    r = await setup.get("/platform/members", headers=auth("claimed-later"))
    assert r.status_code == 401
    await make_project(db, "p-claimed", "owner", dev_token="claimed-later", run_token="claimed-later-run")
    await db.commit()
    with respx.mock:
        respx.post(f"{OPENAI}/chat/completions").respond(json={"id": "c", "choices": [], "usage": {}})
        r = await setup.post("/v1/chat/completions", json={"model": "gpt-5.1"}, headers=auth("claimed-later"))
    assert r.status_code == 200, r.text

async def test_unlisted_path_is_404(setup):
    r = await setup.post("/v1/files", json={}, headers=auth())
    assert r.status_code == 404


@respx.mock
async def test_non_stream_injects_key_and_meters_build_bucket(setup, db):
    route = respx.post(f"{OPENAI}/chat/completions").respond(
        json={"id": "c1", "choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
    )
    r = await setup.post("/v1/chat/completions", json={"model": "gpt-4.1", "messages": []}, headers=auth())
    assert r.status_code == 200
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer sk-real-key"
    rows = await usage_rows(db)
    assert len(rows) == 1
    ev = rows[0]
    assert (ev.bucket, ev.user_sub, ev.input_tokens, ev.output_tokens, ev.model) == ("build", "turn-author", 11, 7, "gpt-4.1")


@respx.mock
async def test_stream_chat_forces_include_usage_and_meters(setup, db):
    sse = (
        b'data: {"choices":[{"delta":{"content":"Hej"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3}}\n\n'
        b"data: [DONE]\n\n"
    )
    route = respx.post(f"{OPENAI}/chat/completions").respond(content=sse, headers={"content-type": "text/event-stream"})
    r = await setup.post("/v1/chat/completions", json={"model": "gpt-4.1", "stream": True}, headers=auth())
    assert r.status_code == 200
    assert r.content == sse
    body = json.loads(route.calls.last.request.content)
    assert body["stream_options"] == {"include_usage": True}
    rows = await usage_rows(db)
    assert [(e.input_tokens, e.output_tokens) for e in rows] == [(5, 3)]


@respx.mock
async def test_stream_responses_api_usage(setup, db):
    sse = (
        b"event: response.output_text.delta\n"
        b'data: {"type":"response.output_text.delta","delta":"Hej"}\n\n'
        b"event: response.completed\n"
        b'data: {"type":"response.completed","response":{"usage":{"input_tokens":20,"output_tokens":9}}}\n\n'
    )
    respx.post(f"{OPENAI}/responses").respond(content=sse, headers={"content-type": "text/event-stream"})
    r = await setup.post("/v1/responses", json={"model": "gpt-4.1", "stream": True}, headers=auth())
    assert r.status_code == 200
    rows = await usage_rows(db)
    assert [(e.input_tokens, e.output_tokens) for e in rows] == [(20, 9)]


@respx.mock
async def test_runtime_token_charges_app_bucket_with_member(setup, db):
    respx.post(f"{OPENAI}/chat/completions").respond(json={"usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    r = await setup.post("/v1/chat/completions", json={"model": "gpt-4.1"}, headers={**auth("run-tok"), "X-Vibe-User": "member-9"})
    assert r.status_code == 200
    ev = (await usage_rows(db))[0]
    assert (ev.bucket, ev.user_sub, ev.member_id, ev.project_id) == ("app", None, "member-9", "p1")


@respx.mock
async def test_nerf_rewrites_model(setup, db):
    await enable_budgets(db, build_nerf=10, build_hard=1000)
    await db.commit()
    route = respx.post(f"{OPENAI}/chat/completions").respond(json={"usage": {"prompt_tokens": 50, "completion_tokens": 0}})
    await setup.post("/v1/chat/completions", json={"model": "gpt-4.1"}, headers=auth())  # 50 tokens -> past nerf
    r = await setup.post("/v1/chat/completions", json={"model": "gpt-4.1"}, headers=auth())
    assert r.headers["x-vibe-nerfed"] == "gpt-6-luna"
    assert json.loads(route.calls.last.request.content)["model"] == "gpt-6-luna"
    ev = (await usage_rows(db))[-1]
    assert ev.nerfed and ev.model == "gpt-6-luna" and ev.requested_model == "gpt-4.1"


@respx.mock
async def test_block_returns_429_without_calling_upstream(setup, db):
    await enable_budgets(db, app_hard=10)
    await db.commit()
    route = respx.post(f"{OPENAI}/chat/completions").respond(json={"usage": {"prompt_tokens": 20, "completion_tokens": 0}})
    await setup.post("/v1/chat/completions", json={"model": "gpt-4.1"}, headers=auth("run-tok"))
    r = await setup.post("/v1/chat/completions", json={"model": "gpt-4.1"}, headers=auth("run-tok"))
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["type"] == "budget_exceeded" and err["retry_at"] and err["bucket"] == "app"
    assert route.call_count == 1


@respx.mock
async def test_platform_forwarding(setup):
    route = respx.get("http://cp.test/platform/members").respond(json={"version": "v1", "members": []})
    r = await setup.get("/platform/members", headers={**auth("run-tok"), "X-Vibe-User": "m1"})
    assert r.status_code == 200 and r.json()["version"] == "v1"
    h = route.calls.last.request.headers
    assert h["x-vibe-project"] == "p1"
    assert h["x-vibe-token-kind"] == "runtime"
    assert h["x-sloptown-internal"] == "internal-test"
    assert h["x-vibe-user"] == "m1"
    assert "authorization" not in h


@respx.mock
async def test_inference_rpm_and_body_limit(setup):
    egress.configure(EgressConfig(inference_rpm_per_project=2, max_body_bytes=200))
    respx.post(f"{OPENAI}/chat/completions").respond(json={"usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    codes = [(await setup.post("/v1/chat/completions", json={"model": "m"}, headers=auth())).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = await setup.post("/v1/chat/completions", json={"model": "m", "pad": "x" * 500}, headers=auth())
    assert r.status_code == 413
    egress.configure(EgressConfig())


async def test_rate_bucket():
    lim = Limiter(EgressConfig(rps_per_project=0.0001, burst_per_project=2))
    lim.check_rate("p")
    lim.check_rate("p")
    try:
        lim.check_rate("p")
        raise AssertionError("expected Throttled")
    except Exception as exc:
        assert exc.__class__.__name__ == "Throttled"


async def test_host_allowed_patterns():
    allow = ["api.miira.dk", "*.example.com:443"]
    assert host_allowed(allow, "api.miira.dk", 443)
    assert host_allowed(allow, "a.example.com", 443)
    assert not host_allowed(allow, "a.example.com", 80)
    assert not host_allowed(allow, "example.com", 443)
    assert not host_allowed(allow, "evil.com", 443)


# --- CONNECT proxy ----------------------------------------------------------------------------


async def _proxy_request(port: int, head: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(head)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(4096), 5)
    return data, reader, writer


async def test_connect_proxy(setup):
    async def echo(r, w):
        data = await r.read(100)
        w.write(b"echo:" + data)
        await w.drain()
        w.close()

    target = await asyncio.start_server(echo, "127.0.0.1", 0)
    tport = target.sockets[0].getsockname()[1]
    cfg = EgressConfig(allowlist=["127.0.0.1"], timeout_seconds=5)
    proxy = await start_connect_proxy("127.0.0.1", 0, cfg, Limiter(cfg))
    pport = proxy.sockets[0].getsockname()[1]
    basic = base64.b64encode(b"vibe:dev-tok").decode()
    try:
        data, _, w = await _proxy_request(pport, f"CONNECT 127.0.0.1:{tport} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert data.startswith(b"HTTP/1.1 407")
        w.close()

        data, r, w = await _proxy_request(pport, f"CONNECT 127.0.0.1:{tport} HTTP/1.1\r\nProxy-Authorization: Basic {basic}\r\n\r\n".encode())
        assert data.startswith(b"HTTP/1.1 200")
        w.write(b"ping")
        await w.drain()
        assert await asyncio.wait_for(r.read(100), 5) == b"echo:ping"
        w.close()

        data, _, w = await _proxy_request(pport, f"CONNECT evil.test:443 HTTP/1.1\r\nProxy-Authorization: Basic {basic}\r\n\r\n".encode())
        assert data.startswith(b"HTTP/1.1 403")
        w.close()

        data, _, w = await _proxy_request(pport, f"GET http://127.0.0.1:{tport}/x HTTP/1.1\r\nHost: 127.0.0.1\r\nProxy-Authorization: Bearer dev-tok\r\n\r\n".encode())
        assert data.startswith(b"echo:GET /x HTTP/1.1")
        assert b"Proxy-Authorization" not in data
        w.close()
    finally:
        proxy.close()
        target.close()
