from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, v: str) -> str:
        """Accept the `postgres://` URLs hosting providers hand out.

        Fly, Heroku, Railway and friends all inject `postgres://…`. SQLAlchemy 2
        refuses that scheme outright, and plain `postgresql://` selects psycopg2,
        which isn't installed — this project uses psycopg 3. Rewriting here means
        DATABASE_URL can be pasted verbatim from any provider.
        """
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v
    jwt_secret: str
    jwt_alg: str = "HS256"
    jwt_expires_hours: int = 720
    fernet_key: str

    apple_bundle_id: str = "com.coach.app"
    apple_team_id: str = ""
    google_client_id_ios: str = ""
    google_client_id_web: str = ""

    anthropic_api_key: str = ""
    anthropic_model_default: str = "claude-sonnet-4-6"
    anthropic_model_cheap: str = "claude-haiku-4-5-20251001"

    oura_client_id: str = ""
    oura_client_secret: str = ""
    # Empty means "derive from public_base_url" — see providers.base.redirect_uri_for.
    oura_redirect_uri: str = ""

    app_env: str = "dev"
    app_deep_link_scheme: str = "coach"
    public_base_url: str = "http://localhost:8000"

    backup_bucket: str = ""
    aws_region: str = "us-east-2"


@lru_cache
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
