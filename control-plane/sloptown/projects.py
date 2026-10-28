"""Projects (Vibes): list, create (claims a warm-pool container), rename, publish, budget, host sessions.

A project's id is the Miira file id. Creation requires a teacher who accepted the
terms and who Miira says owns the file (launch-token `file_role`, or the members API).
"""

import io
import re
import secrets
import tarfile
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from . import budgets
from .access import require_project
from .audit import audit
from .auth import ROLES_THAT_CREATE, CurrentPrincipal, Db, Principal
from .config import get_settings
from .containers import agentd, raise_for
from .errors import ApiError
from .events import hub
from .members import MembersUnavailable, get_members, member_by_sub
from .models import EdgeCode, MembersCache, Project
from .orchestrator import BuildFailed, get_orchestrator
from .pool import claim
from .terms import terms_accepted
from .tokens import dev_token, hash_token, random_token, runtime_token

router = APIRouter()

PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
EDGE_CODE_TTL = timedelta(minutes=2)


def host_url(slug: str, preview: bool = False) -> str:
    s = get_settings()
    host = f"preview-{slug}" if preview else slug
    return f"{s.apps_scheme}://{host}.{s.apps_domain}{s.apps_port}"


KINDS = ("webapp", "scratch")


def is_webapp(p: Project) -> bool:
    return p.kind != "scratch"


def require_webapp(p: Project) -> None:
    """A "fra bunden" project is never served: no preview, no app, no publish."""
    if not is_webapp(p):
        raise ApiError(409, "not_a_webapp", "Projektet er ikke en webapp og kan ikke vises eller udgives")


def project_json(p: Project, role: str) -> dict[str, Any]:
    return {
        "id": p.id,
        "title": p.title,
        "kind": p.kind,
        "slug": p.slug,
        "ownerSub": p.owner_sub,
        "myRole": role,
        "status": p.status,
        "previewUrl": host_url(p.slug, preview=True) + "/" if is_webapp(p) else None,
        "appUrl": host_url(p.slug) + "/" if p.published_sha else None,
        "publishedSha": p.published_sha,
        "public": p.public,
        "description": p.description,
        "createdAt": p.created_at.isoformat() if p.created_at else None,
        "updatedAt": p.updated_at.isoformat() if p.updated_at else None,
    }


def make_slug(title: str) -> str:
    base = title.lower()
    for a, b in (("æ", "ae"), ("ø", "oe"), ("å", "aa")):
        base = base.replace(a, b)
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:24].strip("-") or "vibe"
    if base.startswith("preview"):
        base = "vibe-" + base
    return f"{base}-{secrets.token_hex(3)}"


def normalize_id(file_id: str) -> str:
    pid = file_id.strip().lower()
    if not PROJECT_ID_RE.match(pid):
        raise ApiError(400, "bad_file_id", "Ugyldigt fil-id")
    return pid


async def _assert_can_create(db: Db, principal: Principal, pid: str) -> None:
    if principal.user.role not in ROLES_THAT_CREATE:
        raise ApiError(403, "teachers_only", "Kun lærere kan oprette en Vibe")
    if not terms_accepted(principal.user):
        raise ApiError(403, "terms_required", "Du skal acceptere vilkårene først", version=get_settings().terms_version)
    sess = principal.session
    if sess.file_id is not None:
        if sess.file_id != pid or sess.file_role != "owner":
            raise ApiError(403, "not_owner", "Kun ejeren af filen kan oprette Vibe'en")
        return
    try:
        payload = await get_members(db, pid, force=True)
    except MembersUnavailable:
        raise ApiError(503, "miira_unavailable", "Miira svarer ikke lige nu. Prøv igen om lidt.")
    m = member_by_sub(payload, principal.sub)
    if not m or m.get("role") != "owner":
        raise ApiError(403, "not_owner", "Kun ejeren af filen kan oprette Vibe'en")


async def create_project(
    db: Db, principal: Principal, pid: str, title: str | None, request: Request, kind: str = "webapp"
) -> Project:
    if kind not in KINDS:
        raise ApiError(400, "bad_kind", "Ukendt projekttype")
    await _assert_can_create(db, principal, pid)
    if await db.get(Project, pid) is not None:
        raise ApiError(409, "exists", "Vibe findes allerede")
    slot = await claim(db, pid)
    title = (title or "Ny Vibe").strip()[:120] or "Ny Vibe"
    project = Project(
        id=pid,
        title=title,
        kind=kind,
        slug=make_slug(title),
        owner_sub=principal.sub,
        container_name=f"vibe-dev-{pid}",
        volume_name=slot.volume_name,
        network_name=slot.network_name,
        image_tag=slot.image_tag,
        token_seed=slot.token_seed,
        dev_token_hash=hash_token(dev_token(slot.token_seed)),
        runtime_token_hash=hash_token(runtime_token(pid, slot.token_seed)),
        status="ready",
    )
    db.add(project)
    audit(db, "project.create", actor_sub=principal.sub, project_id=pid,
          details={"title": title, "kind": kind, "container": slot.container_name}, request=request)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError(409, "exists", "Vibe findes allerede")
    await db.refresh(project)
    if kind == "scratch":
        # The warm pool only holds webapp containers; convert the claimed one. Its
        # workspace holds nothing but the platform's seed, so nothing is lost. A
        # failure leaves the project in "error" rather than as a webapp in disguise.
        try:
            async with await agentd(project, timeout=60) as client:
                raise_for(await client.post("/reset-scratch"), "Start fra bunden")
        except Exception:
            project.status = "error"
            await db.commit()
            raise
    return project


@router.get("/api/projects/{pid}/download")
async def download(pid: str, principal: CurrentPrincipal, db: Db, sha: str = "HEAD") -> Response:
    """The project's files at `sha` as a .zip: how a "fra bunden" project leaves
    SlopTown (e.g. an Arduino sketch for the IDE), and a copy of any webapp."""
    project, _ = await require_project(db, principal, pid, "editor")
    async with await agentd(project, timeout=120) as client:
        r = await client.post("/export", json={"sha": sha})
    if r.status_code == 422:
        raise ApiError(422, "locked_files_changed", "Platformens filer er ændret", paths=r.json().get("paths", []))
    if r.status_code >= 400:
        raise_for(r, "Eksport")
    buf = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(r.content)) as tar, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for m in tar.getmembers():
            if m.isfile():
                z.writestr(m.name, tar.extractfile(m).read())  # type: ignore[union-attr]
    name = make_slug(project.title).rsplit("-", 1)[0]
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )


@router.get("/api/projects")
async def list_projects(principal: CurrentPrincipal, db: Db) -> list[dict[str, Any]]:
    shared_ids = select(MembersCache.project_id).where(
        MembersCache.payload["members"].contains([{"sub": principal.sub}])
    )
    rows = (
        await db.execute(
            select(Project)
            .where(or_(Project.owner_sub == principal.sub, Project.id.in_(shared_ids)))
            .order_by(Project.updated_at.desc())
        )
    ).scalars().all()
    out = []
    for p in rows:
        if p.owner_sub == principal.sub:
            role = "owner"
        else:
            cache = await db.get(MembersCache, p.id)
            m = member_by_sub(cache.payload if cache else {}, principal.sub)
            if not m:
                continue
            role = m["role"]
        out.append(project_json(p, role))
    return out


class CreateBody(BaseModel):
    fileId: str
    title: str | None = None
    kind: Literal["webapp", "scratch"] = "webapp"


@router.post("/api/projects", status_code=201)
async def post_project(body: CreateBody, principal: CurrentPrincipal, db: Db, request: Request) -> dict[str, Any]:
    project = await create_project(db, principal, normalize_id(body.fileId), body.title, request, body.kind)
    return project_json(project, "owner")


@router.get("/api/projects/{pid}")
async def get_project(pid: str, principal: CurrentPrincipal, db: Db, request: Request) -> dict[str, Any]:
    pid = normalize_id(pid)
    sess = principal.session
    if await db.get(Project, pid) is None and sess.file_id == pid and sess.file_role == "owner":
        # The owner opening a new Vibe chooses what it is (webapp or "fra bunden")
        # before anything is created; the studio answers with POST /api/projects.
        await _assert_can_create(db, principal, pid)
        raise ApiError(404, "not_started", "Vælg hvad du vil bygge", canStart=True)
    project, role = await require_project(db, principal, pid)
    return project_json(project, role)


class PatchBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    public: bool | None = None


@router.patch("/api/projects/{pid}")
async def patch_project(pid: str, body: PatchBody, principal: CurrentPrincipal, db: Db, request: Request) -> dict[str, Any]:
    project, role = await require_project(db, principal, pid, "editor")
    if body.public is not None and role != "owner":
        raise ApiError(403, "owner_only", "Kun ejeren kan gøre appen offentlig eller lukke den igen")
    if body.title is not None:
        old = project.title
        project.title = body.title.strip()
        audit(db, "project.rename", actor_sub=principal.sub, project_id=pid, details={"from": old, "to": project.title}, request=request)
    if body.description is not None and body.description.strip() != project.description:
        project.description = body.description.strip()
        audit(db, "project.describe", actor_sub=principal.sub, project_id=pid, details={"length": len(project.description)}, request=request)
    if body.public is not None and body.public != project.public:
        require_webapp(project)
        project.public = body.public
        audit(db, "project.visibility", actor_sub=principal.sub, project_id=pid, details={"public": project.public}, request=request)
    await db.commit()
    await db.refresh(project)
    return project_json(project, role)


class PublishBody(BaseModel):
    sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")


@router.post("/api/projects/{pid}/publish")
async def publish(pid: str, body: PublishBody, principal: CurrentPrincipal, db: Db, request: Request) -> dict[str, Any]:
    project, _ = await require_project(db, principal, pid, "editor")
    require_webapp(project)
    async with await agentd(project, timeout=120) as client:
        r = await client.post("/export", json={"sha": body.sha})
    if r.status_code == 422:  # agentd refuses exports that change platform-owned files
        detail = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        raise ApiError(422, "locked_files_changed", "Versionen ændrer platformens filer og kan ikke udgives", **detail)
    if r.status_code >= 400:
        raise_for(r, "Eksport")
    sha = r.headers.get("x-vibe-sha", body.sha)
    assert project.token_seed and project.network_name and project.volume_name
    try:
        await get_orchestrator().publish(pid, project.network_name, project.volume_name, project.token_seed, sha, r.content)
    except BuildFailed as e:
        audit(db, "project.publish_failed", actor_sub=principal.sub, project_id=pid, details={"sha": sha}, request=request)
        await db.commit()
        raise ApiError(422, "build_failed", "Appen kunne ikke bygges. Bed AI'en rette fejlen og prøv igen.", log=e.log)
    project.published_sha = sha
    project.published_at = datetime.now(UTC)
    audit(db, "project.publish", actor_sub=principal.sub, project_id=pid, details={"sha": sha}, request=request)
    await db.commit()
    url = host_url(project.slug) + "/"
    hub.publish(f"project:{pid}", "project.published", {"publishedSha": sha, "url": url})
    return {"publishedSha": sha, "url": url}


@router.post("/api/projects/{pid}/unpublish")
async def unpublish(pid: str, principal: CurrentPrincipal, db: Db, request: Request) -> dict[str, Any]:
    project, _ = await require_project(db, principal, pid, "editor")
    await get_orchestrator().unpublish(pid)
    old = project.published_sha
    project.published_sha = None
    project.published_at = None
    audit(db, "project.unpublish", actor_sub=principal.sub, project_id=pid, details={"sha": old}, request=request)
    await db.commit()
    hub.publish(f"project:{pid}", "project.published", {"publishedSha": None, "url": None})
    return {"ok": True}


@router.get("/api/projects/{pid}/budget")
async def project_budget(pid: str, principal: CurrentPrincipal, db: Db) -> dict[str, Any]:
    await require_project(db, principal, pid)
    return await budgets.budget_status(db, pid, principal.sub)


async def mint_edge_code(db: Db, project: Project, sub: str, kind: str, view_as: str | None = None) -> str:
    code = random_token(24)
    db.add(
        EdgeCode(
            code_hash=hash_token(code),
            project_id=project.id,
            sub=sub,
            kind=kind,
            view_as=view_as,
            expires_at=datetime.now(UTC) + EDGE_CODE_TTL,
        )
    )
    await db.commit()
    return code


class PreviewSessionBody(BaseModel):
    viewAs: Literal["owner", "editor", "viewer"] | None = None


@router.post("/api/projects/{pid}/preview-session")
async def preview_session(
    pid: str, principal: CurrentPrincipal, db: Db, body: PreviewSessionBody | None = None
) -> dict[str, Any]:
    """One-time URL for the preview host. `viewAs` (dev only) is bound to the code and set as a cookie."""
    project, _ = await require_project(db, principal, pid, "editor")
    require_webapp(project)
    view_as = body.viewAs if body and get_settings().dev_mode else None
    code = await mint_edge_code(db, project, principal.sub, "preview", view_as)
    url = f"{host_url(project.slug, preview=True)}/_vibe/session?code={code}&next=/"
    return {"url": url + (f"&viewAs={view_as}" if view_as else "")}


@router.post("/api/projects/{pid}/app-session")
async def app_session(pid: str, principal: CurrentPrincipal, db: Db) -> dict[str, Any]:
    project, _ = await require_project(db, principal, pid)
    require_webapp(project)
    if not project.published_sha:
        raise ApiError(409, "not_published", "Vibe er ikke udgivet endnu")
    code = await mint_edge_code(db, project, principal.sub, "app")
    return {"url": f"{host_url(project.slug)}/_vibe/session?code={code}&next=/"}
