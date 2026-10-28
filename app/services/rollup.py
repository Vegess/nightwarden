"""The daily rollup: one daily_stats row per UTC day, then snapshot retention.

Runs from the first poll that finds poller_state.rolled_through older than
yesterday (UTC). That check is in the database row every poll already reads, so
it survives serverless cold starts and costs no extra query. Missing days are
backfilled, a day already rolled up is left alone, and a snapshot is deleted only
once it is 30 days old AND its day has been rolled up.
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
VERSION = 2               # bump when compute_day gains fields; older rows are refreshed (see upgrade_rows)
BACKFILL_DAYS = 366       # bounds a first run on an old database


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
    decisions = db.execute(select(Decision.action, Decision.triager, Decision.provider, Decision.tokens_in, Decision.tokens_out,
                                  Decision.latency_ms, Decision.degraded).join(Event, Event.id == Decision.event_id).where(
        Decision.ts >= start, Decision.ts < end, Event.source != "eval")).all()
    kinds = dict(db.execute(select(Event.kind, func.count()).where(
        Event.ts >= start, Event.ts < end, Event.source != "eval").group_by(Event.kind)).all())
    severity = dict(db.execute(select(Incident.severity, func.count()).where(
        Incident.opened_at >= start, Incident.opened_at < end).group_by(Incident.severity)).all())
    resolved = db.execute(select(Incident.opened_at, Incident.closed_at).where(
        Incident.closed_at >= start, Incident.closed_at < end)).all()
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
        incidents_resolved=len(resolved),
        resolve_s_sum=int(sum((_utc(c) - _utc(o)).total_seconds() for o, c in resolved)),
        incidents_by_severity=json.dumps(severity), events_by_kind=json.dumps(kinds),
        decisions_degraded=sum(1 for d in decisions if d.degraded), version=VERSION,
        pages=sum(1 for d in decisions if d.action == "page"),
        emails_sent=emails.get(True, 0), emails_failed=emails.get(False, 0),
    )


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


REFRESHED = ("events", "decisions_by_action", "decisions_by_decided_by", "tokens_in", "tokens_out", "decision_ms_p50",
             "decision_ms_p95", "incidents_opened", "incidents_resolved", "pages", "emails_sent", "emails_failed",
             "incidents_by_severity", "events_by_kind", "resolve_s_sum", "decisions_degraded", "version")


def upgrade_rows(db: Session) -> int:
    """Refresh rows written by an older compute_day. Polls come from snapshots that may be gone, so they
    stay; everything else comes from tables kept forever."""
    old = db.scalars(select(DailyStat).where(DailyStat.version < VERSION)).all()
    for row in old:
        fresh = compute_day(db, row.date)
        for col in REFRESHED:
            setattr(row, col, getattr(fresh, col))
    db.commit()
    return len(old)


def _first_day(db: Session, yesterday: date) -> date | None:
    """The oldest day with data, at most BACKFILL_DAYS back."""
    firsts = [db.scalar(select(func.min(m.ts))) for m in (Snapshot, Decision)]
    firsts = [f.date() for f in firsts if f is not None]
    if not firsts:
        return None
    return max(min(firsts), yesterday - timedelta(days=BACKFILL_DAYS))


def last_rolled(db: Session) -> date | None:
    return db.scalar(select(func.max(DailyStat.date)))


def rollup(db: Session, now: datetime | None = None) -> date:
    """Roll up every missing day up to yesterday (UTC), then apply retention. Returns yesterday."""
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
    upgrade_rows(db)
    # Retention: only snapshots whose day has a rollup row (every day up to yesterday now does).
    rolled = last_rolled(db)
    if rolled is not None:
        cutoff = min(now - timedelta(days=RETENTION_DAYS), _bounds(rolled)[1])
        db.execute(delete(Snapshot).where(Snapshot.ts < cutoff).execution_options(synchronize_session=False))
        db.commit()
    if written:
        logger.info("Rolled up %d day(s): %s..%s", len(written), written[0], written[-1])
    return yesterday


def due(rolled_through: date | None, now: datetime) -> bool:
    return rolled_through is None or rolled_through < now.date() - timedelta(days=1)
