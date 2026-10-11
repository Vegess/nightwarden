"""HTTP client for the watched service (Citebase): /metrics and /metrics/logs."""
import re

import httpx

from app.core.config import get_settings

settings = get_settings()

_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def redact(text: str) -> str:
    """Logs end up on the dashboard and in the model's context: drop ids and emails."""
    return _EMAIL.sub("<email>", _UUID.sub("<id>", text))


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.target_metrics_token}"} if settings.target_metrics_token else {}


def fetch_metrics(timeout: float | None = None) -> dict:
    resp = httpx.get(f"{settings.target_url.rstrip('/')}/metrics", headers=_headers(),
                     timeout=timeout or settings.poll_timeout_s)
    resp.raise_for_status()
    return resp.json()


def fetch_logs(since: str | None = None, level: str | None = None, contains: str | None = None,
               limit: int = 50) -> list[dict]:
    params = {k: v for k, v in {"since": since, "level": level, "contains": contains, "limit": limit}.items() if v}
    resp = httpx.get(f"{settings.target_url.rstrip('/')}/metrics/logs", headers=_headers(), params=params, timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    out = []
    for r in rows:
        entry = {k: r.get(k) for k in ("ts", "level", "logger", "msg", "endpoint", "status_code", "fallback_reason", "provider")
                 if r.get(k) is not None}
        entry["msg"] = redact(str(entry.get("msg", "")))[:400]
        if r.get("exc"):
            entry["exc"] = redact(str(r["exc"]).strip().splitlines()[-1])[:300]  # last line: the exception itself
        out.append(entry)
    return out
