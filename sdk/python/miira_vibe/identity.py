"""Who is calling: `current_user` and `require_role` FastAPI dependencies.

Identity comes only from the X-Vibe-* headers that SlopTown's edge sets after
`forward_auth`. They are trusted only together with X-Vibe-Edge-Secret, which
equals VIBE_EDGE_SECRET; the edge strips client-supplied X-Vibe-* headers.
"""

import hmac
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import unquote

from fastapi import HTTPException, Request

from . import _env

Role = Literal["owner", "editor", "viewer"]
ROLE_RANK: dict[str, int] = {"viewer": 1, "editor": 2, "owner": 3}


@dataclass(frozen=True)
class User:
    id: str  # pairwise id for this Vibe; stable, safe to store
    name: str
    role: Role
    classes: list[str] = field(default_factory=list)

    def has_role(self, minimum: Role) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "name": self.name, "role": self.role, "classes": list(self.classes)}


def dev_user() -> User:
    """Identity used in the editor preview when no edge headers are present."""
    role = os.environ.get("VIBE_DEV_ROLE", "owner")
    if role not in ROLE_RANK:
        role = "owner"
    names = {"owner": "Lærer (dig)", "editor": "Medredaktør", "viewer": "Elev"}
    return User(id=f"dev-{role}", name=names[role], role=role, classes=["7.A"] if role == "viewer" else [])  # type: ignore[arg-type]


def user_from_headers(headers) -> User | None:
    """Parse and validate edge identity headers. None if absent or untrusted."""
    secret = _env.edge_secret()
    given = headers.get("x-vibe-edge-secret", "")
    if not secret or not given or not hmac.compare_digest(secret.encode(), given.encode()):
        return None
    uid = headers.get("x-vibe-user", "")
    role = headers.get("x-vibe-role", "")
    if not uid or role not in ROLE_RANK:
        return None
    classes_raw = headers.get("x-vibe-classes", "")
    classes = [unquote(c) for c in classes_raw.split(",") if c]
    return User(id=uid, name=unquote(headers.get("x-vibe-name", "")), role=role, classes=classes)  # type: ignore[arg-type]


async def current_user(request: Request) -> User:
    """FastAPI dependency: the logged-in user, or 401.

    In VIBE_MODE=dev (editor preview) a missing identity falls back to dev_user(),
    so the app works when its API is called directly inside the dev container.
    """
    user = user_from_headers(request.headers)
    if user:
        return user
    if _env.is_dev():
        return dev_user()
    raise HTTPException(status_code=401, detail="Du er ikke logget ind.")


async def optional_user(request: Request) -> User | None:
    """FastAPI dependency: the logged-in user, or None for a visitor of a public app.

    Use it for routes a public app serves to everyone; keep current_user for the rest.
    """
    user = user_from_headers(request.headers)
    if user:
        return user
    return dev_user() if _env.is_dev() else None


def require_role(minimum: Role) -> Callable:
    """FastAPI dependency factory: `user: User = Depends(require_role("editor"))`."""
    if minimum not in ROLE_RANK:
        raise ValueError(f"unknown role {minimum!r}")

    async def dependency(request: Request) -> User:
        user = await current_user(request)
        if not user.has_role(minimum):
            raise HTTPException(status_code=403, detail="Du har ikke adgang til dette.")
        return user

    return dependency
