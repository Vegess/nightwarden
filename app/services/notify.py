"""Alert sinks: a Discord or Slack incoming webhook, and email through Brevo.

page   -> webhook + email (wake someone)
notify -> webhook only (working hours)
watch  -> dashboard only
Every send is logged; a failed send is logged as an error and never raises.
"""
import logging
from email.utils import parseaddr

import httpx

from app.core.config import get_settings

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


def _webhook(text: str, level: str = "info", title: str = "", body_text: str = "", link: str | None = None) -> bool:
    url = settings.notify_webhook_url
    if not url:
        return False
    body = _discord_body(level, title, body_text, link) if "discord" in url else {"text": text[:3000]}
    try:
        httpx.post(url, json=body, timeout=10).raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.error("Webhook alert failed: %s", exc)
        return False


def _email(subject: str, text: str) -> bool:
    if not (settings.brevo_api_key and settings.alert_email):
        return False
    name, address = parseaddr(settings.mail_from)
    try:
        httpx.post("https://api.brevo.com/v3/smtp/email", timeout=15,
                   headers={"api-key": settings.brevo_api_key, "accept": "application/json"},
                   json={"sender": {"name": name or "Nightwarden", "email": address},
                         "to": [{"email": settings.alert_email}], "subject": subject, "textContent": text}
                   ).raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.error("Email alert failed: %s", exc)
        return False


def alert(level: str, title: str, body: str, link: str | None = None) -> dict:
    text = f"{ICON.get(level, '')} **{level.upper()}** · {title}\n{body}" + (f"\n{link}" if link else "")
    sent = {"webhook": False, "email": False}
    if level in ("page", "notify", "info"):
        sent["webhook"] = _webhook(text, level, title, body, link)
    if level == "page":
        sent["email"] = _email(f"[Nightwarden] PAGE: {title}", text.replace("**", ""))
    logger.warning("Alert %s: %s (webhook=%s email=%s)", level, title, sent["webhook"], sent["email"])
    return sent
