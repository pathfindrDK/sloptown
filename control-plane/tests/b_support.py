"""Fixtures for the egress/budgets/admin tests (own database: sloptown_test_b).

Import the fixtures you need into a test module: `from .b_support import bdb, db, ...`.
Tests using them must run on the session event loop (pytestmark below) because the
async engine is bound to the loop it was created on.
"""

import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text

PG = "postgresql://sloptown:sloptown@127.0.0.1:55432"
DBNAME = "sloptown_test_b"
ISSUER = "http://miira.test"

TEST_ENV = {
    "SLOPTOWN_DATABASE_URL": f"postgresql+asyncpg://sloptown:sloptown@127.0.0.1:55432/{DBNAME}",
    "SLOPTOWN_OPENAI_BASE_URL": "https://openai.test/v1",
    "SLOPTOWN_OPENAI_API_KEY": "sk-real-key",
    "SLOPTOWN_CONTROL_PLANE_URL": "http://cp.test",
    "SLOPTOWN_MIIRA_ISSUER": ISSUER,
    "SLOPTOWN_MIIRA_API_BASE": ISSUER,
    "SLOPTOWN_INTERNAL_SECRET": "internal-test",
    "SLOPTOWN_SESSION_SECRET": "session-test-secret-0123456789abcdef",
}

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _reset_globals() -> None:
    from sloptown import db as dbmod
    from sloptown.config import get_settings
    from sloptown.egress import auth as egress_auth
    from sloptown.miira import miira

    get_settings.cache_clear()
    dbmod._engine = None
    dbmod._sessionmaker = None
    egress_auth.clear_cache()
    miira.reset()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def bdb():
    """Creates/clears the test database and points sloptown at it."""
    conn = await asyncpg.connect(f"{PG}/sloptown")
    if not await conn.fetchval("select 1 from pg_database where datname=$1", DBNAME):
        await conn.execute(f'create database "{DBNAME}"')
    await conn.close()

    saved = {k: os.environ.get(k) for k in TEST_ENV}
    os.environ.update(TEST_ENV)
    _reset_globals()

    from sloptown import db as dbmod
    from sloptown.models import Base

    async with dbmod.engine().begin() as c:
        await c.run_sync(Base.metadata.drop_all)
        await c.run_sync(Base.metadata.create_all)
    yield dbmod
    await dbmod.engine().dispose()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _reset_globals()


@pytest_asyncio.fixture(loop_scope="session")
async def db(bdb):
    """A session on a freshly truncated database."""
    from sloptown.models import Base
    from sloptown.egress import auth as egress_auth

    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with bdb.engine().begin() as c:
        await c.execute(text(f"truncate {names} restart identity cascade"))
    egress_auth.clear_cache()
    async with bdb.sessionmaker()() as session:
        yield session


async def make_user(db, sub: str, role: str = "laerer", name: str = "Mette") -> None:
    from sloptown.models import User

    db.add(User(sub=sub, name=name, role=role))
    await db.flush()


async def make_project(db, pid: str, owner: str, dev_token: str = "dev-tok", run_token: str = "run-tok", active_turn_sub: str | None = None) -> None:
    from sloptown.models import Project
    from sloptown.tokens import hash_token

    db.add(
        Project(
            id=pid,
            slug=pid,
            owner_sub=owner,
            title=f"Vibe {pid}",
            dev_token_hash=hash_token(dev_token),
            runtime_token_hash=hash_token(run_token),
            active_turn_sub=active_turn_sub,
        )
    )
    await db.flush()


async def add_usage(db, *, bucket: str, project_id: str, user_sub: str | None, tokens: int, ts: datetime | None = None) -> None:
    from sloptown.models import UsageEvent

    db.add(
        UsageEvent(
            ts=ts or datetime.now(UTC),
            project_id=project_id,
            user_sub=user_sub,
            bucket=bucket,
            model="gpt-4.1",
            requested_model="gpt-4.1",
            input_tokens=tokens,
            output_tokens=0,
        )
    )
    await db.flush()


async def enable_budgets(db, *, build_nerf=None, build_hard=None, app_nerf=None, app_hard=None) -> None:
    from sloptown import budgets

    payload = budgets.normalize_settings({})
    payload["enabled"] = True
    payload["build"]["byRole"]["laerer"] = {"nerfLimitTokens": build_nerf, "hardLimitTokens": build_hard}
    payload["app"]["perProject"] = {"nerfLimitTokens": app_nerf, "hardLimitTokens": app_hard}
    await budgets.put_settings_payload(db, payload, "test")


# --- Miira keys for admin JWTs ---------------------------------------------------------


class MiiraKeys:
    def __init__(self) -> None:
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key(), as_dict=True)
        jwk.update({"kid": "k1", "use": "sig", "alg": "RS256"})
        self.jwks = {"keys": [jwk]}

    def token(self, *, aud: str = "sloptown-admin", role: str = "system-admin", sub: str = "admin-sub", exp_in: int = 60, jti: str | None = None, key=None) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": aud, "sub": sub, "name": "Admin", "role": role, "iat": now, "exp": now + exp_in, "jti": jti or str(uuid.uuid4())}
        return jwt.encode(claims, key or self.private, algorithm="RS256", headers={"kid": "k1"})

    def mock(self, respx_mock) -> None:
        respx_mock.get(f"{ISSUER}/.well-known/openid-configuration").respond(
            json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/.well-known/jwks.json", "token_endpoint": f"{ISSUER}/oauth/token"}
        )
        respx_mock.get(f"{ISSUER}/.well-known/jwks.json").respond(json=self.jwks)


def iso(dt: datetime) -> str:
    return dt.isoformat()


__all__ = ["bdb", "db", "make_user", "make_project", "add_usage", "enable_budgets", "MiiraKeys", "pytestmark", "timedelta"]
