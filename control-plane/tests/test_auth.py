"""Auth: launch-token exchange (replay, audience), OIDC login round trip, /api/me, terms."""

from urllib.parse import parse_qs, urlparse

from .a_support import bdb, db, env, pytestmark, sign  # noqa: F401


def launch_claims(**kw):
    c = {"sub": "s-mette", "name": "Mette", "email": "m@skole.dk", "role": "laerer", "org_id": "o1",
         "file_id": "File1", "file_role": "owner"}
    c.update(kw)
    return c


async def test_launch_exchange_sets_partitioned_cookie(env):
    r = await env.client.post("/api/auth/launch", json={"launchToken": sign(launch_claims(), "sloptown-launch")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fileRole"] == "owner" and body["user"]["sub"] == "s-mette"
    cookie = r.headers["set-cookie"]
    assert "st_session=" in cookie and "Partitioned" in cookie and "SameSite=None" in cookie and "Secure" in cookie
    me = await env.client.get("/api/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.json()["user"]["name"] == "Mette" and me.json()["canCreate"] is True


async def test_launch_token_is_single_use(env):
    token = sign(launch_claims(), "sloptown-launch")
    assert (await env.client.post("/api/auth/launch", json={"launchToken": token})).status_code == 200
    # the client now holds the session cookie, so send the Origin a browser in the studio sends
    from sloptown.config import get_settings

    origin = {"Origin": get_settings().studio_origin}
    r = await env.client.post("/api/auth/launch", json={"launchToken": token}, headers=origin)
    assert r.status_code == 401 and r.json()["error"] == "launch_token_reused"


async def test_launch_rejects_wrong_audience_and_missing_claims(env):
    r = await env.client.post("/api/auth/launch", json={"launchToken": sign(launch_claims(), "sloptown-admin")})
    assert r.status_code == 401
    r = await env.client.post("/api/auth/launch", json={"launchToken": sign(launch_claims(file_role=None), "sloptown-launch")})
    assert r.status_code == 401


async def test_unauthenticated(env):
    r = await env.client.get("/api/me")
    assert r.status_code == 401 and r.json()["error"] == "unauthenticated"


async def test_oidc_login_round_trip(env):
    r = await env.client.get("/auth/login", params={"next": "/p/abc"})
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.path == "/oauth/authorize" and q["code_challenge_method"] == ["S256"]
    env.miira.id_token_claims = {"sub": "s-oidc", "name": "Ole", "role": "laerer", "nonce": q["nonce"][0]}
    r = await env.client.get("/auth/callback", params={"code": "c1", "state": q["state"][0]})
    assert r.status_code == 302 and r.headers["location"] == "/p/abc"
    assert "st_session=" in r.headers["set-cookie"]
    me = await env.client.get("/api/me")  # cookie jar carries the session
    assert me.json()["user"]["sub"] == "s-oidc"


async def test_oidc_rejects_bad_state_and_nonce(env):
    r = await env.client.get("/auth/login")
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert (await env.client.get("/auth/callback", params={"code": "c", "state": "nope"})).status_code == 400
    env.miira.id_token_claims = {"sub": "s-x", "nonce": "wrong"}
    r = await env.client.get("/auth/callback", params={"code": "c", "state": q["state"][0]})
    assert r.status_code == 400 and r.json()["error"] == "bad_nonce"


async def test_login_rejects_offsite_next(env):
    r = await env.client.get("/auth/login", params={"next": "https://evil.example/"})
    q = parse_qs(urlparse(r.headers["location"]).query)
    env.miira.id_token_claims = {"sub": "s-y", "nonce": q["nonce"][0]}
    r = await env.client.get("/auth/callback", params={"code": "c", "state": q["state"][0]})
    assert r.headers["location"] == "/"


async def test_terms_accept(env):
    h = await env.user("s-t", terms=False)
    terms = (await env.client.get("/api/terms")).json()
    assert (await env.client.post("/api/terms/accept", json={"version": "old"}, headers=h)).status_code == 409
    r = await env.client.post("/api/terms/accept", json={"version": terms["version"]}, headers=h)
    assert r.status_code == 200
    me = (await env.client.get("/api/me", headers=h)).json()
    assert me["termsAcceptedAt"] is not None


async def test_studio_config_is_public(env):
    r = await env.client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["miiraOrigins"] and body["miiraOrigin"] == body["miiraOrigins"][0]
    assert not body["miiraOrigin"].endswith("/")


async def test_cookie_writes_must_come_from_the_studio_origin(env):
    """A Vibe on a sibling subdomain must not be able to drive the studio API with the teacher's cookie."""
    from sloptown.config import get_settings

    h = await env.user("s-csrf")
    cookie = {"Cookie": f"st_session={h['Authorization'].split(' ', 1)[1]}"}
    studio = get_settings().studio_origin
    evil = "http://evil-1a2b3c.apps.sloptown.localhost:8080"

    r = await env.client.post("/api/terms/accept", json={"version": "x"}, headers={**cookie, "Origin": evil})
    assert r.status_code == 403 and r.json()["error"] == "bad_origin"
    r = await env.client.post("/api/terms/accept", json={"version": "x"}, headers=cookie)  # no Origin at all
    assert r.status_code == 403
    r = await env.client.post("/api/terms/accept", json={"version": "x"}, headers={**cookie, "Origin": studio})
    assert r.status_code != 403
    # reads, bearer-token calls and server-to-server paths are not affected
    assert (await env.client.get("/api/me", headers={**cookie, "Origin": evil})).status_code == 200
    r = await env.client.post("/api/terms/accept", json={"version": "x"}, headers={**h, "Origin": evil})
    assert r.status_code != 403

