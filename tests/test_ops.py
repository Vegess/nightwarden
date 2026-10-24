import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Alert, DailyStat, Decision, Event, Incident, Snapshot
from app.services.notify import alert as real_alert  # bound before the autouse fixture swaps it out
from tests.test_api import client

NOW = datetime(2026, 10, 24, 0, 5, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from app.routes import ops
    from app.services import rollup
    ops._cache.clear()
    monkeypatch.setitem(rollup._done_through, "date", None)


def _decision(db, ts, action="notify", source="live", triager="agent", tokens=(100, 20), ms=1000, degraded=False):
    ev = Event(service="citebase", kind="error_spike", detail="x", metrics="{}", source=source, ts=ts)
    db.add(ev)
    db.flush()
    db.add(Decision(event_id=ev.id, triager=triager, action=action, ts=ts, tokens_in=tokens[0], tokens_out=tokens[1],
                    latency_ms=ms, degraded=degraded))


def test_alert_records_one_row_per_channel(db, monkeypatch):
    from app.services import notify
    monkeypatch.setattr(notify.settings, "notify_webhook_url", "https://hooks.slack.com/x")
    monkeypatch.setattr(notify.settings, "brevo_api_key", "k")
    monkeypatch.setattr(notify.settings, "alert_email", "a@b.c")

    def post(url, **kw):
        status = 500 if "slack" in url else 201
        return httpx.Response(status, request=httpx.Request("POST", url))
    monkeypatch.setattr(notify.httpx, "post", post)
    assert real_alert("page", "t", "b") == {"webhook": False, "email": True}
    rows = {a.channel: a for a in db.scalars(select(Alert))}
    assert rows["webhook"].ok is False and rows["webhook"].error_short == "HTTP 500" and rows["webhook"].level == "page"
    assert rows["email"].ok is True and rows["email"].error_short is None
    real_alert("watch", "t", "b")  # dashboard only: nothing attempted, nothing recorded
    assert len(db.scalars(select(Alert)).all()) == 2


def test_rollup_backfills_is_idempotent_and_retains_30_days(db):
    from app.services import rollup
    d1, d2 = datetime(2026, 10, 22, 9, tzinfo=timezone.utc), datetime(2026, 10, 23, 9, tzinfo=timezone.utc)
    old = NOW - timedelta(days=40)
    db.add_all([Snapshot(service="citebase", ok=True, ts=d1), Snapshot(service="citebase", ok=False, ts=d1),
                Snapshot(service="citebase", ok=True, ts=d2), Snapshot(service="citebase", ok=True, ts=old),
                Snapshot(service="citebase", ok=True, ts=NOW),
                Incident(service="citebase", kind="k", severity="page", summary="s", opened_at=d2, updated_at=d2,
                         closed_at=d2 + timedelta(hours=1), status="resolved"),
                Alert(level="page", channel="email", ok=True, ts=d2), Alert(level="page", channel="email", ok=False, ts=d2)])
    _decision(db, d2, "page", ms=100)
    _decision(db, d2, "watch", triager="degraded", tokens=(0, 0), ms=300)
    _decision(db, d2, "page", source="eval")  # evaluation runs stay out of the rollup
    db.commit()

    written = rollup.rollup(db, NOW)
    assert written[0] == old.date() and len(written) == 40
    assert written[-1] == date(2026, 10, 23)
    s = db.get(DailyStat, date(2026, 10, 23))
    assert (s.polls, s.polls_ok, s.events, s.pages, s.tokens_in, s.tokens_out) == (1, 1, 2, 1, 100, 20)
    assert json.loads(s.decisions_by_action) == {"page": 1, "watch": 1}
    assert json.loads(s.decisions_by_decided_by) == {"groq": 1, "rules": 1}
    assert (s.decision_ms_p50, s.decision_ms_p95) == (100, 300)
    assert (s.incidents_opened, s.incidents_resolved, s.emails_sent, s.emails_failed) == (1, 1, 1, 1)
    assert db.get(DailyStat, date(2026, 10, 22)).polls == 2
    assert db.get(DailyStat, date(2026, 10, 24)) is None  # today is not rolled up yet

    # Retention: the 40-day-old snapshot is gone; everything inside 30 days stays.
    assert all(datetime.fromisoformat(str(t)).replace(tzinfo=timezone.utc) > NOW - timedelta(days=30)
               for t in db.scalars(select(Snapshot.ts)))
    assert len(db.scalars(select(Snapshot)).all()) == 4

    assert rollup.rollup(db, NOW) == []  # idempotent
    assert db.get(DailyStat, date(2026, 10, 23)).polls == 1


def test_old_snapshots_are_rolled_up_before_they_are_deleted(db):
    from app.services import rollup
    old = NOW - timedelta(days=40)
    db.add(Snapshot(service="citebase", ok=True, ts=old))
    db.commit()
    rollup.rollup(db, NOW)
    assert db.get(DailyStat, old.date()).polls == 1
    assert db.scalars(select(Snapshot)).all() == []


def test_rollup_runs_once_in_the_first_window_after_midnight(db):
    from app.services import rollup
    assert rollup.due(NOW)
    assert not rollup.due(NOW + timedelta(minutes=20))
    rollup.rollup(db, NOW)
    assert not rollup.due(NOW + timedelta(minutes=2))  # already done in this process


def test_poll_rolls_up_after_midnight(db):
    from app.collector import poller
    db.add(Snapshot(service="citebase", ok=True, ts=NOW - timedelta(hours=10), raw="{}", derived="{}"))
    db.commit()
    poller.poll_once(db, fetch=lambda: {"commit": "a", "uptime_s": 100}, now=NOW)
    assert db.get(DailyStat, date(2026, 10, 23)).polls == 1


def test_health_checks(db, monkeypatch):
    c = client(db, monkeypatch)
    from app.routes import ops
    monkeypatch.setattr(ops.settings, "groq_api_key", "k")
    monkeypatch.setattr(ops.settings, "public_url", "http://testserver")
    monkeypatch.setattr(ops.settings, "groq_daily_token_limit", 1000)
    now = datetime.now(timezone.utc)
    db.add(Snapshot(service="citebase", ok=True, ts=now - timedelta(minutes=20)))
    _decision(db, now - timedelta(hours=1), tokens=(700, 150), degraded=True)
    db.commit()
    r = c.get("/api/health/checks").json()
    by = {x["name"]: x for x in r["checks"]}
    assert by["Poll freshness"]["status"] == "warn"
    assert by["Target reachable"]["status"] == "pass"
    assert by["Groq usable"]["status"] == "fail"
    assert by["Public URL matches"]["status"] == "pass"
    assert by["Groq tokens today"]["status"] == "warn" and "850" in by["Groq tokens today"]["detail"]
    assert by["Migrations at head"]["status"] == "fail"  # test DB is created without alembic
    assert by["Database round trip"]["status"] == "pass"
    assert {x["status"] for x in r["checks"]} <= {"pass", "warn", "fail"} and r["overall"] == "fail"
    db.add(Snapshot(service="citebase", ok=False, ts=now))
    db.commit()
    assert c.get("/api/health/checks").json() == r  # cached for 60 s


def test_usage(db, monkeypatch):
    c = client(db, monkeypatch)
    from app.routes import ops
    monkeypatch.setattr(ops.settings, "groq_daily_token_limit", 1000)
    monkeypatch.setattr(ops.settings, "neon_api_key", None)
    now = datetime.now(timezone.utc)
    _decision(db, now, tokens=(150, 50))
    _decision(db, now, source="eval", tokens=(80, 20))
    db.add_all([Snapshot(service="citebase", ok=True, ts=now), Alert(level="page", channel="email", ok=True, ts=now)])
    db.commit()
    u = c.get("/api/usage").json()
    assert u["groq"]["today"] == {"live": 200, "eval": 100, "total": 300}
    assert u["groq"]["mean_tokens_per_triage"] == 150 and u["groq"]["triages_left_today"] == 4  # 700 // 150
    assert u["polls"]["today"] == 1 and u["polls"]["limit_per_day"] == 144
    assert u["emails"]["today"] == 1 and u["emails"]["limit_per_day"] == 300
    assert u["neon"]["configured"] is False and "not configured" in u["neon"]["detail"]


def test_usage_with_no_data_has_no_estimate(db, monkeypatch):
    u = client(db, monkeypatch).get("/api/usage").json()
    assert u["groq"]["triages_left_today"] is None and u["polls"]["today"] == 0


def test_neon_usage_reads_the_project(monkeypatch):
    from app.routes import ops
    monkeypatch.setattr(ops.settings, "neon_api_key", "k")
    monkeypatch.setattr(ops.settings, "neon_project_id", "p")
    monkeypatch.setattr(ops.httpx, "get", lambda url, **kw: httpx.Response(
        200, json={"project": {"compute_time_seconds": 36000, "consumption_period_start": "2026-10-01T00:00:00Z"}},
        request=httpx.Request("GET", url)))
    assert ops._neon()["cu_hours"] == 10.0


def test_health_page_and_uptime_json(db, monkeypatch):
    c = client(db, monkeypatch)
    assert c.get("/health").json() == {"status": "ok"}
    page = c.get("/health", headers={"Accept": "text/html,*/*"})
    assert page.status_code == 200 and "<nav" in page.text
    assert c.get("/usage").status_code == 200


def test_migrations_reach_head_and_match_the_models(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect
    from app.core.config import get_settings
    url = f"sqlite:///{tmp_path}/m.db"
    monkeypatch.setattr(get_settings(), "database_url", url)
    command.upgrade(Config("alembic.ini"), "head")
    cols = {t: {c["name"] for c in inspect(create_engine(url)).get_columns(t)} for t in ("alerts", "daily_stats")}
    assert cols["alerts"] == {c.name for c in Alert.__table__.columns}
    assert cols["daily_stats"] == {c.name for c in DailyStat.__table__.columns}
    command.downgrade(Config("alembic.ini"), "0001_initial")
