"""Egress + inference proxy (`sloptown.egress.app:app`, port 8200).

The only way out of a project container. Every request carries the project's
bearer token (VIBE_PROJECT_TOKEN), which identifies the project and whether the
caller is the dev container (build bucket) or the published runtime (app bucket).

- /v1/*        OpenAI-compatible reverse proxy. Injects the shared key, applies the
               weekly budget (nerf = swap model, block = 429), meters usage_events.
- /platform/*  forwarded to the control plane with X-Vibe-Project/X-Vibe-Token-Kind
               and the internal secret. Streams (SSE) pass through.
- /search/web  web search (Brave Search API with the shared key) for the build agent
               and, unless app_search_enabled is off, published apps (metered on the
               app budget); results trimmed to title/url/description/age.
- /health      liveness.

The raw CONNECT/forward proxy for allowlisted hosts runs beside this app on
EGRESS_PROXY_PORT (see connect.py) and is started from the lifespan.
"""

import html
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .. import budgets, notify
from ..config import get_settings
from ..db import sessionmaker
from ..models import Project
from .auth import Caller, bearer, resolve_token
from .config import EgressConfig
from .connect import start_connect_proxy
from .limits import Limiter, Throttled

log = logging.getLogger("sloptown.egress")


class RedactSearchQuery(logging.Filter):
    """uvicorn's access log prints the full URL; a search's query string is the teacher's
    work and must not reach the logs. Args are (client, method, path, http_version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and args[2].startswith("/search/"):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(RedactSearchQuery())

INFERENCE_PATHS = {"chat/completions", "responses", "embeddings", "models"}
METERED_PATHS = {"chat/completions", "responses", "embeddings"}
PASS_REQUEST_HEADERS = {"content-type", "accept", "openai-beta"}
PASS_RESPONSE_HEADERS = {"content-type", "x-request-id", "openai-processing-ms", "cache-control", "retry-after"}
PLATFORM_REQUEST_HEADERS = {"content-type", "accept", "last-event-id", "x-vibe-user", "cache-control"}


def platform_path(path: str) -> str | None:
    """The path below /platform/ as the control plane must see it, or None.

    The route parameter is already percent-decoded, and httpx collapses dot segments,
    so `/platform/..%2Finternal/x` would otherwise reach `/internal/x` carrying the
    internal secret. Only plain segments pass; a `%` left after decoding means the
    caller encoded twice, which no SDK call does.
    """
    if not path or "%" in path or "\\" in path:
        return None
    segments = path.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        return None
    return "/".join(quote(seg, safe="") for seg in segments)


class State:
    cfg: EgressConfig
    limiter: Limiter
    client: httpx.AsyncClient | None = None


state = State()


def configure(cfg: EgressConfig | None = None) -> None:
    """(Re)load throttling config. Tests call this with a custom config."""
    state.cfg = cfg or EgressConfig.load()
    state.limiter = Limiter(state.cfg)


configure()


def client() -> httpx.AsyncClient:
    if state.client is None:
        state.client = httpx.AsyncClient(timeout=httpx.Timeout(state.cfg.timeout_seconds))
    return state.client


@asynccontextmanager
async def lifespan(_: FastAPI):
    server = None
    if state.cfg.proxy_port:
        server = await start_connect_proxy("0.0.0.0", state.cfg.proxy_port, state.cfg, state.limiter)
    try:
        yield
    finally:
        if server is not None:
            server.close()
        if state.client is not None:
            await state.client.aclose()
            state.client = None


app = FastAPI(title="SlopTown egress", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _error(status: int, type_: str, message: str, **extra: Any) -> JSONResponse:
    headers = {}
    if "retry_after" in extra:
        headers["Retry-After"] = str(extra.pop("retry_after"))
    return JSONResponse({"error": {"type": type_, "message": message, **extra}}, status_code=status, headers=headers)


def _throttled(t: Throttled) -> JSONResponse:
    wait = 60 if t.reason.startswith("inference") else 1
    return _error(429, "rate_limited", "For mange forespørgsler. Prøv igen om lidt.", reason=t.reason, retry_after=wait)


class BodyTooLarge(Exception):
    pass


async def _read_body(request: Request) -> bytes:
    limit = state.cfg.max_body_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise BodyTooLarge
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise BodyTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


async def _authenticate(request: Request) -> Caller | None:
    return await resolve_token(bearer(request.headers.get("authorization")))


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


# --- inference -------------------------------------------------------------------


def _usage_tokens(usage: dict[str, Any] | None) -> tuple[int, int] | None:
    if not isinstance(usage, dict):
        return None
    inp = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    out = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    return int(inp), int(out)


def _reasoning_tokens(usage: dict[str, Any] | None) -> int | None:
    """Reasoning share of the output (chat: completion_tokens_details, responses: output_tokens_details)."""
    if not isinstance(usage, dict):
        return None
    details = usage.get("completion_tokens_details") or usage.get("output_tokens_details")
    value = details.get("reasoning_tokens") if isinstance(details, dict) else None
    return int(value) if isinstance(value, int) else None


def _usage_block(obj: Any) -> dict[str, Any] | None:
    if not isinstance(obj, dict):
        return None
    if obj.get("type") == "response.completed":
        usage = (obj.get("response") or {}).get("usage")
    else:
        usage = obj.get("usage")
    return usage if isinstance(usage, dict) and usage else None


def _usage_from_event(obj: Any) -> tuple[int, int] | None:
    return _usage_tokens(_usage_block(obj))


class SseUsageScanner:
    """Feeds raw SSE bytes, remembers the last usage block seen."""

    def __init__(self) -> None:
        self.buf = b""
        self.usage: tuple[int, int] | None = None
        self.reasoning: int | None = None

    def feed(self, chunk: bytes) -> None:
        self.buf += chunk
        *lines, self.buf = self.buf.split(b"\n")
        for line in lines:
            self._line(line)

    def close(self) -> None:
        if self.buf:
            self._line(self.buf)
            self.buf = b""

    def _line(self, line: bytes) -> None:
        line = line.strip()
        if not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            return
        try:
            block = _usage_block(json.loads(data))
        except ValueError:
            return
        if block:
            self.usage = _usage_tokens(block)
            self.reasoning = _reasoning_tokens(block)


async def _attribution(caller: Caller, request: Request) -> tuple[str | None, str | None]:
    """(user_sub, member_id) to charge: build -> turn author (else owner), app -> member."""
    if caller.bucket == "app":
        return None, request.headers.get("x-vibe-user") or None
    async with sessionmaker()() as db:
        project = await db.get(Project, caller.project_id)
    if project is None:
        return None, None
    return project.active_turn_sub or project.owner_sub, None


async def _record(caller: Caller, user_sub: str | None, member_id: str | None, model: str, requested: str, nerfed: bool, usage: tuple[int, int] | None,
                  reasoning: int | None = None, duration_ms: int | None = None) -> None:
    if usage is None:
        log.warning("no usage reported for project=%s model=%s", caller.project_id, model)
        return
    try:
        async with sessionmaker()() as db:
            await budgets.record_usage(
                db,
                project_id=caller.project_id,
                bucket=caller.bucket,
                user_sub=user_sub,
                member_id=member_id,
                model=model,
                requested_model=requested,
                input_tokens=usage[0],
                output_tokens=usage[1],
                nerfed=nerfed,
                reasoning_tokens=reasoning,
                duration_ms=duration_ms,
            )
            await db.commit()
    except Exception:  # never fail the user's response because metering failed
        log.exception("failed to record usage for project=%s", caller.project_id)
        return
    try:  # separate transaction: a notification problem must not lose the usage row
        async with sessionmaker()() as db:
            notices = await notify.check_after_usage(
                db, bucket=caller.bucket, project_id=caller.project_id, user_sub=user_sub
            )
            await db.commit()
        notify.send_later(notices)
    except Exception:
        log.exception("budget notification check failed for project=%s", caller.project_id)


@app.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def inference(path: str, request: Request) -> Response:
    caller = await _authenticate(request)
    if caller is None:
        return _error(401, "invalid_token", "Ugyldig projekt-token.")
    if path not in INFERENCE_PATHS:
        return _error(404, "not_found", f"/v1/{path} er ikke tilladt.")
    try:
        body = await _read_body(request)
        state.limiter.check_rate(caller.project_id)
    except BodyTooLarge:
        return _error(413, "body_too_large", "Forespørgslen er for stor.")
    except Throttled as t:
        return _throttled(t)

    metered = request.method == "POST" and path in METERED_PATHS
    user_sub = member_id = None
    requested = model = ""
    nerfed = False
    if metered:
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            return _error(400, "invalid_json", "Body er ikke gyldig JSON.")
        try:
            state.limiter.check_inference(caller.project_id)
        except Throttled as t:
            return _throttled(t)
        requested = model = str(payload.get("model") or "")
        user_sub, member_id = await _attribution(caller, request)
        async with sessionmaker()() as db:
            decision = await budgets.decide(db, caller.bucket, project_id=caller.project_id, user_sub=user_sub, requested_model=requested)
            await db.commit()  # settings may have been seeded
        if decision.blocked:
            retry_at = decision.retry_at.isoformat() if decision.retry_at else None
            msg = "Ugens AI-budget er brugt op. Det nulstilles mandag." if caller.bucket == "build" else "Appens AI-budget for denne uge er brugt op."
            return _error(429, "budget_exceeded", msg, retry_at=retry_at, bucket=caller.bucket)
        if decision.nerfed:
            payload["model"] = model = decision.model
            nerfed = True
        if payload.get("stream") is True and path == "chat/completions":
            payload.setdefault("stream_options", {})["include_usage"] = True
        body = json.dumps(payload).encode()

    settings = get_settings()
    headers = {k: v for k, v in request.headers.items() if k.lower() in PASS_REQUEST_HEADERS}
    headers["authorization"] = f"Bearer {settings.openai_api_key}"
    headers["accept-encoding"] = "identity"
    url = settings.openai_base_url.rstrip("/") + "/" + path
    upstream_req = client().build_request(request.method, url, params=request.query_params, content=body if body else None, headers=headers)

    try:
        state.limiter.acquire(caller.project_id)
    except Throttled as t:
        return _throttled(t)
    started = time.monotonic()
    try:
        upstream = await client().send(upstream_req, stream=True)
    except httpx.HTTPError as exc:
        state.limiter.release(caller.project_id)
        log.warning("upstream error: %s", exc)
        return _error(502, "upstream_error", "AI-tjenesten svarede ikke.")

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() in PASS_RESPONSE_HEADERS}
    if nerfed:
        resp_headers["x-vibe-nerfed"] = model
    is_stream = upstream.headers.get("content-type", "").startswith("text/event-stream")

    if not is_stream:
        try:
            content = await upstream.aread()
        finally:
            await upstream.aclose()
            state.limiter.release(caller.project_id)
        if metered and upstream.status_code < 400:
            elapsed = int((time.monotonic() - started) * 1000)
            try:
                block = json.loads(content).get("usage")
                usage, reasoning = _usage_tokens(block), _reasoning_tokens(block)
            except (ValueError, AttributeError):
                usage, reasoning = None, None
            await _record(caller, user_sub, member_id, model, requested, nerfed, usage, reasoning, elapsed)
        return Response(content, status_code=upstream.status_code, headers=resp_headers)

    scanner = SseUsageScanner()

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                if metered:
                    scanner.feed(chunk)
                yield chunk
        finally:
            await upstream.aclose()
            state.limiter.release(caller.project_id)
            if metered and upstream.status_code < 400:
                scanner.close()
                elapsed = int((time.monotonic() - started) * 1000)
                await _record(caller, user_sub, member_id, model, requested, nerfed, scanner.usage, scanner.reasoning, elapsed)

    return StreamingResponse(relay(), status_code=upstream.status_code, headers=resp_headers)


# --- platform API ------------------------------------------------------------------


@app.api_route("/platform/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def platform(path: str, request: Request) -> Response:
    caller = await _authenticate(request)
    if caller is None:
        return _error(401, "invalid_token", "Ugyldig projekt-token.")
    safe = platform_path(path)
    if safe is None:
        return _error(404, "not_found", "Ukendt platform-adresse.")
    try:
        body = await _read_body(request)
        state.limiter.check_rate(caller.project_id)
    except BodyTooLarge:
        return _error(413, "body_too_large", "Forespørgslen er for stor.")
    except Throttled as t:
        return _throttled(t)

    settings = get_settings()
    headers = {k: v for k, v in request.headers.items() if k.lower() in PLATFORM_REQUEST_HEADERS}
    headers.update(
        {
            "x-vibe-project": caller.project_id,
            "x-vibe-token-kind": caller.kind,
            "x-sloptown-internal": settings.internal_secret,
        }
    )
    url = settings.control_plane_url.rstrip("/") + "/platform/" + safe
    req = client().build_request(request.method, url, params=request.query_params, content=body or None, headers=headers)

    # Long-lived event streams don't count against the concurrency cap.
    long_lived = path.endswith("events")
    if not long_lived:
        try:
            state.limiter.acquire(caller.project_id)
        except Throttled as t:
            return _throttled(t)
    try:
        upstream = await client().send(req, stream=True)
    except httpx.HTTPError:
        if not long_lived:
            state.limiter.release(caller.project_id)
        return _error(502, "upstream_error", "Platformen svarede ikke.")

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() in {"content-type", "cache-control", "etag"}}

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            if not long_lived:
                state.limiter.release(caller.project_id)

    return StreamingResponse(relay(), status_code=upstream.status_code, headers=resp_headers)


# --- web search (build agent) -----------------------------------------------------

SEARCH_MAX_QUERY = 400  # Brave's limits: 400 characters, 50 words, count <= 20, offset <= 9
SEARCH_MAX_WORDS = 50
SEARCH_TIMEOUT = 15.0
SEARCH_MODEL = "brave-search"  # usage_events.model of a metered app search
_TAG = re.compile(r"<[^>]*>")
_LANG = re.compile(r"^[a-z]{2}(-[a-z]{2,4})?$")
_COUNTRY = re.compile(r"^([A-Z]{2}|ALL)$")
_FRESHNESS = re.compile(r"^(pd|pw|pm|py|\d{4}-\d{2}-\d{2}to\d{4}-\d{2}-\d{2})$")


def _plain(text: Any) -> str:
    """Brave marks matches with <strong>; the agent gets plain text."""
    return html.unescape(_TAG.sub("", text)).strip() if isinstance(text, str) else ""


def search_results(data: Any) -> list[dict[str, str]]:
    """Brave's web results as [{title, url, description, age?}]; anything malformed is skipped."""
    web = data.get("web") if isinstance(data, dict) else None
    results = web.get("results") if isinstance(web, dict) else None
    out: list[dict[str, str]] = []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        if not item["url"].startswith(("https://", "http://")):
            continue
        hit = {"title": _plain(item.get("title")), "url": item["url"], "description": _plain(item.get("description"))}
        if isinstance(item.get("age"), str) and item["age"]:
            hit["age"] = item["age"]
        out.append(hit)
    return out


def _int_param(raw: str | None, default: int, lo: int, hi: int) -> int | None:
    if raw is None or raw == "":
        return default
    if not raw.isdigit() or not lo <= int(raw) <= hi:
        return None
    return int(raw)


@app.get("/search/web")
async def search_web(request: Request) -> Response:
    caller = await _authenticate(request)
    if caller is None:
        return _error(401, "invalid_token", "Ugyldig projekt-token.")
    settings = get_settings()
    app_call = caller.kind != "dev"
    if app_call and not settings.app_search_enabled:
        return _error(403, "forbidden", "Websøgning er ikke slået til for apps.")
    params = request.query_params
    q = " ".join((params.get("q") or "").split())
    if not q:
        return _error(400, "invalid_query", "Søgningen er tom.")
    if len(q) > SEARCH_MAX_QUERY or len(q.split()) > SEARCH_MAX_WORDS:
        return _error(400, "invalid_query", f"Søgningen må højst have {SEARCH_MAX_QUERY} tegn og {SEARCH_MAX_WORDS} ord.")
    count = _int_param(params.get("count"), 8, 1, 20)
    offset = _int_param(params.get("offset"), 0, 0, 9)
    if count is None or offset is None:
        return _error(400, "invalid_query", "count skal være 1–20 og offset 0–9.")
    upstream_params: dict[str, Any] = {"q": q, "count": count, "offset": offset, "safesearch": "strict", "text_decorations": "false"}
    for name, pattern in (("search_lang", _LANG), ("country", _COUNTRY), ("freshness", _FRESHNESS)):
        value = params.get(name)
        if value:
            if not pattern.match(value):
                return _error(400, "invalid_query", f"Ugyldig værdi for {name}.")
            upstream_params[name] = value

    if not settings.brave_api_key:
        return _error(503, "search_unavailable", "Websøgning er ikke slået til på denne server.")
    try:
        state.limiter.check_rate(caller.project_id)
        state.limiter.check_search(caller.project_id, app=app_call)
    except Throttled as t:
        return _error(429, "rate_limited", "For mange søgninger. Vent et minut.", reason=t.reason,
                      retry_after=60 if t.reason.startswith("search") else 1)
    member_id = None
    if app_call:  # an app search costs app budget, like an AI call; the agent's searches are free
        member_id = request.headers.get("x-vibe-user") or None
        async with sessionmaker()() as db:
            decision = await budgets.decide(db, "app", project_id=caller.project_id, user_sub=None, requested_model=SEARCH_MODEL)
            await db.commit()
        if decision.blocked:
            retry_at = decision.retry_at.isoformat() if decision.retry_at else None
            return _error(429, "budget_exceeded", "Appens AI-budget for denne uge er brugt op.", retry_at=retry_at, bucket="app")
    try:
        state.limiter.acquire(caller.project_id)
    except Throttled as t:
        return _error(429, "rate_limited", "For mange søgninger. Vent et minut.", reason=t.reason,
                      retry_after=60 if t.reason.startswith("search") else 1)
    try:
        upstream = await client().get(
            settings.brave_search_url,
            params=upstream_params,
            headers={"accept": "application/json", "x-subscription-token": settings.brave_api_key},
            timeout=SEARCH_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        log.warning("search upstream error: %s", exc)
        return _error(502, "upstream_error", "Søgetjenesten svarede ikke.")
    finally:
        state.limiter.release(caller.project_id)
    # The query is the teacher's (or a student's) work; log only that a search happened.
    log.info("search project=%s kind=%s status=%s", caller.project_id, caller.kind, upstream.status_code)
    if upstream.status_code == 429:
        return _error(429, "rate_limited", "Søgetjenesten har for travlt. Prøv igen om lidt.", reason="search_upstream", retry_after=2)
    if upstream.status_code >= 400:
        log.warning("search upstream status %s: %s", upstream.status_code, upstream.text[:300])
        return _error(502, "upstream_error", "Søgetjenesten svarede med en fejl.")
    try:
        results = search_results(upstream.json())
    except ValueError:
        return _error(502, "upstream_error", "Søgetjenesten svarede ikke med JSON.")
    if app_call:
        await _record(caller, None, member_id, SEARCH_MODEL, SEARCH_MODEL, False, (state.cfg.search_tokens_per_call, 0))
    return JSONResponse({"query": q, "results": results})


def main() -> None:
    import uvicorn

    uvicorn.run("sloptown.egress.app:app", host="0.0.0.0", port=8200, workers=1)
