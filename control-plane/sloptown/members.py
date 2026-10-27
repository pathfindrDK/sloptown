"""Members of a Vibe, fetched from Miira's members API and cached in `members_cache`.

Miira is the source of truth; SlopTown only caches (TTL `members_ttl_seconds`,
invalidated by the access-changed webhook). Member rows carry the service-level
`sub` so the control plane can map a logged-in user to their per-Vibe `id`;
`for_app()` strips it, and nothing that reaches app code may include it.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .miira import MiiraError, miira
from .models import MembersCache

log = logging.getLogger(__name__)

APP_FIELDS = ("id", "name", "role", "classes", "avatarHue")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class MembersUnavailable(Exception):
    pass


async def _fetch(project_id: str) -> dict[str, Any]:
    token = await miira.service_token()
    url = f"{get_settings().miira_api_base.rstrip('/')}/api/vibe/{project_id}/members"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 404:
        return {"fileId": project_id, "version": "missing", "members": []}
    if r.status_code != 200:
        raise MiiraError(f"members fetch failed: {r.status_code}")
    return r.json()


async def get_members(
    db: AsyncSession, project_id: str, *, force: bool = False, max_age: timedelta | None = None
) -> dict[str, Any]:
    """Cached member list (including `sub`). Falls back to a stale cache if Miira is down.

    `max_age` tightens the TTL for one call (used to recheck on an access miss).
    """
    row = await db.get(MembersCache, project_id)
    ttl = max_age if max_age is not None else timedelta(seconds=get_settings().members_ttl_seconds)
    if row is not None and not force and datetime.now(UTC) - row.fetched_at < ttl:
        return row.payload
    try:
        payload = await _fetch(project_id)
    except (MiiraError, httpx.HTTPError) as e:
        if row is not None:
            log.warning("members fetch failed for %s, serving stale cache: %s", project_id, e)
            return row.payload
        raise MembersUnavailable(str(e)) from e
    if row is None:
        row = MembersCache(project_id=project_id)
        db.add(row)
    row.version = str(payload.get("version", ""))
    row.payload = payload
    row.fetched_at = datetime.now(UTC)
    await db.commit()
    return payload


async def invalidate(db: AsyncSession, project_id: str) -> None:
    row = await db.get(MembersCache, project_id)
    if row is not None:
        row.fetched_at = EPOCH
        await db.commit()


def member_by_sub(payload: dict[str, Any], sub: str) -> dict[str, Any] | None:
    for m in payload.get("members", []):
        if m.get("sub") == sub:
            return m
    return None


def to_app(member: dict[str, Any]) -> dict[str, Any]:
    return {k: member.get(k) for k in APP_FIELDS}


def for_app(payload: dict[str, Any]) -> dict[str, Any]:
    """Member list as apps see it: per-Vibe ids only, never `sub`."""
    return {
        "version": str(payload.get("version", "")),
        "members": [to_app(m) for m in payload.get("members", [])],
    }
