"""Settings, read from environment (prefix SLOPTOWN_) or a .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SLOPTOWN_", env_file=".env", extra="ignore")

    # Public origins
    studio_origin: str = "http://studio.sloptown.localhost:8080"
    apps_domain: str = "apps.sloptown.localhost"
    apps_scheme: str = "http"
    apps_port: str = ":8080"  # appended to app/preview URLs; "" in prod
    miira_origins: list[str] = ["http://miira.sloptown.localhost:8080"]

    # Database
    database_url: str = "postgresql+asyncpg://sloptown:sloptown@localhost:5432/sloptown"

    # Miira (OIDC issuer + APIs). issuer is what tokens carry; api_base is how we reach it.
    miira_issuer: str = "http://miira.sloptown.localhost:8080"
    miira_api_base: str = "http://localhost:8100"
    oidc_client_id: str = "sloptown"
    oidc_client_secret: str = "dev-client-secret"
    miira_webhook_secret: str = "dev-webhook-secret"

    # Secrets shared inside SlopTown
    session_secret: str = "dev-session-secret"
    internal_secret: str = "dev-internal-secret"  # egress -> control plane
    edge_secret: str = "dev-edge-secret"  # edge -> apps (X-Vibe-Edge-Secret)

    # Podman
    podman_url: str = "unix:///run/podman/podman.sock"
    registry: str = "localhost:5000/sloptown"
    dev_image: str = "vibe-dev:latest"
    runtime_image: str = "vibe-runtime:latest"
    userns_auto: bool = True  # per-container user namespaces; needs subuid/subgid for "containers"
    # "runsc" for gVisor. Over the libpod API podman ignores a per-container runtime and
    # uses its service's, so the runtime really comes from the socket (deploy/harden-host.sh
    # runs a runsc service); this value also marks the isolation in orchestrator.slot_tag.
    oci_runtime: str = ""
    edge_container: str = "sloptown-edge"
    egress_container: str = "sloptown-egress"
    cp_container: str = "sloptown-cp"  # joins project networks to reach OpenCode/agentd
    background_tasks: bool = True  # pool refill loop and friends; off in tests
    members_ttl_seconds: int = 60
    egress_url_for_containers: str = "http://sloptown-egress:8200"
    egress_proxy_port: int = 8201  # CONNECT proxy on the egress host
    pool_size: int = 5
    container_memory: str = "2g"
    container_cpus: float = 1.0
    container_pids: int = 512
    orchestrator_enabled: bool = True

    # Inference. The agent (OpenCode) uses the Responses API: GPT-6 models only take
    # function tools there. app_ai_model is the SDK default for ai.chat in apps.
    agent_model: str = "gpt-6.1-sol"
    agent_small_model: str = "gpt-6-luna"
    app_ai_model: str = "gpt-6-luna"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    control_plane_url: str = "http://sloptown-cp:8000"  # used by egress

    # Admin API
    admin_allowed_ips: list[str] = []  # also enforced at the edge; empty = edge only

    # Budget notifications to the watchers in budget settings. Empty host = log only.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    smtp_from: str = "SlopTown <noreply@sloptown.dk>"

    terms_version: str = "2026-10-1"
    dev_mode: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
