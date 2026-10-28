"""Self-checks and free-tier usage for the /health and /usage pages.

Both read only what Nightwarden already stores (no extra polling of the
target, no Groq calls) and are cached: checks 60 s, usage 5 min, Neon's API 1 h.
"""
import math
import os
import time
from datetime import datetime, time as dtime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Alert, DailyStat, Decision, Event, PollerState, Snapshot
from app.db.session import get_db
from app.services import notify

router = APIRouter(prefix="/api")
settings = get_settings()
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
POLLS_PER_DAY = 144      # one every 10 minutes
EMAILS_PER_DAY = 300     # Brevo free plan
_cache: dict[str, tuple[float, dict]] = {}
RANK = {"pass": 0, "warn": 1, "fail": 2}


def _cached(key: str, ttl: float, build):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = build()
    _cache[key] = (time.monotonic(), value)
    return value


def _utc(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _day_start(now: datetime) -> datetime:
    return datetime.combine(now.date(), dtime.min, tzinfo=timezone.utc)


def _tokens_since(db: Session, since: datetime) -> dict:
    rows = db.execute(select(Event.source == "eval", func.sum(Decision.tokens_in + Decision.tokens_out))
                      .join(Event, Event.id == Decision.event_id).where(Decision.ts >= since)
                      .group_by(Event.source == "eval")).all()
    by = {bool(is_eval): int(n or 0) for is_eval, n in rows}
    return {"live": by.get(False, 0), "eval": by.get(True, 0), "total": by.get(False, 0) + by.get(True, 0)}


def _check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def _alembic_check(db: Session) -> dict:
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    try:
        heads = set(ScriptDirectory.from_config(Config(os.path.join(ROOT, "alembic.ini"))).get_heads())
        current = set(MigrationContext.configure(db.connection()).get_current_heads())
    except Exception as exc:  # noqa: BLE001
        return _check("Migrations at head", "fail", f"could not read the migration state ({type(exc).__name__})")
    if current == heads:
        return _check("Migrations at head", "pass", ", ".join(sorted(heads)))
    return _check("Migrations at head", "fail",
                  f"database at {', '.join(sorted(current)) or 'nothing (not stamped)'}; code expects "
                  f"{', '.join(sorted(heads))}. Run alembic upgrade head.")


def _backup_check(db: Session, now: datetime) -> dict:
    """Pass only when the key is set AND the poll's 1-token check succeeded in the last 24 h."""
    name = "Backup AI provider"
    if not settings.backup_llm_api_key:
        return _check(name, "warn", "BACKUP_LLM_API_KEY not set; rules take over if Groq fails")
    which = settings.backup_llm_provider
    row = db.execute(select(PollerState.backup_checked_at, PollerState.backup_ok_at, PollerState.backup_error)
                     .where(PollerState.service == settings.target_name)).first()
    if row is None or row.backup_checked_at is None:
        return _check(name, "warn", f"{which}: key set, not tested yet (the poll tests it every 12 h)")
    if row.backup_ok_at and now - _utc(row.backup_ok_at) < timedelta(hours=24):
        return _check(name, "pass", f"{which}: test call succeeded {_ago(now, row.backup_ok_at)}")
    return _check(name, "fail", f"{which}: last test call failed ({row.backup_error or 'unknown error'})"
                  + (f"; last success {_ago(now, row.backup_ok_at)}" if row.backup_ok_at else "; never succeeded"))


def _ago(now: datetime, ts: datetime) -> str:
    h = (now - _utc(ts)).total_seconds() / 3600
    return f"{h * 60:.0f} min ago" if h < 1 else f"{h:.0f} h ago"


def build_checks(db: Session, request_host: str | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = []

    started = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        ms = round((time.perf_counter() - started) * 1000)
        out.append(_check("Database round trip", "pass" if ms < 500 else "warn", f"{ms} ms"))
    except Exception as exc:  # noqa: BLE001
        out.append(_check("Database round trip", "fail", f"database unreachable ({type(exc).__name__})"))
        return {"overall": "fail", "checked_at": now.isoformat(), "checks": out}

    latest = db.execute(select(Snapshot.ts, Snapshot.ok, Snapshot.error).where(Snapshot.service == settings.target_name)
                        .order_by(Snapshot.ts.desc()).limit(1)).first()
    if latest is None:
        out.append(_check("Poll freshness", "fail", "no poll recorded yet"))
        out.append(_check("Target reachable", "warn", "no poll recorded yet"))
    else:
        age = (now - _utc(latest.ts)).total_seconds() / 60
        out.append(_check("Poll freshness", "pass" if age < 15 else "warn" if age < 30 else "fail",
                          f"last poll {age:.0f} min ago (pass under 15, warn under 30)"))
        out.append(_check("Target reachable", "pass" if latest.ok else "fail",
                          f"{settings.target_name} answered the last poll" if latest.ok
                          else f"last poll failed: {latest.error or 'unknown error'}"))

    if not settings.groq_api_key:
        out.append(_check("Groq usable", "fail", "GROQ_API_KEY not set: triage is rules-only"))
    else:
        last = db.execute(select(Decision.degraded).join(Event, Event.id == Decision.event_id)
                          .where(Event.source != "eval", Decision.ts >= now - timedelta(hours=24))
                          .order_by(Decision.ts.desc()).limit(1)).first()
        if last is None:
            out.append(_check("Groq usable", "pass", "key set; no live triage in the last 24 h"))
        elif last.degraded:
            out.append(_check("Groq usable", "fail", "the last live triage fell back to rules (degraded)"))
        else:
            out.append(_check("Groq usable", "pass", "the last live triage used the AI agent"))

    def configured(name, value, why):
        out.append(_check(name, "pass" if value else "warn", "configured" if value else why))
    out.append(_backup_check(db, now))
    configured("Webhook alerts", settings.notify_webhook_url, "NOTIFY_WEBHOOK_URL not set: pages reach no channel")
    configured("Email alerts", notify.email_configured(), "BREVO_API_KEY or ALERT_EMAIL not set: pages are not emailed")
    configured("Heartbeat", settings.heartbeat_url, "HEARTBEAT_URL not set: nobody notices if polling stops")

    out.append(_alembic_check(db))

    public = urlparse(settings.public_url).hostname
    out.append(_check("Public URL matches", "pass" if public and public == request_host else "warn",
                      f"PUBLIC_URL host {public or '(unset)'}; this request came to {request_host or '(unknown)'}"
                      + ("" if public == request_host else ". Alert links may point elsewhere.")))

    used = _tokens_since(db, _day_start(now))["total"]
    limit = settings.groq_daily_token_limit
    share = used / limit if limit else 1.0
    out.append(_check("Groq tokens today", "pass" if share < 0.8 else "warn" if share < 1 else "fail",
                      f"{used:,} of {limit:,} ({share:.0%}); warn at 80 %"))

    neon = neon_usage()
    if not neon["configured"]:
        out.append(_check("Neon network transfer", "warn", neon["detail"]))
    elif neon.get("error"):
        out.append(_check("Neon network transfer", "warn", neon["error"]))
    else:
        t = neon["transfer"]
        out.append(_check("Neon network transfer", transfer_status(t["share"]),
                          f"{t['gb']:.2f} of {t['limit_gb']:g} GB this month ({t['share']:.0%}); warn at 70 %, fail at 90 %"))

    overall = max((c["status"] for c in out), key=RANK.__getitem__)
    return {"overall": overall, "checked_at": now.isoformat(), "checks": out}


@router.get("/health/checks")
def health_checks(request: Request, db: Session = Depends(get_db)):
    host = request.url.hostname
    return _cached(f"checks:{host}", 60, lambda: build_checks(db, host))


def transfer_status(share: float) -> str:
    return "fail" if share >= 0.9 else "warn" if share >= 0.7 else "pass"


def _neon_fetch() -> dict:
    if not (settings.neon_api_key and settings.neon_project_id):
        return {"configured": False, "detail": "not configured (set NEON_API_KEY and NEON_PROJECT_ID)"}
    try:
        r = httpx.get(f"https://console.neon.tech/api/v2/projects/{settings.neon_project_id}", timeout=10,
                      headers={"Authorization": f"Bearer {settings.neon_api_key}", "Accept": "application/json"})
        r.raise_for_status()
        p = r.json().get("project", {})
    except (httpx.HTTPError, ValueError) as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        return {"configured": True, "error": f"Neon API failed ({status})", "limit": settings.neon_cu_hours_limit}
    limit_gb = settings.neon_transfer_limit_gb
    gb = (p.get("data_transfer_bytes") or 0) / 1e9
    return {"configured": True, "cu_hours": round((p.get("compute_time_seconds") or 0) / 3600, 2),
            "limit": settings.neon_cu_hours_limit, "period_start": p.get("consumption_period_start"),
            "transfer": {"gb": round(gb, 3), "limit_gb": limit_gb, "share": round(gb / limit_gb, 4) if limit_gb else 1.0}}


def neon_usage() -> dict:
    """Neon's consumption for the billing month; one API call an hour at most (both pages share it)."""
    return _cached("neon", 3600, _neon_fetch)


def _iso(dt):
    return _utc(dt).isoformat() if dt else None


def poll_counts(db: Session, now: datetime) -> dict:
    """Polls today and month to date, and how many were expected.

    Month to date used to count snapshots, which a 7-day retention had already deleted, so it showed
    7 x 144 = 1,008 whatever the date. Finished days now come from daily_stats (kept forever) and only
    today from snapshots. Expected polls run from the 1st of the month, or from the first poll ever
    recorded if that is later.
    """
    today = _day_start(now)
    month = today.replace(day=1)
    count_today = db.scalar(select(func.count()).select_from(Snapshot).where(
        Snapshot.service == settings.target_name, Snapshot.ts >= today)) or 0
    finished = db.scalar(select(func.sum(DailyStat.polls)).where(
        DailyStat.date >= month.date(), DailyStat.date < today.date())) or 0
    firsts = [db.scalar(select(func.min(DailyStat.date)).where(DailyStat.polls > 0)),
              db.scalar(select(func.min(Snapshot.ts)).where(Snapshot.service == settings.target_name))]
    starts = [datetime.combine(f, dtime.min, tzinfo=timezone.utc) if not isinstance(f, datetime) else _utc(f)
              for f in firsts if f is not None]
    first = min(starts) if starts else None
    since = max(month, first) if first else None
    expected = int((now - since).total_seconds() // 600) + 1 if since else 0
    return {"today": count_today, "month": int(finished) + count_today, "month_limit": expected,
            "expected_since": _iso(since)}


def build_usage(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = _day_start(now)
    month = today.replace(day=1)
    days = now.day
    limit = settings.groq_daily_token_limit

    triages = db.scalars(select(Decision.tokens_in + Decision.tokens_out)
                         .where(Decision.triager == "agent", Decision.tokens_in + Decision.tokens_out > 0)
                         .order_by(Decision.ts.desc()).limit(100)).all()
    mean = round(sum(triages) / len(triages)) if triages else None
    used_today = _tokens_since(db, today)
    left = max(0, limit - used_today["total"])

    polls = poll_counts(db, now)

    def emails(since):
        return dict(db.execute(select(Alert.ok, func.count()).where(Alert.channel == "email", Alert.ts >= since)
                               .group_by(Alert.ok)).all())
    e_today, e_month = emails(today), emails(month)
    return {
        "generated_at": now.isoformat(),
        "groq": {
            "limit_per_day": limit,
            "today": used_today, "month": _tokens_since(db, month),
            "mean_tokens_per_triage": mean, "triage_sample": len(triages),
            "triages_left_today": math.floor(left / mean) if mean else None,
        },
        "polls": {"limit_per_day": POLLS_PER_DAY, **polls},
        "emails": {"limit_per_day": EMAILS_PER_DAY,
                   "tracking_since": _iso(db.scalar(select(func.min(Alert.ts)))),
                   "today": e_today.get(True, 0), "today_failed": e_today.get(False, 0),
                   "month": e_month.get(True, 0), "month_failed": e_month.get(False, 0),
                   "month_limit": EMAILS_PER_DAY * days},
        "neon": neon_usage(),
    }


@router.get("/usage")
def usage(db: Session = Depends(get_db)):
    return _cached("usage", 300, lambda: build_usage(db))
