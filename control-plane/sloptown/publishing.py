"""Publishing a Vibe: export, the pre-publish check, the AI fix turns it starts, release.

`POST /api/projects/{pid}/publish { sha }` exports the tree at `sha` and scans it
(`appcheck`). A clean tree is released at once (built and started, as before). If the
scan finds something, the teachers who build Vibes can't fix code themselves, so the
AI does it: a fix turn runs in the project's "Tjek før udgivelse" thread (`202
{ status: "fixing", ... }`, event `publish.checking`). When that turn is done, the new
head is scanned again and released (`project.published`), another fix turn runs (up to
MAX_FIX_ATTEMPTS), or publish gives up (`publish.failed`). The previously published
version keeps running until a release succeeds.
"""

import logging
import tarfile
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .access import require_project
from .appcheck import Finding, fix_prompt, scan_tar
from .audit import audit
from .auth import CurrentPrincipal, Db
from .containers import agentd, raise_for
from .db import sessionmaker
from .errors import ApiError
from .events import hub
from .models import Project, Thread, Turn, User
from .orchestrator import BuildFailed, get_orchestrator
from .projects import host_url, require_webapp
from .threads import new_thread, turns

log = logging.getLogger(__name__)
router = APIRouter()

MAX_FIX_ATTEMPTS = 2
CHECK_THREAD_TITLE = "Tjek før udgivelse"


class PublishBody(BaseModel):
    sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")


async def export_tree(project: Project, sha: str) -> tuple[str, bytes]:
    """(full sha, tar of the tree at sha) from agentd."""
    async with await agentd(project, timeout=120) as client:
        r = await client.post("/export", json={"sha": sha})
    if r.status_code == 422:  # agentd refuses exports that change platform-owned files
        detail = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        raise ApiError(422, "locked_files_changed", "Versionen ændrer platformens filer og kan ikke udgives", **detail)
    if r.status_code >= 400:
        raise_for(r, "Eksport")
    return r.headers.get("x-vibe-sha", sha), r.content


async def head_sha(project: Project) -> str | None:
    async with await agentd(project) as client:
        commits = (raise_for(await client.get("/history", params={"limit": 1}), "Historik") or {}).get("commits") or []
    return commits[0]["sha"] if commits else None


def scan(tar: bytes) -> list[Finding]:
    try:
        return scan_tar(tar)
    except tarfile.TarError:
        log.warning("export is not a tar archive; publishing without the check")
        return []


def findings_json(findings: list[Finding]) -> list[dict[str, Any]]:
    return [f.to_json() for f in findings]


async def release(db: Any, project: Project, actor_sub: str, sha: str, tar: bytes, request: Request | None = None) -> dict[str, Any]:
    """Build the exported tree and run it as the published app."""
    pid = project.id
    assert project.token_seed and project.network_name and project.volume_name
    try:
        await get_orchestrator().publish(pid, project.network_name, project.volume_name, project.token_seed, sha, tar)
    except BuildFailed as e:
        audit(db, "project.publish_failed", actor_sub=actor_sub, project_id=pid, details={"sha": sha}, request=request)
        await db.commit()
        raise ApiError(422, "build_failed", "Appen kunne ikke bygges. Bed AI'en rette fejlen og prøv igen.", log=e.log) from e
    project.published_sha = sha
    project.published_at = datetime.now(UTC)
    audit(db, "project.publish", actor_sub=actor_sub, project_id=pid, details={"sha": sha}, request=request)
    await db.commit()
    url = host_url(project.slug) + "/"
    hub.publish(f"project:{pid}", "project.published", {"publishedSha": sha, "url": url})
    return {"publishedSha": sha, "url": url}


async def pending_fix(db: Any, pid: str) -> Turn | None:
    q = select(Turn).where(Turn.project_id == pid, Turn.publish_attempt.is_not(None), Turn.status.in_(("queued", "running")))
    return (await db.execute(q.limit(1))).scalars().first()


async def start_fix(
    db: Any, project: Project, author: User, findings: list[Finding], attempt: int, sha: str, request: Request | None = None
) -> dict[str, Any]:
    """Have the AI fix the findings; publishing carries on when its turn is done."""
    q = select(Thread).where(Thread.project_id == project.id, Thread.title == CHECK_THREAD_TITLE, Thread.archived.is_(False))
    thread = (await db.execute(q.order_by(Thread.created_at.desc()).limit(1))).scalars().first()
    if thread is None:
        thread = await new_thread(db, project, author.sub, CHECK_THREAD_TITLE, request)
    submitted = await turns.submit(db, project, thread, author, fix_prompt(findings), [], publish_attempt=attempt)
    audit(
        db, "project.publish_check", actor_sub=author.sub, project_id=project.id, request=request,
        details={"sha": sha, "attempt": attempt, "rules": sorted({f.rule for f in findings}), "turn": submitted["turnId"]},
    )
    await db.commit()
    data = {"status": "fixing", "turnId": submitted["turnId"], "threadId": thread.id, "attempt": attempt, "findings": findings_json(findings)}
    hub.publish(f"project:{project.id}", "publish.checking", data)
    return data


@router.post("/api/projects/{pid}/publish")
async def publish(pid: str, body: PublishBody, principal: CurrentPrincipal, db: Db, request: Request, response: Response) -> dict[str, Any]:
    project, _ = await require_project(db, principal, pid, "editor")
    require_webapp(project)
    if await pending_fix(db, pid):
        raise ApiError(409, "publish_pending", "Appen bliver allerede tjekket og udgivet")
    sha, tar = await export_tree(project, body.sha)
    findings = scan(tar)
    if not findings:
        return await release(db, project, principal.sub, sha, tar, request)
    if sha != await head_sha(project):
        # The fix lands on the newest version, not on this older one.
        audit(db, "project.publish_check", actor_sub=principal.sub, project_id=pid, request=request,
              details={"sha": sha, "attempt": 0, "rules": sorted({f.rule for f in findings})})
        await db.commit()
        raise ApiError(
            422, "check_failed",
            "Denne version har kode, der skal rettes, før den kan udgives. Udgiv den nyeste version, så retter AI'en det først.",
            findings=findings_json(findings),
        )
    response.status_code = 202
    return await start_fix(db, project, principal.user, findings, 1, sha, request)


async def after_fix_turn(pid: str, turn_id: str, status: str) -> None:
    """Called by the turn manager when a fix turn has finished (still holding the turn slot)."""
    channel = f"project:{pid}"
    try:
        async with sessionmaker()() as db:
            turn = await db.get(Turn, turn_id)
            project = await db.get(Project, pid)
            author = await db.get(User, turn.author_sub)
            attempt = turn.publish_attempt or 1
            if status != "done":
                raise ApiError(409, "check_stopped", "Udgivelsen blev afbrudt, før AI'en var færdig med at rette appen. Udgiv igen for at prøve forfra.")
            head = await head_sha(project)
            if head is None:
                raise ApiError(409, "check_stopped", "Der er ingen version at udgive.")
            sha, tar = await export_tree(project, head)
            findings = scan(tar)
            if not findings:
                await release(db, project, turn.author_sub, sha, tar)
            elif attempt < MAX_FIX_ATTEMPTS:
                await start_fix(db, project, author, findings, attempt + 1, sha)
            else:
                audit(db, "project.publish_check", actor_sub=turn.author_sub, project_id=pid,
                      details={"sha": sha, "attempt": attempt, "rules": sorted({f.rule for f in findings}), "gaveUp": True})
                await db.commit()
                raise ApiError(
                    422, "check_failed",
                    "AI'en kunne ikke rette alt, som tjekket fandt, så appen er ikke udgivet. Bed AI'en rette punkterne og udgiv igen.",
                    findings=findings_json(findings),
                )
    except ApiError as e:
        hub.publish(channel, "publish.failed", {"error": e.error, "message": e.message, **e.extra})
    except Exception:
        log.exception("publish after fix turn %s failed", turn_id)
        hub.publish(channel, "publish.failed", {"error": "internal", "message": "Appen kunne ikke udgives. Prøv igen."})
