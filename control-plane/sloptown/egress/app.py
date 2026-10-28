"""Egress + inference proxy (`sloptown.egress.app:app`, port 8200).

The only way out of a project container. Every request carries the project's
bearer token (VIBE_PROJECT_TOKEN), which identifies the project and whether the
caller is the dev container (build bucket) or the published runtime (app bucket).

- /v1/*        OpenAI-compatible reverse proxy. Injects the shared key, applies the
               weekly budget (nerf = swap model, block = 429), meters usage_events.
- /platform/*  forwarded to the control plane with X-Vibe-Project/X-Vibe-Token-Kind
               and the internal secret. Streams (SSE) pass through.
- /health      liveness.

The raw CONNECT/forward proxy for allowlisted hosts runs beside this app on
EGRESS_PROXY_PORT (see connect.py) and is started from the lifespan.
"""

import json
import logging
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


def _usage_from_event(obj: Any) -> tuple[int, int] | None:
    if not isinstance(obj, dict):
        return None
    if obj.get("type") == "response.completed":
        return _usage_tokens((obj.get("response") or {}).get("usage"))
    return _usage_tokens(obj.get("usage")) if obj.get("usage") else None


class SseUsageScanner:
    """Feeds raw SSE bytes, remembers the last usage block seen."""

    def __init__(self) -> None:
        self.buf = b""
        self.usage: tuple[int, int] | None = None

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
            found = _usage_from_event(json.loads(data))
        except ValueError:
            return
        if found:
            self.usage = found


async def _attribution(caller: Caller, request: Request) -> tuple[str | None, str | None]:
    """(user_sub, member_id) to charge: build -> turn author (else owner), app -> member."""
    if caller.bucket == "app":
        return None, request.headers.get("x-vibe-user") or None
    async with sessionmaker()() as db:
        project = await db.get(Project, caller.project_id)
    if project is None:
        return None, None
    return project.active_turn_sub or project.owner_sub, None


async def _record(caller: Caller, user_sub: str | None, member_id: str | None, model: str, requested: str, nerfed: bool, usage: tuple[int, int] | None) -> None:
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
            try:
                usage = _usage_tokens(json.loads(content).get("usage"))
            except (ValueError, AttributeError):
                usage = None
            await _record(caller, user_sub, member_id, model, requested, nerfed, usage)
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
                await _record(caller, user_sub, member_id, model, requested, nerfed, scanner.usage)

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


def main() -> None:
    import uvicorn

    uvicorn.run("sloptown.egress.app:app", host="0.0.0.0", port=8200, workers=1)
