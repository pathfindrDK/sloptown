"""Chat threads and AI turns, proxied to `opencode serve` in the project's vibe-dev container.

Each thread is one OpenCode session. All threads of a project share one working
tree, so only ONE turn runs per project at a time and at most ONE more waits in
the queue (`409 queue_full` beyond that). While a turn runs,
`projects.active_turn_sub` names its author so the egress proxy charges the
build budget to the right teacher. When a turn ends, the tree is committed via
vibe-agentd with `Vibe-Thread`/`Vibe-Author` trailers.

The run state lives in this process (`TurnManager`); the `turns` table records
it. On startup, turns left running/queued by a previous process are marked error.
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from . import budgets
from .access import require_project
from .audit import audit
from .auth import CurrentPrincipal, Db
from .containers import opencode, raise_for
from .db import sessionmaker
from .errors import ApiError, not_found
from .events import hub, iter_sse
from .files import commit_turn
from .models import Project, Thread, Turn, User

log = logging.getLogger(__name__)
router = APIRouter()

TURN_TIMEOUT = 30 * 60
MAX_OUTPUT = 4000


# ---------------------------------------------------------------- mapping

def _ms_iso(ms: int | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat() if ms else None


def map_part(part: dict[str, Any]) -> dict[str, Any] | None:
    """OpenCode Part -> studio Part (docs/internal-api.md). None for parts the studio doesn't show."""
    t = part.get("type")
    if t == "text":
        if part.get("synthetic") or part.get("ignored"):
            return None
        return {"type": "text", "text": part.get("text", "")}
    if t == "tool":
        state = part.get("state") or {}
        status = {"completed": "done", "error": "error"}.get(state.get("status", ""), "running")
        out: dict[str, Any] = {
            "type": "tool",
            "tool": part.get("tool", ""),
            "title": state.get("title") or part.get("tool", ""),
            "status": status,
            "input": state.get("input"),
        }
        if status == "done":
            out["output"] = (state.get("output") or "")[:MAX_OUTPUT]
        elif status == "error":
            out["output"] = (state.get("error") or "")[:MAX_OUTPUT]
        return out
    if t == "file" and str(part.get("mime", "")).startswith("image/"):
        return {"type": "image", "mime": part["mime"], "url": part.get("url", "")}
    return None


def map_message(item: dict[str, Any], thread_id: str) -> dict[str, Any]:
    info, raw_parts = item.get("info", {}), item.get("parts", [])
    author = None
    for p in raw_parts:
        meta = p.get("metadata") or {}
        if meta.get("vibeAuthor"):
            author = meta["vibeAuthor"]
            break
    parts = [mp for mp in (map_part(p) for p in raw_parts) if mp is not None]
    return {
        "id": info.get("id"),
        "threadId": thread_id,
        "role": info.get("role", "assistant"),
        "authorName": author,
        "parts": parts,
        "createdAt": _ms_iso((info.get("time") or {}).get("created")),
    }


def _event_session(ev: dict[str, Any]) -> str | None:
    props = ev.get("properties") or {}
    if "sessionID" in props:
        return props["sessionID"]
    if "part" in props:
        return props["part"].get("sessionID")
    if "info" in props:
        return props["info"].get("sessionID")
    return None


def thread_json(t: Thread, status: str = "idle") -> dict[str, Any]:
    return {
        "id": t.id,
        "projectId": t.project_id,
        "title": t.title,
        "ownerSub": t.owner_sub,
        "createdAt": t.created_at.isoformat() if t.created_at else None,
        "archived": t.archived,
        "status": status,
    }


# ---------------------------------------------------------------- turn manager

class TurnManager:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}  # project_id -> running turn task
        self._running: dict[str, str] = {}  # project_id -> turn_id
        self._stop: set[str] = set()  # turn ids asked to stop

    def lock(self, pid: str) -> asyncio.Lock:
        return self._locks.setdefault(pid, asyncio.Lock())

    def running_turn(self, pid: str) -> str | None:
        return self._running.get(pid)

    async def submit(self, db: Any, project: Project, thread: Thread, author: User, text: str, images: list[dict[str, str]]) -> dict[str, Any]:
        async with self.lock(project.id):
            allowed, retry_at = await budgets.check_build_allowed(db, author.sub)
            if not allowed:
                raise ApiError(
                    429, "budget_blocked", "Du har brugt din AI-kvote for denne uge",
                    retryAt=retry_at.isoformat() if retry_at else None,
                )
            busy = project.id in self._running
            if busy:
                queued = (
                    await db.execute(select(Turn.id).where(Turn.project_id == project.id, Turn.status == "queued"))
                ).first()
                if queued:
                    raise ApiError(409, "queue_full", "Der venter allerede en besked. Vent til den er færdig.")
            turn = Turn(
                id=uuid.uuid4().hex,
                project_id=project.id,
                thread_id=thread.id,
                author_sub=author.sub,
                text=text,
                images=images,
                status="queued" if busy else "running",
            )
            db.add(turn)
            await db.commit()
            if busy:
                hub.publish(f"project:{project.id}", "turn.queued", {"threadId": thread.id, "turnId": turn.id})
            else:
                self._start(project.id, turn.id)
            return {"turnId": turn.id, "queued": busy}

    def _start(self, pid: str, turn_id: str) -> None:
        self._running[pid] = turn_id
        self._tasks[pid] = asyncio.create_task(self._run(pid, turn_id), name=f"turn-{turn_id}")

    async def stop(self, db: Any, thread: Thread) -> bool:
        pid = thread.project_id
        stopped = False
        queued = (
            await db.execute(select(Turn).where(Turn.thread_id == thread.id, Turn.status == "queued"))
        ).scalars().all()
        for t in queued:
            t.status = "stopped"
            t.finished_at = datetime.now(UTC)
            hub.publish(f"project:{pid}", "turn.finished", {"threadId": thread.id, "turnId": t.id, "commitSha": None, "changedFiles": [], "status": "stopped"})
            stopped = True
        await db.commit()
        turn_id = self._running.get(pid)
        if turn_id:
            turn = await db.get(Turn, turn_id)
            if turn and turn.thread_id == thread.id:
                self._stop.add(turn_id)
                project = await db.get(Project, pid)
                async with await opencode(project) as client:
                    await client.post(f"/session/{thread.opencode_session_id}/abort")
                stopped = True
        return stopped

    async def wait_idle(self, pid: str) -> None:
        """Test helper: wait until the project has no running or queued turn."""
        while pid in self._tasks:
            await asyncio.shield(self._tasks[pid])

    async def _run(self, pid: str, turn_id: str) -> None:
        channel = f"project:{pid}"
        status, commit_sha, changed = "error", None, []
        thread_id = ""
        try:
            async with sessionmaker()() as db:
                turn = await db.get(Turn, turn_id)
                project = await db.get(Project, pid)
                thread = await db.get(Thread, turn.thread_id)
                author = await db.get(User, turn.author_sub)
                thread_id = thread.id
                turn.status = "running"
                turn.started_at = datetime.now(UTC)
                project.active_turn_sub = turn.author_sub
                project.last_active_at = datetime.now(UTC)
                audit(db, "turn.start", actor_sub=turn.author_sub, project_id=pid, details={"turn": turn_id, "thread": thread.id})
                await db.commit()
                hub.publish(channel, "turn.started", {"threadId": thread.id, "turnId": turn_id})
                hub.publish(channel, "thread.updated", thread_json(thread, "running"))

                try:
                    status = await asyncio.wait_for(
                        self._converse(project, thread, author, turn), timeout=TURN_TIMEOUT
                    )
                except TimeoutError:
                    status = "error"
                    log.warning("turn %s timed out", turn_id)
                if turn_id in self._stop:
                    status = "stopped"

                try:
                    commit_sha, changed = await commit_turn(project, thread, author, turn.text)
                except Exception:
                    log.exception("commit after turn %s failed", turn_id)

                turn.status = status
                turn.commit_sha = commit_sha
                turn.finished_at = datetime.now(UTC)
                project.active_turn_sub = None
                project.last_active_at = datetime.now(UTC)
                audit(db, "turn.finish", actor_sub=turn.author_sub, project_id=pid, details={"turn": turn_id, "status": status, "commit": commit_sha})
                await db.commit()
                if changed:
                    hub.publish(channel, "files.changed", {"paths": changed})
                try:
                    hub.publish(channel, "budget", await budgets.budget_status(db, pid, turn.author_sub))
                except Exception:
                    log.exception("budget status after turn failed")
        except Exception:
            log.exception("turn %s crashed", turn_id)
            async with sessionmaker()() as db:
                await db.execute(update(Turn).where(Turn.id == turn_id).values(status="error", finished_at=datetime.now(UTC)))
                await db.execute(update(Project).where(Project.id == pid).values(active_turn_sub=None))
                await db.commit()
        finally:
            hub.publish(channel, "turn.finished", {"threadId": thread_id, "turnId": turn_id, "commitSha": commit_sha, "changedFiles": changed, "status": status})
            self._stop.discard(turn_id)
            await self._finish(pid)

    async def _finish(self, pid: str) -> None:
        """Release the project's turn slot and start the queued turn, atomically w.r.t. submit()."""
        async with self.lock(pid), sessionmaker()() as db:
            self._running.pop(pid, None)
            self._tasks.pop(pid, None)
            nxt = (
                await db.execute(
                    select(Turn).where(Turn.project_id == pid, Turn.status == "queued").order_by(Turn.created_at).limit(1)
                )
            ).scalar_one_or_none()
            if nxt is None:
                return
            allowed, retry_at = await budgets.check_build_allowed(db, nxt.author_sub)
            if not allowed:
                nxt.status = "error"
                nxt.finished_at = datetime.now(UTC)
                await db.commit()
                hub.publish(f"project:{pid}", "turn.finished", {"threadId": nxt.thread_id, "turnId": nxt.id, "commitSha": None, "changedFiles": [], "status": "error", "error": "budget_blocked", "retryAt": retry_at.isoformat() if retry_at else None})
                return
            nxt.status = "running"
            await db.commit()
            self._start(pid, nxt.id)

    async def _converse(self, project: Project, thread: Thread, author: User, turn: Turn) -> str:
        """Send the prompt and relay OpenCode's events until the session goes idle."""
        channel = f"project:{project.id}"
        sid = thread.opencode_session_id
        parts: list[dict[str, Any]] = [
            {"type": "text", "text": turn.text, "metadata": {"vibeAuthor": author.name, "vibeTurn": turn.id}}
        ]
        for img in turn.images or []:
            parts.append({"type": "file", "mime": img["mime"], "url": img["dataUrl"], "filename": "billede"})
        part_index: dict[str, list[str]] = {}
        seen_messages: set[str] = set()
        status = "done"
        async with await opencode(project, timeout=None) as client:
            async with client.stream("GET", "/event") as stream:
                r = await client.post(f"/session/{sid}/prompt_async", json={"parts": parts})
                if r.status_code >= 400:
                    raise_for(r, "OpenCode")
                async for _event, data in iter_sse(stream.aiter_lines()):
                    try:
                        ev = json.loads(data)
                    except ValueError:
                        continue
                    if _event_session(ev) != sid:
                        continue
                    etype, props = ev.get("type"), ev.get("properties") or {}
                    if etype == "message.updated":
                        info = props.get("info", {})
                        if info.get("id") not in seen_messages:
                            seen_messages.add(info["id"])
                            msg = map_message({"info": info, "parts": []}, thread.id)
                            if info.get("role") == "user":
                                msg["authorName"] = author.name
                            hub.publish(channel, "message.created", msg)
                        if (info.get("error") or {}).get("name") == "MessageAbortedError":
                            status = "stopped"
                    elif etype == "message.part.updated":
                        raw = props.get("part", {})
                        mapped = map_part(raw)
                        if mapped is None:
                            continue
                        ids = part_index.setdefault(raw["messageID"], [])
                        if raw["id"] not in ids:
                            ids.append(raw["id"])
                        hub.publish(channel, "message.part", {
                            "threadId": thread.id, "messageId": raw["messageID"],
                            "index": ids.index(raw["id"]), "part": mapped,
                        })
                    elif etype == "session.error":
                        name = (props.get("error") or {}).get("name")
                        status = "stopped" if name == "MessageAbortedError" else "error"
                    elif etype == "session.idle" or (
                        etype == "session.status" and (props.get("status") or {}).get("type") == "idle"
                    ):
                        break
        return status

    async def recover(self) -> None:
        """Mark turns orphaned by a previous process as errored."""
        async with sessionmaker()() as db:
            await db.execute(
                update(Turn).where(Turn.status.in_(["running", "queued"])).values(status="error", finished_at=datetime.now(UTC))
            )
            await db.execute(update(Project).values(active_turn_sub=None))
            await db.commit()


turns = TurnManager()


async def thread_status(db: Any, thread: Thread) -> str:
    rid = turns.running_turn(thread.project_id)
    if rid:
        t = await db.get(Turn, rid)
        if t and t.thread_id == thread.id:
            return "running"
    q = (await db.execute(select(Turn.id).where(Turn.thread_id == thread.id, Turn.status == "queued"))).first()
    return "queued" if q else "idle"


async def _load_thread(db: Any, principal: Any, tid: str, minimum: str = "editor") -> tuple[Thread, Project]:
    thread = await db.get(Thread, tid)
    if thread is None:
        raise not_found("Tråden findes ikke")
    project, _ = await require_project(db, principal, thread.project_id, minimum)
    return thread, project


# ---------------------------------------------------------------- routes

@router.get("/api/projects/{pid}/threads")
async def list_threads(pid: str, principal: CurrentPrincipal, db: Db, archived: int = 0) -> list[dict[str, Any]]:
    await require_project(db, principal, pid, "editor")
    q = select(Thread).where(Thread.project_id == pid).order_by(Thread.created_at.desc())
    if not archived:
        q = q.where(Thread.archived.is_(False))
    rows = (await db.execute(q)).scalars().all()
    return [thread_json(t, await thread_status(db, t)) for t in rows]


class NewThread(BaseModel):
    title: str | None = Field(default=None, max_length=120)


@router.post("/api/projects/{pid}/threads", status_code=201)
async def create_thread(pid: str, body: NewThread, principal: CurrentPrincipal, db: Db, request: Request) -> dict[str, Any]:
    project, _ = await require_project(db, principal, pid, "editor")
    title = (body.title or "").strip() or "Ny tråd"
    async with await opencode(project) as client:
        session = raise_for(await client.post("/session", json={"title": title}), "OpenCode")
    thread = Thread(id=uuid.uuid4().hex, project_id=pid, opencode_session_id=session["id"], title=title, owner_sub=principal.sub)
    db.add(thread)
    audit(db, "thread.create", actor_sub=principal.sub, project_id=pid, details={"thread": thread.id, "title": title}, request=request)
    await db.commit()
    await db.refresh(thread)
    data = thread_json(thread)
    hub.publish(f"project:{pid}", "thread.updated", data)
    return data


class PatchThread(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None


@router.patch("/api/threads/{tid}")
async def patch_thread(tid: str, body: PatchThread, principal: CurrentPrincipal, db: Db, request: Request) -> dict[str, Any]:
    thread, project = await _load_thread(db, principal, tid)
    if body.title is not None:
        thread.title = body.title.strip()
        try:
            async with await opencode(project) as client:
                await client.patch(f"/session/{thread.opencode_session_id}", json={"title": thread.title})
        except (httpx.HTTPError, ApiError):
            log.warning("could not rename OpenCode session for thread %s", tid)
    if body.archived is not None:
        thread.archived = body.archived
    audit(db, "thread.update", actor_sub=principal.sub, project_id=project.id, details={"thread": tid, **body.model_dump(exclude_none=True)}, request=request)
    await db.commit()
    data = thread_json(thread, await thread_status(db, thread))
    hub.publish(f"project:{project.id}", "thread.updated", data)
    return data


@router.get("/api/threads/{tid}/messages")
async def get_messages(tid: str, principal: CurrentPrincipal, db: Db) -> list[dict[str, Any]]:
    thread, project = await _load_thread(db, principal, tid)
    async with await opencode(project) as client:
        items = raise_for(await client.get(f"/session/{thread.opencode_session_id}/message"), "OpenCode") or []
    return [map_message(i, thread.id) for i in items]


class ImageIn(BaseModel):
    mime: str = Field(pattern=r"^image/(png|jpeg|gif|webp)$")
    dataUrl: str = Field(max_length=8_000_000)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    images: list[ImageIn] = Field(default_factory=list, max_length=6)


@router.post("/api/threads/{tid}/messages", status_code=202)
async def post_message(tid: str, body: MessageIn, principal: CurrentPrincipal, db: Db) -> dict[str, Any]:
    thread, project = await _load_thread(db, principal, tid)
    if thread.archived:
        raise ApiError(409, "archived", "Tråden er arkiveret")
    for img in body.images:
        if not img.dataUrl.startswith(f"data:{img.mime};base64,"):
            raise ApiError(400, "bad_image", "Billedet skal være en data-URL")
    return await turns.submit(db, project, thread, principal.user, body.text, [i.model_dump() for i in body.images])


@router.post("/api/threads/{tid}/stop")
async def stop_thread(tid: str, principal: CurrentPrincipal, db: Db, request: Request) -> dict[str, Any]:
    thread, project = await _load_thread(db, principal, tid)
    stopped = await turns.stop(db, thread)
    if stopped:
        audit(db, "turn.stop", actor_sub=principal.sub, project_id=project.id, details={"thread": tid}, request=request)
        await db.commit()
    return {"stopped": stopped}
