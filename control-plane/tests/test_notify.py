"""Budget notifications: each level once per budget window, re-armed by a reset, mailed after commit."""

from sloptown import budgets, notify

from .b_support import add_usage, bdb, db, enable_budgets, make_project, make_user, pytestmark  # noqa: F401


async def _check(db, bucket="build", project_id="p1", user_sub="t1"):
    notices = await notify.check_after_usage(db, bucket=bucket, project_id=project_id, user_sub=user_sub)
    await db.commit()
    return notices


async def _watch(db, emails):
    payload = await budgets.get_settings_payload(db)
    payload["watcherEmails"] = emails
    await budgets.put_settings_payload(db, payload, "test")


async def test_levels_announced_once_per_window(db):
    await make_user(db, "t1", name="Mette Hansen")
    await make_project(db, "p1", "t1")
    await enable_budgets(db, build_nerf=900, build_hard=1000)  # warnings at 0.8 and 0.95
    await _watch(db, ["drift@miira.dk"])

    await add_usage(db, bucket="build", project_id="p1", user_sub="t1", tokens=500)
    assert await _check(db) == []

    await add_usage(db, bucket="build", project_id="p1", user_sub="t1", tokens=320)  # 820: warning 0.8
    [n] = await _check(db)
    assert n.to == ("drift@miira.dk",) and "80%" in n.subject and "Mette Hansen" in n.subject
    assert "820" in n.body and "1.000" in n.body
    assert await _check(db) == [], "same level is not announced twice"

    await add_usage(db, bucket="build", project_id="p1", user_sub="t1", tokens=200)  # 1020: 0.95, nerfed, blocked
    subjects = [n.subject for n in await _check(db)]
    assert len(subjects) == 3
    assert any("95%" in s for s in subjects) and any("lille model" in s for s in subjects)
    assert any("blokeret" in s for s in subjects)

    # A reset moves the window floor: the same levels can be announced again.
    await budgets.reset(db, "user", "t1", "build", "admin")
    await db.commit()
    assert await _check(db) == []
    await add_usage(db, bucket="build", project_id="p1", user_sub="t1", tokens=850)
    assert any("80%" in n.subject for n in await _check(db))


async def test_app_bucket_names_the_vibe_and_disabled_budgets_are_silent(db):
    await make_user(db, "t1")
    await make_project(db, "p1", "t1")
    await _watch(db, ["drift@miira.dk"])
    await add_usage(db, bucket="app", project_id="p1", user_sub=None, tokens=10_000)
    assert await _check(db, bucket="app", user_sub=None) == [], "budgets disabled"

    await enable_budgets(db, app_hard=1000)  # resets settings, so watch again
    await _watch(db, ["drift@miira.dk"])
    [*_, last] = await _check(db, bucket="app", user_sub=None)
    assert "Vibe p1" in last.subject and "app-budget" in last.subject


async def test_no_watchers_records_but_mails_nothing(db):
    from sqlalchemy import func, select

    from sloptown.models import BudgetNotification

    await make_user(db, "t1")
    await make_project(db, "p1", "t1")
    await enable_budgets(db, build_hard=100)
    await add_usage(db, bucket="build", project_id="p1", user_sub="t1", tokens=200)
    assert await _check(db) == []
    assert (await db.execute(select(func.count()).select_from(BudgetNotification))).scalar_one() >= 1


async def test_send_later_mails_over_smtp(monkeypatch):
    import asyncio

    from sloptown.config import get_settings

    sent = []
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.test")
    monkeypatch.setattr(notify, "_send", lambda n: sent.append(n))
    notify.send_later([notify.Notice(("a@b.dk",), "[SlopTown] x", "y")])
    await asyncio.gather(*notify._tasks)
    assert [n.subject for n in sent] == ["[SlopTown] x"]
