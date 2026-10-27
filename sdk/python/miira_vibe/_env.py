"""Environment lookups. Read at call time so tests and dev servers can change them."""

import os


def mode() -> str:
    return os.environ.get("VIBE_MODE", "dev")


def is_dev() -> bool:
    return mode() == "dev"


def platform_url() -> str:
    return os.environ.get("VIBE_PLATFORM_URL", "").rstrip("/")


def project_token() -> str:
    return os.environ.get("VIBE_PROJECT_TOKEN", "")


def edge_secret() -> str:
    return os.environ.get("VIBE_EDGE_SECRET", "")


def openai_base_url() -> str:
    return os.environ.get("OPENAI_BASE_URL", (platform_url() + "/v1") if platform_url() else "").rstrip("/")


def openai_api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "") or project_token()


def default_model() -> str:
    return os.environ.get("VIBE_AI_MODEL", "gpt-6-luna")


def db_path() -> str:
    return os.environ.get("VIBE_DB_PATH", "/data/app.sqlite")
