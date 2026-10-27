"""Signing key, JWT helpers and pairwise ids (docs/miira-integration.md §1)."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .config import get_settings

KID = "fake-miira-1"
_private_key: rsa.RSAPrivateKey | None = None


def private_key() -> rsa.RSAPrivateKey:
    global _private_key
    if _private_key is None:
        path = get_settings().key_file
        if path and Path(path).exists():
            _private_key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)  # type: ignore[assignment]
        else:
            _private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            if path:
                Path(path).write_bytes(
                    _private_key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    )
                )
    return _private_key  # type: ignore[return-value]


def jwks() -> dict[str, Any]:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key().public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


def sign(claims: dict[str, Any], ttl: int) -> str:
    now = int(time.time())
    payload = {"iss": get_settings().issuer, "iat": now, "exp": now + ttl, "jti": str(uuid.uuid4()), **claims}
    return jwt.encode(payload, private_key(), algorithm="RS256", headers={"kid": KID})


def verify(token: str, audience: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        private_key().public_key(),
        algorithms=["RS256"],
        audience=audience,
        issuer=get_settings().issuer,
    )


def pairwise(sector: str, miira_user_id: str) -> str:
    mac = hmac.new(get_settings().pairwise_secret.encode(), f"{sector}:{miira_user_id}".encode(), hashlib.sha256)
    return base64.b32encode(mac.digest()).decode()[:26].lower()


def service_sub(miira_user_id: str) -> str:
    return pairwise("sloptown", miira_user_id)


def vibe_member_id(file_id: str, miira_user_id: str) -> str:
    return pairwise(f"vibe:{file_id}", miira_user_id)


def avatar_hue(member_id: str) -> int:
    return int(hashlib.sha256(member_id.encode()).hexdigest()[:8], 16) % 360


def webhook_signature(timestamp: str, body: bytes) -> str:
    mac = hmac.new(get_settings().webhook_secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()
