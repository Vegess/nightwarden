"""Nightwarden's tables.

snapshots  one row per poll of the target's /metrics (raw JSON + derived rates)
events     a signal a detector fired; each one is triaged
deploys    target deploys (seen as a commit change, or posted by CI)
decisions  one row per triage run: the audit trail AND the evaluation table
incidents  what a human sees: opened by page/notify/watch decisions
alerts     one row per alert delivery attempt (webhook or email), written by notify.alert
daily_stats one row per UTC day: the rollup that outlives the 30-day snapshot retention
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    service: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    raw: Mapped[str] = mapped_column(Text, nullable=False, default="{}")       # /metrics as returned
    derived: Mapped[str] = mapped_column(Text, nullable=False, default="{}")   # rates since the previous snapshot


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    service: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    detail: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    metrics: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="live")  # live | eval | manual


class Deploy(Base):
    __tablename__ = "deploys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    service: Mapped[str] = mapped_column(String(40), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    service: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)        # page | notify | watch
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="open")  # open | resolved
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    incident_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    triager: Mapped[str] = mapped_column(String(12), nullable=False)          # agent | baseline | degraded
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)
    summary: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Evaluation only.
    scenario: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_action: Mapped[str | None] = mapped_column(String(10), nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)          # webhook | email
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_short: Mapped[str | None] = mapped_column(String(200), nullable=True)


class DailyStat(Base):
    __tablename__ = "daily_stats"

    date: Mapped[date] = mapped_column(Date, primary_key=True)                # UTC day
    polls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    polls_ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decisions_by_action: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    decisions_by_decided_by: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decision_ms_p50: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_ms_p95: Mapped[int | None] = mapped_column(Integer, nullable=True)
    incidents_opened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incidents_resolved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    emails_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    emails_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
