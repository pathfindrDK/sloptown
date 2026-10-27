"""Audit log: one row per state change on a project, budget or user (ISAE groundwork).

Callers add the row to their own session so it commits atomically with the change.
"""

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def audit(
    db: AsyncSession,
    action: str,
    *,
    actor_sub: str | None,
    actor_kind: str = "user",
    project_id: str | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_sub=actor_sub,
            actor_kind=actor_kind,
            action=action,
            project_id=project_id,
            details=details or {},
            ip=client_ip(request),
        )
    )
