"""Measure database egress: what Nightwarden reads back from its database.

    python -m scripts.measure_egress            # synthetic 24 h of data in a temp SQLite file
    python -m scripts.measure_egress --sizes    # only row sizes, against DATABASE_URL (e.g. Neon)

The synthetic run counts the bytes of every value the app fetches during one poll and one
dashboard refresh (Live page: /api/status, /api/stats, /api/decisions). Postgres wire overhead
comes on top, so treat the numbers as a lower bound and the ratio between versions as the result.
"""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

FETCHED = {"bytes": 0}


def _size(row) -> int:
    return sum(len(v) if isinstance(v, (str, bytes)) else 8 for v in row if v is not None)


class _Cursor(sqlite3.Cursor):
    def fetchone(self):
        row = super().fetchone()
        FETCHED["bytes"] += _size(row) if row else 0
        return row

    def fetchmany(self, *a):
        rows = super().fetchmany(*a)
        FETCHED["bytes"] += sum(map(_size, rows))
        return rows

    def fetchall(self):
        rows = super().fetchall()
        FETCHED["bytes"] += sum(map(_size, rows))
        return rows


class _Conn(sqlite3.Connection):
    def cursor(self, factory=_Cursor):
        return super().cursor(factory)


def sizes(url: str) -> None:
    from sqlalchemy import create_engine, func, select
    from app.db.models import Snapshot
    with create_engine(url).connect() as c:
        for col in (Snapshot.raw, Snapshot.derived):
            n, avg, mx = c.execute(select(func.count(), func.avg(func.length(col)), func.max(func.length(col)))).one()
            print(f"snapshots.{col.key}: rows={n} avg={float(avg or 0):.0f} B max={mx or 0} B")


RAW = {"uptime_s": 86400, "requests_total": 15234, "latency_p50_ms": 180, "latency_p95_ms": 2400,
       "requests_by_status": {"200": 14000, "201": 300, "204": 50, "304": 200, "400": 40, "401": 120, "403": 10,
                              "404": 300, "409": 4, "422": 60, "429": 30, "500": 3, "502": 1},
       "chat_requests_total": 900, "chat_requests_5m": 2, "empty_retrievals_total": 12, "fallback_total": 5,
       "fallback_reasons": {"groq_429": 3, "groq_timeout": 2}, "fallback_5m": 0, "fallback_rate_5m": 0.0,
       "started_at": "2026-10-27T05:12:33.123456+00:00", "commit": "4ccf4686be47fd2e881f39d3c92357d71fccf97b",
       "llm_provider": "groq", "llm_model": "openai/gpt-oss-120b", "llm_key_loaded": True, "documents_total": 42,
       "documents_by_status": {"ready": 40, "failed": 1, "processing": 1}, "chunks_total": 3100,
       "jobs_by_status": {"done": 60, "failed": 2}, "questions_today": 17, "db_stats_age_s": 1834}


def synthetic() -> None:
    tmp = tempfile.mkdtemp()
    url = f"sqlite:///{tmp}/egress.db"
    os.environ["DATABASE_URL"] = url
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import app.db.session as session_mod
    from app.collector import poller
    from app.core.config import get_settings
    from app.db.base import Base
    from app.db.models import Decision, Event
    from app.services import notify

    get_settings().poller_enabled = False
    notify.alert = lambda *a, **k: {}
    engine = create_engine(url, creator=lambda: sqlite3.connect(f"{tmp}/egress.db", factory=_Conn,
                                                                check_same_thread=False))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session_mod.SessionLocal = Session
    now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
    with Session() as db:
        for i in range(144, 0, -1):  # 24 h of 10-minute polls
            raw = {**RAW, "requests_total": RAW["requests_total"] + (144 - i) * 50, "uptime_s": RAW["uptime_s"] + (144 - i) * 600}
            poller.poll_once(db, fetch=lambda raw=raw: raw, now=now - timedelta(minutes=10 * i))
        calls = [{"round": r, "tool": "get_recent_logs", "args": {"minutes": 30}, "result_summary": "12 lines",
                  "result_preview": "x" * 1500, "duration_ms": 300} for r in range(4)]
        for i in range(10):
            ev = Event(service="citebase", kind="error_spike", detail="5% of 200 requests failed", source="live",
                       metrics=json.dumps({"error_rate": 0.05}), ts=now - timedelta(hours=i))
            db.add(ev)
            db.flush()
            db.add(Decision(event_id=ev.id, triager="agent", action="notify", summary="s" * 120, rationale="r" * 600,
                            tool_calls=json.dumps(calls), ts=ev.ts))
        db.commit()

        FETCHED["bytes"] = 0
        poller.poll_once(db, fetch=lambda: {**RAW, "requests_total": 30000, "uptime_s": 200000}, now=now)
        poll = FETCHED["bytes"]

        from app.main import app
        from app.db.session import get_db
        app.dependency_overrides[get_db] = lambda: (yield db)
        c = TestClient(app)
        FETCHED["bytes"] = 0
        for path in ("/api/status", "/api/stats", "/api/decisions?limit=6&brief=1"):
            c.get(path)
        page = FETCHED["bytes"]
    print(f"one poll: {poll:,} B fetched -> {poll * 144 * 30 / 1e6:,.1f} MB/month at 144 polls/day")
    print(f"one Live refresh: {page:,} B fetched")
    sizes(url)


if __name__ == "__main__":
    if "--sizes" in sys.argv:
        from app.core.config import get_settings
        sizes(get_settings().database_url)
    else:
        synthetic()
