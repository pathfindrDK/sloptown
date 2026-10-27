"""Routes the browser SDK (@miira/vibe) talks to, mounted by `setup(app)`.

The app's FastAPI is served under /api, so the browser sees:
  GET  /api/_vibe/me               current user
  GET  /api/_vibe/members          {version, members}
  GET  /api/_vibe/members/events   SSE: `members.changed {version}`, `ping`
  POST /api/_vibe/ai/chat          {messages, model?, stream?} -> {text} or SSE `delta`/`done`/`error`
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from . import ai, members
from .identity import User, current_user

PING_SECONDS = 15
SSE_HEADERS = {"cache-control": "no-cache", "x-accel-buffering": "no"}


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _budget_response(e: ai.BudgetExceeded) -> JSONResponse:
    return JSONResponse(
        {"error": "budget_exceeded", "message": e.message, "retryAt": e.retry_at.isoformat() if e.retry_at else None},
        status_code=429,
    )


def build_router(ai_route: bool = True) -> APIRouter:
    router = APIRouter(tags=["vibe-platform"])

    @router.get("/me")
    async def me(user: User = Depends(current_user)) -> dict[str, Any]:
        return user.to_dict()

    @router.get("/members")
    async def list_members(user: User = Depends(current_user)) -> dict[str, Any]:
        version, items = await members.fetch()
        return {"version": version, "members": [m.to_dict() for m in items]}

    @router.get("/members/events")
    async def member_events(user: User = Depends(current_user)) -> StreamingResponse:
        async def gen() -> AsyncIterator[str]:
            sub = members.subscribe()
            yield _sse("ready", {})
            next_version = asyncio.ensure_future(anext(sub))
            try:
                while True:
                    done, _ = await asyncio.wait({next_version}, timeout=PING_SECONDS)
                    if done:
                        yield _sse("members.changed", {"version": next_version.result()})
                        next_version = asyncio.ensure_future(anext(sub))
                    else:
                        yield _sse("ping", {})
            finally:
                # The pending anext() must finish before the generator can be closed,
                # or aclose() raises "asynchronous generator is already running".
                next_version.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                    await next_version
                await sub.aclose()

        return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)

    if ai_route:

        @router.post("/ai/chat", response_model=None)
        async def ai_chat(body: dict[str, Any] = Body(...), user: User = Depends(current_user)):
            messages = body.get("messages") or []
            model = body.get("model")
            if not body.get("stream"):
                try:
                    return {"text": await ai.chat(messages, model=model, user=user)}
                except ai.BudgetExceeded as e:
                    return _budget_response(e)

            deltas = ai.stream(messages, model=model, user=user)
            try:
                first = await anext(deltas, None)
            except ai.BudgetExceeded as e:
                return _budget_response(e)

            async def gen() -> AsyncIterator[str]:
                try:
                    if first is not None:
                        yield _sse("delta", {"text": first})
                    async for d in deltas:
                        yield _sse("delta", {"text": d})
                    yield _sse("done", {})
                except Exception as e:  # stream already started; report in-band
                    yield _sse("error", {"message": str(e)})

            return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)

    return router


def setup(app: FastAPI, *, prefix: str = "/_vibe", ai_route: bool = True) -> FastAPI:
    """Mount the platform routes and run the members watcher for the app's lifetime."""
    app.include_router(build_router(ai_route=ai_route), prefix=prefix)
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: FastAPI):
        task = asyncio.create_task(members.watch_forever())
        try:
            async with inner(a) as state:
                yield state
        finally:
            task.cancel()

    app.router.lifespan_context = lifespan
    return app
