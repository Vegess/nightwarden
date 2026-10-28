"""Poll the target's /metrics once a minute, store it, and triage what fires.

Also notices, without any setup on the target's side:
- a deploy: the reported commit changed
- a restart: uptime dropped / start time changed with the same commit
- an outage: three polls in a row failed (about 3 minutes; a free-tier cold
  start takes about one)
After each healthy cycle it pings HEARTBEAT_URL, so if Nightwarden itself
stops, the heartbeat service raises the alarm. The first cycle after midnight
UTC also writes yesterday's daily_stats row and applies snapshot retention.

Database egress (Neon's free plan meters it): a poll reads one small
poller_state row instead of re-reading past snapshots, stores only the
/metrics keys something reads (KEEP), and selects columns, never whole rows.
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.evidence import LiveEvidence
from app.collector import detectors, signals, target
from app.core.config import get_settings
from app.db.models import Decision, Deploy, Event, Incident, PollerState, Snapshot
from app.db.session import SessionLocal
from app.services import notify, rollup, triage

logger = logging.getLogger("nightwarden.poller")
settings = get_settings()
WINDOW = timedelta(minutes=5, seconds=30)
BACKUP_CHECK_EVERY = timedelta(hours=12)  # health passes on a success within 24 h
BACKUP_RETRY_AFTER = timedelta(hours=1)   # after a failed check
SLOT_S = 600                 # typical traffic: one requests_5m value per 10-minute slot, 24 h of slots
TYPICAL_MIN_SLOTS = 30
# The /metrics keys signals.delta/window, the deploy check and /api/status read. Everything else is dropped.
KEEP = ("uptime_s", "started_at", "commit", "llm_model", "llm_key_loaded", "requests_total", "requests_by_status",
        "latency_p95_ms", "chat_requests_total", "empty_retrievals_total", "fallback_total", "fallback_5m",
        "fallback_rate_5m", "chunks_total", "documents_total")
DELTA_KEYS = ("interval_s", "requests", "chat", "fallback", "empty", "s5xx", "s4xx", "s429", "restarted")
_state = {"last_poll": None, "last_ok": None, "running": False}


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def trim(raw: dict) -> dict:
    out = {k: raw[k] for k in KEEP if k in raw}
    for k in ("documents_by_status", "jobs_by_status"):  # only the failed count is read
        if isinstance(raw.get(k), dict):
            out[k] = {"failed": raw[k].get("failed", 0)}
    return out


def _load_state(db: Session, service: str) -> PollerState:
    """The state row; on first use it is seeded once from the newest snapshots."""
    state = db.get(PollerState, service)
    if state is not None:
        return state
    state = PollerState(service=service, last_raw="{}", deltas="[]", traffic="[]", failures=0)
    last = db.execute(select(Snapshot.ts, Snapshot.raw).where(Snapshot.service == service, Snapshot.ok.is_(True))
                      .order_by(Snapshot.ts.desc()).limit(1)).first()
    if last:
        state.last_ok_ts, state.last_raw = last.ts, json.dumps(trim(json.loads(last.raw or "{}")))
        since = _utc(last.ts) - timedelta(hours=24)
        traffic = {}
        for ts, derived in db.execute(select(Snapshot.ts, Snapshot.derived).where(
                Snapshot.service == service, Snapshot.ok.is_(True), Snapshot.ts >= since)):
            v = json.loads(derived or "{}").get("window", {}).get("requests_5m")
            if v is not None:
                traffic[int(_utc(ts).timestamp()) // SLOT_S] = v
        state.traffic = json.dumps(sorted(traffic.items()))
    from app.services import rollup
    state.rolled_through = rollup.last_rolled(db)
    db.add(state)
    return state


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
    state = _load_state(db, service)

    try:
        raw = fetch()
    except Exception as exc:  # noqa: BLE001 — an unreachable target is data, not a crash
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
        db.add(Snapshot(service=service, ok=False, error=error, ts=now))
        state.failures, state.updated_at = state.failures + 1, now
        db.commit()
        if state.failures >= settings.down_after_polls:
            e = _fire(db, "target_down", f"{settings.down_after_polls} polls in a row failed: {error}", {}, now, triage_client)
            fired += [e] if e else []
        return fired

    raw = trim(raw)
    prev_raw = json.loads(state.last_raw) if state.last_ok_ts else None
    interval = (now - _utc(state.last_ok_ts)).total_seconds() if state.last_ok_ts else float(settings.poll_interval_s)
    d = {k: v for k, v in signals.delta(prev_raw, raw, interval).items() if k in DELTA_KEYS}

    cutoff = (now - WINDOW).timestamp()
    deltas = [[t, x] for t, x in json.loads(state.deltas) if t >= cutoff] + [[now.timestamp(), d]]
    slot = int(now.timestamp()) // SLOT_S
    traffic = [[sl, v] for sl, v in json.loads(state.traffic) if slot - 144 < sl < slot]
    history = [v for _, v in traffic]
    typical = signals.typical(history) if len(history) >= TYPICAL_MIN_SLOTS else None
    w = signals.window([x for _, x in deltas], raw, typical)
    db.add(Snapshot(service=service, ok=True, raw=json.dumps(raw), derived=json.dumps({"window": w}), ts=now))
    had_failures = state.failures > 0
    state.last_ok_ts, state.last_raw, state.failures, state.updated_at = now, json.dumps(raw), 0, now
    state.deltas = json.dumps(deltas)
    state.traffic = json.dumps(traffic + [[slot, w["requests_5m"]]])

    # Deploys and restarts.
    if prev_raw and raw.get("commit") and raw.get("commit") != prev_raw.get("commit") and raw.get("commit") != "unknown":
        db.add(Deploy(service=service, commit_sha=raw["commit"], note="new commit seen in /metrics", ts=now))
    elif d["restarted"] and prev_raw and float(prev_raw.get("uptime_s") or 0) > 600:
        e = _fire(db, "restart", f"restarted without a new deploy (was up {int(float(prev_raw['uptime_s']) // 60)} min)",
                  w, now, triage_client)
        fired += [e] if e else []

    # Back up after an outage.
    if had_failures:
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
    db.commit()
    if rollup.due(state.rolled_through, now):
        try:
            state.rolled_through = rollup.rollup(db, now)
            db.commit()
        except Exception:  # noqa: BLE001 — a failed rollup is retried on the next poll
            db.rollback()
            logger.exception("Daily rollup failed")
    check_backup(db, state, now)
    try:
        from app.services import reports
        reports.maybe_send_digest(db, state, now)
    except Exception:  # noqa: BLE001 — a digest must never break a poll
        db.rollback()
        logger.exception("Weekly digest failed")
    _state["last_ok"] = now.isoformat()
    if settings.heartbeat_url:
        try:
            httpx.get(settings.heartbeat_url, timeout=10)
        except httpx.HTTPError:
            logger.warning("Heartbeat ping failed")
    return fired


def check_backup(db: Session, state: PollerState, now: datetime) -> None:
    """The backup AI's 1-token check, twice a day, stored on the state row for /api/health/checks."""
    from app.agent import providers
    backup = providers.backup()
    if backup is None:
        return
    every = BACKUP_CHECK_EVERY if state.backup_error is None else BACKUP_RETRY_AFTER
    if state.backup_checked_at and now - _utc(state.backup_checked_at) < every:
        return
    ok, error = providers.ping(backup)
    state.backup_checked_at, state.backup_error = now, error
    if ok:
        state.backup_ok_at = now
    else:
        logger.warning("Backup AI check failed: %s", error)
    db.commit()


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
