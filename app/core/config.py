"""All settings come from the environment (or .env) through pydantic-settings.
Nothing else in the code reads os.environ."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Storage. SQLite for local runs and tests; Neon Postgres in production.
    database_url: str = "sqlite:///./nightwarden.db"

    # The service being watched (Citebase's API).
    target_name: str = "citebase"
    target_url: str = "http://localhost:8000"
    target_metrics_token: str | None = None
    poll_interval_s: int = 60
    poll_timeout_s: int = 70          # long enough for a free-tier cold start
    poller_enabled: bool = True

    # Triage agent (Groq, OpenAI-compatible tool calling).
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    agent_max_rounds: int = 6

    # Alerts. Discord or Slack incoming webhook; email through Brevo.
    notify_webhook_url: str | None = None
    brevo_api_key: str | None = None
    mail_from: str = "Nightwarden <alerts@example.com>"
    alert_email: str | None = None
    public_url: str = "http://localhost:7860"

    # Dead-man switch: pinged after every healthy poll cycle (e.g. healthchecks.io).
    heartbeat_url: str | None = None

    # Admin actions (resolve incidents, re-run triage, record deploys).
    admin_token: str | None = None

    log_json: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
