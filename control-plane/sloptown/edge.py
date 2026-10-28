"""Edge integration: Caddy `forward_auth` for app and preview hosts, and host-cookie login.

Flow for `<slug>.APPS` (and `preview-<slug>.APPS`):
1. Caddy calls `/edge/auth/app` (or `/edge/auth/preview`) with the original
   request's headers and `X-Forwarded-Host`/`-Uri`.
2. No `vibe_app` cookie -> 302 to `STUDIO/auth/app-login`, which needs a studio
   session, checks the role, mints a one-time code and redirects to
   `<host>/_vibe/session?code=` (Caddy routes that path to `/edge/session`).
3. `/edge/session` swaps the code for a host-only partitioned cookie.
4. With a cookie, forward_auth answers 200 with `X-Vibe-*` identity headers and
   `X-Vibe-Upstream`, which Caddy copies onto the proxied request.

Apps only ever see the per-Vibe member id, never the service-level `sub`.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select

from .access import ROLE_RANK, role_for, role_for_sub
from .activity import touch
from .audit import audit
from .auth import Db, cookie_name, optional_principal, set_cookie
from .config import get_settings
from .containers import RUNTIME_PORT, VITE_PORT
from .errors import ApiError
from .members import MembersUnavailable, get_members, member_by_sub
from .models import EdgeCode, HostSession, Project
from .orchestrator import OrchestratorError, get_orchestrator, runtime_container_name
from .projects import host_url, mint_edge_code
from .tokens import edge_secret_for, hash_token, random_token

router = APIRouter()

HOST_SESSION_TTL = timedelta(hours=12)
COOKIE = {"app": "vibe_app", "preview": "vibe_preview"}
VIEW_AS_NAMES = {"owner": "Test-ejer", "editor": "Test-redaktør", "viewer": "Test-elev"}


# Platform hosts that may share the apps domain (e.g. studio.miira-apps.dk in a test setup).
# Slugs always end in a random suffix, so these can't be slugs; this is a second guard.
RESERVED_LABELS = frozenset({"studio", "miira", "www", "admin", "api", "preview"})


def parse_host(host: str) -> tuple[str, str] | None:
    """`<slug>.APPS` -> ("app", slug); `preview-<slug>.APPS` -> ("preview", slug)."""
    host = host.split(":")[0].lower()
    suffix = "." + get_settings().apps_domain
    if not host.endswith(suffix):
        return None
    label = host[: -len(suffix)]
    if not label or "." in label or label in RESERVED_LABELS:
        return None
    if label.startswith("preview-"):
        return "preview", label[len("preview-") :]
    return "app", label


def _original_url(request: Request) -> str:
    s = get_settings()
    host = request.headers.get("x-forwarded-host", "")
    uri = request.headers.get("x-forwarded-uri", "/")
    proto = request.headers.get("x-forwarded-proto", s.apps_scheme)
    return f"{proto}://{host}{uri}"


def local_member_id(project_id: str, sub: str) -> str:
    """Fallback per-Vibe id for a user missing from Miira's list (e.g. the owner in a stale cache)."""
    key = get_settings().internal_secret.encode()
    return "l" + hashlib.sha256(key + f"{project_id}:{sub}".encode()).hexdigest()[:25]


async def _project_for_host(db: Db, request: Request, want: str) -> Project:
    parsed = parse_host(request.headers.get("x-forwarded-host", ""))
    if parsed is None or parsed[0] != want:
        raise ApiError(404, "unknown_host", "Ukendt adresse")
    project = (await db.execute(select(Project).where(Project.slug == parsed[1]))).scalar_one_or_none()
    # A "fra bunden" project has no hosts at all: it is never served on the web.
    if project is None or project.kind == "scratch":
        raise ApiError(404, "unknown_host", "Ukendt adresse")
    return project


async def _host_session_sub(db: Db, request: Request, project: Project, kind: str) -> str | None:
    token = request.cookies.get(cookie_name(COOKIE[kind]))
    if not token:
        return None
    hs = await db.get(HostSession, hash_token(token))
    if hs is None or hs.project_id != project.id or hs.kind != kind or hs.expires_at < datetime.now(UTC):
        return None
    return hs.sub


def _login_redirect(project: Project, kind: str, request: Request) -> Response:
    s = get_settings()
    q = urlencode({"project": project.id, "kind": kind, "next": urlparse(_original_url(request)).path or "/"})
    return RedirectResponse(f"{s.studio_origin.rstrip('/')}/auth/app-login?{q}", status_code=302)


def _identity_headers(project: Project, member_id: str, name: str, role: str, classes: list[str], upstream: str) -> dict[str, str]:
    return {
        "X-Vibe-Edge-Secret": edge_secret_for(project.token_seed or ""),
        "X-Vibe-User": member_id,
        "X-Vibe-Name": quote(name or ""),
        "X-Vibe-Role": role,
        "X-Vibe-Classes": ",".join(quote(c) for c in classes),
        "X-Vibe-Upstream": upstream,
    }


async def _app_upstream(project: Project) -> str:
    try:
        host = await get_orchestrator().address(runtime_container_name(project.id), project.network_name or "")
    except OrchestratorError:
        raise ApiError(503, "not_running", "Vibe starter op. Prøv igen om lidt.")
    return f"{host}:{RUNTIME_PORT}"


async def _forward_auth(request: Request, db: Db, kind: str) -> Response:
    project = await _project_for_host(db, request, kind)
    if kind == "app" and not project.published_sha:
        raise ApiError(404, "not_published", "Vibe er ikke udgivet")
    sub = await _host_session_sub(db, request, project, kind)
    role = await role_for_sub(db, project, sub) if sub else None
    # A public app lets everyone in. Without a member session the app gets no X-Vibe-User,
    # so the SDK's current_user answers 401 and optional_user None: nobody is anyone.
    if kind == "app" and project.public and role is None:
        return Response(
            status_code=200,
            headers={"X-Vibe-Edge-Secret": edge_secret_for(project.token_seed or ""), "X-Vibe-Upstream": await _app_upstream(project)},
        )
    if sub is None:
        return _login_redirect(project, kind, request)
    if role is None or (kind == "preview" and ROLE_RANK[role] < ROLE_RANK["editor"]):
        raise ApiError(403, "forbidden", "Du har ikke adgang til denne Vibe")

    member: dict[str, Any] | None = None
    try:
        member = member_by_sub(await get_members(db, project.id), sub)
    except MembersUnavailable:
        pass
    member_id = member["id"] if member else local_member_id(project.id, sub)
    name = member["name"] if member else ""
    classes = list(member.get("classes") or []) if member else []

    if kind == "preview" and get_settings().dev_mode:
        view_as = request.headers.get("x-vibe-view-as") or request.cookies.get(cookie_name("vibe_view_as"))
        if view_as in VIEW_AS_NAMES and view_as != role:
            member_id, name, role, classes = f"dev-{view_as}", VIEW_AS_NAMES[view_as], view_as, ["Testklasse"]

    if kind == "app":
        upstream = await _app_upstream(project)
        return Response(status_code=200, headers=_identity_headers(project, member_id, name, role, classes, upstream))
    assert project.container_name and project.network_name
    orch = get_orchestrator()
    try:
        touch(project.id)
        await orch.ensure_running(project.container_name)
        host = await orch.address(project.container_name, project.network_name)
        upstream = f"{host}:{VITE_PORT}"
    except OrchestratorError:
        raise ApiError(503, "not_running", "Vibe starter op. Prøv igen om lidt.")
    return Response(status_code=200, headers=_identity_headers(project, member_id, name, role, classes, upstream))


@router.get("/edge/auth/app")
async def auth_app(request: Request, db: Db) -> Response:
    return await _forward_auth(request, db, "app")


@router.get("/edge/auth/preview")
async def auth_preview(request: Request, db: Db) -> Response:
    return await _forward_auth(request, db, "preview")


@router.get("/edge/tls-ask")
async def tls_ask(db: Db, domain: str = "") -> Response:
    """Caddy on-demand TLS: only issue certificates for hosts of existing projects,
    plus the platform's own hosts when they share the apps domain (public test setup,
    where Caddy treats studio.<apps> as covered by the on-demand *.<apps> site)."""
    s = get_settings()
    platform_hosts = {urlparse(o).hostname for o in [s.studio_origin, *s.miira_origins]}
    if domain.lower() in platform_hosts:
        return Response(status_code=200)
    parsed = parse_host(domain)
    if parsed is None:
        return Response(status_code=404)
    found = (
        await db.execute(select(Project.id).where(Project.slug == parsed[1], Project.kind != "scratch"))
    ).first()
    return Response(status_code=200 if found else 404)


@router.get("/auth/app-login")
async def app_login(request: Request, db: Db, project: str, kind: str = "app", next: str = "/") -> Response:
    """Studio-host step: turn the studio session into a one-time code for the app/preview host."""
    if kind not in COOKIE:
        raise ApiError(400, "bad_kind", "Ugyldig forespørgsel")
    principal = await optional_principal(request, db)
    if principal is None:
        here = f"/auth/app-login?{urlencode({'project': project, 'kind': kind, 'next': next})}"
        return RedirectResponse(f"/auth/login?{urlencode({'next': here})}", status_code=302)
    p = await db.get(Project, project)
    if p is None:
        raise ApiError(404, "not_found", "Vibe findes ikke")
    role = await role_for(db, principal, p)
    if role is None or (kind == "preview" and ROLE_RANK[role] < ROLE_RANK["editor"]):
        raise ApiError(403, "forbidden", "Du har ikke adgang til denne Vibe")
    code = await mint_edge_code(db, p, principal.sub, kind)
    nxt = next if next.startswith("/") and not next.startswith("//") else "/"
    q = urlencode({"code": code, "next": nxt})
    return RedirectResponse(f"{host_url(p.slug, preview=kind == 'preview')}/_vibe/session?{q}", status_code=302)


@router.get("/edge/session")
async def edge_session(request: Request, db: Db, code: str, next: str = "/") -> Response:
    """App/preview-host step: swap a one-time code for a host-only cookie."""
    parsed = parse_host(request.headers.get("x-forwarded-host") or request.headers.get("host", ""))
    if parsed is None:
        raise ApiError(404, "unknown_host", "Ukendt adresse")
    kind, slug = parsed
    row = await db.get(EdgeCode, hash_token(code))
    if row is None or row.expires_at < datetime.now(UTC) or row.kind != kind:
        raise ApiError(400, "bad_code", "Linket er udløbet. Prøv igen.")
    project = await db.get(Project, row.project_id)
    if project is None or project.slug != slug:
        raise ApiError(400, "bad_code", "Linket er udløbet. Prøv igen.")
    await db.delete(row)
    await db.execute(delete(EdgeCode).where(EdgeCode.expires_at < datetime.now(UTC)))
    token = random_token()
    db.add(
        HostSession(
            token_hash=hash_token(token),
            project_id=project.id,
            sub=row.sub,
            kind=kind,
            expires_at=datetime.now(UTC) + HOST_SESSION_TTL,
        )
    )
    audit(db, f"edge.session.{kind}", actor_sub=row.sub, project_id=project.id, request=request)
    await db.commit()
    nxt = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(nxt, status_code=302)
    max_age = int(HOST_SESSION_TTL.total_seconds())
    set_cookie(response, COOKIE[kind], token, max_age=max_age, partitioned=True)
    if kind == "preview":
        # Always (re)set so switching back to "owner" clears an earlier view-as.
        set_cookie(response, "vibe_view_as", row.view_as or "", max_age=max_age if row.view_as else 0, partitioned=True)
    return response
