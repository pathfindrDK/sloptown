"""SlopTown admin API (/admin/v1/*), called only by Miira's backend for its system-admin UI.

Auth: RS256 JWT from Miira's JWKS, aud=sloptown-admin, role=system-admin, short
lived, single use (jti stored in launch_jti with an "admin:" prefix). Optionally
the source IP must be in settings.admin_allowed_ips (the edge enforces this too).
Every write is audit-logged with the acting admin's sub. Contract:
docs/miira-integration.md §8.
"""

import ipaddress
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import jwt
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import budgets
from .audit import audit, client_ip
from .config import get_settings
from .db import get_db
from .egress.config import EgressConfig
from .errors import ApiError
from .miira import MiiraError, miira
from .models import LaunchJti, Project, UsageEvent, User

AUDIENCE = "sloptown-admin"

Db = Annotated[AsyncSession, Depends(get_db)]


def _ip_allowed(ip: str | None, allowed: list[str]) -> bool:
    if not allowed:
        return True
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(net, strict=False) for net in allowed)


async def admin_claims(request: Request, db: Db) -> dict[str, Any]:
    """Dependency: verified admin JWT claims, or ApiError 401/403."""
    if not _ip_allowed(client_ip(request), get_settings().admin_allowed_ips):
        raise ApiError(403, "forbidden_ip", "Adgang nægtet fra denne adresse.")
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise ApiError(401, "unauthorized", "Mangler admin-token.")
    try:
        claims = await miira.verify(header[7:].strip(), AUDIENCE)
    except (jwt.PyJWTError, MiiraError) as exc:
        raise ApiError(401, "unauthorized", f"Ugyldigt admin-token: {exc}") from exc
    if claims.get("role") != "system-admin":
        raise ApiError(403, "forbidden", "Kun systemadministratorer.")
    jti = claims.get("jti")
    if not jti:
        raise ApiError(401, "unauthorized", "Admin-token mangler jti.")
    try:
        async with db.begin_nested():
            db.add(LaunchJti(jti=f"admin:{jti}", exp=datetime.fromtimestamp(claims["exp"], UTC)))
    except IntegrityError as exc:
        raise ApiError(401, "replayed", "Admin-token er allerede brugt.") from exc
    await db.commit()
    return claims


Admin = Annotated[dict[str, Any], Depends(admin_claims)]

router = APIRouter(prefix="/admin/v1", tags=["admin"])


# --- budget settings -------------------------------------------------------------------


class RoleLimits(BaseModel):
    nerfLimitTokens: int | None = Field(default=None, ge=0)
    hardLimitTokens: int | None = Field(default=None, ge=0)


class BuildSettings(BaseModel):
    byRole: dict[str, RoleLimits]


class AppSettings(BaseModel):
    perProject: RoleLimits


class BudgetSettings(BaseModel):
    enabled: bool
    nerfModel: str
    warningThresholds: list[float] = Field(default_factory=list)
    watcherEmails: list[str] = Field(default_factory=list)
    build: BuildSettings
    app: AppSettings


@router.get("/budget-settings")
async def get_budget_settings(_: Admin, db: Db) -> dict[str, Any]:
    payload = await budgets.get_settings_payload(db)
    await db.commit()
    return payload


@router.put("/budget-settings")
async def put_budget_settings(body: BudgetSettings, claims: Admin, db: Db, request: Request) -> dict[str, Any]:
    for t in body.warningThresholds:
        if not 0 < t <= 1:
            raise ApiError(422, "invalid", "warningThresholds skal være mellem 0 og 1.")
    before = await budgets.get_settings_payload(db)
    payload = await budgets.put_settings_payload(db, body.model_dump(), claims["sub"])
    audit(db, "budget.settings.update", actor_sub=claims["sub"], actor_kind="admin", details={"before": before, "after": payload}, request=request)
    await db.commit()
    return payload


# --- limited, reset, grant ---------------------------------------------------------------


@router.get("/budgets/limited")
async def get_limited(_: Admin, db: Db) -> dict[str, Any]:
    items = await budgets.limited_items(db)
    await db.commit()
    return {"items": items}


class ResetBody(BaseModel):
    subject: Literal["user", "project"]
    id: str
    bucket: Literal["build", "app"]


class GrantBody(ResetBody):
    extraTokens: int = Field(gt=0)


def _check_pair(subject: str, bucket: str) -> None:
    if (subject, bucket) not in {("user", "build"), ("project", "app")}:
        raise ApiError(422, "invalid", "Byggebudget gælder brugere, app-budget gælder projekter.")


@router.post("/budgets/reset")
async def post_reset(body: ResetBody, claims: Admin, db: Db, request: Request) -> dict[str, Any]:
    _check_pair(body.subject, body.bucket)
    await budgets.reset(db, body.subject, body.id, body.bucket, claims["sub"])
    audit(
        db,
        "budget.reset",
        actor_sub=claims["sub"],
        actor_kind="admin",
        project_id=body.id if body.subject == "project" else None,
        details=body.model_dump(),
        request=request,
    )
    await db.commit()
    return {"ok": True}


@router.post("/budgets/grant")
async def post_grant(body: GrantBody, claims: Admin, db: Db, request: Request) -> dict[str, Any]:
    _check_pair(body.subject, body.bucket)
    await budgets.grant(db, body.subject, body.id, body.bucket, body.extraTokens, claims["sub"])
    audit(
        db,
        "budget.grant",
        actor_sub=claims["sub"],
        actor_kind="admin",
        project_id=body.id if body.subject == "project" else None,
        details=body.model_dump(),
        request=request,
    )
    await db.commit()
    return {"ok": True}


# --- usage ------------------------------------------------------------------------------------


def _parse_ts(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiError(422, "invalid", f"Ugyldig dato: {value}") from exc
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


_SUMS = (
    func.sum(UsageEvent.input_tokens).label("input"),
    func.sum(UsageEvent.output_tokens).label("output"),
    func.count().label("calls"),
)


def _totals(row: Any) -> dict[str, int]:
    inp, out = int(row.input or 0), int(row.output or 0)
    return {"inputTokens": inp, "outputTokens": out, "totalTokens": inp + out, "calls": int(row.calls)}


@router.get("/usage")
async def get_usage(_: Admin, db: Db, request: Request) -> dict[str, Any]:
    now = datetime.now(UTC)
    start = _parse_ts(request.query_params.get("from"), budgets.week_start(now))
    end = _parse_ts(request.query_params.get("to"), now + timedelta(seconds=1))
    window = (UsageEvent.ts >= start, UsageEvent.ts < end)

    by_user = (
        await db.execute(
            select(UsageEvent.user_sub, User.name, *_SUMS)
            .outerjoin(User, User.sub == UsageEvent.user_sub)
            .where(*window, UsageEvent.bucket == "build")
            .group_by(UsageEvent.user_sub, User.name)
        )
    ).all()
    by_project = (
        await db.execute(
            select(UsageEvent.project_id, UsageEvent.bucket, Project.title, *_SUMS)
            .outerjoin(Project, Project.id == UsageEvent.project_id)
            .where(*window)
            .group_by(UsageEvent.project_id, UsageEvent.bucket, Project.title)
        )
    ).all()
    by_model = (await db.execute(select(UsageEvent.model, *_SUMS).where(*window).group_by(UsageEvent.model))).all()

    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "byUser": [{"id": r.user_sub, "name": r.name or r.user_sub, **_totals(r)} for r in by_user],
        "byProject": [{"id": r.project_id, "title": r.title or r.project_id, "bucket": r.bucket, **_totals(r)} for r in by_project],
        "byModel": [{"model": r.model, **_totals(r)} for r in by_model],
    }


# --- egress settings ----------------------------------------------------------------------


@router.get("/egress-settings")
async def get_egress_settings(_: Admin) -> dict[str, Any]:
    return EgressConfig.load().public()
