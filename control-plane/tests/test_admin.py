"""Admin API: JWT auth (aud, role, replay, IP), settings, limited, reset/grant, usage, egress."""

import httpx
import pytest_asyncio
import respx
from fastapi import FastAPI
from sqlalchemy import select

from sloptown.admin import router
from sloptown.config import get_settings
from sloptown.errors import ApiError, api_error_handler
from sloptown.models import AuditLog, BudgetOverride

from .b_support import ISSUER, MiiraKeys, add_usage, bdb, db, enable_budgets, make_project, make_user, pytestmark  # noqa: F401

KEYS = MiiraKeys()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    from sloptown.miira import miira

    miira.reset()
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(router)
    with respx.mock(assert_all_called=False) as mock:
        KEYS.mock(mock)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cp") as c:
            yield c


def bearer(**kw) -> dict:
    return {"Authorization": f"Bearer {KEYS.token(**kw)}"}


async def test_auth_failures(client):
    assert (await client.get("/admin/v1/budget-settings")).status_code == 401
    assert (await client.get("/admin/v1/budget-settings", headers=bearer(aud="sloptown-launch"))).status_code == 401
    assert (await client.get("/admin/v1/budget-settings", headers=bearer(role="laerer"))).status_code == 403
    assert (await client.get("/admin/v1/budget-settings", headers=bearer(exp_in=-120))).status_code == 401
    other = MiiraKeys()
    assert (await client.get("/admin/v1/budget-settings", headers={"Authorization": f"Bearer {other.token()}"})).status_code == 401


async def test_replay_rejected(client):
    h = bearer(jti="once")
    assert (await client.get("/admin/v1/budget-settings", headers=h)).status_code == 200
    r = await client.get("/admin/v1/budget-settings", headers=h)
    assert r.status_code == 401 and r.json()["error"] == "replayed"


async def test_ip_allowlist(client):
    s = get_settings()
    s.admin_allowed_ips = ["10.0.0.0/8"]
    try:
        r = await client.get("/admin/v1/budget-settings", headers={**bearer(), "X-Forwarded-For": "192.168.1.5"})
        assert r.status_code == 403
        r = await client.get("/admin/v1/budget-settings", headers={**bearer(), "X-Forwarded-For": "10.1.2.3"})
        assert r.status_code == 200
    finally:
        s.admin_allowed_ips = []


async def test_settings_roundtrip_and_audit(client, db):
    r = await client.get("/admin/v1/budget-settings", headers=bearer())
    settings = r.json()
    assert settings["enabled"] is False
    assert settings["build"]["byRole"]["laerer"] == {"nerfLimitTokens": None, "hardLimitTokens": None}

    settings["enabled"] = True
    settings["build"]["byRole"]["laerer"] = {"nerfLimitTokens": 100, "hardLimitTokens": 200}
    r = await client.put("/admin/v1/budget-settings", json=settings, headers=bearer(sub="admin-42"))
    assert r.status_code == 200 and r.json()["build"]["byRole"]["laerer"]["hardLimitTokens"] == 200

    r = await client.get("/admin/v1/budget-settings", headers=bearer())
    assert r.json()["enabled"] is True

    bad = {**settings, "warningThresholds": [1.5]}
    assert (await client.put("/admin/v1/budget-settings", json=bad, headers=bearer())).status_code == 422

    rows = (await db.execute(select(AuditLog).where(AuditLog.action == "budget.settings.update"))).scalars().all()
    assert len(rows) == 1 and rows[0].actor_sub == "admin-42" and rows[0].actor_kind == "admin"


async def test_limited_reset_grant(client, db):
    await make_user(db, "u1", name="Mette")
    await make_project(db, "p1", "u1")
    await enable_budgets(db, build_hard=100, app_hard=10)
    await add_usage(db, bucket="build", project_id="p1", user_sub="u1", tokens=150)
    await add_usage(db, bucket="app", project_id="p1", user_sub=None, tokens=15)
    await db.commit()

    items = (await client.get("/admin/v1/budgets/limited", headers=bearer())).json()["items"]
    assert {(i["subject"], i["id"], i["state"]) for i in items} == {("user", "u1", "blocked"), ("project", "p1", "blocked")}

    r = await client.post("/admin/v1/budgets/reset", json={"subject": "user", "id": "u1", "bucket": "build"}, headers=bearer())
    assert r.json() == {"ok": True}
    r = await client.post("/admin/v1/budgets/grant", json={"subject": "project", "id": "p1", "bucket": "app", "extraTokens": 100}, headers=bearer())
    assert r.json() == {"ok": True}
    assert (await client.get("/admin/v1/budgets/limited", headers=bearer())).json()["items"] == []

    bad = await client.post("/admin/v1/budgets/reset", json={"subject": "user", "id": "u1", "bucket": "app"}, headers=bearer())
    assert bad.status_code == 422
    neg = await client.post("/admin/v1/budgets/grant", json={"subject": "user", "id": "u1", "bucket": "build", "extraTokens": 0}, headers=bearer())
    assert neg.status_code == 422

    db.expire_all()
    assert len((await db.execute(select(BudgetOverride))).scalars().all()) == 2
    actions = {a.action for a in (await db.execute(select(AuditLog))).scalars()}
    assert {"budget.reset", "budget.grant"} <= actions


async def test_usage_totals(client, db):
    await make_user(db, "u1", name="Mette")
    await make_project(db, "p1", "u1")
    await add_usage(db, bucket="build", project_id="p1", user_sub="u1", tokens=30)
    await add_usage(db, bucket="app", project_id="p1", user_sub=None, tokens=12)
    await db.commit()
    body = (await client.get("/admin/v1/usage", headers=bearer())).json()
    assert body["byUser"] == [{"id": "u1", "name": "Mette", "inputTokens": 30, "outputTokens": 0, "totalTokens": 30, "calls": 1}]
    assert {(p["bucket"], p["totalTokens"]) for p in body["byProject"]} == {("build", 30), ("app", 12)}
    assert body["byModel"][0]["totalTokens"] == 42
    r = await client.get("/admin/v1/usage?from=2000-01-01T00:00:00Z&to=2000-01-02T00:00:00Z", headers=bearer())
    assert r.json()["byModel"] == []


async def test_egress_settings(client):
    body = (await client.get("/admin/v1/egress-settings", headers=bearer())).json()
    assert body["editable"] is False
    assert body["rpsPerProject"] == 10 and body["proxyPort"] == 8201
    assert ISSUER  # keys mocked against this issuer
