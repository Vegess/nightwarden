"""All settings come from the environment (or .env) through pydantic-settings.
Nothing else in the code reads os.environ."""
from functools import lru_cache
from typing import Literal

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
    poller_enabled: bool = True       # background thread; off on serverless hosts (Vercel)
    down_after_polls: int = 3         # consecutive failed polls before target_down
    # Serverless mode: an external scheduler calls /api/cron/poll with this bearer secret.
    cron_secret: str | None = None
    vercel: bool = False              # set to 1 by Vercel itself; disables the background thread

    # Triage agent (Groq, OpenAI-compatible tool calling).
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    agent_max_rounds: int = 6
    orchestrator: Literal["loop", "langgraph"] = "loop"

    # Backup AI provider, tried when Groq is unavailable (Gemini first if both are set),
    # before falling back to the rules. Decisions it makes are marked degraded.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"

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
