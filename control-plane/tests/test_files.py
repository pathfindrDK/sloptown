"""Files and history via vibe-agentd: hidden paths filtered, thread titles joined, viewer blocked."""

from .a_support import bdb, db, dev_base, env, pytestmark  # noqa: F401


async def test_files_history_diff(env):
    from sloptown.models import Thread

    owner = await env.user("s-t")
    await env.project("h1", "s-t", owner)
    ad = dev_base("h1", 4097)
    env.mock.get(f"{ad}/files").respond(json={"files": [
        {"path": "web/src/App.tsx", "size": 10, "locked": False, "changed": True},
        {"path": ".git/HEAD", "size": 1, "locked": True, "changed": False},
        {"path": "web/node_modules/x/index.js", "size": 1, "locked": True, "changed": False},
    ]})
    env.mock.get(f"{ad}/files/web/src/App.tsx").respond(json={"path": "web/src/App.tsx", "content": "x", "locked": False, "language": "tsx"})
    env.db.add(Thread(id="th1", project_id="h1", opencode_session_id="s", title="Farver", owner_sub="s-t"))
    await env.db.commit()
    env.mock.get(f"{ad}/history").respond(json={"commits": [
        {"sha": "abc1234", "message": "m", "authorName": "Mette", "threadId": "th1", "createdAt": "t", "files": []},
        {"sha": "abc1235", "message": "init", "authorName": "SlopTown", "threadId": None, "createdAt": "t", "files": []},
    ]})
    env.mock.get(f"{ad}/diff/abc1234").respond(json={"sha": "abc1234", "files": [
        {"path": "web/src/App.tsx", "status": "M", "patch": "@@"}, {"path": ".data/app.sqlite", "status": "M", "patch": ""}]})

    files = (await env.client.get("/api/projects/h1/files", headers=owner)).json()["files"]
    assert [f["path"] for f in files] == ["web/src/App.tsx"]
    assert (await env.client.get("/api/projects/h1/files/web/src/App.tsx", headers=owner)).json()["language"] == "tsx"
    assert (await env.client.get("/api/projects/h1/files/.git/config", headers=owner)).status_code == 403
    assert (await env.client.get("/api/projects/h1/files/web/../.data/app.sqlite", headers=owner)).status_code in (403, 404)

    hist = (await env.client.get("/api/projects/h1/history", headers=owner)).json()
    assert [c["threadTitle"] for c in hist["commits"]] == ["Farver", None] and hist["publishedSha"] is None
    diff = (await env.client.get("/api/projects/h1/history/abc1234/diff", headers=owner)).json()
    assert [f["path"] for f in diff["files"]] == ["web/src/App.tsx"]
    assert (await env.client.get("/api/projects/h1/history/zzz/diff", headers=owner)).status_code == 400

    await env.share("h1", "s-v", "viewer")
    viewer = await env.user("s-v", role="elev")
    assert (await env.client.get("/api/projects/h1/files", headers=viewer)).status_code == 403


async def test_unreachable_dev_container_is_503(env):
    import httpx

    owner = await env.user("s-t")
    await env.project("u1", "s-t", owner)
    env.mock.get(f"{dev_base('u1', 4097)}/files").mock(side_effect=httpx.ConnectTimeout("timed out"))
    r = await env.client.get("/api/projects/u1/files", headers=owner)
    assert r.status_code == 503 and r.json()["error"] == "upstream_unreachable"


async def test_raw_media_for_the_file_preview(env):
    owner = await env.user("s-t")
    await env.project("h2", "s-t", owner)
    ad = dev_base("h2", 4097)
    env.mock.get(f"{ad}/raw/web/logo.svg").respond(200, content=b"<svg/>", headers={"content-type": "image/svg+xml"})
    env.mock.get(f"{ad}/raw/web/App.tsx").respond(415, json={"detail": "no preview"})
    env.mock.get(f"{ad}/raw/web/odd.png").respond(200, content=b"<html>", headers={"content-type": "text/html"})

    r = await env.client.get("/api/projects/h2/raw/web/logo.svg", headers=owner)
    assert r.status_code == 200 and r.content == b"<svg/>" and r.headers["content-type"].startswith("image/svg+xml")
    # On the studio origin: never rendered there if opened directly.
    assert r.headers["content-disposition"] == "attachment" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"].startswith("sandbox")
    assert (await env.client.get("/api/projects/h2/raw/web/App.tsx", headers=owner)).json()["error"] == "no_preview"
    assert (await env.client.get("/api/projects/h2/raw/web/odd.png", headers=owner)).status_code == 502
    assert (await env.client.get("/api/projects/h2/raw/.git/config", headers=owner)).status_code == 403
    await env.share("h2", "s-v", "viewer")
    assert (await env.client.get("/api/projects/h2/raw/web/logo.svg", headers=await env.user("s-v", role="elev"))).status_code == 403
