"""Platform API for app code (`/platform/*`), reached only through the egress proxy.

Egress authenticates the container's project token and forwards with
`X-Sloptown-Internal`, `X-Vibe-Project` and `X-Vibe-Token-Kind`. Responses carry
per-Vibe member ids only; `sub` is stripped by `members.for_app`.
"""

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from .auth import Db
from .config import get_settings
from .errors import ApiError
from .events import sse_response
from .members import MembersUnavailable, for_app, get_members, member_by_sub, to_app
from .models import Project
from .tokens import consteq

router = APIRouter(prefix="/platform")


@dataclass
class InternalCaller:
    project: Project
    kind: str  # dev|runtime


async def internal_caller(request: Request, db: Db) -> InternalCaller:
    secret = request.headers.get("x-sloptown-internal", "")
    if not secret or not consteq(secret, get_settings().internal_secret):
        raise ApiError(403, "forbidden", "Kun via egress")
    pid = request.headers.get("x-vibe-project", "")
    project = await db.get(Project, pid) if pid else None
    if project is None:
        raise ApiError(404, "not_found", "Ukendt projekt")
    kind = request.headers.get("x-vibe-token-kind", "runtime")
    return InternalCaller(project, "dev" if kind == "dev" else "runtime")


Caller = Annotated[InternalCaller, Depends(internal_caller)]


@router.get("/members")
async def members(caller: Caller, db: Db) -> dict[str, Any]:
    try:
        return for_app(await get_members(db, caller.project.id))
    except MembersUnavailable:
        raise ApiError(503, "miira_unavailable", "Medlemslisten kan ikke hentes lige nu")


@router.get("/members/events")
async def member_events(caller: Caller, db: Db) -> StreamingResponse:
    # Every vibe-dev and vibe-runtime keeps this stream open: release the request's DB
    # connection (taken by the token check) before streaming.
    await db.close()
    return sse_response(f"members:{caller.project.id}")


@router.get("/whoami")
async def whoami(caller: Caller, db: Db) -> dict[str, Any]:
    """Dev helper: the owner's identity as the app would see it."""
    if caller.kind != "dev":
        raise ApiError(403, "dev_only", "Kun i udviklingstilstand")
    try:
        m = member_by_sub(await get_members(db, caller.project.id), caller.project.owner_sub)
    except MembersUnavailable:
        m = None
    if m is None:
        return {"id": "dev-owner", "name": "Ejer", "role": "owner", "classes": [], "avatarHue": 200}
    return to_app(m)
