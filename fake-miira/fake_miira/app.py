"""fake-miira: the Miira side of docs/miira-integration.md, for development only.

Run: uvicorn fake_miira.app:app --port 8100
"""

import asyncio
import base64
import hashlib
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import BackgroundTasks, Body, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import pages
from .config import get_settings
from .store import CLASSES, USERS, MiiraUser, Share, store
from .tokens import jwks, service_sub, sign, webhook_signature

app = FastAPI(title="fake-miira")

USER_COOKIE = "fm_user"
# In-memory OIDC state: authorization codes and opaque access tokens.
_codes: dict[str, dict[str, Any]] = {}
_access_tokens: dict[str, dict[str, Any]] = {}


# --- helpers -----------------------------------------------------------------


def _identity_claims(u: MiiraUser) -> dict[str, Any]:
    return {
        "sub": service_sub(u.id),
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "org_id": get_settings().org_id,
    }


def current_user(request: Request) -> MiiraUser:
    uid = request.cookies.get(USER_COOKIE)
    if not uid or uid not in USERS:
        raise HTTPException(401, "not logged in to fake-miira")
    return USERS[uid]


def _client_ok(request: Request, client_id: str | None, client_secret: str | None) -> bool:
    s = get_settings()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        cid, _, csec = base64.b64decode(auth[6:]).decode().partition(":")
        client_id, client_secret = cid, csec
    return client_id == s.client_id and secrets.compare_digest(client_secret or "", s.client_secret)


def _file_or_404(file_id: str):
    f = store().files.get(file_id)
    if not f:
        raise HTTPException(404, "no such vibe file")
    return f


# --- OIDC (§4) -----------------------------------------------------------------


@app.get("/.well-known/openid-configuration")
def discovery() -> dict[str, Any]:
    iss = get_settings().issuer
    return {
        "issuer": iss,
        "authorization_endpoint": f"{iss}/oidc/authorize",
        "token_endpoint": f"{iss}/oidc/token",
        "jwks_uri": f"{iss}/oidc/jwks",
        "userinfo_endpoint": f"{iss}/oidc/userinfo",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "subject_types_supported": ["pairwise"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["openid", "profile", "email", "miira", "vibe:members"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
    }


@app.get("/oidc/jwks")
def get_jwks() -> dict[str, Any]:
    return jwks()


@app.get("/oidc/authorize", response_class=HTMLResponse)
def authorize(request: Request) -> HTMLResponse:
    q = request.query_params
    s = get_settings()
    if q.get("client_id") != s.client_id:
        raise HTTPException(400, "unknown client_id")
    if not (q.get("redirect_uri") or "").startswith(s.studio_origin.rstrip("/") + "/"):
        raise HTTPException(400, "redirect_uri not registered")
    if q.get("response_type") != "code" or q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
        raise HTTPException(400, "authorization code with PKCE S256 required")
    return HTMLResponse(pages.login_page(dict(q), USERS.values(), action="/oidc/authorize/select"))


@app.post("/oidc/authorize/select")
def authorize_select(
    user_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    nonce: str = Form(""),
    code_challenge: str = Form(...),
    client_id: str = Form(...),
) -> RedirectResponse:
    if user_id not in USERS or client_id != get_settings().client_id:
        raise HTTPException(400, "bad request")
    if not redirect_uri.startswith(get_settings().studio_origin.rstrip("/") + "/"):
        raise HTTPException(400, "redirect_uri not registered")
    code = secrets.token_urlsafe(24)
    _codes[code] = {
        "user_id": user_id,
        "redirect_uri": redirect_uri,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "exp": time.time() + 120,
    }
    sep = "&" if "?" in redirect_uri else "?"
    resp = RedirectResponse(f"{redirect_uri}{sep}{urlencode({'code': code, 'state': state})}", status_code=302)
    resp.set_cookie(USER_COOKIE, user_id, httponly=True, samesite="lax")
    return resp


@app.post("/oidc/token")
async def token(request: Request) -> JSONResponse:
    form = await request.form()
    if not _client_ok(request, form.get("client_id"), form.get("client_secret")):  # type: ignore[arg-type]
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    grant = form.get("grant_type")
    if grant == "client_credentials":
        scopes = str(form.get("scope") or "").split()
        if scopes != ["vibe:members"]:
            return JSONResponse({"error": "invalid_scope"}, status_code=400)
        tok = secrets.token_urlsafe(32)
        _access_tokens[tok] = {"kind": "service", "scope": "vibe:members", "exp": time.time() + 600}
        return JSONResponse({"access_token": tok, "token_type": "Bearer", "expires_in": 600, "scope": "vibe:members"})
    if grant != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    entry = _codes.pop(str(form.get("code")), None)
    if not entry or entry["exp"] < time.time() or entry["redirect_uri"] != form.get("redirect_uri"):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    verifier = str(form.get("code_verifier") or "")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if not secrets.compare_digest(challenge, entry["code_challenge"]):
        return JSONResponse({"error": "invalid_grant", "error_description": "PKCE mismatch"}, status_code=400)
    u = USERS[entry["user_id"]]
    claims = {"aud": get_settings().client_id, **_identity_claims(u)}
    if entry["nonce"]:
        claims["nonce"] = entry["nonce"]
    access = secrets.token_urlsafe(32)
    _access_tokens[access] = {"kind": "user", "user_id": u.id, "exp": time.time() + 600}
    return JSONResponse(
        {"access_token": access, "token_type": "Bearer", "expires_in": 600, "id_token": sign(claims, ttl=600)}
    )


def _bearer(request: Request) -> dict[str, Any]:
    auth = request.headers.get("authorization", "")
    entry = _access_tokens.get(auth[7:]) if auth.lower().startswith("bearer ") else None
    if not entry or entry["exp"] < time.time():
        raise HTTPException(401, "invalid token")
    return entry


@app.get("/oidc/userinfo")
def userinfo(entry: dict[str, Any] = Depends(_bearer)) -> dict[str, Any]:
    if entry["kind"] != "user":
        raise HTTPException(403, "user token required")
    return _identity_claims(USERS[entry["user_id"]])


# --- Members API (§6) ----------------------------------------------------------


@app.get("/api/vibe/{file_id}/members")
def members(file_id: str, entry: dict[str, Any] = Depends(_bearer)) -> dict[str, Any]:
    if entry.get("scope") != "vibe:members":
        raise HTTPException(403, "scope vibe:members required")
    _file_or_404(file_id)
    ms = store().members(file_id)
    version = hashlib.sha256(json.dumps(ms, sort_keys=True).encode()).hexdigest()[:16]
    return {"fileId": file_id, "version": version, "members": ms}


# --- Webhook (§7) --------------------------------------------------------------


async def deliver_access_changed(file_id: str, delays: tuple[float, ...] = (1, 2, 4)) -> bool:
    """POST the signed webhook; 3 attempts with backoff. Returns True on 2xx."""
    body = json.dumps({"fileId": file_id, "event": "access-changed"}).encode()
    url = f"{get_settings().studio_backend}/hooks/miira/access-changed"
    async with httpx.AsyncClient(timeout=5) as client:
        for attempt, delay in enumerate(delays):
            ts = str(int(time.time()))
            headers = {
                "content-type": "application/json",
                "x-miira-timestamp": ts,
                "x-miira-signature": webhook_signature(ts, body),
            }
            try:
                r = await client.post(url, content=body, headers=headers)
                if r.is_success:
                    return True
            except httpx.HTTPError:
                pass
            if attempt < len(delays) - 1:
                await asyncio.sleep(delay)
    return False


# --- Drive-like UI and its JSON API --------------------------------------------


@app.get("/", response_class=HTMLResponse)
def drive(request: Request) -> HTMLResponse:
    uid = request.cookies.get(USER_COOKIE)
    if uid not in USERS:
        return HTMLResponse(pages.login_page({}, USERS.values(), action="/login"))
    u = USERS[uid]
    files = [(f, role) for f, role in store().files_for(u.id)]
    return HTMLResponse(pages.drive_page(u, files))


@app.post("/login")
def login(user_id: str = Form(...)) -> RedirectResponse:
    if user_id not in USERS:
        raise HTTPException(400, "unknown user")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(USER_COOKIE, user_id, httponly=True, samesite="lax")
    return resp


@app.post("/logout")
def logout() -> RedirectResponse:
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(USER_COOKIE)
    return resp


@app.get("/files/{file_id}", response_class=HTMLResponse)
def open_file(file_id: str, u: MiiraUser = Depends(current_user)) -> HTMLResponse:
    f = _file_or_404(file_id)
    role = store().role_of(f, u.id)
    if not role:
        raise HTTPException(403, "no access")
    return HTMLResponse(pages.file_page(u, f, role, get_settings().studio_origin.rstrip("/")))


@app.get("/drive/new")
def drive_new(request: Request, type: str = "vibe") -> RedirectResponse:
    """Deep link from the standalone studio's "Ny Vibe" (contract §2)."""
    uid = request.cookies.get(USER_COOKIE)
    if uid not in USERS:
        return RedirectResponse("/", status_code=303)
    if type != "vibe" or USERS[uid].role != "laerer":
        raise HTTPException(403, "only teachers can create Vibes")
    f = store().create_file(uid, "Ny Vibe")
    return RedirectResponse(f"/files/{f.id}", status_code=303)


@app.get("/drive/file/{file_id}")
def drive_file(file_id: str, share: int = 0) -> RedirectResponse:
    """Deep link from the standalone studio's Share button (contract §2)."""
    _file_or_404(file_id)
    return RedirectResponse(f"/files/{file_id}" + ("?share=1" if share else ""), status_code=303)


@app.post("/api/files")
def create_file(body: dict[str, Any] = Body(default={}), u: MiiraUser = Depends(current_user)) -> dict[str, Any]:
    if u.role != "laerer":
        raise HTTPException(403, "only teachers can create Vibes")
    f = store().create_file(u.id, str(body.get("title") or "Ny Vibe"))
    return {"id": f.id, "title": f.title, "type": "vibe", "content": f.content}


@app.patch("/api/files/{file_id}")
def rename_file(file_id: str, body: dict[str, Any] = Body(...), u: MiiraUser = Depends(current_user)) -> dict[str, Any]:
    f = _file_or_404(file_id)
    if store().role_of(f, u.id) not in ("owner", "editor"):
        raise HTTPException(403, "no edit access")
    store().rename(file_id, str(body.get("title") or f.title)[:200])
    return {"id": f.id, "title": f.title}


@app.post("/api/files/{file_id}/launch")
def launch(file_id: str, u: MiiraUser = Depends(current_user)) -> dict[str, str]:
    """Mint the §5 launch token for the iframe."""
    f = _file_or_404(file_id)
    role = store().role_of(f, u.id)
    if not role:
        raise HTTPException(403, "no access")
    claims = {"aud": "sloptown-launch", **_identity_claims(u), "file_id": f.id, "file_role": role}
    return {"launchToken": sign(claims, ttl=120)}


@app.get("/api/files/{file_id}/shares")
def get_shares(file_id: str, u: MiiraUser = Depends(current_user)) -> dict[str, Any]:
    f = _file_or_404(file_id)
    if not store().role_of(f, u.id):
        raise HTTPException(403, "no access")
    return {
        "shares": [s.__dict__ for s in f.shares],
        "candidates": {
            "users": [{"id": x.id, "name": x.name} for x in USERS.values() if x.id != f.owner_id],
            "classes": [{"id": c.id, "name": c.name} for c in CLASSES.values()],
        },
        "canEdit": f.owner_id == u.id,
    }


@app.put("/api/files/{file_id}/shares")
def put_shares(
    file_id: str,
    background: BackgroundTasks,
    body: dict[str, Any] = Body(...),
    u: MiiraUser = Depends(current_user),
) -> dict[str, Any]:
    f = _file_or_404(file_id)
    if f.owner_id != u.id:
        raise HTTPException(403, "only the owner can share")
    shares: list[Share] = []
    for s in body.get("shares", []):
        if s.get("kind") not in ("user", "class") or s.get("role") not in ("editor", "viewer"):
            raise HTTPException(400, "bad share")
        known = USERS if s["kind"] == "user" else CLASSES
        if s.get("id") not in known or s.get("id") == f.owner_id:
            raise HTTPException(400, "unknown share target")
        shares.append(Share(kind=s["kind"], id=s["id"], role=s["role"]))
    store().set_shares(file_id, shares)
    background.add_task(deliver_access_changed, file_id)
    return {"shares": [s.__dict__ for s in shares]}


# --- System admin "Vibe" section (§8) ------------------------------------------


@app.get("/admin/vibe", response_class=HTMLResponse)
def admin_page(u: MiiraUser = Depends(current_user)) -> HTMLResponse:
    if u.role != "system-admin":
        raise HTTPException(403, "system admins only")
    subs = [(x.name, x.role, service_sub(x.id)) for x in USERS.values()]
    return HTMLResponse(pages.admin_page(u, subs))


@app.post("/admin/vibe/call")
async def admin_call(body: dict[str, Any] = Body(...), u: MiiraUser = Depends(current_user)) -> JSONResponse:
    """Server-side caller for SlopTown's /admin/v1/* with a freshly signed admin JWT."""
    if u.role != "system-admin":
        raise HTTPException(403, "system admins only")
    method = str(body.get("method", "GET")).upper()
    path = str(body.get("path", ""))
    if method not in ("GET", "PUT", "POST") or not path.startswith("/admin/v1/"):
        raise HTTPException(400, "only /admin/v1/* with GET/PUT/POST")
    jwt_ = sign({"aud": "sloptown-admin", "sub": service_sub(u.id), "name": u.name, "role": u.role}, ttl=60)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.request(
                method,
                get_settings().studio_backend + path,
                headers={"authorization": f"Bearer {jwt_}"},
                json=body.get("body") if method != "GET" else None,
            )
        except httpx.HTTPError as e:
            return JSONResponse({"status": 502, "body": {"error": "unreachable", "message": str(e)}})
    try:
        payload: Any = r.json()
    except ValueError:
        payload = r.text
    return JSONResponse({"status": r.status_code, "body": payload})


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}
