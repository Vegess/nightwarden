"""Alert sinks: a Discord or Slack incoming webhook, and email through Brevo.

page   -> webhook + email (wake someone)
notify -> webhook only (working hours)
watch  -> dashboard only
Every send is logged and recorded in the alerts table; a failed send is logged
as an error and never raises.
"""
import logging
from email.utils import parseaddr

import httpx

from app.core.config import get_settings
from app.db import session as db_session
from app.db.models import Alert

logger = logging.getLogger("nightwarden.notify")
settings = get_settings()
ICON = {"page": "🔴", "notify": "🟠", "watch": "🔵", "info": "ℹ️"}


COLOR = {"page": 0xEF6461, "notify": 0xE3A64A, "watch": 0x6AA6FF, "info": 0x3FB68B}


def _discord_body(level: str, title: str, body: str, link: str | None) -> dict:
    """A Discord embed: coloured by severity, title links to the incident on the dashboard."""
    embed = {"title": f"{ICON.get(level, '')} {level.upper()} · {title}"[:250], "description": body[:3500],
             "color": COLOR.get(level, 0x7A8696), "footer": {"text": "Nightwarden"}}
    if link:
        embed["url"] = link
    # Pages mention @here so they notify people with the channel muted for ordinary messages.
    return {"content": "@here" if level == "page" else None, "embeds": [embed],
            "allowed_mentions": {"parse": ["everyone"] if level == "page" else []}}


def _short(exc: httpx.HTTPError) -> str:
    """No URLs or response bodies: webhook URLs are secrets."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _webhook(text: str, level: str = "info", title: str = "", body_text: str = "",
             link: str | None = None) -> tuple[bool, str | None] | None:
    """None when no webhook is configured, else (ok, short error)."""
    url = settings.notify_webhook_url
    if not url:
        return None
    body = _discord_body(level, title, body_text, link) if "discord" in url else {"text": text[:3000]}
    try:
        httpx.post(url, json=body, timeout=10).raise_for_status()
        return True, None
    except httpx.HTTPError as exc:
        logger.error("Webhook alert failed: %s", exc)
        return False, _short(exc)


def email_configured() -> bool:
    return bool(settings.brevo_api_key and settings.alert_email)


def _email(subject: str, text: str) -> tuple[bool, str | None] | None:
    if not email_configured():
        return None
    name, address = parseaddr(settings.mail_from)
    try:
        httpx.post("https://api.brevo.com/v3/smtp/email", timeout=15,
                   headers={"api-key": settings.brevo_api_key, "accept": "application/json"},
                   json={"sender": {"name": name or "Nightwarden", "email": address},
                         "to": [{"email": settings.alert_email}], "subject": subject, "textContent": text}
                   ).raise_for_status()
        return True, None
    except httpx.HTTPError as exc:
        logger.error("Email alert failed: %s", exc)
        return False, _short(exc)


def _record(level: str, attempts: dict) -> None:
    """One alerts row per channel tried. Bookkeeping must never stop an alert, so errors are only logged."""
    if not attempts:
        return
    try:
        with db_session.SessionLocal() as db:
            db.add_all([Alert(level=level, channel=ch, ok=ok, error_short=(err or None) and err[:200])
                        for ch, (ok, err) in attempts.items()])
            db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Could not record alert delivery")


def alert(level: str, title: str, body: str, link: str | None = None) -> dict:
    text = f"{ICON.get(level, '')} **{level.upper()}** · {title}\n{body}" + (f"\n{link}" if link else "")
    attempts = {}
    if level in ("page", "notify", "info"):
        attempts["webhook"] = _webhook(text, level, title, body, link)
    if level == "page":
        attempts["email"] = _email(f"[Nightwarden] PAGE: {title}", text.replace("**", ""))
    attempts = {ch: r for ch, r in attempts.items() if r is not None}
    _record(level, attempts)
    sent = {"webhook": attempts.get("webhook", (False,))[0], "email": attempts.get("email", (False,))[0]}
    logger.warning("Alert %s: %s (webhook=%s email=%s)", level, title, sent["webhook"], sent["email"])
    return sent
