"""FastAPI application: wires routers, the error handler, startup recovery and background tasks.

Run: `uvicorn sloptown.app:app --port 8000` with ONE worker (turn state and SSE
fan-out are in-process, see events.py and threads.py).
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import StreamingResponse

from . import (
    activity,
    admin,
    auth,
    edge,
    files,
    hooks,
    internal,
    peers,
    platform,
    projects,
    terms,
    threads,
)
from .access import require_project
from .auth import CurrentPrincipal, Db
from .config import get_settings
from .db import create_all
from .errors import (
    ApiError,
    api_error_handler,
    http_error_handler,
    unreachable_error_handler,
    validation_error_handler,
)
from .events import hub, sse_response
from .orchestrator import get_orchestrator
from .pool import refill_loop

log = logging.getLogger(__name__)

RECONNECT_INTERVAL = 30.0
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def reconnect_loop() -> None:
    """Keep edge/egress/cp attached to all project networks (they lose them when recreated)."""
    while True:
        try:
            made = await get_orchestrator().reconnect_platform()
            if made:
                log.info("reattached platform containers to %d project network(s)", made)
        except Exception:
            log.exception("platform reconnect failed")
        await asyncio.sleep(RECONNECT_INTERVAL)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await create_all()
    await threads.turns.recover()
    hub.on_first = files.start_relay
    hub.on_last = files.stop_relay
    tasks: list[asyncio.Task[Any]] = []
    if get_settings().background_tasks:
        tasks.append(asyncio.create_task(reconnect_loop(), name="platform-reconnect"))
        tasks.append(asyncio.create_task(refill_loop(), name="pool-refill"))
        tasks.append(asyncio.create_task(internal.editor_rollover_loop(), name="editor-rollover"))
        tasks.append(asyncio.create_task(activity.idle_stop_loop(), name="idle-stop"))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()


def create_app() -> FastAPI:
    app = FastAPI(title="SlopTown control plane", lifespan=lifespan)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(httpx.TransportError, unreachable_error_handler)
    for r in (
        auth.router,
        terms.router,
        projects.router,
        threads.router,
        files.router,
        edge.router,
        platform.router,
        hooks.router,
        internal.router,
        admin.router,  # mounted at /admin/v1 by its own prefix
    ):
        app.include_router(r)

    @app.get("/api/projects/{pid}/events")
    async def project_events(pid: str, principal: CurrentPrincipal, db: Db) -> StreamingResponse:
        await require_project(db, principal, pid, "editor")
        activity.touch(pid)
        # The stream can stay open for hours: give the DB connection back now, or every
        # open editor holds one and the pool runs dry.
        await db.close()
        return sse_response(f"project:{pid}")

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next: Any) -> Response:
        """CSRF guard for cookie sessions.

        Apps live on sibling subdomains that browsers treat as the same *site*, so
        SameSite=Lax cookies alone don't stop an app from POSTing to the studio API.
        A state-changing request that carries the studio session cookie must come
        from the studio origin. Bearer-token calls (no cookie) and server-to-server
        paths (hooks, admin, edge, platform, internal) are not affected.
        """
        if (
            request.method not in SAFE_METHODS
            and request.url.path.startswith(("/api/", "/auth/"))
            and auth.cookie_name(auth.SESSION_COOKIE) in request.cookies
            and not request.headers.get("authorization", "").lower().startswith("bearer ")
        ):
            origin = request.headers.get("origin") or ""
            if origin.rstrip("/") != get_settings().studio_origin.rstrip("/"):
                return JSONResponse(
                    {"error": "bad_origin", "message": "Forespørgslen kom ikke fra SlopTown studio"}, status_code=403
                )
        return await call_next(request)

    @app.middleware("http")
    async def platform_peers_only(request: Request, call_next: Any) -> Response:
        """Vibes share networks with the control plane but have no business here (peers.py)."""
        if not await peers.allowed(request.client.host if request.client else None):
            return JSONResponse({"error": "not_found", "message": "Ikke fundet"}, status_code=404)
        return await call_next(request)

    @app.get("/api/config")
    async def studio_config() -> dict[str, Any]:
        """Public runtime config for the studio SPA (one build serves every environment)."""
        origins = [o.rstrip("/") for o in get_settings().miira_origins]
        return {"miiraOrigins": origins, "miiraOrigin": origins[0] if origins else ""}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
