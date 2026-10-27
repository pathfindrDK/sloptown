"""Fixtures for the control-plane tests (auth, projects, edge, members, hooks, turns, pool).

Shares worker B's database fixtures (`bdb`, `db` from b_support: database
sloptown_test_b, truncated per test) so both suites can run in one pytest session.
Miira, OpenCode and vibe-agentd are mocked with respx; containers use
FakeOrchestrator, whose addresses are `fake-<container>`.
"""

import hashlib
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest_asyncio
import respx

from .b_support import ISSUER, MiiraKeys, bdb, db, pytestmark  # noqa: F401

KEYS = MiiraKeys()


def sign(claims: dict[str, Any], aud: str) -> str:
    now = int(time.time())
    base = {"iss": ISSUER, "aud": aud, "iat": now, "exp": now + 120, "jti": str(uuid.uuid4())}
    return jwt.encode({**base, **claims}, KEYS.private, algorithm="RS256", headers={"kid": "k1"})


class FakeMiira:
    """Members API + token endpoint on respx. `members[file_id]` is a list of member dicts."""

    def __init__(self, mock: respx.MockRouter) -> None:
        self.members: dict[str, list[dict[str, Any]]] = {}
        self.version = 1
        self.calls = 0
        KEYS.mock(mock)
        mock.get(f"{ISSUER}/.well-known/openid-configuration").respond(
            json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/oauth/authorize",
                "token_endpoint": f"{ISSUER}/oauth/token",
                "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
            }
        )
        self.token_route = mock.post(f"{ISSUER}/oauth/token").mock(side_effect=self._token)
        self.id_token_claims: dict[str, Any] = {}
        mock.get(url__regex=rf"^{ISSUER}/api/vibe/(?P<fid>[^/]+)/members$").mock(side_effect=self._members)

    def _token(self, request: httpx.Request, **_: Any) -> httpx.Response:
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        if form.get("grant_type") == "client_credentials":
            return httpx.Response(200, json={"access_token": "svc-token", "expires_in": 600, "token_type": "Bearer"})
        return httpx.Response(200, json={"id_token": sign(self.id_token_claims, "sloptown"), "access_token": "x"})

    def _members(self, request: httpx.Request, fid: str) -> httpx.Response:
        self.calls += 1
        assert request.headers["authorization"] == "Bearer svc-token"
        if fid not in self.members:
            return httpx.Response(404)
        return httpx.Response(200, json={"fileId": fid, "version": str(self.version), "members": self.members[fid]})

    def share(self, fid: str, sub: str, role: str, name: str = "Elev", classes: list[str] | None = None) -> dict[str, Any]:
        m = {"id": "v" + hashlib.sha256(f"{fid}:{sub}".encode()).hexdigest()[:25], "sub": sub, "name": name, "role": role, "classes": classes or ["7.A"], "avatarHue": 100}
        self.members.setdefault(fid, []).append(m)
        self.version += 1
        return m


@pytest_asyncio.fixture(loop_scope="session")
async def env(db):
    """App client + fake Miira + fake orchestrator + captured hub events."""
    from sloptown import events, threads
    from sloptown.app import create_app
    from sloptown.miira import miira
    from sloptown.orchestrator import FakeOrchestrator, set_orchestrator

    miira.reset()
    orch = FakeOrchestrator()
    set_orchestrator(orch)
    threads.turns.__init__()
    published: list[tuple[str, str, Any]] = []
    original = events.hub.publish

    def capture(channel: str, event: str, data: Any) -> None:
        published.append((channel, event, data))
        original(channel, event, data)

    events.hub.publish = capture  # type: ignore[method-assign]
    app = create_app()
    with respx.mock(assert_all_called=False) as mock:
        fm = FakeMiira(mock)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://studio.test") as c:
            yield Env(c, db, fm, orch, mock, published)
    events.hub.publish = original  # type: ignore[method-assign]
    set_orchestrator(None)


class Env:
    def __init__(self, client, db, miira, orch, mock, published) -> None:
        self.client: httpx.AsyncClient = client
        self.db = db
        self.miira: FakeMiira = miira
        self.orch = orch
        self.mock: respx.MockRouter = mock
        self.published: list[tuple[str, str, Any]] = published

    async def user(self, sub: str, role: str = "laerer", name: str = "Mette", terms: bool = True,
                   file_id: str | None = None, file_role: str | None = None) -> dict[str, str]:
        """Create a user + session directly; returns auth headers."""
        from sloptown.auth import create_session
        from sloptown.config import get_settings
        from sloptown.models import User

        u = await self.db.get(User, sub)
        if u is None:
            u = User(sub=sub, name=name, role=role)
            self.db.add(u)
            await self.db.flush()
        if terms:
            u.terms_version = get_settings().terms_version
            u.terms_accepted_at = datetime.now(UTC)
        token = await create_session(self.db, sub, file_id=file_id, file_role=file_role)
        await self.db.commit()
        return {"Authorization": f"Bearer {token}"}

    async def project(self, pid: str, owner_sub: str, owner_headers: dict[str, str], title: str = "Quiz") -> dict[str, Any]:
        """Create a project the way a standalone teacher does (ownership checked via the members API)."""
        self.miira.share(pid, owner_sub, "owner", name="Mette")
        r = await self.client.post("/api/projects", json={"fileId": pid, "title": title}, headers=owner_headers)
        assert r.status_code == 201, r.text
        return r.json()

    async def share(self, fid: str, sub: str, role: str, **kw: Any) -> dict[str, Any]:
        """Share in fake Miira and drop the cache, as the access-changed webhook would."""
        from sloptown.members import invalidate

        m = self.miira.share(fid, sub, role, **kw)
        await invalidate(self.db, fid)
        return m

    def events(self, name: str) -> list[Any]:
        return [d for (_c, e, d) in self.published if e == name]


def dev_base(pid: str, port: int) -> str:
    return f"http://fake-vibe-dev-{pid}:{port}"


__all__ = ["bdb", "db", "env", "pytestmark", "sign", "dev_base", "KEYS", "ISSUER", "timedelta"]
