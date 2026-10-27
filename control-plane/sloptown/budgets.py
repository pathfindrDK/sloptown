"""Weekly token budgets (build bucket per teacher, app bucket per published Vibe).

The window is the ISO week in Europe/Copenhagen. The ledger (usage_events) is never
rewritten: a reset moves the window floor forward (budget_overrides.reset_at) and a
grant adds headroom (budget_overrides.extra_tokens) for the current week.
"""

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import BudgetOverride, BudgetSettingsRow, Project, UsageEvent, User

TZ = ZoneInfo("Europe/Copenhagen")
BUCKETS = ("build", "app")
ROLE_KEYS = ("laerer", "elev", "admin", "default")

_OFF = {"nerfLimitTokens": None, "hardLimitTokens": None}

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "nerfModel": "gpt-6-luna",
    "warningThresholds": [0.8, 0.95],
    "watcherEmails": [],
    "build": {"byRole": {k: dict(_OFF) for k in ROLE_KEYS}},
    "app": {"perProject": dict(_OFF)},
}


# --- time window ---------------------------------------------------------------


def week_start(now: datetime | None = None) -> datetime:
    """Monday 00:00 Europe/Copenhagen of the week containing `now`, as aware UTC."""
    local = (now or datetime.now(timezone.utc)).astimezone(TZ)
    monday = (local - timedelta(days=local.weekday())).date()
    return datetime(monday.year, monday.month, monday.day, tzinfo=TZ).astimezone(timezone.utc)


def next_reset(now: datetime | None = None) -> datetime:
    """Next Monday 00:00 Europe/Copenhagen (DST-safe: built from the local date)."""
    local = week_start(now).astimezone(TZ)
    nxt = (local + timedelta(days=7, hours=12)).date()
    return datetime(nxt.year, nxt.month, nxt.day, tzinfo=TZ).astimezone(timezone.utc)


# --- settings ------------------------------------------------------------------


def normalize_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Fill missing keys from defaults so older/partial payloads stay valid."""
    out = copy.deepcopy(DEFAULT_SETTINGS)
    for key in ("enabled", "nerfModel", "warningThresholds", "watcherEmails"):
        if key in payload:
            out[key] = payload[key]
    by_role = (payload.get("build") or {}).get("byRole") or {}
    for role, limits in by_role.items():
        out["build"]["byRole"][role] = {**_OFF, **(limits or {})}
    per_project = (payload.get("app") or {}).get("perProject")
    if per_project:
        out["app"]["perProject"] = {**_OFF, **per_project}
    return out


async def get_settings_payload(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(BudgetSettingsRow, 1)
    if row is None:
        row = BudgetSettingsRow(id=1, payload=copy.deepcopy(DEFAULT_SETTINGS), updated_by="system")
        db.add(row)
        await db.flush()
    return normalize_settings(row.payload)


async def put_settings_payload(db: AsyncSession, payload: dict[str, Any], by: str) -> dict[str, Any]:
    clean = normalize_settings(payload)
    row = await db.get(BudgetSettingsRow, 1)
    if row is None:
        db.add(BudgetSettingsRow(id=1, payload=clean, updated_by=by))
    else:
        row.payload = clean
        row.updated_by = by
        row.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return clean


def _role_key(role: str | None) -> str:
    if role == "system-admin":
        return "admin"
    return role if role in ROLE_KEYS else "default"


def _limits_for(settings: dict[str, Any], bucket: str, role: str | None) -> dict[str, int | None]:
    if bucket == "app":
        return settings["app"]["perProject"]
    by_role = settings["build"]["byRole"]
    return by_role.get(_role_key(role)) or by_role.get("default") or dict(_OFF)


# --- usage ---------------------------------------------------------------------


@dataclass
class Window:
    floor: datetime
    extra: int


async def _window(db: AsyncSession, subject: str, subject_id: str, bucket: str, now: datetime) -> Window:
    ws = week_start(now)
    rows = (
        await db.execute(
            select(BudgetOverride).where(
                BudgetOverride.subject == subject,
                BudgetOverride.subject_id == subject_id,
                BudgetOverride.bucket == bucket,
                BudgetOverride.week_start == ws,
            )
        )
    ).scalars()
    floor, extra = ws, 0
    for o in rows:
        if o.reset_at and o.reset_at > floor:
            floor = o.reset_at
        extra += o.extra_tokens or 0
    return Window(floor, extra)


async def _used(db: AsyncSession, subject: str, subject_id: str, bucket: str, since: datetime) -> int:
    col = UsageEvent.user_sub if subject == "user" else UsageEvent.project_id
    total = await db.scalar(
        select(func.coalesce(func.sum(UsageEvent.input_tokens + UsageEvent.output_tokens), 0)).where(
            UsageEvent.bucket == bucket, col == subject_id, UsageEvent.ts >= since
        )
    )
    return int(total or 0)


def _bucket_status(settings: dict[str, Any], limits: dict[str, int | None], used: int, extra: int, resets_at: datetime) -> dict[str, Any]:
    nerf = limits.get("nerfLimitTokens")
    hard = limits.get("hardLimitTokens")
    nerf = None if nerf is None else nerf + extra
    hard = None if hard is None else hard + extra
    state = "ok"
    if settings["enabled"]:
        thresholds = [t for t in settings.get("warningThresholds") or [] if t]
        if hard is not None and used >= hard:
            state = "blocked"
        elif nerf is not None and used >= nerf:
            state = "nerfed"
        elif hard is not None and thresholds and used >= min(thresholds) * hard:
            state = "warning"
    return {
        "usedTokens": used,
        "nerfLimitTokens": nerf,
        "hardLimitTokens": hard,
        "state": state,
        "resetsAt": resets_at.isoformat(),
    }


async def bucket_status(
    db: AsyncSession, bucket: str, subject_id: str, role: str | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Status of one bucket. subject_id is a user sub (build) or project id (app)."""
    now = now or datetime.now(timezone.utc)
    settings = await get_settings_payload(db)
    subject = "user" if bucket == "build" else "project"
    win = await _window(db, subject, subject_id, bucket, now)
    used = await _used(db, subject, subject_id, bucket, win.floor)
    return _bucket_status(settings, _limits_for(settings, bucket, role), used, win.extra, next_reset(now))


async def _user_role(db: AsyncSession, sub: str | None) -> str | None:
    if not sub:
        return None
    user = await db.get(User, sub)
    return user.role if user else None


async def budget_status(db: AsyncSession, project_id: str, user_sub: str, now: datetime | None = None) -> dict[str, Any]:
    """BudgetStatus (docs/internal-api.md): caller's build bucket + project's app bucket."""
    settings = await get_settings_payload(db)
    role = await _user_role(db, user_sub)
    return {
        "enabled": settings["enabled"],
        "build": await bucket_status(db, "build", user_sub, role, now),
        "app": await bucket_status(db, "app", project_id, None, now),
    }


@dataclass
class Decision:
    model: str
    nerfed: bool
    blocked: bool
    retry_at: datetime | None
    state: str


async def decide(
    db: AsyncSession, bucket: str, *, project_id: str, user_sub: str | None, requested_model: str, now: datetime | None = None
) -> Decision:
    """What to do with one inference call: pass, nerf (swap model) or block."""
    now = now or datetime.now(timezone.utc)
    settings = await get_settings_payload(db)
    if not settings["enabled"]:
        return Decision(requested_model, False, False, None, "ok")
    if bucket == "build":
        if not user_sub:
            return Decision(requested_model, False, False, None, "ok")
        status = await bucket_status(db, "build", user_sub, await _user_role(db, user_sub), now)
    else:
        status = await bucket_status(db, "app", project_id, None, now)
    if status["state"] == "blocked":
        return Decision(requested_model, False, True, next_reset(now), "blocked")
    if status["state"] == "nerfed" and settings["nerfModel"] and requested_model != settings["nerfModel"]:
        return Decision(settings["nerfModel"], True, False, None, "nerfed")
    return Decision(requested_model, False, False, None, status["state"])


async def check_build_allowed(db: AsyncSession, user_sub: str, now: datetime | None = None) -> tuple[bool, datetime | None]:
    """Gate for starting an AI turn in the editor."""
    settings = await get_settings_payload(db)
    if not settings["enabled"]:
        return True, None
    status = await bucket_status(db, "build", user_sub, await _user_role(db, user_sub), now)
    if status["state"] == "blocked":
        return False, next_reset(now)
    return True, None


async def record_usage(
    db: AsyncSession,
    *,
    project_id: str,
    bucket: str,
    user_sub: str | None,
    member_id: str | None,
    model: str,
    requested_model: str,
    input_tokens: int,
    output_tokens: int,
    nerfed: bool,
) -> None:
    db.add(
        UsageEvent(
            project_id=project_id,
            bucket=bucket,
            user_sub=user_sub,
            member_id=member_id,
            model=model,
            requested_model=requested_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            nerfed=nerfed,
        )
    )
    await db.flush()


# --- admin operations -------------------------------------------------------------


async def reset(db: AsyncSession, subject: str, subject_id: str, bucket: str, by: str, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    db.add(BudgetOverride(subject=subject, subject_id=subject_id, bucket=bucket, week_start=week_start(now), reset_at=now, created_by=by))
    await db.flush()


async def grant(db: AsyncSession, subject: str, subject_id: str, bucket: str, extra_tokens: int, by: str, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    db.add(BudgetOverride(subject=subject, subject_id=subject_id, bucket=bucket, week_start=week_start(now), extra_tokens=extra_tokens, created_by=by))
    await db.flush()


async def limited_items(db: AsyncSession, now: datetime | None = None) -> list[dict[str, Any]]:
    """Users and projects currently nerfed or blocked this week."""
    now = now or datetime.now(timezone.utc)
    settings = await get_settings_payload(db)
    if not settings["enabled"]:
        return []
    ws = week_start(now)
    items: list[dict[str, Any]] = []

    subs = (await db.execute(select(UsageEvent.user_sub).where(UsageEvent.bucket == "build", UsageEvent.ts >= ws, UsageEvent.user_sub.is_not(None)).distinct())).scalars().all()
    for sub in subs:
        user = await db.get(User, sub)
        st = await bucket_status(db, "build", sub, user.role if user else None, now)
        if st["state"] in ("nerfed", "blocked"):
            items.append(_limited("user", sub, user.name if user else sub, "build", st))

    pids = (await db.execute(select(UsageEvent.project_id).where(UsageEvent.bucket == "app", UsageEvent.ts >= ws).distinct())).scalars().all()
    for pid in pids:
        project = await db.get(Project, pid)
        st = await bucket_status(db, "app", pid, None, now)
        if st["state"] in ("nerfed", "blocked"):
            items.append(_limited("project", pid, project.title if project else pid, "app", st))
    return items


def _limited(subject: str, sid: str, label: str, bucket: str, st: dict[str, Any]) -> dict[str, Any]:
    limit = st["hardLimitTokens"] if st["state"] == "blocked" else st["nerfLimitTokens"]
    return {
        "subject": subject,
        "id": sid,
        "label": label,
        "bucket": bucket,
        "state": st["state"],
        "usedTokens": st["usedTokens"],
        "limitTokens": limit,
        "resetsAt": st["resetsAt"],
    }
