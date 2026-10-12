"""HTTP client for the watched service (Citebase): /metrics and /metrics/logs."""
import re

import httpx

from app.core.config import get_settings

settings = get_settings()

_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


_SECRET = re.compile(r"\b(?:gsk_|sk-|sk_|xox[bp]-|ghp_|github_pat_)[A-Za-z0-9_\-]{8,}|"
                     r"(?i:bearer)\s+[A-Za-z0-9._\-]{12,}|\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9+/_\-]{40,}={0,2}")
# Anything after these markers can carry user data: SQL parameters (questions, names), or the
# model's own output quoted back in a provider error (which may quote document text).
_PAYLOAD = re.compile(r"\[SQL:|\[parameters:|failed_generation|\(Background on this error|'messages':|\"messages\":",
                      re.I)
_EXC_LINE = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt|Timeout)\b.*", re.M)


def redact(text: str) -> str:
    """Logs end up on the public dashboard and in the model's context.

    Drop ids, emails and anything shaped like a credential, and cut every line at the first
    marker of a data payload, so a question, a passage or a key can never be shown.
    """
    text = str(text)
    m = _PAYLOAD.search(text)
    if m:
        text = text[:m.start()].rstrip(" ,;:{[(") + " …[payload removed]"
    return _SECRET.sub("<secret>", _EMAIL.sub("<email>", _UUID.sub("<id>", text)))


def exception_line(exc: str) -> str:
    """The `Type: message` line of a traceback (not SQLAlchemy's trailing 'Background' link)."""
    lines = _EXC_LINE.findall(str(exc))
    line = lines[-1] if lines else next((ln for ln in reversed(str(exc).strip().splitlines()) if ln.strip()), "")
    return redact(line.strip())[:300]


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
        for k in ("endpoint", "fallback_reason", "provider"):
            if k in entry:
                entry[k] = redact(str(entry[k]))[:120]
        if r.get("exc"):
            entry["exc"] = exception_line(r["exc"])
        out.append(entry)
    return out
