"""Per-project token authentication for the egress proxy.

Tokens are stored as sha256 hex digests in projects.dev_token_hash and
projects.runtime_token_hash. Which column matched decides the token kind and
therefore the budget bucket (dev -> build, runtime -> app). Only hits are
cached (30 s); unknown tokens are looked up every time.
"""

import time
from dataclasses import dataclass

from sqlalchemy import or_, select

from ..db import sessionmaker
from ..models import Project
from ..tokens import hash_token


@dataclass(frozen=True)
class Caller:
    project_id: str
    kind: str  # dev|runtime

    @property
    def bucket(self) -> str:
        return "build" if self.kind == "dev" else "app"


_CACHE_TTL = 30.0
_cache: dict[str, tuple[float, Caller | None]] = {}


async def resolve_token(token: str) -> Caller | None:
    if not token:
        return None
    digest = hash_token(token)
    hit = _cache.get(digest)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    async with sessionmaker()() as db:
        project = (
            await db.execute(select(Project).where(or_(Project.dev_token_hash == digest, Project.runtime_token_hash == digest)))
        ).scalar_one_or_none()
    if project is None:
        # Not cached: a warm-pool container's token becomes valid the moment the
        # slot is claimed, and its first agent call follows within seconds.
        return None
    caller = Caller(project.id, "dev" if project.dev_token_hash == digest else "runtime")
    _cache[digest] = (time.monotonic() + _CACHE_TTL, caller)
    return caller


def clear_cache() -> None:
    _cache.clear()


def bearer(header: str | None) -> str:
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""
