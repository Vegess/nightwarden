"""The daily rollup: one daily_stats row per UTC day, then snapshot retention.

Runs from the poll on its first cycle after midnight UTC (the 00:00-00:10 window
holds exactly one cron poll at the 10-minute interval), so ordinary polls pay no
extra queries. Missing days are backfilled, a day already rolled up is left
alone, and a snapshot is deleted only once it is 30 days old AND its day has
been rolled up.
"""
import json
import logging
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import Alert, DailyStat, Decision, Event, Incident, Snapshot

logger = logging.getLogger("nightwarden.rollup")
RETENTION_DAYS = 30
BACKFILL_DAYS = 366       # bounds a first run on an old database
WINDOW = timedelta(minutes=10)
_done_through: dict = {"date": None}


def _bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def compute_day(db: Session, day: date) -> DailyStat:
    from app.routes.api import decided_by  # one definition of "decided by"
    start, end = _bounds(day)
    snaps = dict(db.execute(select(Snapshot.ok, func.count()).where(Snapshot.ts >= start, Snapshot.ts < end)
                            .group_by(Snapshot.ok)).all())
    events = db.scalar(select(func.count()).select_from(Event).where(
        Event.ts >= start, Event.ts < end, Event.source != "eval")) or 0
    decisions = db.scalars(select(Decision).join(Event, Event.id == Decision.event_id).where(
        Decision.ts >= start, Decision.ts < end, Event.source != "eval")).all()
    ms = [d.latency_ms for d in decisions]
    emails = dict(db.execute(select(Alert.ok, func.count()).where(
        Alert.ts >= start, Alert.ts < end, Alert.channel == "email").group_by(Alert.ok)).all())
    return DailyStat(
        date=day, polls=sum(snaps.values()), polls_ok=snaps.get(True, 0), events=events,
        decisions_by_action=json.dumps(dict(Counter(d.action for d in decisions))),
        decisions_by_decided_by=json.dumps(dict(Counter(decided_by(d) for d in decisions))),
        tokens_in=sum(d.tokens_in for d in decisions), tokens_out=sum(d.tokens_out for d in decisions),
        decision_ms_p50=_pct(ms, 0.5), decision_ms_p95=_pct(ms, 0.95),
        incidents_opened=db.scalar(select(func.count()).select_from(Incident).where(
            Incident.opened_at >= start, Incident.opened_at < end)) or 0,
        incidents_resolved=db.scalar(select(func.count()).select_from(Incident).where(
            Incident.closed_at >= start, Incident.closed_at < end)) or 0,
        pages=sum(1 for d in decisions if d.action == "page"),
        emails_sent=emails.get(True, 0), emails_failed=emails.get(False, 0),
    )


def _first_day(db: Session, yesterday: date) -> date | None:
    """The oldest day with data, at most BACKFILL_DAYS back."""
    firsts = [db.scalar(select(func.min(m.ts))) for m in (Snapshot, Decision)]
    firsts = [f.date() for f in firsts if f is not None]
    if not firsts:
        return None
    return max(min(firsts), yesterday - timedelta(days=BACKFILL_DAYS))


def rollup(db: Session, now: datetime | None = None) -> list[date]:
    """Roll up every missing day up to yesterday (UTC), then apply retention. Returns the days written."""
    now = now or datetime.now(timezone.utc)
    yesterday = now.date() - timedelta(days=1)
    first = _first_day(db, yesterday)
    written: list[date] = []
    if first is not None:
        have = set(db.scalars(select(DailyStat.date).where(DailyStat.date >= first)).all())
        day = first
        while day <= yesterday:
            if day not in have:
                db.add(compute_day(db, day))
                written.append(day)
            day += timedelta(days=1)
        db.commit()
    # Retention: only snapshots whose day has a rollup row (every day up to yesterday now does).
    rolled = db.scalar(select(func.max(DailyStat.date)))
    if rolled is not None:
        cutoff = min(now - timedelta(days=RETENTION_DAYS), _bounds(rolled)[1])
        db.execute(delete(Snapshot).where(Snapshot.ts < cutoff).execution_options(synchronize_session=False))
        db.commit()
    _done_through["date"] = yesterday
    if written:
        logger.info("Rolled up %d day(s): %s..%s", len(written), written[0], written[-1])
    return written


def due(now: datetime) -> bool:
    """First poll after midnight UTC, once per process; no database access."""
    midnight = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    return now - midnight < WINDOW and _done_through["date"] != now.date() - timedelta(days=1)
