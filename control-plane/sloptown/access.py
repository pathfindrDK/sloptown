"""Who may do what with a project. Roles come from Miira (owner/editor/viewer), never from SlopTown.

Resolution order: project owner; the role Miira asserted in a launch token for
this file (authoritative at open time); the cached Miira member list.
"""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Principal
from .errors import ApiError, not_found
from .members import MembersUnavailable, get_members, member_by_sub
from .models import Project

ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}
MISS_RECHECK = timedelta(seconds=10)


async def role_for_sub(db: AsyncSession, project: Project, sub: str) -> str | None:
    if project.owner_sub == sub:
        return "owner"
    try:
        m = member_by_sub(await get_members(db, project.id), sub)
        if m is None:
            # Just shared and the webhook hasn't landed yet? Recheck, at most every MISS_RECHECK.
            m = member_by_sub(await get_members(db, project.id, max_age=MISS_RECHECK), sub)
    except MembersUnavailable:
        return None
    return m["role"] if m else None


async def role_for(db: AsyncSession, principal: Principal, project: Project) -> str | None:
    if project.owner_sub == principal.sub:
        return "owner"
    sess = principal.session
    if sess.file_id == project.id and sess.file_role:
        return sess.file_role
    return await role_for_sub(db, project, principal.sub)


async def require_project(db: AsyncSession, principal: Principal, project_id: str, minimum: str = "viewer") -> tuple[Project, str]:
    project = await db.get(Project, project_id)
    if project is None:
        raise not_found("Vibe findes ikke")
    role = await role_for(db, principal, project)
    if role is None:
        raise not_found("Vibe findes ikke")
    if ROLE_RANK[role] < ROLE_RANK[minimum]:
        raise ApiError(403, "forbidden", "Du har ikke adgang til at redigere denne Vibe", role=role)
    return project, role
