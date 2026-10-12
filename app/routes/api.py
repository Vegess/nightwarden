"""Read-only JSON for the dashboard, plus a few admin actions (Bearer ADMIN_TOKEN).

The dashboard is public by design: it shows the watched service's health and
Nightwarden's reasoning. Nothing here exposes credentials, and log lines are
redacted (ids, emails) before they are stored.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from secrets import compare_digest
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.evidence import LiveEvidence
from app.collector import poller
from app.core.config import get_settings
from app.db.models import Decision, Deploy, Event, Incident, Snapshot
from app.db.session import get_db
from app.services import triage

router = APIRouter(prefix="/api")
settings = get_settings()
EVAL_RESULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "eval", "results.json")


def require_admin(authorization: str | None = Header(default=None)) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not compare_digest(token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def is_admin(authorization: str | None = Header(default=None)) -> bool:
    """Optional admin view: the same pages, with raw evidence (tool results) included."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    return bool(settings.admin_token and token and compare_digest(token, settings.admin_token))


def _public_calls(calls: list[dict], full: bool) -> list[dict]:
    # Tool names, arguments and the size of each result are public; the result itself (log lines,
    # metric points) is admin-only. Logs are redacted at collection, this is a second layer.
    if full:
        return calls
    return [{**{k: v for k, v in c.items() if k != "result_preview"},
             **({"result_hidden": True} if "result_preview" in c else {})} for c in calls]


def _iso(dt):
    return (dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat() if dt else None


def _decision_out(d: Decision, event: Event | None = None, full: bool = False) -> dict:
    return {"id": d.id, "ts": _iso(d.ts), "triager": d.triager, "action": d.action, "confidence": d.confidence,
            "summary": d.summary, "rationale": d.rationale, "tool_calls": _public_calls(json.loads(d.tool_calls or "[]"), full),
            "rounds": d.rounds, "latency_ms": d.latency_ms, "tokens": d.tokens_in + d.tokens_out,
            "degraded": d.degraded, "incident_id": d.incident_id,
            **({"event": {"kind": event.kind, "detail": event.detail, "metrics": json.loads(event.metrics or "{}")}}
               if event else {})}


def _incident_out(i: Incident) -> dict:
    return {"id": i.id, "opened_at": _iso(i.opened_at), "updated_at": _iso(i.updated_at), "closed_at": _iso(i.closed_at),
            "kind": i.kind, "severity": i.severity, "status": i.status, "summary": i.summary,
            "occurrences": i.occurrences}


@router.get("/status")
def status(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    snaps = db.scalars(select(Snapshot).where(Snapshot.service == settings.target_name,
                                              Snapshot.ts >= now - timedelta(minutes=90)).order_by(Snapshot.ts)).all()
    latest = snaps[-1] if snaps else None
    last_ok = next((s for s in reversed(snaps) if s.ok), None)
    series = [{"ts": _iso(s.ts), **{k: json.loads(s.derived).get("window", {}).get(k)
                                    for k in ("requests_5m", "error_rate", "fallback_rate", "latency_p95_ms")}}
              for s in snaps if s.ok]
    last_decision = db.scalar(select(Decision).join(Event, Event.id == Decision.event_id)
                              .where(Event.source == "live").order_by(Decision.ts.desc()))
    raw = json.loads(last_ok.raw) if last_ok else {}
    return {
        "service": settings.target_name,
        "target_host": urlparse(settings.target_url).hostname,
        "reachable": bool(latest and latest.ok),
        "last_poll": _iso(latest.ts) if latest else None,
        "last_error": latest.error if latest and not latest.ok else None,
        "poller": poller.state(),
        "ai_triage": "ready" if settings.groq_api_key else "off (no key): rules only",
        "last_triage_degraded": bool(last_decision and last_decision.degraded),
        "window": json.loads(last_ok.derived).get("window") if last_ok else None,
        "commit": raw.get("commit"), "llm_model": raw.get("llm_model"),
        "uptime_s": raw.get("uptime_s"),
        "open_incidents": db.scalar(select(func.count()).select_from(Incident).where(Incident.status == "open")) or 0,
        "series": series[-90:],
        "deploys": [{"ts": _iso(d.ts), "commit": (d.commit_sha or "")[:10], "note": d.note}
                    for d in db.scalars(select(Deploy).order_by(Deploy.ts.desc()).limit(5))],
    }


@router.get("/incidents")
def incidents(status: str | None = None, limit: int = 50, db: Session = Depends(get_db)):
    q = select(Incident).order_by(Incident.opened_at.desc()).limit(max(1, min(limit, 200)))
    if status:
        q = q.where(Incident.status == status)
    return [_incident_out(i) for i in db.scalars(q)]


@router.get("/incidents/{incident_id}")
def incident(incident_id: str, db: Session = Depends(get_db), full: bool = Depends(is_admin)):
    inc = db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    rows = db.execute(select(Decision, Event).join(Event, Event.id == Decision.event_id)
                      .where(Decision.incident_id == inc.id).order_by(Decision.ts)).all()
    return {**_incident_out(inc), "decisions": [_decision_out(d, e, full) for d, e in rows]}


@router.get("/decisions")
def decisions(limit: int = 30, db: Session = Depends(get_db), full: bool = Depends(is_admin)):
    rows = db.execute(select(Decision, Event).join(Event, Event.id == Decision.event_id)
                      .where(Event.source != "eval").order_by(Decision.ts.desc()).limit(max(1, min(limit, 100)))).all()
    return [_decision_out(d, e, full) for d, e in rows]


@router.get("/eval")
def eval_results():
    if not os.path.exists(EVAL_RESULTS):
        return {"available": False}
    from eval.report import summarize
    with open(EVAL_RESULTS, encoding="utf-8") as fh:
        data = json.load(fh)
    return {"available": True, "generated_at": data.get("generated_at"), "model": data.get("model"),
            "summary": summarize(data)}


class DeployIn(BaseModel):
    commit: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=300)


@router.post("/deploys", status_code=201, dependencies=[Depends(require_admin)])
def record_deploy(body: DeployIn, db: Session = Depends(get_db)):
    d = Deploy(service=settings.target_name, commit_sha=body.commit, note=body.note or "posted by CI")
    db.add(d)
    db.commit()
    return {"id": d.id}


class EventIn(BaseModel):
    kind: str = Field(max_length=40)
    detail: str = Field(default="", max_length=300)
    metrics: dict = {}


@router.post("/events", status_code=201, dependencies=[Depends(require_admin)])
def manual_event(body: EventIn, db: Session = Depends(get_db)):
    """Fire an event by hand (to test alerts end to end)."""
    event = Event(service=settings.target_name, kind=body.kind, detail=body.detail, metrics=json.dumps(body.metrics),
                  source="manual")
    db.add(event)
    db.commit()
    d = triage.run(db, event, LiveEvidence(db, settings.target_name, fetch_logs=poller._safe_fetch_logs))
    return _decision_out(d, event, full=True)


@router.post("/incidents/{incident_id}/resolve", dependencies=[Depends(require_admin)])
def resolve(incident_id: str, db: Session = Depends(get_db)):
    inc = db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    inc.status, inc.closed_at = "resolved", datetime.now(timezone.utc)
    db.commit()
    return _incident_out(inc)
