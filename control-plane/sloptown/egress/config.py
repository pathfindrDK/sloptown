"""Egress throttling settings from edge/egress.env (or the process environment)."""

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[3] / "edge" / "egress.env"

KEYS = (
    "EGRESS_ALLOWLIST",
    "EGRESS_RPS_PER_PROJECT",
    "EGRESS_BURST_PER_PROJECT",
    "EGRESS_MAX_CONCURRENT_PER_PROJECT",
    "INFERENCE_RPM_PER_PROJECT",
    "INFERENCE_RPM_GLOBAL",
    "EGRESS_MAX_BODY_BYTES",
    "EGRESS_TIMEOUT_SECONDS",
    "EGRESS_PROXY_PORT",
)


def env_file_path() -> Path:
    return Path(os.environ.get("SLOPTOWN_EGRESS_ENV_FILE", DEFAULT_ENV_FILE))


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text()
    except FileNotFoundError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass
class EgressConfig:
    allowlist: list[str] = field(default_factory=list)
    rps_per_project: float = 10
    burst_per_project: int = 30
    max_concurrent_per_project: int = 8
    inference_rpm_per_project: int = 60
    inference_rpm_global: int = 600
    max_body_bytes: int = 8 * 1024 * 1024
    timeout_seconds: float = 120
    proxy_port: int = 8201

    @classmethod
    def load(cls) -> "EgressConfig":
        """File values first, process environment overrides (quadlet EnvironmentFile sets both)."""
        raw = {**parse_env_file(env_file_path()), **{k: os.environ[k] for k in KEYS if k in os.environ}}
        cfg = cls()
        if raw.get("EGRESS_ALLOWLIST"):
            cfg.allowlist = [h.strip().lower() for h in raw["EGRESS_ALLOWLIST"].split(",") if h.strip()]
        conv = {
            "EGRESS_RPS_PER_PROJECT": ("rps_per_project", float),
            "EGRESS_BURST_PER_PROJECT": ("burst_per_project", int),
            "EGRESS_MAX_CONCURRENT_PER_PROJECT": ("max_concurrent_per_project", int),
            "INFERENCE_RPM_PER_PROJECT": ("inference_rpm_per_project", int),
            "INFERENCE_RPM_GLOBAL": ("inference_rpm_global", int),
            "EGRESS_MAX_BODY_BYTES": ("max_body_bytes", int),
            "EGRESS_TIMEOUT_SECONDS": ("timeout_seconds", float),
            "EGRESS_PROXY_PORT": ("proxy_port", int),
        }
        for key, (attr, typ) in conv.items():
            if raw.get(key):
                setattr(cfg, attr, typ(raw[key]))
        return cfg

    def public(self) -> dict:
        """Shape returned by GET /admin/v1/egress-settings."""
        d = asdict(self)
        return {
            "allowlist": d["allowlist"],
            "rpsPerProject": d["rps_per_project"],
            "burstPerProject": d["burst_per_project"],
            "maxConcurrentPerProject": d["max_concurrent_per_project"],
            "inferenceRpmPerProject": d["inference_rpm_per_project"],
            "inferenceRpmGlobal": d["inference_rpm_global"],
            "maxBodyBytes": d["max_body_bytes"],
            "timeoutSeconds": d["timeout_seconds"],
            "proxyPort": d["proxy_port"],
            "source": "env",
            "editable": False,
        }


def host_allowed(allowlist: list[str], host: str, port: int) -> bool:
    host = host.lower().rstrip(".")
    for entry in allowlist:
        pattern, _, pport = entry.partition(":")
        if pport and int(pport) != port:
            continue
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False
