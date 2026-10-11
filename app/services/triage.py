"""Run triage for one event and act on the decision.

Normal path: the agent. If the model can't be reached (no key, provider
down, rate limited), the DEGRADED path runs instead: the rules baseline,
raised to at least `notify` (and to `page` for the failure classes that are
invisible to rules), recorded with degraded=True and announced. Citebase
and Nightwarden share an AI provider; when it fails, that is exactly when a
watcher must not go quiet.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.baseline import rules_triage
from app.agent.evidence import Evidence
from app.agent.orchestrator import AgentUnavailable, TriageResult, triage as agent_triage
from app.core.config import get_settings
from app.db.models import Decision, Event, Incident
from app.services import notify

logger = logging.getLogger("nightwarden.triage")
settings = get_settings()
RANK = {"suppress": 0, "watch": 1, "notify": 2, "page": 3}
LOUD_FLOOR = {"fallback_spike": "page", "zero_chunks": "page", "target_down": "page"}
_last_degraded_notice: datetime | None = None


def degraded_triage(event: dict, reason: str) -> TriageResult:
    rules = rules_triage(event)
    floor = LOUD_FLOOR.get(event["kind"], "notify")
    action = rules if RANK[rules] >= RANK[floor] else floor
    return TriageResult(
        action=action, confidence="low",
        summary=f"{event['kind'].replace('_', ' ')}: {event.get('detail', '')}"[:300],
        rationale=(f"AI triage unavailable ({reason}). Rules said '{rules}'; raised to at least '{floor}' "
                   "because without the agent this signal can't be investigated. Check it by hand."),
    )


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _incident_for(db: Session, event: Event, result: TriageResult) -> tuple[Incident | None, bool]:
    """Open or update an incident. Returns (incident, should_alert)."""
    if result.action == "suppress":
        return None, False
    now = _utc(event.ts) if event.ts else datetime.now(timezone.utc)
    open_inc = db.scalar(
        select(Incident).where(Incident.service == event.service, Incident.kind == event.kind,
                               Incident.status == "open", Incident.updated_at >= now - timedelta(hours=6))
        .order_by(Incident.updated_at.desc())
    )
    if open_inc is None:
        inc = Incident(service=event.service, kind=event.kind, severity=result.action, summary=result.summary[:300],
                       opened_at=now, updated_at=now)
        db.add(inc)
        db.flush()
        return inc, True
    escalated = RANK[result.action] > RANK[open_inc.severity]
    open_inc.occurrences += 1
    open_inc.updated_at = now
    if escalated:
        open_inc.severity = result.action
        open_inc.summary = result.summary[:300]
    return open_inc, escalated


def run(db: Session, event: Event, evidence: Evidence, client=None) -> Decision:
    global _last_degraded_notice
    payload = {"kind": event.kind, "service": event.service, "detail": event.detail,
               "metrics": json.loads(event.metrics or "{}")}
    degraded = False
    try:
        result = agent_triage(payload, evidence, client=client)
        triager = "agent"
    except AgentUnavailable as exc:
        result = degraded_triage(payload, str(exc))
        triager, degraded = "degraded", True
        now = datetime.now(timezone.utc)
        if _last_degraded_notice is None or now - _last_degraded_notice > timedelta(hours=1):
            _last_degraded_notice = now
            notify.alert("notify", "Nightwarden is triaging without AI",
                         f"The triage model is unavailable ({str(exc)[:160]}). Signals are handled by rules, "
                         "raised to notify/page, until it recovers.")

    incident, should_alert = _incident_for(db, event, result)
    decision = Decision(
        event_id=event.id, incident_id=incident.id if incident else None, triager=triager,
        action=result.action, confidence=result.confidence, summary=result.summary, rationale=result.rationale,
        tool_calls=json.dumps(result.tool_calls, default=str), rounds=result.rounds,
        tokens_in=result.tokens_in, tokens_out=result.tokens_out, latency_ms=result.latency_ms, degraded=degraded,
    )
    db.add(decision)
    db.commit()
    logger.info("Triage %s -> %s", event.kind, result.action,
                extra={"event_id": event.id, "decision_id": decision.id, "action": result.action,
                       "degraded": degraded, "rounds": result.rounds, "latency_ms": result.latency_ms})
    if should_alert and result.action in ("page", "notify"):
        link = f"{settings.public_url.rstrip('/')}/#incidents/{incident.id}"
        notify.alert(result.action, f"{event.service}: {result.summary}"[:180],
                     (result.rationale[:700] + ("\n(degraded: decided without AI)" if degraded else "")), link)
    return decision


def auto_resolve(db: Session, quiet_hours: int = 3, now: datetime | None = None) -> int:
    """Close incidents that haven't recurred for a while."""
    now = now or datetime.now(timezone.utc)
    stale = db.scalars(select(Incident).where(Incident.status == "open",
                                              Incident.updated_at < now - timedelta(hours=quiet_hours))).all()
    for inc in stale:
        inc.status, inc.closed_at = "resolved", now
    db.commit()
    return len(stale)
