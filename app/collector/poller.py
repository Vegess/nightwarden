"""Poll the target's /metrics once a minute, store it, and triage what fires.

Also notices, without any setup on the target's side:
- a deploy: the reported commit changed
- a restart: uptime dropped / start time changed with the same commit
- an outage: three polls in a row failed (about 3 minutes; a free-tier cold
  start takes about one)
After each healthy cycle it pings HEARTBEAT_URL, so if Nightwarden itself
stops, the heartbeat service raises the alarm.
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agent.evidence import LiveEvidence
from app.collector import detectors, signals, target
from app.core.config import get_settings
from app.db.models import Decision, Deploy, Event, Incident, Snapshot
from app.db.session import SessionLocal
from app.services import notify, triage

logger = logging.getLogger("nightwarden.poller")
settings = get_settings()
WINDOW = timedelta(minutes=5, seconds=30)
_state = {"last_poll": None, "last_ok": None, "running": False}


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _recent_event(db: Session, kind: str, now: datetime) -> bool:
    return db.scalar(select(Event.id).where(
        Event.service == settings.target_name, Event.kind == kind, Event.source == "live",
        Event.ts >= now - timedelta(minutes=detectors.COOLDOWN_MIN))) is not None


def _fire(db: Session, kind: str, detail: str, metrics: dict, now: datetime, triage_client=None) -> Event | None:
    if _recent_event(db, kind, now):
        return None
    event = Event(service=settings.target_name, kind=kind, detail=detail[:300], metrics=json.dumps(metrics), ts=now)
    db.add(event)
    db.commit()
    evidence = LiveEvidence(db, settings.target_name, fetch_logs=_safe_fetch_logs, now=now)
    triage.run(db, event, evidence, client=triage_client)
    return event


def triage_pending(db: Session, now: datetime, triage_client=None, max_age_h: int = 3) -> int:
    """Triage live events that never got a decision (e.g. a serverless invocation cut off mid-triage)."""
    pending = db.scalars(
        select(Event).where(Event.source == "live", Event.ts >= now - timedelta(hours=max_age_h),
                            ~select(Decision.id).where(Decision.event_id == Event.id).exists())
        .order_by(Event.ts)).all()
    for event in pending:
        evidence = LiveEvidence(db, settings.target_name, fetch_logs=_safe_fetch_logs, now=now)
        triage.run(db, event, evidence, client=triage_client)
    return len(pending)


def _safe_fetch_logs(**kw):
    try:
        return target.fetch_logs(**kw)
    except httpx.HTTPError as exc:
        return [{"level": "INFO", "msg": f"(could not read the service's logs: {type(exc).__name__})"}]


def poll_once(db: Session, fetch=None, now: datetime | None = None, triage_client=None) -> list[Event]:
    now = now or datetime.now(timezone.utc)
    fetch = fetch or target.fetch_metrics
    service = settings.target_name
    _state["last_poll"] = now.isoformat()
    fired: list[Event] = []

    try:
        raw = fetch()
    except Exception as exc:  # noqa: BLE001 — an unreachable target is data, not a crash
        db.add(Snapshot(service=service, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}", ts=now))
        db.commit()
        recent = db.scalars(select(Snapshot).where(Snapshot.service == service).order_by(Snapshot.ts.desc())
                            .limit(settings.down_after_polls)).all()
        if len(recent) == settings.down_after_polls and not any(s.ok for s in recent):
            e = _fire(db, "target_down", f"{settings.down_after_polls} polls in a row failed: {recent[0].error}", {}, now, triage_client)
            fired += [e] if e else []
        return fired

    prev = db.scalar(select(Snapshot).where(Snapshot.service == service, Snapshot.ok.is_(True))
                     .order_by(Snapshot.ts.desc()))
    prev_raw = json.loads(prev.raw) if prev else None
    interval = (now - _utc(prev.ts)).total_seconds() if prev else float(settings.poll_interval_s)
    d = signals.delta(prev_raw, raw, interval)

    window_snaps = db.scalars(select(Snapshot).where(Snapshot.service == service, Snapshot.ok.is_(True),
                                                     Snapshot.ts >= now - WINDOW)).all()
    deltas = [json.loads(s.derived).get("delta") for s in window_snaps if json.loads(s.derived).get("delta")] + [d]
    day = db.scalars(select(Snapshot.derived).where(Snapshot.service == service, Snapshot.ok.is_(True),
                                                    Snapshot.ts >= now - timedelta(hours=24))).all()
    history = [json.loads(x).get("window", {}).get("requests_5m") for x in day]
    history = [h for h in history if h is not None]
    typical = signals.typical(history) if len(history) >= 30 else None
    w = signals.window(deltas, raw, typical)
    db.add(Snapshot(service=service, ok=True, raw=json.dumps(raw), derived=json.dumps({"delta": d, "window": w}), ts=now))

    # Deploys and restarts.
    if prev_raw and raw.get("commit") and raw.get("commit") != prev_raw.get("commit") and raw.get("commit") != "unknown":
        db.add(Deploy(service=service, commit_sha=raw["commit"], note="new commit seen in /metrics", ts=now))
    elif d["restarted"] and prev_raw and float(prev_raw.get("uptime_s") or 0) > 600:
        e = _fire(db, "restart", f"restarted without a new deploy (was up {int(float(prev_raw['uptime_s']) // 60)} min)",
                  w, now, triage_client)
        fired += [e] if e else []

    # Back up after an outage.
    for inc in db.scalars(select(Incident).where(Incident.service == service, Incident.kind == "target_down",
                                                 Incident.status == "open")).all():
        inc.status, inc.closed_at = "resolved", now
        notify.alert("info", f"{service} is reachable again", f"Recovered at {now:%H:%M UTC}.")
    db.commit()

    for sig in detectors.detect(w, previous_requests=typical if typical else None):
        e = _fire(db, sig.kind, sig.detail, w, now, triage_client)
        fired += [e] if e else []

    triage_pending(db, now, triage_client)
    triage.auto_resolve(db, now=now)
    db.execute(delete(Snapshot).where(Snapshot.ts < now - timedelta(days=7)).execution_options(synchronize_session=False))
    db.commit()
    _state["last_ok"] = now.isoformat()
    if settings.heartbeat_url:
        try:
            httpx.get(settings.heartbeat_url, timeout=10)
        except httpx.HTTPError:
            logger.warning("Heartbeat ping failed")
    return fired


def _loop(stop: threading.Event) -> None:
    _state["running"] = True
    logger.info("Poller started: %s every %ss", settings.target_url, settings.poll_interval_s)
    while not stop.is_set():
        started = time.monotonic()
        try:
            with SessionLocal() as db:
                poll_once(db)
        except Exception:  # noqa: BLE001 — the loop must survive anything and say so
            logger.exception("Poll cycle failed")
        stop.wait(max(5.0, settings.poll_interval_s - (time.monotonic() - started)))
    _state["running"] = False


def start() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), name="nightwarden-poller", daemon=True).start()
    return stop


def state() -> dict:
    return dict(_state)
