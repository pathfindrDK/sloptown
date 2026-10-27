"""Weekly budget engine: window, nerf/hard limits, reset and grant overrides."""

from datetime import UTC, datetime, timedelta

from sloptown import budgets

from .b_support import add_usage, bdb, db, enable_budgets, make_project, make_user, pytestmark  # noqa: F401


async def test_week_start_is_monday_midnight_copenhagen():
    # Wednesday 2026-10-28 12:00 UTC -> Monday 2026-10-26 00:00 CET (23:00 UTC Sunday)
    ws = budgets.week_start(datetime(2026, 10, 28, 12, tzinfo=UTC))
    assert ws == datetime(2026, 10, 25, 23, tzinfo=UTC)
    # Sunday 22:30 UTC is already Monday 00:30 in Copenhagen (CEST, summer)
    ws2 = budgets.week_start(datetime(2026, 6, 14, 22, 30, tzinfo=UTC))
    assert ws2 == datetime(2026, 6, 14, 22, tzinfo=UTC)


async def test_next_reset_across_dst_change():
    # Week containing the switch to winter time (2026-10-25 is a Sunday)
    now = datetime(2026, 10, 21, 12, tzinfo=UTC)
    assert budgets.week_start(now) == datetime(2026, 10, 18, 22, tzinfo=UTC)  # CEST
    assert budgets.next_reset(now) == datetime(2026, 10, 25, 23, tzinfo=UTC)  # CET


async def test_defaults_are_off_and_never_block(db):
    await make_user(db, "u1")
    await make_project(db, "p1", "u1")
    await add_usage(db, bucket="build", project_id="p1", user_sub="u1", tokens=10**9)
    settings = await budgets.get_settings_payload(db)
    assert settings["enabled"] is False
    d = await budgets.decide(db, "build", project_id="p1", user_sub="u1", requested_model="gpt-4.1")
    assert not d.blocked and not d.nerfed
    assert await budgets.check_build_allowed(db, "u1") == (True, None)


async def test_states_warning_nerf_block(db):
    await make_user(db, "u1")
    await make_project(db, "p1", "u1")
    await enable_budgets(db, build_nerf=1000, build_hard=2000)

    await add_usage(db, bucket="build", project_id="p1", user_sub="u1", tokens=500)
    st = await budgets.bucket_status(db, "build", "u1", "laerer")
    assert st["state"] == "ok"

    await add_usage(db, bucket="build", project_id="p1", user_sub="u1", tokens=600)  # 1100
    d = await budgets.decide(db, "build", project_id="p1", user_sub="u1", requested_model="gpt-4.1")
    assert d.nerfed and d.model == "gpt-6-luna" and not d.blocked

    await add_usage(db, bucket="build", project_id="p1", user_sub="u1", tokens=500)  # 1600 = 0.8 * 2000 -> still nerfed
    assert (await budgets.bucket_status(db, "build", "u1", "laerer"))["state"] == "nerfed"

    await add_usage(db, bucket="build", project_id="p1", user_sub="u1", tokens=400)  # 2000
    d = await budgets.decide(db, "build", project_id="p1", user_sub="u1", requested_model="gpt-4.1")
    assert d.blocked and d.retry_at == budgets.next_reset()
    allowed, retry = await budgets.check_build_allowed(db, "u1")
    assert not allowed and retry is not None


async def test_warning_without_nerf(db):
    await make_user(db, "u1")
    await make_project(db, "p1", "u1")
    await enable_budgets(db, build_hard=1000)
    await add_usage(db, bucket="build", project_id="p1", user_sub="u1", tokens=850)
    assert (await budgets.bucket_status(db, "build", "u1", "laerer"))["state"] == "warning"


async def test_last_week_usage_does_not_count(db):
    await make_user(db, "u1")
    await make_project(db, "p1", "u1")
    await enable_budgets(db, build_hard=1000)
    await add_usage(db, bucket="build", project_id="p1", user_sub="u1", tokens=5000, ts=budgets.week_start() - timedelta(minutes=1))
    st = await budgets.bucket_status(db, "build", "u1", "laerer")
    assert st["usedTokens"] == 0 and st["state"] == "ok"


async def test_reset_moves_floor_and_grant_adds_headroom(db):
    await make_user(db, "u1")
    await make_project(db, "p1", "u1")
    await enable_budgets(db, build_hard=1000, app_hard=100)
    past = datetime.now(UTC) - timedelta(seconds=5)
    await add_usage(db, bucket="build", project_id="p1", user_sub="u1", tokens=1500, ts=past)
    assert (await budgets.bucket_status(db, "build", "u1", "laerer"))["state"] == "blocked"

    await budgets.reset(db, "user", "u1", "build", "admin")
    st = await budgets.bucket_status(db, "build", "u1", "laerer")
    assert st["usedTokens"] == 0 and st["state"] == "ok"

    # app bucket: grant extra instead of reset
    await add_usage(db, bucket="app", project_id="p1", user_sub=None, tokens=150)
    assert (await budgets.bucket_status(db, "app", "p1"))["state"] == "blocked"
    await budgets.grant(db, "project", "p1", "app", 100, "admin")
    st = await budgets.bucket_status(db, "app", "p1")
    assert st["hardLimitTokens"] == 200 and st["state"] == "ok"

    # the ledger is untouched
    from sqlalchemy import func, select

    from sloptown.models import UsageEvent

    assert await db.scalar(select(func.count()).select_from(UsageEvent)) == 2


async def test_budget_status_shape_and_role_limits(db):
    await make_user(db, "t1", role="laerer")
    await make_user(db, "s1", role="elev")
    await make_project(db, "p1", "t1")
    await enable_budgets(db, build_hard=1000, app_hard=50)
    st = await budgets.budget_status(db, "p1", "t1")
    assert st["enabled"] is True
    assert st["build"]["hardLimitTokens"] == 1000
    assert st["app"]["hardLimitTokens"] == 50
    assert set(st["build"]) == {"usedTokens", "nerfLimitTokens", "hardLimitTokens", "state", "resetsAt"}
    # elev has no limit configured -> falls back to unlimited role entry
    st_s = await budgets.budget_status(db, "p1", "s1")
    assert st_s["build"]["hardLimitTokens"] is None


async def test_limited_items(db):
    await make_user(db, "u1", name="Mette")
    await make_project(db, "p1", "u1")
    await enable_budgets(db, build_nerf=10, build_hard=100, app_hard=10)
    await add_usage(db, bucket="build", project_id="p1", user_sub="u1", tokens=50)
    await add_usage(db, bucket="app", project_id="p1", user_sub=None, tokens=20)
    items = {(i["subject"], i["state"]): i for i in await budgets.limited_items(db)}
    assert items[("user", "nerfed")]["label"] == "Mette"
    assert items[("user", "nerfed")]["limitTokens"] == 10
    assert items[("project", "blocked")]["usedTokens"] == 20
