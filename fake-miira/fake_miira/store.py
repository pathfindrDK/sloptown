"""Seeded users and classes, plus Vibe files and their shares.

Miira-internal user ids (`u-...`) never leave fake-miira; everything sent to
SlopTown uses pairwise ids from tokens.py.
"""

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import get_settings
from .tokens import avatar_hue, service_sub, vibe_member_id


@dataclass(frozen=True)
class MiiraUser:
    id: str
    name: str
    email: str
    role: str  # laerer|elev|admin|system-admin


@dataclass(frozen=True)
class MiiraClass:
    id: str
    name: str
    member_ids: tuple[str, ...]


USERS: dict[str, MiiraUser] = {
    u.id: u
    for u in [
        MiiraUser("u-mette", "Mette Hansen", "mette@skole.dk", "laerer"),
        MiiraUser("u-jonas", "Jonas Holm", "jonas@skole.dk", "laerer"),
        MiiraUser("u-admin", "Sanne Sysadmin", "sanne@miira.dk", "system-admin"),
        MiiraUser("u-ali", "Ali Yilmaz", "ali@elev.skole.dk", "elev"),
        MiiraUser("u-freja", "Freja Nielsen", "freja@elev.skole.dk", "elev"),
        MiiraUser("u-noah", "Noah Larsen", "noah@elev.skole.dk", "elev"),
        MiiraUser("u-ida", "Ida Pedersen", "ida@elev.skole.dk", "elev"),
    ]
}

CLASSES: dict[str, MiiraClass] = {
    "c-7a": MiiraClass("c-7a", "7.A", ("u-ali", "u-freja", "u-noah", "u-ida")),
}

ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


@dataclass
class Share:
    kind: str  # user|class
    id: str  # Miira user id or class id
    role: str  # editor|viewer


@dataclass
class VibeFile:
    id: str
    title: str
    owner_id: str
    shares: list[Share] = field(default_factory=list)

    @property
    def content(self) -> dict[str, str]:
        return {"projectId": self.id}


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.files: dict[str, VibeFile] = {}
        self._load()

    # persistence -----------------------------------------------------------

    def _path(self) -> Path | None:
        p = get_settings().data_file
        return Path(p) if p else None

    def _load(self) -> None:
        path = self._path()
        if not path or not path.exists():
            return
        raw = json.loads(path.read_text())
        for f in raw.get("files", []):
            shares = [Share(**s) for s in f.pop("shares", [])]
            self.files[f["id"]] = VibeFile(**f, shares=shares)

    def _save(self) -> None:
        path = self._path()
        if path:
            path.write_text(json.dumps({"files": [asdict(f) for f in self.files.values()]}, indent=2))

    # files -----------------------------------------------------------------

    def create_file(self, owner_id: str, title: str) -> VibeFile:
        with self._lock:
            f = VibeFile(id=uuid.uuid4().hex[:20], title=title or "Ny Vibe", owner_id=owner_id)
            self.files[f.id] = f
            self._save()
            return f

    def rename(self, file_id: str, title: str) -> None:
        with self._lock:
            self.files[file_id].title = title
            self._save()

    def set_shares(self, file_id: str, shares: list[Share]) -> None:
        with self._lock:
            self.files[file_id].shares = shares
            self._save()

    def role_of(self, f: VibeFile, user_id: str) -> str | None:
        if f.owner_id == user_id:
            return "owner"
        best: str | None = None
        for s in f.shares:
            hit = (s.kind == "user" and s.id == user_id) or (
                s.kind == "class" and s.id in CLASSES and user_id in CLASSES[s.id].member_ids
            )
            if hit and (best is None or ROLE_RANK[s.role] > ROLE_RANK[best]):
                best = s.role
        return best

    def files_for(self, user_id: str) -> list[tuple[VibeFile, str]]:
        return [(f, r) for f in self.files.values() if (r := self.role_of(f, user_id))]

    def members(self, file_id: str) -> list[dict[str, Any]]:
        """Everyone with access, class shares expanded (docs/miira-integration.md §6)."""
        f = self.files[file_id]
        user_ids = {f.owner_id}
        for s in f.shares:
            if s.kind == "user":
                user_ids.add(s.id)
            elif s.id in CLASSES:
                user_ids.update(CLASSES[s.id].member_ids)
        out = []
        for uid in sorted(user_ids):
            role = self.role_of(f, uid)
            if not role or uid not in USERS:
                continue
            mid = vibe_member_id(file_id, uid)
            out.append(
                {
                    "id": mid,
                    "sub": service_sub(uid),
                    "name": USERS[uid].name,
                    "role": role,
                    "classes": sorted(c.name for c in CLASSES.values() if uid in c.member_ids),
                    "avatarHue": avatar_hue(mid),
                }
            )
        return out


_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def reset_store() -> None:
    """Tests only."""
    global _store
    _store = None
