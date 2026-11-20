"""Files, version history, diff and restore, served from vibe-agentd in the project's vibe-dev container.

Also owns `commit_turn` (one git commit per AI turn) and the relay of agentd's
`files.changed` SSE into the project's event channel while a studio is listening.
"""

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .access import require_project
from .audit import audit
from .auth import CurrentPrincipal, Db
from .containers import agentd, raise_for
from .db import sessionmaker
from .errors import ApiError, forbidden
from .events import hub, iter_sse
from .models import Project, Thread, User

log = logging.getLogger(__name__)
router = APIRouter()

HIDDEN_PREFIXES = (".git/", "node_modules/", ".opencode/", ".data/", ".uploads/")
SHA_RE = r"^[0-9a-f]{7,40}$"


def is_hidden(path: str) -> bool:
    p = path.lstrip("/")
    return any(p.startswith(h) or f"/{h}" in f"/{p}" for h in HIDDEN_PREFIXES) or ".." in p.split("/")


def _summary(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= 72 else line[:71] + "…"


async def commit_turn(project: Project, thread: Thread, author: User, prompt: str) -> tuple[str | None, list[str]]:
    """Commit the working tree after a turn. Returns (sha or None if nothing changed, changed paths)."""
    async with await agentd(project, timeout=60) as client:
        body = raise_for(
            await client.post(
                "/commit",
                json={
                    "message": _summary(prompt) or "AI-ændring",
                    "trailers": {"Vibe-Thread": thread.id, "Vibe-Author": author.name or author.sub},
                },
            ),
            "Commit",
        ) or {}
    return body.get("sha"), list(body.get("files") or [])


@router.get("/api/projects/{pid}/files")
async def list_files(pid: str, principal: CurrentPrincipal, db: Db) -> dict[str, Any]:
    project, _ = await require_project(db, principal, pid, "editor")
    async with await agentd(project) as client:
        body = raise_for(await client.get("/files"), "Filer") or {}
    files = [f for f in body.get("files", []) if not is_hidden(f.get("path", ""))]
    return {"files": files}


@router.get("/api/projects/{pid}/files/{path:path}")
async def read_file(pid: str, path: str, principal: CurrentPrincipal, db: Db, sha: str | None = None) -> dict[str, Any]:
    project, _ = await require_project(db, principal, pid, "editor")
    if is_hidden(path):
        raise forbidden("Filen er skjult")
    params = {"sha": sha} if sha else None
    async with await agentd(project) as client:
        return raise_for(await client.get(f"/files/{path}", params=params), "Fil")


# What agentd's /raw may answer with (its RAW_TYPES): media for the file preview.
RAW_MEDIA = re.compile(r"^(image|audio|video)/[a-z0-9.+-]+$|^application/pdf$")


@router.get("/api/projects/{pid}/raw/{path:path}")
async def read_raw(pid: str, path: str, principal: CurrentPrincipal, db: Db, sha: str | None = None) -> Response:
    """A media file's bytes for the studio's file preview (fetched with the bearer, shown
    from a blob URL). On the studio origin, so: download-only and sandboxed if opened
    directly, never sniffed."""
    project, _ = await require_project(db, principal, pid, "editor")
    if is_hidden(path):
        raise forbidden("Filen er skjult")
    params = {"sha": sha} if sha else None
    async with await agentd(project, timeout=60) as client:
        r = await client.get(f"/raw/{path}", params=params)
    if r.status_code == 415:
        raise ApiError(415, "no_preview", "Filen kan ikke vises")
    if r.status_code == 413:
        raise ApiError(413, "too_large", "Filen er for stor til at blive vist")
    if r.status_code >= 400:
        raise_for(r, "Fil")
    media = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if not RAW_MEDIA.match(media):
        raise ApiError(502, "upstream_error", "Fil fejlede")
    return Response(
        r.content,
        media_type=media,
        headers={
            "Content-Disposition": "attachment",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/api/projects/{pid}/history")
async def history(pid: str, principal: CurrentPrincipal, db: Db) -> dict[str, Any]:
    project, _ = await require_project(db, principal, pid, "editor")
    async with await agentd(project) as client:
        body = raise_for(await client.get("/history"), "Historik") or {}
    commits = body.get("commits", [])
    tids = {c.get("threadId") for c in commits if c.get("threadId")}
    titles: dict[str, str] = {}
    if tids:
        rows = (await db.execute(select(Thread.id, Thread.title).where(Thread.id.in_(tids)))).all()
        titles = {r.id: r.title for r in rows}
    for c in commits:
        c["threadTitle"] = titles.get(c.get("threadId") or "")
    return {"commits": commits, "publishedSha": project.published_sha}


@router.get("/api/projects/{pid}/history/{sha}/diff")
async def diff(pid: str, sha: str, principal: CurrentPrincipal, db: Db) -> dict[str, Any]:
    project, _ = await require_project(db, principal, pid, "editor")
    if not re.match(SHA_RE, sha):
        raise ApiError(400, "bad_sha", "Ugyldig version")
    async with await agentd(project) as client:
        body = raise_for(await client.get(f"/diff/{sha}"), "Diff") or {}
    body["files"] = [f for f in body.get("files", []) if not is_hidden(f.get("path", ""))]
    return body


class RestoreBody(BaseModel):
    sha: str = Field(pattern=SHA_RE)


@router.post("/api/projects/{pid}/restore")
async def restore(pid: str, body: RestoreBody, principal: CurrentPrincipal, db: Db, request: Request) -> dict[str, Any]:
    from .threads import turns

    project, _ = await require_project(db, principal, pid, "editor")
    async with turns.lock(pid):
        if turns.running_turn(pid):
            raise ApiError(409, "turn_running", "Vent til AI'en er færdig, før du gendanner")
        async with await agentd(project, timeout=60) as client:
            commit = raise_for(
                await client.post(
                    "/restore",
                    json={
                        "sha": body.sha,
                        "message": f"Gendan version {body.sha[:7]}",
                        "trailers": {"Vibe-Author": principal.user.name or principal.sub},
                    },
                ),
                "Gendan",
            )
    audit(db, "project.restore", actor_sub=principal.sub, project_id=pid, details={"to": body.sha, "commit": commit.get("sha")}, request=request)
    await db.commit()
    hub.publish(f"project:{pid}", "files.changed", {"paths": commit.get("files", [])})
    return commit


# ---------------------------------------------------------------- chat attachments

UPLOAD_MAX = 25 * 1024 * 1024  # agentd checks the same limit
UPLOAD_ID_RE = r"^[0-9a-f]{12}$"
UPLOAD_ERRORS = {
    "too_large": (413, "upload_too_large", "Filen er for stor. Den må højst fylde 25 MB."),
    "unpacked_too_large": (413, "upload_too_large", "Zip-filen fylder for meget, når den er pakket ud (højst 200 MB)."),
    "too_many_files": (413, "upload_too_large", "Zip-filen indeholder for mange filer (højst 2000)."),
    "bad_zip": (400, "bad_upload", "Zip-filen kunne ikke åbnes. Prøv at pakke den igen."),
    "encrypted_zip": (400, "bad_upload", "Zip-filen er beskyttet med adgangskode. Pak den uden adgangskode."),
    "empty": (400, "bad_upload", "Filen er tom."),
}


def upload_summary(m: dict[str, Any]) -> dict[str, Any]:
    """The part of an agentd upload manifest the studio and the turn keep (file list capped)."""
    return ({k: m[k] for k in ("id", "name", "kind", "dir", "bytes", "fileCount")}
            | {"files": m["files"][:200], "public": m.get("public")})


@router.post("/api/projects/{pid}/uploads", status_code=201)
async def upload(pid: str, name: str, principal: CurrentPrincipal, db: Db, request: Request) -> dict[str, Any]:
    """A file attached in the chat: stored (a .zip unpacked) in the project's hidden `.uploads/`."""
    project, _ = await require_project(db, principal, pid, "editor")
    if int(request.headers.get("content-length") or 0) > UPLOAD_MAX:
        raise ApiError(*UPLOAD_ERRORS["too_large"])
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > UPLOAD_MAX:
            raise ApiError(*UPLOAD_ERRORS["too_large"])
    if not body:
        raise ApiError(*UPLOAD_ERRORS["empty"])
    async with await agentd(project, timeout=120) as client:
        r = await client.post("/uploads", params={"name": name[:255]}, content=bytes(body))
    if r.status_code in (400, 413):
        detail = (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}).get("detail")
        raise ApiError(*UPLOAD_ERRORS.get(detail, UPLOAD_ERRORS["bad_zip"]))
    manifest = upload_summary(raise_for(r, "Upload"))
    audit(db, "project.upload", actor_sub=principal.sub, project_id=pid, request=request,
          details={"id": manifest["id"], "name": manifest["name"], "bytes": manifest["bytes"], "fileCount": manifest["fileCount"]})
    await db.commit()
    return manifest


def _detail(r: httpx.Response) -> Any:
    try:
        return r.json().get("detail")
    except (ValueError, AttributeError):
        return None


async def upload_manifests(db: Any, actor_sub: str, project: Project, ids: list[str]) -> list[dict[str, Any]]:
    """The attached uploads of a message being sent (ids from its body), each copied into the
    project's public library when the app can show it as it is (agentd decides, once)."""
    if not ids:
        return []
    out = []
    async with await agentd(project) as client:
        for uid in ids:
            r = await client.post(f"/uploads/{uid}/public")
            if r.status_code in (404, 405) and _detail(r) != "unknown upload":
                r = await client.get(f"/uploads/{uid}")  # an agentd from before the library
            if r.status_code == 404:
                raise ApiError(400, "unknown_upload", "En vedhæftet fil findes ikke længere. Vedhæft den igen.")
            m = upload_summary(raise_for(r, "Upload"))
            if m["public"]:
                audit(db, "project.upload.public", actor_sub=actor_sub, project_id=project.id,
                      details={"id": m["id"], "name": m["name"], "path": m["public"]})
            out.append(m)
    await db.commit()
    return out


# ---------------------------------------------------------------- live file events

_relays: dict[str, asyncio.Task[None]] = {}


async def _relay(pid: str) -> None:
    """Forward agentd's files.changed events into project:<pid> until cancelled."""
    while True:
        try:
            async with sessionmaker()() as db:
                project = await db.get(Project, pid)
            if project is None:
                return
            async with await agentd(project, timeout=None) as client, client.stream("GET", "/events") as stream:
                async for event, data in iter_sse(stream.aiter_lines()):
                    if event == "files.changed":
                        paths = [p for p in json.loads(data).get("paths", []) if not is_hidden(p)]
                        if paths:
                            hub.publish(f"project:{pid}", "files.changed", {"paths": paths})
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, ApiError, ValueError) as e:
            log.info("agentd event relay for %s dropped: %s", pid, e)
        await asyncio.sleep(3)


def start_relay(channel: str) -> None:
    if channel.startswith("project:"):
        pid = channel.split(":", 1)[1]
        if pid not in _relays:
            _relays[pid] = asyncio.create_task(_relay(pid), name=f"relay-{pid}")


def stop_relay(channel: str) -> None:
    if channel.startswith("project:"):
        task = _relays.pop(channel.split(":", 1)[1], None)
        if task:
            task.cancel()
