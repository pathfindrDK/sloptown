"""Edge forward_auth: login redirect, one-time code -> host cookie, identity headers, role gates."""

from urllib.parse import parse_qs, unquote, urlparse

from .a_support import bdb, db, dev_base, env, pytestmark  # noqa: F401

APPS = "apps.sloptown.localhost"


async def published(env, pid="e1"):
    owner = await env.user("s-t")
    p = await env.project(pid, "s-t", owner)
    env.mock.post(f"{dev_base(pid, 4097)}/export").respond(200, content=b"T", headers={"x-vibe-sha": "a" * 40})
    assert (await env.client.post(f"/api/projects/{pid}/publish", json={"sha": "a" * 7}, headers=owner)).status_code == 200
    return p, owner


def fwd(host, uri="/", cookie=None, **extra):
    h = {"X-Forwarded-Host": host, "X-Forwarded-Uri": uri, "X-Forwarded-Proto": "https", **extra}
    if cookie:
        h["Cookie"] = cookie
    return h


async def host_cookie(env, host, url):
    """Follow a /_vibe/session?code= URL the way Caddy routes it to /edge/session."""
    q = urlparse(url).query
    r = await env.client.get(f"/edge/session?{q}", headers={"X-Forwarded-Host": host})
    assert r.status_code == 302, r.text
    sc = r.headers["set-cookie"]
    assert "Partitioned" in sc
    env.client.cookies.clear()
    return sc.split(";")[0]


async def test_app_forward_auth_flow(env):
    p, owner = await published(env)
    host = f"{p['slug']}.{APPS}"
    student = await env.share("e1", "s-elev", "viewer", name="Ali Ø", classes=["7.A", "8 B"])
    stud_h = await env.user("s-elev", role="elev")

    r = await env.client.get("/edge/auth/app", headers=fwd(host, "/quiz?x=1"))
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    assert loc.path == "/auth/app-login" and parse_qs(loc.query)["next"] == ["/quiz"]

    r = await env.client.get(f"/auth/app-login?{loc.query}", headers=stud_h)
    assert r.status_code == 302 and r.headers["location"].startswith(f"http://{host}")
    cookie = await host_cookie(env, host, r.headers["location"])

    r = await env.client.get("/edge/auth/app", headers=fwd(host, cookie=cookie))
    assert r.status_code == 200, r.text
    assert r.headers["x-vibe-user"] == student["id"] and "s-elev" not in r.headers["x-vibe-user"]
    assert r.headers["x-vibe-role"] == "viewer"
    assert unquote(r.headers["x-vibe-name"]) == "Ali Ø"
    assert [unquote(c) for c in r.headers["x-vibe-classes"].split(",")] == ["7.A", "8 B"]
    assert r.headers["x-vibe-upstream"] == "fake-vibe-run-e1:8000"
    assert r.headers["x-vibe-edge-secret"]

    # The cookie is bound to its host: another app must not accept it.
    p2, _ = await published(env, "e2")
    r = await env.client.get("/edge/auth/app", headers=fwd(f"{p2['slug']}.{APPS}", cookie=cookie))
    assert r.status_code == 302


async def test_code_is_single_use_and_host_bound(env):
    p, owner = await published(env)
    url = (await env.client.post("/api/projects/e1/app-session", headers=owner)).json()["url"]
    q = urlparse(url).query
    wrong = await env.client.get(f"/edge/session?{q}", headers={"X-Forwarded-Host": f"preview-{p['slug']}.{APPS}"})
    assert wrong.status_code == 400
    url = (await env.client.post("/api/projects/e1/app-session", headers=owner)).json()["url"]
    await host_cookie(env, f"{p['slug']}.{APPS}", url)
    again = await env.client.get(f"/edge/session?{urlparse(url).query}", headers={"X-Forwarded-Host": f"{p['slug']}.{APPS}"})
    assert again.status_code == 400


async def test_access_revoked_after_cookie(env):
    p, owner = await published(env)
    host = f"{p['slug']}.{APPS}"
    await env.share("e1", "s-elev", "viewer")
    stud_h = await env.user("s-elev", role="elev")
    loc = (await env.client.get("/edge/auth/app", headers=fwd(host))).headers["location"]
    r = await env.client.get(f"/auth/app-login?{urlparse(loc).query}", headers=stud_h)
    cookie = await host_cookie(env, host, r.headers["location"])
    env.miira.members["e1"] = [m for m in env.miira.members["e1"] if m["sub"] != "s-elev"]
    from sloptown.members import invalidate
    await invalidate(env.db, "e1")
    r = await env.client.get("/edge/auth/app", headers=fwd(host, cookie=cookie))
    assert r.status_code == 403


async def test_preview_requires_editor_and_supports_view_as(env):
    p, owner = await published(env)
    phost = f"preview-{p['slug']}.{APPS}"
    await env.share("e1", "s-elev", "viewer")
    stud_h = await env.user("s-elev", role="elev")
    assert (await env.client.post("/api/projects/e1/preview-session", headers=stud_h)).status_code == 403

    url = (await env.client.post("/api/projects/e1/preview-session", headers=owner)).json()["url"]
    cookie = await host_cookie(env, phost, url)
    r = await env.client.get("/edge/auth/preview", headers=fwd(phost, cookie=cookie))
    assert r.status_code == 200 and r.headers["x-vibe-role"] == "owner"
    assert r.headers["x-vibe-upstream"] == "fake-vibe-dev-e1:5173"
    r = await env.client.get("/edge/auth/preview", headers=fwd(phost, cookie=cookie + "; vibe_view_as=viewer"))
    assert r.headers["x-vibe-role"] == "viewer" and r.headers["x-vibe-user"] == "dev-viewer"


async def test_unpublished_and_unknown_hosts(env):
    owner = await env.user("s-t")
    p = await env.project("e3", "s-t", owner)
    r = await env.client.get("/edge/auth/app", headers=fwd(f"{p['slug']}.{APPS}"))
    assert r.status_code == 404
    r = await env.client.get("/edge/auth/app", headers=fwd("nope.example.com"))
    assert r.status_code == 404


async def test_edge_login_without_session_goes_to_oidc(env):
    r = await env.client.get("/auth/app-login", params={"project": "e9", "kind": "app", "next": "/"})
    assert r.status_code == 302 and r.headers["location"].startswith("/auth/login?next=")


async def test_tls_ask(env):
    owner = await env.user("s-t")
    p = await env.project("e4", "s-t", owner)
    assert (await env.client.get("/edge/tls-ask", params={"domain": f"{p['slug']}.{APPS}"})).status_code == 200
    assert (await env.client.get("/edge/tls-ask", params={"domain": f"preview-{p['slug']}.{APPS}"})).status_code == 200
    assert (await env.client.get("/edge/tls-ask", params={"domain": f"nope.{APPS}"})).status_code == 404
    assert (await env.client.get("/edge/tls-ask", params={"domain": "evil.com"})).status_code == 404
    # the platform's own hosts (studio, Miira) are allowed; other reserved labels are not
    from urllib.parse import urlparse

    from sloptown.config import get_settings

    studio_host = urlparse(get_settings().studio_origin).hostname
    assert (await env.client.get("/edge/tls-ask", params={"domain": studio_host})).status_code == 200
    assert (await env.client.get("/edge/tls-ask", params={"domain": f"www.{APPS}"})).status_code == 404


async def test_preview_session_binds_view_as(env):
    owner = await env.user("s-t")
    p = await env.project("e5", "s-t", owner)
    phost = f"preview-{p['slug']}.{APPS}"
    url = (await env.client.post("/api/projects/e5/preview-session", json={"viewAs": "viewer"}, headers=owner)).json()["url"]
    r = await env.client.get(f"/edge/session?{urlparse(url).query}", headers={"X-Forwarded-Host": phost})
    cookies = r.headers.get_list("set-cookie")
    assert any(c.startswith("vibe_view_as=viewer;") and "Partitioned" in c for c in cookies)
    session = next(c for c in cookies if c.startswith("vibe_preview=")).split(";")[0]
    env.client.cookies.clear()
    r = await env.client.get("/edge/auth/preview", headers=fwd(phost, cookie=session + "; vibe_view_as=viewer"))
    assert r.headers["x-vibe-role"] == "viewer"

    url = (await env.client.post("/api/projects/e5/preview-session", headers=owner)).json()["url"]
    r = await env.client.get(f"/edge/session?{urlparse(url).query}", headers={"X-Forwarded-Host": phost})
    assert any(c.startswith("vibe_view_as=;") and "Max-Age=0" in c for c in r.headers.get_list("set-cookie"))
    env.client.cookies.clear()
    assert (await env.client.post("/api/projects/e5/preview-session", json={"viewAs": "admin"}, headers=owner)).status_code == 422
