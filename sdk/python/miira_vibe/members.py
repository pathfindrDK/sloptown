"""Members of this Vibe: everyone Miira says has access, expanded from classes.

The list comes from the control plane (`GET /platform/members` through the
egress proxy, authenticated with VIBE_PROJECT_TOKEN). The app never talks to
Miira itself. Member ids are pairwise per Vibe and equal `User.id`.
"""

import asyncio
import builtins
import inspect
import json
import logging
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import _env
from .db import connect

log = logging.getLogger("miira_vibe.members")
CACHE_SECONDS = 15


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    role: str  # owner|editor|viewer
    classes: list[str] = field(default_factory=list)
    avatarHue: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "role": self.role, "classes": builtins.list(self.classes), "avatarHue": self.avatarHue}


_DEV_MEMBERS = [
    Member("dev-owner", "Lærer (dig)", "owner", [], 212),
    Member("dev-editor", "Medredaktør", "editor", [], 30),
    Member("dev-viewer", "Elev", "viewer", ["7.A"], 140),
    Member("dev-elev-2", "Freja (test)", "viewer", ["7.A"], 300),
    Member("dev-elev-3", "Noah (test)", "viewer", ["7.A"], 80),
]

_cache: tuple[float, str, list[Member]] | None = None


def _headers() -> dict[str, str]:
    return {"authorization": f"Bearer {_env.project_token()}"}


def invalidate() -> None:
    global _cache
    _cache = None


async def fetch() -> tuple[str, list[Member]]:
    """(version, members). Cached for CACHE_SECONDS; invalidated on change events."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1], _cache[2]
    base = _env.platform_url()
    if not base:
        if not _env.is_dev():
            raise RuntimeError("VIBE_PLATFORM_URL is not set")
        version, items = "dev", builtins.list(_DEV_MEMBERS)
    else:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            r = await client.get(f"{base}/platform/members", headers=_headers())
            r.raise_for_status()
            body = r.json()
        version = str(body.get("version", ""))
        items = [
            Member(
                id=m["id"],
                name=m.get("name", ""),
                role=m.get("role", "viewer"),
                classes=builtins.list(m.get("classes", [])),
                avatarHue=int(m.get("avatarHue", 0)),
            )
            for m in body.get("members", [])
        ]
    _cache = (time.monotonic(), version, items)
    return version, items


async def list() -> list[Member]:  # noqa: A001 - public API name from the plan
    return (await fetch())[1]


async def get(member_id: str) -> Member | None:
    return next((m for m in await list() if m.id == member_id), None)


SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL,
  classes TEXT NOT NULL DEFAULT '[]',
  avatar_hue INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  synced_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


async def sync(conn: sqlite3.Connection | None = None) -> int:
    """Upsert the member list into the app's SQLite `members` table.

    Members who lost access are kept with active=0 so rows that reference them
    stay valid. Returns the number of active members. Uses the given sqlite3
    connection, or opens VIBE_DB_PATH.
    """
    items = await list()
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute(SCHEMA)
        conn.execute("UPDATE members SET active = 0")
        conn.executemany(
            """INSERT INTO members (id, name, role, classes, avatar_hue, active, synced_at)
               VALUES (?, ?, ?, ?, ?, 1, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, role=excluded.role,
                 classes=excluded.classes, avatar_hue=excluded.avatar_hue, active=1, synced_at=excluded.synced_at""",
            [(m.id, m.name, m.role, json.dumps(m.classes, ensure_ascii=False), m.avatarHue) for m in items],
        )
        conn.commit()
    finally:
        if own:
            conn.close()
    return len(items)


# --- change notifications -----------------------------------------------------

Callback = Callable[[str], Awaitable[None] | None]
_callbacks: "list[Callback]" = []
_subscribers: set[asyncio.Queue[str]] = set()


def on_members_changed(callback: Callback) -> Callback:
    """Register `callback(version)`; usable as a decorator. Sync or async."""
    _callbacks.append(callback)
    return callback


async def _dispatch(version: str) -> None:
    invalidate()
    for q in _subscribers:
        q.put_nowait(version)
    for cb in _callbacks:
        try:
            res = cb(version)
            if inspect.isawaitable(res):
                await res
        except Exception:  # a broken callback must not stop the watcher
            log.exception("on_members_changed callback failed")


async def subscribe() -> AsyncIterator[str]:
    """Async iterator of versions, for streaming changes to browsers."""
    q: asyncio.Queue[str] = asyncio.Queue()
    _subscribers.add(q)
    try:
        while True:
            yield await q.get()
    finally:
        _subscribers.discard(q)


async def _parse_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, str]]:
    event, data = "message", []
    async for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


async def watch_forever() -> None:
    """Follow /platform/members/events, reconnecting with backoff."""
    base = _env.platform_url()
    if not base:
        return
    delay = 1.0
    while True:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=None), trust_env=False) as client:
                async with client.stream("GET", f"{base}/platform/members/events", headers=_headers()) as r:
                    r.raise_for_status()
                    delay = 1.0
                    async for event, data in _parse_sse(r.aiter_lines()):
                        if event == "members.changed":
                            await _dispatch(str(json.loads(data).get("version", "")))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("members event stream failed: %s; retrying in %.0fs", e, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)
