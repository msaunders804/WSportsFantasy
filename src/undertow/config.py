from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Must be a direct (session-mode) connection: LISTEN/NOTIFY does not work through a
    # transaction-mode pooler.
    database_url: str = "postgresql+psycopg://undertow:undertow@localhost:5433/undertow"
    secret_key: str = Field(default="dev-only-change-me", min_length=8)
    environment: str = "dev"

    app_name: str = "Undertow Sports"
    base_url: str = "http://localhost:8000"

    hockeytech_key: str = ""
    hockeytech_client_code: str = "pwhl"
    hockeytech_user_agent: str = "UndertowSports/0.1 (contact: set HOCKEYTECH_USER_AGENT)"

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
