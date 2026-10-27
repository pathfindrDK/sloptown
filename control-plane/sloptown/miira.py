"""Client for Miira as identity provider: OIDC discovery, JWKS, JWT verification, service token.

Two Miira base URLs are in play: `miira_issuer` is what tokens carry in `iss`
(the real Miira's is `<origin>/api/vibe/oidc`, fake-miira's its bare origin) and
its origin is where browsers are sent; `miira_api_base` is the origin this server
reaches Miira on (in dev an internal name). Discovery is the issuer plus
`/.well-known/openid-configuration`, as OIDC prescribes; it and every back-channel
URL found in it are rewritten from the issuer's origin to the api base.
"""

import time
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt

from .config import get_settings

DISCOVERY_TTL = 3600
SERVICE_SCOPE = "vibe:members"


class MiiraError(Exception):
    pass


class MiiraClient:
    def __init__(self) -> None:
        self._discovery: dict[str, Any] | None = None
        self._discovery_at = 0.0
        self._jwks: jwt.PyJWKSet | None = None
        self._jwks_at = 0.0
        self._service_token: str | None = None
        self._service_token_exp = 0.0

    def reset(self) -> None:
        self.__init__()

    def backchannel(self, url: str) -> str:
        s = get_settings()
        public = issuer_origin()
        if url == public or url.startswith(public + "/"):
            return s.miira_api_base.rstrip("/") + url[len(public) :]
        return url

    async def discovery(self) -> dict[str, Any]:
        if self._discovery is None or time.time() - self._discovery_at > DISCOVERY_TTL:
            url = self.backchannel(get_settings().miira_issuer.rstrip("/") + "/.well-known/openid-configuration")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url)
            if r.status_code != 200:
                raise MiiraError(f"discovery failed: {r.status_code}")
            self._discovery = r.json()
            self._discovery_at = time.time()
        return self._discovery

    async def _jwk_set(self, force: bool = False) -> jwt.PyJWKSet:
        if force or self._jwks is None or time.time() - self._jwks_at > DISCOVERY_TTL:
            d = await self.discovery()
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(self.backchannel(d["jwks_uri"]))
            if r.status_code != 200:
                raise MiiraError(f"jwks failed: {r.status_code}")
            self._jwks = jwt.PyJWKSet.from_dict(r.json())
            self._jwks_at = time.time()
        return self._jwks

    async def verify(self, token: str, audience: str) -> dict[str, Any]:
        """Verify an RS256 JWT signed by Miira for `audience`. Raises jwt.PyJWTError."""
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        keys = await self._jwk_set()
        key = _find_key(keys, kid)
        if key is None:  # key rotation: refetch once
            key = _find_key(await self._jwk_set(force=True), kid)
        if key is None:
            raise jwt.InvalidKeyError("unknown signing key")
        return jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=get_settings().miira_issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            leeway=30,
        )

    async def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        s = get_settings()
        d = await self.discovery()
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                self.backchannel(d["token_endpoint"]),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                },
                auth=(s.oidc_client_id, s.oidc_client_secret),
            )
        if r.status_code != 200:
            raise MiiraError(f"token exchange failed: {r.status_code} {r.text[:200]}")
        return r.json()

    async def service_token(self) -> str:
        """client_credentials token for the members API, cached until shortly before expiry."""
        if self._service_token and time.time() < self._service_token_exp - 30:
            return self._service_token
        s = get_settings()
        d = await self.discovery()
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                self.backchannel(d["token_endpoint"]),
                data={"grant_type": "client_credentials", "scope": SERVICE_SCOPE},
                auth=(s.oidc_client_id, s.oidc_client_secret),
            )
        if r.status_code != 200:
            raise MiiraError(f"client_credentials failed: {r.status_code}")
        body = r.json()
        self._service_token = body["access_token"]
        self._service_token_exp = time.time() + int(body.get("expires_in", 300))
        return self._service_token


def issuer_origin() -> str:
    u = urlsplit(get_settings().miira_issuer)
    return f"{u.scheme}://{u.netloc}"


def _find_key(keys: jwt.PyJWKSet, kid: str | None) -> jwt.PyJWK | None:
    for k in keys.keys:
        if kid is None or k.key_id == kid:
            return k
    return None


miira = MiiraClient()
