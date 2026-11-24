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
async def test_records_reasoning_tokens_and_duration(setup, db):
    """Slow answers are usually hidden reasoning; both forms of usage report it."""
    respx.post(f"{OPENAI}/chat/completions").respond(json={"usage": {
        "prompt_tokens": 10, "completion_tokens": 40, "completion_tokens_details": {"reasoning_tokens": 30}}})
    r = await setup.post("/v1/chat/completions", json={"model": "gpt-4.1"}, headers=auth())
    assert r.status_code == 200
    sse = (b'data: {"type":"response.completed","response":{"usage":{"input_tokens":2,"output_tokens":9,'
           b'"output_tokens_details":{"reasoning_tokens":4}}}}\n\n')
    respx.post(f"{OPENAI}/responses").respond(content=sse, headers={"content-type": "text/event-stream"})
    r = await setup.post("/v1/responses", json={"model": "gpt-4.1", "stream": True}, headers=auth())
    assert r.status_code == 200
    rows = sorted(await usage_rows(db), key=lambda e: e.id)
    assert [(e.output_tokens, e.reasoning_tokens) for e in rows] == [(40, 30), (9, 4)]
    assert all(isinstance(e.duration_ms, int) and e.duration_ms >= 0 for e in rows)


@respx.mock
async def test_reasoning_tokens_missing_is_null(setup, db):
    respx.post(f"{OPENAI}/chat/completions").respond(json={"usage": {"prompt_tokens": 1, "completion_tokens": 2}})
    await setup.post("/v1/chat/completions", json={"model": "gpt-4.1"}, headers=auth())
    rows = await usage_rows(db)
    assert [e.reasoning_tokens for e in rows] == [None]


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
async def test_platform_paths_cannot_leave_platform(setup):
    """httpx collapses dot segments, so a decoded `..` would reach /internal/* with the
    internal secret attached. Such paths stop at the egress."""
    route = respx.route(host="cp.test").respond(200)
    for raw in ["/platform/..%2Finternal%2Fimages%2Frolled", "/platform/%2E%2E/internal/x",
                "/platform/members/..%2F..%2Fadmin%2Fv1", "/platform/members%252F..", "/platform/a//b"]:
        r = await setup.post(raw, headers=auth("run-tok"))
        assert r.status_code == 404, raw
    assert not route.called


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


async def test_per_host_minute_limits():
    """One Vibe can't flood an external host, and all Vibes together can't either."""
    lim = Limiter(EgressConfig(host_rpm_per_project=2, host_rpm_global=3))
    lim.check_host("p1", "api.example.com")
    lim.check_host("p1", "API.example.com.")
    reasons = []
    for project in ("p1", "p2", "p3"):
        try:
            lim.check_host(project, "api.example.com")
        except Exception as exc:
            reasons.append(exc.reason)
    assert reasons == ["host_rpm", "host_rpm_global"]
    lim.check_host("p3", "other.example.com")


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


# --- web search (/search/web -> Brave) ------------------------------------------------

BRAVE = "https://brave.test/res/v1/web/search"
BRAVE_REPLY = {
    "type": "search",
    "web": {
        "results": [
            {"title": "<strong>Brøker</strong> &amp; procent", "url": "https://emu.dk/brok",
             "description": "Om <strong>brøker</strong> i 5. klasse", "age": "3. mar. 2025", "profile": {"x": 1}},
            {"title": "no url"},
            {"title": "js", "url": "javascript:alert(1)", "description": "x"},
            {"title": "Plain", "url": "http://example.com/", "description": None},
        ]
    },
    "news": {"results": [{"url": "https://ignored.test"}]},
}


@pytest_asyncio.fixture(loop_scope="session")
async def search(setup):
    from sloptown.config import get_settings

    s = get_settings()
    saved = (s.brave_api_key, s.brave_search_url)
    s.brave_api_key, s.brave_search_url = "brave-key", BRAVE
    egress.configure(EgressConfig())
    yield setup
    s.brave_api_key, s.brave_search_url = saved
    egress.configure(EgressConfig())


@respx.mock
async def test_search_injects_key_and_trims_results(search):
    route = respx.get(BRAVE).respond(json=BRAVE_REPLY)
    r = await search.get("/search/web", params={"q": "  brøker   5. klasse ", "count": "5", "search_lang": "da", "country": "DK"},
                         headers=auth())
    assert r.status_code == 200, r.text
    assert r.json() == {
        "query": "brøker 5. klasse",
        "results": [
            {"title": "Brøker & procent", "url": "https://emu.dk/brok", "description": "Om brøker i 5. klasse", "age": "3. mar. 2025"},
            {"title": "Plain", "url": "http://example.com/", "description": ""},
        ],
    }
    sent = route.calls.last.request
    assert sent.headers["x-subscription-token"] == "brave-key"
    assert "authorization" not in sent.headers  # the project token never leaves
    assert dict(sent.url.params) == {"q": "brøker 5. klasse", "count": "5", "offset": "0", "safesearch": "strict",
                                     "text_decorations": "false", "search_lang": "da", "country": "DK"}


async def test_search_needs_a_project_token(search):
    assert (await search.get("/search/web", params={"q": "x"})).status_code == 401
    assert (await search.get("/search/web", params={"q": "x"}, headers=auth("nope"))).status_code == 401


@respx.mock
async def test_agent_search_is_not_metered(search, db):
    respx.get(BRAVE).respond(json=BRAVE_REPLY)
    assert (await search.get("/search/web", params={"q": "x"}, headers=auth())).status_code == 200
    assert await usage_rows(db) == []


@respx.mock
async def test_app_search_is_metered_on_the_app_budget(search, db):
    egress.configure(EgressConfig(search_tokens_per_call=1500))
    respx.get(BRAVE).respond(json=BRAVE_REPLY)
    r = await search.get("/search/web", params={"q": "nyheder om klima"}, headers={**auth("run-tok"), "X-Vibe-User": "member-7"})
    assert r.status_code == 200, r.text
    assert len(r.json()["results"]) == 2
    [ev] = await usage_rows(db)
    assert (ev.bucket, ev.project_id, ev.member_id, ev.user_sub) == ("app", "p1", "member-7", None)
    assert (ev.model, ev.input_tokens, ev.output_tokens, ev.nerfed) == ("brave-search", 1500, 0, False)


@respx.mock
async def test_app_search_blocked_by_app_budget(search, db):
    await enable_budgets(db, app_hard=1000)
    await db.commit()
    route = respx.get(BRAVE).respond(json=BRAVE_REPLY)
    assert (await search.get("/search/web", params={"q": "x"}, headers=auth("run-tok"))).status_code == 200  # 2000 > 1000
    r = await search.get("/search/web", params={"q": "x"}, headers=auth("run-tok"))
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["type"] == "budget_exceeded" and err["bucket"] == "app" and err["retry_at"]
    assert route.call_count == 1
    # the build agent's searches don't depend on the app budget
    assert (await search.get("/search/web", params={"q": "x"}, headers=auth())).status_code == 200


@respx.mock
async def test_app_search_has_its_own_minute_limit(search):
    egress.configure(EgressConfig(search_rpm_per_project=1, search_rpm_per_app=2, search_rpm_global=10))
    respx.get(BRAVE).respond(json={"web": {"results": []}})
    app = [(await search.get("/search/web", params={"q": "x"}, headers=auth("run-tok"))).status_code for _ in range(3)]
    agent = [(await search.get("/search/web", params={"q": "x"}, headers=auth())).status_code for _ in range(2)]
    assert app == [200, 200, 429] and agent == [200, 429]


async def test_app_search_can_be_switched_off(search):
    from sloptown.config import get_settings

    get_settings().app_search_enabled = False
    try:
        r = await search.get("/search/web", params={"q": "x"}, headers=auth("run-tok"))
        assert r.status_code == 403 and r.json()["error"]["type"] == "forbidden"
    finally:
        get_settings().app_search_enabled = True


async def test_search_validates_parameters(search):
    bad = [{}, {"q": "   "}, {"q": "x" * 401}, {"q": " ".join(["o"] * 51)}, {"q": "x", "count": "21"},
           {"q": "x", "count": "0"}, {"q": "x", "offset": "10"}, {"q": "x", "count": "-1"},
           {"q": "x", "search_lang": "da&x=1"}, {"q": "x", "country": "dk"}, {"q": "x", "freshness": "yesterday"}]
    for params in bad:
        r = await search.get("/search/web", params=params, headers=auth())
        assert r.status_code == 400, params
        assert r.json()["error"]["type"] == "invalid_query"


async def test_search_off_without_key(setup):
    r = await setup.get("/search/web", params={"q": "x"}, headers=auth())
    assert r.status_code == 503
    assert r.json()["error"]["type"] == "search_unavailable"


@respx.mock
async def test_search_throttles_per_project(search):
    egress.configure(EgressConfig(search_rpm_per_project=2, search_rpm_global=10))
    respx.get(BRAVE).respond(json={"web": {"results": []}})
    codes = [(await search.get("/search/web", params={"q": "x"}, headers=auth())).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = await search.get("/search/web", params={"q": "x"}, headers=auth())
    assert r.json()["error"]["reason"] == "search_rpm"
    assert r.headers["retry-after"] == "60"


@respx.mock
async def test_search_upstream_errors(search):
    respx.get(BRAVE).mock(side_effect=[
        httpx.Response(429, json={}),
        httpx.Response(401, json={"error": "bad token"}),
        httpx.Response(200, text="not json"),
        httpx.ConnectError("down"),
    ])
    got = []
    for _ in range(4):
        r = await search.get("/search/web", params={"q": "x"}, headers=auth())
        got.append((r.status_code, r.json()["error"]["type"]))
    assert got == [(429, "rate_limited"), (502, "upstream_error"), (502, "upstream_error"), (502, "upstream_error")]
    assert egress.state.limiter._open["p1"] == 0  # every slot was released


async def test_search_query_never_reaches_the_access_log():
    import logging

    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("10.0.0.2:1", "GET", "/search/web?q=elevens+navn&count=3", "1.1", 200), None)
    assert egress.RedactSearchQuery().filter(rec)
    assert rec.getMessage() == '10.0.0.2:1 - "GET /search/web HTTP/1.1" 200'
    other = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                              ("10.0.0.2:1", "GET", "/platform/members?x=1", "1.1", 200), None)
    egress.RedactSearchQuery().filter(other)
    assert "x=1" in other.getMessage()
    assert any(isinstance(f, egress.RedactSearchQuery) for f in logging.getLogger("uvicorn.access").filters)


async def test_search_limiter_global():
    lim = Limiter(EgressConfig(search_rpm_per_project=5, search_rpm_global=2))
    lim.check_search("p1")
    lim.check_search("p2")
    try:
        lim.check_search("p3")
        raise AssertionError("expected Throttled")
    except Exception as exc:
        assert exc.reason == "search_rpm_global"
