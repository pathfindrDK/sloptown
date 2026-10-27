import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import respx
from fastapi.testclient import TestClient

from fake_miira import app as app_module
from fake_miira.config import get_settings
from fake_miira.store import reset_store
from fake_miira.tokens import pairwise, service_sub, vibe_member_id

STUDIO = get_settings().studio_origin
ISS = get_settings().issuer


@pytest.fixture(autouse=True)
def fresh():
    reset_store()
    yield


@pytest.fixture
def client():
    return TestClient(app_module.app)


def login(client: TestClient, user_id: str) -> None:
    r = client.post("/login", data={"user_id": user_id}, follow_redirects=False)
    assert r.status_code == 303


def verify(token: str, aud: str, client: TestClient) -> dict:
    jwk = client.get("/oidc/jwks").json()["keys"][0]
    key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
    return jwt.decode(token, key, algorithms=["RS256"], audience=aud, issuer=ISS)


def service_token(client: TestClient) -> str:
    r = client.post(
        "/oidc/token",
        data={"grant_type": "client_credentials", "scope": "vibe:members"},
        auth=("sloptown", "dev-client-secret"),
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_pairwise_matches_contract_formula():
    mac = hmac.new(b"dev-pairwise-secret", b"sloptown:u-mette", hashlib.sha256).digest()
    assert service_sub("u-mette") == base64.b32encode(mac).decode()[:26].lower()
    assert vibe_member_id("f1", "u-ali") != vibe_member_id("f2", "u-ali")
    assert pairwise("sloptown", "u-ali") != vibe_member_id("f1", "u-ali")


def test_oidc_code_flow_with_pkce(client):
    verifier = "v" * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    redirect = f"{STUDIO}/auth/callback"
    params = {
        "client_id": "sloptown",
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": "openid profile email miira",
        "state": "st",
        "nonce": "nn",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    assert client.get("/oidc/authorize", params=params).status_code == 200
    r = client.post(
        "/oidc/authorize/select",
        data={"user_id": "u-mette", **{k: params[k] for k in ("redirect_uri", "state", "nonce", "code_challenge", "client_id")}},
        follow_redirects=False,
    )
    loc = urlparse(r.headers["location"])
    q = parse_qs(loc.query)
    assert q["state"] == ["st"]
    bad = client.post(
        "/oidc/token",
        data={"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": redirect, "code_verifier": "wrong"},
        auth=("sloptown", "dev-client-secret"),
    )
    assert bad.status_code == 400  # code is consumed even on failure

    r = client.post(
        "/oidc/authorize/select",
        data={"user_id": "u-mette", **{k: params[k] for k in ("redirect_uri", "state", "nonce", "code_challenge", "client_id")}},
        follow_redirects=False,
    )
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    tok = client.post(
        "/oidc/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect, "code_verifier": verifier},
        auth=("sloptown", "dev-client-secret"),
    ).json()
    claims = verify(tok["id_token"], "sloptown", client)
    assert claims["sub"] == service_sub("u-mette")
    assert claims["role"] == "laerer" and claims["nonce"] == "nn" and claims["name"] == "Mette Hansen"
    info = client.get("/oidc/userinfo", headers={"authorization": f"Bearer {tok['access_token']}"}).json()
    assert info["sub"] == claims["sub"]


def test_authorize_rejects_foreign_redirect(client):
    r = client.get(
        "/oidc/authorize",
        params={"client_id": "sloptown", "redirect_uri": "https://evil.example/cb", "response_type": "code",
                "code_challenge": "x", "code_challenge_method": "S256"},
    )
    assert r.status_code == 400


def test_bad_client_secret(client):
    r = client.post("/oidc/token", data={"grant_type": "client_credentials", "scope": "vibe:members"}, auth=("sloptown", "nope"))
    assert r.status_code == 401


def test_create_share_members_and_launch(client):
    login(client, "u-ali")
    assert client.post("/api/files", json={"title": "x"}).status_code == 403  # students can't create

    login(client, "u-mette")
    f = client.post("/api/files", json={"title": "Quiz"}).json()
    assert f["content"] == {"projectId": f["id"]}

    with respx.mock(assert_all_called=True) as mock:
        hook = mock.post(f"{STUDIO}/hooks/miira/access-changed").mock(return_value=httpx.Response(204))
        r = client.put(
            f"/api/files/{f['id']}/shares",
            json={"shares": [{"kind": "class", "id": "c-7a", "role": "viewer"}, {"kind": "user", "id": "u-jonas", "role": "editor"}]},
        )
        assert r.status_code == 200
        req = hook.calls.last.request
        ts = req.headers["x-miira-timestamp"]
        expect = "sha256=" + hmac.new(b"dev-webhook-secret", ts.encode() + b"." + req.content, hashlib.sha256).hexdigest()
        assert req.headers["x-miira-signature"] == expect
        assert json.loads(req.content) == {"fileId": f["id"], "event": "access-changed"}

    tok = service_token(client)
    body = client.get(f"/api/vibe/{f['id']}/members", headers={"authorization": f"Bearer {tok}"}).json()
    by_name = {m["name"]: m for m in body["members"]}
    assert len(by_name) == 6  # owner + editor + 4 students
    assert by_name["Mette Hansen"]["role"] == "owner"
    assert by_name["Jonas Holm"]["role"] == "editor"
    assert by_name["Ali Yilmaz"] == {
        "id": vibe_member_id(f["id"], "u-ali"),
        "sub": service_sub("u-ali"),
        "name": "Ali Yilmaz",
        "role": "viewer",
        "classes": ["7.A"],
        "avatarHue": by_name["Ali Yilmaz"]["avatarHue"],
    }
    assert "email" not in by_name["Ali Yilmaz"]

    # version changes with shares
    v1 = body["version"]
    with respx.mock:
        respx.post(f"{STUDIO}/hooks/miira/access-changed").mock(return_value=httpx.Response(204))
        client.put(f"/api/files/{f['id']}/shares", json={"shares": []})
    v2 = client.get(f"/api/vibe/{f['id']}/members", headers={"authorization": f"Bearer {tok}"}).json()["version"]
    assert v1 != v2

    launch = client.post(f"/api/files/{f['id']}/launch").json()["launchToken"]
    claims = verify(launch, "sloptown-launch", client)
    assert claims["file_id"] == f["id"] and claims["file_role"] == "owner"
    assert claims["exp"] - claims["iat"] <= 120 and claims["jti"]


def test_members_requires_service_scope(client):
    login(client, "u-mette")
    f = client.post("/api/files", json={}).json()
    assert client.get(f"/api/vibe/{f['id']}/members").status_code == 401
    assert client.get("/api/vibe/nope/members", headers={"authorization": f"Bearer {service_token(client)}"}).status_code == 404


def test_launch_denied_without_access(client):
    login(client, "u-mette")
    f = client.post("/api/files", json={}).json()
    login(client, "u-ali")
    assert client.post(f"/api/files/{f['id']}/launch").status_code == 403
    assert client.get(f"/files/{f['id']}").status_code == 403


def test_admin_call_signs_jwt(client):
    login(client, "u-mette")
    assert client.get("/admin/vibe").status_code == 403
    login(client, "u-admin")
    assert client.get("/admin/vibe").status_code == 200
    with respx.mock:
        route = respx.get(f"{STUDIO}/admin/v1/budget-settings").mock(return_value=httpx.Response(200, json={"enabled": False}))
        r = client.post("/admin/vibe/call", json={"method": "GET", "path": "/admin/v1/budget-settings"}).json()
        assert r == {"status": 200, "body": {"enabled": False}}
        token = route.calls.last.request.headers["authorization"][7:]
    claims = verify(token, "sloptown-admin", client)
    assert claims["sub"] == service_sub("u-admin") and claims["role"] == "system-admin"
    assert claims["exp"] - claims["iat"] <= 60
    assert client.post("/admin/vibe/call", json={"method": "GET", "path": "/api/projects"}).status_code == 400


async def test_webhook_retries():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(500 if calls < 3 else 204)

    with respx.mock:
        respx.post(f"{STUDIO}/hooks/miira/access-changed").mock(side_effect=handler)
        assert await app_module.deliver_access_changed("f", delays=(0, 0, 0)) is True
    assert calls == 3


def test_deep_links(client):
    r = client.get("/drive/new", params={"type": "vibe"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"  # not logged in
    login(client, "u-ali")
    assert client.get("/drive/new", params={"type": "vibe"}, follow_redirects=False).status_code == 403
    login(client, "u-mette")
    r = client.get("/drive/new", params={"type": "vibe"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/files/")
    fid = r.headers["location"].rsplit("/", 1)[1]
    r = client.get(f"/drive/file/{fid}", params={"share": 1}, follow_redirects=False)
    assert r.headers["location"] == f"/files/{fid}?share=1"
    assert "openShare()" in client.get(f"/files/{fid}?share=1").text
