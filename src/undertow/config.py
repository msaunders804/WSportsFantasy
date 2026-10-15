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
    # Shown on the privacy page for questions and deletion requests.
    contact_email: str = ""
    # Serve the /preview design mockups even in prod (they are always on outside prod).
    preview_enabled: bool = False

    # Serve sign-up, login and league pages even in prod (they are always on outside prod).
    # Keep off in prod until the launch checklist says accounts open.
    accounts_enabled: bool = False

    # Resend API key for outgoing alert emails (e.g. new signups). Empty disables sending.
    email_api_key: str = ""
    # Where signup alerts go.
    alert_email: str = ""
    # Must be on a domain verified with the email provider (Resend).
    email_from: str = "Undertow Fantasy <notifications@undertowsports.com>"

    hockeytech_key: str = ""
    hockeytech_client_code: str = "pwhl"
    hockeytech_user_agent: str = "UndertowSports/0.1 (contact: set HOCKEYTECH_USER_AGENT)"

    # Comma-separated HockeyTech season IDs the worker keeps in sync (11 = 2026-27 regular season).
    ingest_season_ids: str = "11"

    @property
    def season_ids(self) -> list[int]:
        return [int(x) for x in self.ingest_season_ids.split(",") if x.strip()]

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
