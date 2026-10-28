"""Period reports (week / month / year) from daily_stats, agent performance from decisions,
and the weekly Discord digest.

Reports read the daily rollup, so a year costs at most 366 small rows; only the
current day, which has no rollup row yet, is computed from the live tables.
"""
import csv
import io
import json
import statistics
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DailyStat, Decision, Event, PollerState
from app.services import notify
from app.services.rollup import compute_day

PERIODS = ("week", "month", "year")
PERFORMANCE_DAYS = (7, 30, 365)
DIGEST_AT = time(3, 30)   # Monday 03:30 UTC = 09:00 IST


# ---------- periods ----------

def period_bounds(period: str, offset: int, today: date) -> tuple[date, date]:
    """[start, end) of the period `offset` periods before the one holding `today`."""
    if period == "week":
        start = today - timedelta(days=today.weekday()) - timedelta(weeks=offset)
        return start, start + timedelta(days=7)
    if period == "month":
        y, m = today.year, today.month - offset
        while m < 1:
            y, m = y - 1, m + 12
        start = date(y, m, 1)
        return start, date(y + (m == 12), m % 12 + 1, 1)
    start = date(today.year - offset, 1, 1)
    return start, date(start.year + 1, 1, 1)


def period_label(period: str, start: date) -> str:
    if period == "week":
        return f"Week of {start:%b} {start.day}, {start.year}"
    return f"{start:%B %Y}" if period == "month" else str(start.year)


def _days(db: Session, start: date, end: date, today: date) -> list[DailyStat]:
    rows = db.scalars(select(DailyStat).where(DailyStat.date >= start, DailyStat.date < min(end, today))
                      .order_by(DailyStat.date)).all()
    if start <= today < end:
        rows = [*rows, compute_day(db, today)]  # today has no rollup row yet
    return list(rows)


def _sum_json(rows, attr: str) -> Counter:
    total: Counter = Counter()
    for r in rows:
        total.update(json.loads(getattr(r, attr) or "{}"))
    return total


def summarize(rows: list[DailyStat]) -> dict:
    polls, ok = sum(r.polls for r in rows), sum(r.polls_ok for r in rows)
    decisions = sum(sum(json.loads(r.decisions_by_action or "{}").values()) for r in rows)
    degraded = sum(r.decisions_degraded or 0 for r in rows)
    resolved = sum(r.incidents_resolved for r in rows)
    kinds = _sum_json(rows, "events_by_kind")
    return {
        "days": len(rows),
        "uptime": round(ok / polls, 5) if polls else None,
        "polls": polls,
        "incidents": sum(r.incidents_opened for r in rows),
        "incidents_by_severity": dict(_sum_json(rows, "incidents_by_severity")),
        "resolved": resolved,
        "mttr_s": round(sum(r.resolve_s_sum or 0 for r in rows) / resolved) if resolved else None,
        "pages": sum(r.pages for r in rows),
        "decisions": decisions,
        "degraded_pct": round(degraded / decisions, 4) if decisions else None,
        "tokens": sum(r.tokens_in + r.tokens_out for r in rows),
        "emails_sent": sum(r.emails_sent for r in rows),
        "emails_failed": sum(r.emails_failed for r in rows),
        "events": sum(r.events for r in rows),
        "top_kinds": [{"kind": k, "n": n} for k, n in kinds.most_common(5)],
    }


DELTA_KEYS = ("uptime", "polls", "incidents", "mttr_s", "pages", "degraded_pct", "tokens", "emails_sent", "events")


def report(db: Session, period: str, offset: int = 0, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.date()
    start, end = period_bounds(period, offset, today)
    rows = _days(db, start, end, today)
    cur = summarize(rows)
    p_start, p_end = period_bounds(period, offset + 1, today)
    prev = summarize(_days(db, p_start, p_end, today))
    delta = {k: (None if cur[k] is None or prev[k] is None else round(cur[k] - prev[k], 5)) for k in DELTA_KEYS}
    first = db.scalar(select(func.min(DailyStat.date)))
    return {
        "period": period, "offset": offset, "label": period_label(period, start),
        "start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat(),
        "partial": start <= today < end, "has_next": offset > 0,
        "has_prev": first is not None and first < start,
        "summary": cur, "previous": {"label": period_label(period, p_start), **prev}, "delta": delta,
        "daily": [_day_row(r) for r in rows],
    }


def _day_row(r: DailyStat) -> dict:
    decisions = sum(json.loads(r.decisions_by_action or "{}").values())
    return {"date": r.date.isoformat(), "polls": r.polls, "polls_ok": r.polls_ok,
            "uptime": round(r.polls_ok / r.polls, 5) if r.polls else None, "events": r.events,
            "incidents_opened": r.incidents_opened, "incidents_resolved": r.incidents_resolved, "pages": r.pages,
            "decisions": decisions, "degraded": r.decisions_degraded or 0, "tokens": r.tokens_in + r.tokens_out,
            "emails_sent": r.emails_sent, "emails_failed": r.emails_failed}


def to_csv(rep: dict) -> str:
    buf = io.StringIO()
    cols = ["date", "polls", "polls_ok", "uptime", "events", "incidents_opened", "incidents_resolved", "pages",
            "decisions", "degraded", "tokens", "emails_sent", "emails_failed"]
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    w.writerows(rep["daily"])
    return buf.getvalue()


# ---------- performance ----------

def _median(values):
    return statistics.median(values) if values else None


def _p95(values):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]


def _group(decided_by: str) -> str:
    return {"groq": "primary", "rules": "rules"}.get(decided_by, "backup")


def performance(db: Session, days: int, now: datetime | None = None) -> dict:
    from app.routes.api import _count, decided_by
    now = now or datetime.now(timezone.utc)
    tool_count = _count(Decision.tool_calls, '"tool": ') - _count(Decision.tool_calls, '"tool": "decide"')
    rows = db.execute(select(
        Decision.action, Decision.triager, Decision.provider, Decision.degraded, Decision.latency_ms,
        (Decision.tokens_in + Decision.tokens_out).label("tokens"), Decision.rules_action, Event.kind,
        Decision.summary.like("Triage inconclusive%").label("inconclusive"), tool_count.label("tools"))
        .join(Event, Event.id == Decision.event_id)
        .where(Event.source != "eval", Decision.ts >= now - timedelta(days=days))).all()
    n = len(rows)
    agent = [r for r in rows if r.triager == "agent"]
    compared = [r for r in agent if r.rules_action]
    disagree = [r for r in compared if r.rules_action != r.action]
    per_kind = Counter(r.kind for r in compared)
    top = Counter(r.kind for r in disagree).most_common(5)
    return {
        "days": days, "decisions": n,
        "by_action": dict(Counter(r.action for r in rows)),
        "decided_by": dict(Counter(_group(decided_by(r)) for r in rows)),
        "degraded_pct": round(sum(1 for r in rows if r.degraded) / n, 4) if n else None,
        "decision_ms": {"median": _median([r.latency_ms for r in rows]), "p95": _p95([r.latency_ms for r in rows])},
        "tool_calls_median": _median([int(r.tools or 0) for r in agent]),
        "mean_tokens": round(statistics.mean(r.tokens for r in agent)) if agent else None,
        "inconclusive_pct": round(sum(1 for r in agent if r.inconclusive) / len(agent), 4) if agent else None,
        "disagreement": {
            "compared": len(compared), "disagree": len(disagree),
            "pct": round(len(disagree) / len(compared), 4) if compared else None,
            "top_kinds": [{"kind": k, "n": c, "of": per_kind[k]} for k, c in top],
        },
    }


# ---------- weekly digest ----------

def _week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def digest_due(state: PollerState, now: datetime) -> bool:
    monday = now.date() - timedelta(days=now.weekday())
    return (now >= datetime.combine(monday, DIGEST_AT, tzinfo=timezone.utc)
            and state.digest_week != _week_id(now.date()))


def _fmt_delta(v, pct=False, unit=""):
    if v is None or v == 0:
        return ""
    arrow = "▲" if v > 0 else "▼"
    return f" ({arrow}{abs(v) * 100:.1f} pt)" if pct else f" ({arrow}{abs(v):,}{unit})"


def digest_text(rep: dict) -> str:
    s, d = rep["summary"], rep["delta"]
    sev = ", ".join(f"{n} {k}" for k, n in sorted(s["incidents_by_severity"].items())) or "none"
    up = "—" if s["uptime"] is None else f"{s['uptime'] * 100:.2f}%"
    mttr = "—" if s["mttr_s"] is None else f"{s['mttr_s'] / 60:.0f} min"
    deg = "—" if s["degraded_pct"] is None else f"{s['degraded_pct']:.0%}"
    top = ", ".join(f"{k['kind'].replace('_', ' ')} ×{k['n']}" for k in s["top_kinds"][:3]) or "none"
    return "\n".join([
        f"Uptime {up}{_fmt_delta(d['uptime'], pct=True)} over {s['polls']:,} polls",
        f"Incidents {s['incidents']}{_fmt_delta(d['incidents'])} ({sev}) · pages {s['pages']} · time to resolve {mttr}",
        f"Decisions {s['decisions']} · degraded {deg} · Groq tokens {s['tokens']:,}",
        f"Alert emails {s['emails_sent']}" + (f" ({s['emails_failed']} failed)" if s["emails_failed"] else ""),
        f"Top signals: {top}",
    ])


def maybe_send_digest(db: Session, state: PollerState, now: datetime) -> bool:
    """Once a week, on the first poll after Monday 03:30 UTC: last week's report to the webhook.
    The week is recorded on the state row before anything else, so it can never repeat."""
    if not digest_due(state, now):
        return False
    state.digest_week = _week_id(now.date())
    db.commit()
    if not get_settings().notify_webhook_url:
        return False
    rep = report(db, "week", 1, now)
    link = f"{get_settings().public_url.rstrip('/')}/reports?period=week&offset=1"
    notify.alert("info", f"Weekly report · {rep['label']}", digest_text(rep), link)
    return True
