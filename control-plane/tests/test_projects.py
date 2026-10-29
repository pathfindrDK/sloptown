"""Projects: create rules, warm-pool claim and refill, listing, roles, publish, host-session codes."""

import asyncio

from sqlalchemy import func, select

from .a_support import bdb, db, dev_base, env, pytestmark  # noqa: F401


async def test_create_requires_teacher_terms_and_ownership(env):
    elev = await env.user("s-elev", role="elev")
    r = await env.client.post("/api/projects", json={"fileId": "f1"}, headers=elev)
    assert r.status_code == 403 and r.json()["error"] == "teachers_only"

    for sub, role in (("s-sysadm", "system-admin"), ("s-adm", "admin")):
        r = await env.client.post("/api/projects", json={"fileId": "f1"}, headers=await env.user(sub, role=role))
        assert r.json()["error"] != "teachers_only"

    no_terms = await env.user("s-nt", terms=False)
    r = await env.client.post("/api/projects", json={"fileId": "f1"}, headers=no_terms)
    assert r.json()["error"] == "terms_required"

    teacher = await env.user("s-t")
    env.miira.share("f1", "s-other", "owner")
    env.miira.share("f1", "s-t", "editor")
    r = await env.client.post("/api/projects", json={"fileId": "f1"}, headers=teacher)
    assert r.status_code == 403 and r.json()["error"] == "not_owner"


async def test_create_claims_container_and_audits(env):
    from sloptown.models import AuditLog, PoolSlot, Project
    from sloptown.pool import refill_once
    from sloptown.tokens import dev_token, hash_token

    assert await refill_once() == 5
    h = await env.user("s-t")
    p = await env.project("f2", "s-t", h, title="Brøkquiz")
    assert p["myRole"] == "owner" and p["slug"].startswith("broekquiz-")
    assert p["previewUrl"].startswith("http://preview-" + p["slug"])
    assert "vibe-dev-f2" in env.orch.containers
    assert (await env.db.execute(select(func.count()).select_from(PoolSlot))).scalar_one() == 4
    row = await env.db.get(Project, "f2")
    await env.db.refresh(row)
    assert row.dev_token_hash == hash_token(dev_token(row.token_seed))
    actions = (await env.db.execute(select(AuditLog.action))).scalars().all()
    assert "project.create" in actions
    r = await env.client.post("/api/projects", json={"fileId": "f2"}, headers=h)
    assert r.status_code == 409


async def test_concurrent_claims_get_distinct_slots(env):
    from sloptown.db import sessionmaker
    from sloptown.pool import claim, refill_once

    await refill_once()

    async def one(pid):
        async with sessionmaker()() as s:
            slot = await claim(s, pid)
            await asyncio.sleep(0.05)  # hold the row lock
            await s.commit()
            return slot.container_name

    names = await asyncio.gather(one("c1"), one("c2"), one("c3"))
    assert len(set(names)) == 3


async def test_refill_discards_stale_image(env):
    from sloptown.models import PoolSlot
    from sloptown.orchestrator import slot_tag
    from sloptown.pool import refill_once

    await refill_once()
    env.orch.image = "sha256:new"
    await refill_once()
    tags = (await env.db.execute(select(PoolSlot.image_tag))).scalars().all()
    assert tags and set(tags) == {slot_tag("sha256:new")}


async def test_refill_discards_slots_with_old_spec(env, monkeypatch):
    from sloptown import orchestrator
    from sloptown.models import PoolSlot
    from sloptown.pool import refill_once

    await refill_once()
    before = set((await env.db.execute(select(PoolSlot.container_name))).scalars().all())
    monkeypatch.setattr(orchestrator, "SLOT_SPEC", orchestrator.SLOT_SPEC + 1)
    await refill_once()
    env.db.expire_all()
    after = set((await env.db.execute(select(PoolSlot.container_name))).scalars().all())
    assert after and not (before & after), "every slot from the old spec is rebuilt"


async def test_owner_opening_a_new_vibe_chooses_before_anything_is_created(env):
    from sloptown.models import Project

    h = await env.user("s-t", file_id="f3", file_role="owner")
    r = await env.client.get("/api/projects/f3", headers=h)
    assert r.status_code == 404 and r.json()["error"] == "not_started" and r.json()["canStart"] is True
    assert await env.db.get(Project, "f3") is None
    r = await env.client.post("/api/projects", json={"fileId": "f3", "kind": "webapp"}, headers=h)
    assert r.status_code == 201 and r.json()["kind"] == "webapp" and r.json()["previewUrl"]
    assert (await env.client.get("/api/projects/f3", headers=h)).json()["myRole"] == "owner"
    other = await env.user("s-x", file_id="f4", file_role="editor")
    r = await env.client.get("/api/projects/f4", headers=other)
    assert r.status_code == 404 and r.json()["error"] != "not_started"
    no_terms = await env.user("s-nt", terms=False, file_id="f9", file_role="owner")
    assert (await env.client.get("/api/projects/f9", headers=no_terms)).json()["error"] == "terms_required"


async def test_scratch_project_is_never_published_but_has_a_preview(env):
    import io
    import tarfile
    import zipfile

    from .test_edge import fwd, host_cookie

    h = await env.user("s-t", file_id="fs", file_role="owner")
    reset = env.mock.post(f"{dev_base('fs', 4097)}/reset-scratch").respond(200, json={"kind": "scratch"})
    r = await env.client.post("/api/projects", json={"fileId": "fs", "kind": "scratch", "title": "Trafiklys"}, headers=h)
    assert r.status_code == 201, r.text
    p = r.json()
    assert reset.called and p["kind"] == "scratch" and p["appUrl"] is None
    assert p["previewUrl"].startswith("http://preview-" + p["slug"])
    for path in ("publish", "app-session"):
        r = await env.client.post(f"/api/projects/fs/{path}", json={"sha": "abc1234"}, headers=h)
        assert r.status_code == 409 and r.json()["error"] == "not_a_webapp", path
    # No app host at all.
    app_host = f"{p['slug']}.apps.sloptown.localhost"
    assert (await env.client.get("/edge/tls-ask", params={"domain": app_host})).status_code == 404
    assert (await env.client.get("/edge/auth/app", headers=fwd(app_host))).status_code == 404
    # The editor-only preview host: its files, from vibe-static on Vite's port. viewAs is ignored.
    phost = f"preview-{p['slug']}.apps.sloptown.localhost"
    assert (await env.client.get("/edge/tls-ask", params={"domain": phost})).status_code == 200
    assert (await env.client.get("/edge/auth/preview", headers=fwd(phost))).status_code == 302
    url = (await env.client.post("/api/projects/fs/preview-session", json={"viewAs": "viewer"}, headers=h)).json()["url"]
    assert "viewAs" not in url
    r = await env.client.get("/edge/auth/preview", headers=fwd(phost, cookie=await host_cookie(env, phost, url)))
    assert r.status_code == 200 and r.headers["x-vibe-upstream"] == "fake-vibe-dev-fs:5173"
    await env.share("fs", "s-v", "viewer")
    assert (await env.client.post("/api/projects/fs/preview-session", headers=await env.user("s-v", role="elev"))).status_code == 403

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        data = b"void setup() {}\n"
        info = tarfile.TarInfo("trafiklys/trafiklys.ino")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    env.mock.post(f"{dev_base('fs', 4097)}/export").respond(200, content=tar_buf.getvalue(), headers={"x-vibe-sha": "c" * 40})
    r = await env.client.get("/api/projects/fs/download", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="trafiklys.zip"' in r.headers["content-disposition"]
    assert zipfile.ZipFile(io.BytesIO(r.content)).read("trafiklys/trafiklys.ino") == b"void setup() {}\n"


async def test_scratch_conversion_failure_marks_the_project(env):
    from sloptown.models import Project

    h = await env.user("s-t", file_id="fe", file_role="owner")
    env.mock.post(f"{dev_base('fe', 4097)}/reset-scratch").respond(409, json={"detail": "project already has work"})
    r = await env.client.post("/api/projects", json={"fileId": "fe", "kind": "scratch"}, headers=h)
    assert r.status_code == 502
    row = await env.db.get(Project, "fe")
    await env.db.refresh(row)
    assert row.kind == "scratch" and row.status == "error"
    r = await env.client.post("/api/projects", json={"fileId": "fx", "kind": "native"}, headers=h)
    assert r.status_code == 422


async def test_list_and_roles(env):
    owner = await env.user("s-t")
    await env.project("f5", "s-t", owner)
    env.miira.share("f5", "s-v", "viewer")
    viewer = await env.user("s-v", role="elev")
    # The viewer's list comes from the members cache, so read it once as the owner.
    await env.client.get("/api/projects/f5/budget", headers=owner)
    from sloptown.members import get_members
    await get_members(env.db, "f5", force=True)

    lst = (await env.client.get("/api/projects", headers=viewer)).json()
    assert [p["id"] for p in lst] == ["f5"] and lst[0]["myRole"] == "viewer"
    r = await env.client.patch("/api/projects/f5", json={"title": "Ny"}, headers=viewer)
    assert r.status_code == 403
    r = await env.client.patch("/api/projects/f5", json={"title": "Ny"}, headers=owner)
    assert r.json()["title"] == "Ny"
    stranger = await env.user("s-z")
    assert (await env.client.get("/api/projects/f5", headers=stranger)).status_code == 404


async def test_publish_exports_and_starts_runtime(env):
    owner = await env.user("s-t")
    p = await env.project("f6", "s-t", owner)
    route = env.mock.post(f"{dev_base('f6', 4097)}/export").respond(
        200, content=b"TARDATA", headers={"x-vibe-sha": "abcdef1234567"}
    )
    r = await env.client.post("/api/projects/f6/publish", json={"sha": "abcdef1"}, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["publishedSha"] == "abcdef1234567"
    assert route.calls.last.request.headers["authorization"].startswith("Bearer vt_")
    assert env.orch.published["f6"] == {"sha": "abcdef1234567", "size": 7}
    proj = (await env.client.get("/api/projects/f6", headers=owner)).json()
    assert proj["appUrl"] == f"http://{p['slug']}.apps.sloptown.localhost:8080/"

    env.mock.post(f"{dev_base('f6', 4097)}/export").respond(422, json={"paths": ["web/package.json"]})
    r = await env.client.post("/api/projects/f6/publish", json={"sha": "abcdef2"}, headers=owner)
    assert r.status_code == 422 and r.json()["paths"] == ["web/package.json"]

    assert (await env.client.post("/api/projects/f6/unpublish", headers=owner)).json() == {"ok": True}
    assert "f6" not in env.orch.published


async def test_publish_build_failure_keeps_previous_version(env):
    owner = await env.user("s-t")
    await env.project("f8", "s-t", owner)
    env.mock.post(f"{dev_base('f8', 4097)}/export").respond(200, content=b"T", headers={"x-vibe-sha": "1" * 40})
    assert (await env.client.post("/api/projects/f8/publish", json={"sha": "1111111"}, headers=owner)).status_code == 200

    env.orch.build_error = "src/App.tsx(3,1): error TS2304"
    env.mock.post(f"{dev_base('f8', 4097)}/export").respond(200, content=b"T", headers={"x-vibe-sha": "2" * 40})
    r = await env.client.post("/api/projects/f8/publish", json={"sha": "2222222"}, headers=owner)
    assert r.status_code == 422
    assert r.json()["error"] == "build_failed" and "TS2304" in r.json()["log"]
    proj = (await env.client.get("/api/projects/f8", headers=owner)).json()
    assert proj["publishedSha"] == "1" * 40



async def test_budget_endpoint(env):
    owner = await env.user("s-t")
    await env.project("f7", "s-t", owner)
    b = (await env.client.get("/api/projects/f7/budget", headers=owner)).json()
    assert b["enabled"] is False and b["build"]["state"] == "ok"


async def test_refill_discards_slots_when_the_agent_model_changes(env, monkeypatch):
    from sloptown.config import get_settings
    from sloptown.models import PoolSlot
    from sloptown.pool import refill_once

    await refill_once()
    before = set((await env.db.execute(select(PoolSlot.container_name))).scalars().all())
    monkeypatch.setattr(get_settings(), "agent_model", "gpt-other")
    await refill_once()
    env.db.expire_all()
    after = set((await env.db.execute(select(PoolSlot.container_name))).scalars().all())
    assert after and not (before & after), "slots carry the model in their environment"


async def test_design_catalog_matches_template_design(env):
    import json
    from pathlib import Path

    h = await env.user("s-t")
    r = await env.client.get("/api/design", headers=h)
    repo = json.loads((Path(__file__).resolve().parents[2] / "template-design" / "catalog.json").read_text())
    assert r.status_code == 200 and r.json() == repo
    assert {s["id"] for s in r.json()["styles"]} >= {"miira", "bootstrap", "tailwind", "bulma", "materialize"}


async def test_new_webapp_gets_its_design_from_agentd(env):
    import json

    from sloptown import design
    from sloptown.models import AuditLog

    h = await env.user("s-t", file_id="fd", file_role="owner")
    route = env.mock.routes["agentd-design"]
    r = await env.client.post("/api/projects", json={"fileId": "fd", "style": "bulma", "palette": "hav"}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["style"] == "bulma" and r.json()["palette"] == "hav"
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"style": "bulma", "palette": "hav", "starter": True}

    # Skipping the choice: Miira's look and a random palette, kept from then on.
    h2 = await env.user("s-t", file_id="fr", file_role="owner")
    p = (await env.client.post("/api/projects", json={"fileId": "fr"}, headers=h2)).json()
    assert p["style"] == "miira" and p["palette"] in design.palette_ids()
    assert json.loads(route.calls.last.request.content)["palette"] == p["palette"]
    details = (await env.db.execute(select(AuditLog.details).where(AuditLog.project_id == "fr"))).scalars().all()
    assert {"style": "miira", "palette": p["palette"]}.items() <= details[0].items()

    # A scratch project has no design, and agentd is not asked for one.
    calls = route.call_count
    env.mock.post(f"{dev_base('fz', 4097)}/reset-scratch").respond(200, json={"kind": "scratch"})
    h3 = await env.user("s-t", file_id="fz", file_role="owner")
    p = (await env.client.post("/api/projects", json={"fileId": "fz", "kind": "scratch", "style": "bulma"}, headers=h3)).json()
    assert p["style"] is None and p["palette"] is None and route.call_count == calls


async def test_unknown_design_is_refused_before_a_container_is_claimed(env):
    from sloptown.models import Project

    h = await env.user("s-t", file_id="fu", file_role="owner")
    for body, err in (({"style": "foundation"}, "bad_style"), ({"palette": "lilla"}, "bad_palette")):
        r = await env.client.post("/api/projects", json={"fileId": "fu", **body}, headers=h)
        assert r.status_code == 400 and r.json()["error"] == err
    assert await env.db.get(Project, "fu") is None


async def test_design_failure_marks_the_project(env):
    import httpx

    from sloptown.models import Project

    env.mock.routes["agentd-design"].side_effect = lambda req: httpx.Response(500, json={"detail": "boom"})
    h = await env.user("s-t", file_id="ff", file_role="owner")
    r = await env.client.post("/api/projects", json={"fileId": "ff"}, headers=h)
    assert r.status_code == 502
    row = await env.db.get(Project, "ff")
    await env.db.refresh(row)
    assert row.status == "error"


async def test_change_design_later(env):
    import json

    from sloptown.models import AuditLog, Project
    from sloptown.threads import turns

    from sloptown import design

    owner = await env.user("s-t")
    start = (await env.project("fc", "s-t", owner))["palette"]  # random
    other = next(p for p in design.palette_ids() if p != start)
    route = env.mock.routes["agentd-design"]

    r = await env.client.patch("/api/projects/fc", json={"palette": other}, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["palette"] == other
    assert json.loads(route.calls.last.request.content) == {"style": None, "palette": other}
    assert env.events("files.changed")[-1] == {"paths": ["web/src/palette.css"]}

    calls = route.call_count
    r = await env.client.patch("/api/projects/fc", json={"palette": other, "title": "Ny titel"}, headers=owner)
    assert r.status_code == 200 and route.call_count == calls, "an unchanged design is not re-applied"

    turns._running["fc"] = "turn-1"
    try:
        r = await env.client.patch("/api/projects/fc", json={"style": "tailwind"}, headers=owner)
        assert r.status_code == 409 and r.json()["error"] == "turn_running"
    finally:
        turns._running.pop("fc", None)
    r = await env.client.patch("/api/projects/fc", json={"style": "tailwind"}, headers=owner)
    assert r.json()["style"] == "tailwind" and r.json()["palette"] == other
    row = await env.db.get(Project, "fc")
    await env.db.refresh(row)
    assert (row.style, row.palette) == ("tailwind", other)
    audits = (await env.db.execute(select(AuditLog.details).where(AuditLog.action == "project.design"))).scalars().all()
    assert audits[-1]["from"]["style"] == "miira" and audits[-1]["to"] == {"style": "tailwind", "palette": other}

    r = await env.client.patch("/api/projects/fc", json={"palette": "lilla"}, headers=owner)
    assert r.status_code == 400 and r.json()["error"] == "bad_palette"
    await env.share("fc", "s-v", "viewer")
    viewer = await env.user("s-v", role="elev")
    r = await env.client.patch("/api/projects/fc", json={"palette": "hav"}, headers=viewer)
    assert r.status_code == 403, "students can't restyle the app"


async def test_older_webapp_without_design_gets_one_on_first_change(env):
    from sloptown.models import Project

    owner = await env.user("s-t")
    await env.project("fo", "s-t", owner)
    row = await env.db.get(Project, "fo")
    row.style = row.palette = None  # created before design systems existed
    await env.db.commit()
    p = (await env.client.get("/api/projects/fo", headers=owner)).json()
    assert p["style"] is None and p["palette"] is None
    p = (await env.client.patch("/api/projects/fo", json={"palette": "hav"}, headers=owner)).json()
    assert p["style"] == "miira" and p["palette"] == "hav", "agentd answers with the style it kept"
