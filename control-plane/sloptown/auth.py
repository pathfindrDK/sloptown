"""Studio authentication: OIDC login (code + PKCE) against Miira, launch-token exchange, sessions.

Owns /auth/login, /auth/callback, /auth/logout, /api/auth/launch and /api/me, plus
the `current_principal` dependency every studio route uses. Sessions are opaque
random tokens; only their SHA-256 hash is stored.
"""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode, urlparse

import jwt
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .audit import audit
from .config import get_settings
from .db import get_db
from .errors import ApiError
from .miira import MiiraError, miira
from .models import LaunchJti, Session, User
from .tokens import hash_token, random_token

router = APIRouter()

SESSION_COOKIE = "st_session"
OIDC_COOKIE = "st_oidc"
SESSION_TTL = timedelta(hours=12)
LAUNCH_AUDIENCE = "sloptown-launch"
ROLES_THAT_CREATE = {"laerer"}


@dataclass
class Principal:
    user: User
    session: Session

    @property
    def sub(self) -> str:
        return self.user.sub

    @property
    def is_system_admin(self) -> bool:
        return self.user.role == "system-admin"


def secure_cookies() -> bool:
    origin = get_settings().studio_origin
    return origin.startswith("https://") or ".localhost" in origin or "//localhost" in origin


def set_cookie(
    response: Response, name: str, value: str, *, max_age: int, partitioned: bool, path: str = "/"
) -> None:
    """Set a host-only HttpOnly cookie; partitioned (CHIPS) cookies are SameSite=None."""
    parts = [f"{name}={value}", f"Path={path}", f"Max-Age={max_age}", "HttpOnly"]
    if partitioned:
        parts += ["Secure", "SameSite=None", "Partitioned"]
    else:
        parts.append("SameSite=Lax")
        if secure_cookies():
            parts.append("Secure")
    response.headers.append("set-cookie", "; ".join(parts))


def clear_cookie(response: Response, name: str) -> None:
    response.headers.append("set-cookie", f"{name}=; Path=/; Max-Age=0; HttpOnly")


async def upsert_user(db: AsyncSession, claims: dict[str, Any]) -> User:
    user = await db.get(User, claims["sub"])
    if user is None:
        user = User(sub=claims["sub"])
        db.add(user)
    user.name = claims.get("name") or user.name or ""
    user.email = claims.get("email") or user.email or ""
    user.role = claims.get("role") or user.role or "elev"
    user.org_id = claims.get("org_id") or user.org_id or ""
    user.last_seen_at = datetime.now(UTC)
    await db.flush()  # sessions reference users; no ORM relationship orders the inserts
    return user


async def create_session(
    db: AsyncSession,
    sub: str,
    *,
    partitioned: bool = False,
    file_id: str | None = None,
    file_role: str | None = None,
) -> str:
    token = random_token()
    db.add(
        Session(
            id=secrets.token_hex(8),
            token_hash=hash_token(token),
            sub=sub,
            partitioned=partitioned,
            file_id=file_id,
            file_role=file_role,
            expires_at=datetime.now(UTC) + SESSION_TTL,
        )
    )
    return token


def _session_token(request: Request) -> str | None:
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        return authz[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


async def optional_principal(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> Principal | None:
    token = _session_token(request)
    if not token:
        return None
    sess = (await db.execute(select(Session).where(Session.token_hash == hash_token(token)))).scalar_one_or_none()
    if sess is None or sess.expires_at < datetime.now(UTC):
        return None
    user = await db.get(User, sess.sub)
    if user is None:
        return None
    return Principal(user=user, session=sess)


async def current_principal(
    principal: Annotated[Principal | None, Depends(optional_principal)],
) -> Principal:
    if principal is None:
        raise ApiError(401, "unauthenticated", "Du er ikke logget ind")
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
Db = Annotated[AsyncSession, Depends(get_db)]


def safe_next(next_url: str | None) -> str:
    """Only allow relative paths or the studio origin as post-login targets."""
    if not next_url:
        return "/"
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    studio = urlparse(get_settings().studio_origin)
    target = urlparse(next_url)
    if (target.scheme, target.netloc) == (studio.scheme, studio.netloc):
        return next_url
    return "/"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def redirect_uri() -> str:
    return get_settings().studio_origin.rstrip("/") + "/auth/callback"


@router.get("/auth/login")
async def login(next: str | None = None) -> Response:
    s = get_settings()
    d = await miira.discovery()
    verifier, challenge = _pkce_pair()
    state, nonce = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": s.oidc_client_id,
        "redirect_uri": redirect_uri(),
        "scope": "openid profile email miira",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    response = RedirectResponse(d["authorization_endpoint"] + "?" + urlencode(params), status_code=302)
    flow = jwt.encode(
        {"state": state, "verifier": verifier, "nonce": nonce, "next": safe_next(next), "exp": int(time.time()) + 600},
        s.session_secret,
        algorithm="HS256",
    )
    set_cookie(response, OIDC_COOKIE, flow, max_age=600, partitioned=False, path="/auth")
    return response


@router.get("/auth/callback")
async def callback(request: Request, db: Db, code: str = "", state: str = "") -> Response:
    s = get_settings()
    raw = request.cookies.get(OIDC_COOKIE)
    try:
        flow = jwt.decode(raw or "", s.session_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise ApiError(400, "login_expired", "Login-forløbet er udløbet. Prøv igen.")
    if not code or not secrets.compare_digest(state, flow["state"]):
        raise ApiError(400, "bad_state", "Ugyldigt login-svar")
    try:
        tokens = await miira.exchange_code(code, flow["verifier"], redirect_uri())
        claims = await miira.verify(tokens["id_token"], audience=s.oidc_client_id)
    except (MiiraError, jwt.PyJWTError, KeyError) as e:
        raise ApiError(400, "login_failed", "Login mislykkedes", detail=str(e))
    if claims.get("nonce") != flow["nonce"]:
        raise ApiError(400, "bad_nonce", "Ugyldigt login-svar")
    await upsert_user(db, claims)
    token = await create_session(db, claims["sub"])
    audit(db, "auth.login", actor_sub=claims["sub"], request=request)
    await db.commit()
    response = RedirectResponse(flow["next"], status_code=302)
    set_cookie(response, SESSION_COOKIE, token, max_age=int(SESSION_TTL.total_seconds()), partitioned=False)
    clear_cookie(response, OIDC_COOKIE)
    return response


@router.post("/auth/logout")
async def logout(request: Request, db: Db) -> Response:
    token = _session_token(request)
    if token:
        await db.execute(delete(Session).where(Session.token_hash == hash_token(token)))
        await db.commit()
    response = Response(status_code=204)
    clear_cookie(response, SESSION_COOKIE)
    return response


class LaunchBody(BaseModel):
    launchToken: str


@router.post("/api/auth/launch")
async def launch(body: LaunchBody, request: Request, db: Db) -> Response:
    try:
        claims = await miira.verify(body.launchToken, audience=LAUNCH_AUDIENCE)
    except (MiiraError, jwt.PyJWTError) as e:
        raise ApiError(401, "invalid_launch_token", "Ugyldigt eller udløbet adgangslink", detail=str(e))
    for field in ("jti", "file_id", "file_role"):
        if not claims.get(field):
            raise ApiError(401, "invalid_launch_token", f"Mangler {field}")
    if claims["file_role"] not in ("owner", "editor", "viewer"):
        raise ApiError(401, "invalid_launch_token", "Ukendt rolle")
    db.add(LaunchJti(jti=claims["jti"], exp=datetime.fromtimestamp(claims["exp"], UTC)))
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ApiError(401, "launch_token_reused", "Adgangslinket er allerede brugt")
    user = await upsert_user(db, claims)
    token = await create_session(
        db, user.sub, partitioned=True, file_id=claims["file_id"].lower(), file_role=claims["file_role"]
    )
    await db.execute(delete(LaunchJti).where(LaunchJti.exp < datetime.now(UTC) - timedelta(minutes=5)))
    audit(db, "auth.launch", actor_sub=user.sub, project_id=claims["file_id"].lower(), request=request)
    await db.commit()
    response = JSONResponse(
        {"token": token, "user": user_json(user), "fileId": claims["file_id"], "fileRole": claims["file_role"]}
    )
    set_cookie(response, SESSION_COOKIE, token, max_age=int(SESSION_TTL.total_seconds()), partitioned=True)
    return response


def user_json(user: User) -> dict[str, Any]:
    return {
        "sub": user.sub,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "orgId": user.org_id,
        "isSystemAdmin": user.role == "system-admin",
    }


@router.get("/api/me")
async def me(principal: CurrentPrincipal) -> dict[str, Any]:
    u = principal.user
    return {
        "user": user_json(u),
        "termsAcceptedAt": u.terms_accepted_at.isoformat() if u.terms_accepted_at else None,
        "termsVersion": u.terms_version,
        "canCreate": u.role in ROLES_THAT_CREATE,
    }
