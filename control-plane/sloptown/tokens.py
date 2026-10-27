"""Token helpers: random secrets, hashing, and derivation of per-project container tokens.

Container tokens are derived (HMAC of a stored seed), so the control plane can
always recompute them to call into a container while the database only keeps
their SHA-256 hashes, which is what the egress proxy looks up.
"""

import hashlib
import hmac
import secrets

from .config import get_settings


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _derive(label: str) -> str:
    key = get_settings().internal_secret.encode()
    return "vt_" + hmac.new(key, label.encode(), hashlib.sha256).hexdigest()


def dev_token(seed: str) -> str:
    """Token handed to a vibe-dev container (build bucket)."""
    return _derive(f"dev:{seed}")


def runtime_token(project_id: str, seed: str) -> str:
    """Token handed to a vibe-runtime container (app bucket)."""
    return _derive(f"run:{project_id}:{seed}")


def consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
