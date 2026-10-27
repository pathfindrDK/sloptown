"""Settings, read from FAKE_MIIRA_* environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FAKE_MIIRA_", extra="ignore")

    # Public origin of fake-miira, as the browser sees it. Also the OIDC issuer.
    issuer: str = "http://miira.sloptown.localhost:8080"
    # Studio origin as the browser sees it (iframe src, redirect URI check).
    studio_origin: str = "http://studio.sloptown.localhost:8080"
    # How fake-miira's backend reaches SlopTown (webhook, admin API). Defaults to studio_origin.
    studio_api_base: str = ""
    client_id: str = "sloptown"
    client_secret: str = "dev-client-secret"
    webhook_secret: str = "dev-webhook-secret"
    pairwise_secret: str = "dev-pairwise-secret"
    org_id: str = "org-skole"
    # Optional persistence. Empty = in memory (signing key regenerated on restart).
    data_file: str = ""
    key_file: str = ""

    @property
    def studio_backend(self) -> str:
        return (self.studio_api_base or self.studio_origin).rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
