"""Where the agent's tools get their answers.

`Evidence` is the interface the tools call. `LiveEvidence` reads Nightwarden's
own tables and the target's log feed; the evaluation harness supplies a
fixture with the same interface, so the agent code is identical in both.
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Deploy, Incident, Snapshot

METRICS = ("error_rate", "rate_4xx", "rate_429", "latency_p95_ms", "fallback_rate", "requests_5m",
           "chat_5m", "empty_retrieval_rate", "traffic_ratio", "chunks_total", "documents_total", "documents_failed")


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Evidence(Protocol):
    now: datetime

    def recent_logs(self, minutes: int, level: str | None, contains: str | None) -> list[dict]: ...
    def metric_series(self, metric: str, minutes: int) -> list[dict]: ...
    def deploys(self, hours: int) -> list[dict]: ...
    def past_incidents(self, query: str, limit: int) -> list[dict]: ...


class LiveEvidence:
    def __init__(self, db: Session, service: str, fetch_logs=None, now: datetime | None = None):
        self.db = db
        self.service = service
        self.now = now or datetime.now(timezone.utc)
        self._fetch_logs = fetch_logs

    def recent_logs(self, minutes, level=None, contains=None):
        if self._fetch_logs is None:
            return []
        since = (self.now - timedelta(minutes=minutes)).isoformat()
        return self._fetch_logs(since=since, level=level, contains=contains, limit=40)

    def metric_series(self, metric, minutes):
        rows = self.db.scalars(
            select(Snapshot).where(Snapshot.service == self.service, Snapshot.ok.is_(True),
                                   Snapshot.ts >= self.now - timedelta(minutes=minutes)).order_by(Snapshot.ts)
        ).all()
        out = []
        for r in rows:
            w = json.loads(r.derived or "{}").get("window") or {}
            if metric in w:
                out.append({"minutes_ago": round((self.now - _utc(r.ts)).total_seconds() / 60, 1), "value": w[metric]})
        return out[-30:]  # enough to see a trend, small enough for the context

    def deploys(self, hours):
        rows = self.db.scalars(
            select(Deploy).where(Deploy.service == self.service, Deploy.ts >= self.now - timedelta(hours=hours))
            .order_by(Deploy.ts.desc())
        ).all()
        return [{"minutes_ago": int((self.now - _utc(d.ts)).total_seconds() // 60), "commit": (d.commit_sha or "")[:10],
                 "note": d.note} for d in rows]

    def past_incidents(self, query, limit):
        words = [w for w in query.lower().split() if len(w) > 2][:6]
        q = select(Incident).where(Incident.service == self.service).order_by(Incident.opened_at.desc())
        if words:
            q = q.where(or_(*[Incident.summary.ilike(f"%{w}%") for w in words], *[Incident.kind.ilike(f"%{w}%") for w in words]))
        return [{"days_ago": round((self.now - _utc(i.opened_at)).total_seconds() / 86400, 1), "kind": i.kind,
                 "severity": i.severity, "status": i.status, "summary": i.summary, "occurrences": i.occurrences}
                for i in self.db.scalars(q.limit(limit)).all()]
