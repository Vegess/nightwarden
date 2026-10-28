"""Database egress: the poller reads one state row, public reads are edge-cached, Neon transfer is watched."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import event

from app.db.models import Decision, Event, PollerState, Snapshot
from tests.test_api import client
from tests.test_collector import raw

T0 = datetime(2026, 10, 27, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh():
    from app.routes import ops
    ops._cache.clear()


def _statements(db):
    seen = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda c, cur, stmt, *a: seen.append(stmt))
    return seen


def _poll(db, t, **kw):
    from app.collector import poller
    return poller.poll_once(db, fetch=lambda: raw(**kw), now=t)


def test_a_poll_never_reads_past_snapshots(db):
    _poll(db, T0)  # first poll seeds the state row
    seen = _statements(db)
    _poll(db, T0 + timedelta(minutes=10), requests_total=160)
    reads = [s for s in seen if s.lstrip().upper().startswith("SELECT")]
    assert reads and not any("FROM snapshots" in s for s in reads)
    assert not any("snapshots.raw" in s or "snapshots.derived" in s for s in reads)
    assert not any("SELECT events.id, events.ts, events.service" in s and "decisions" not in s for s in reads)


def test_typical_traffic_is_kept_incrementally_and_still_detects_a_drop(db):
    total = 1000
    for i in range(40):  # 40 ten-minute slots of ~100 requests each
        total += 100
        _poll(db, T0 + timedelta(minutes=10 * i), requests_total=total, uptime_s=1000 + 600 * i)
    state = db.get(PollerState, "citebase")
    assert len(json.loads(state.traffic)) == 40 and state.failures == 0
    fired = _poll(db, T0 + timedelta(minutes=400), requests_total=total + 1, uptime_s=1000 + 600 * 40)
    assert [e.kind for e in fired] == ["traffic_drop"]
    for i in range(41, 200):  # the ring never grows past 24 h of slots
        _poll(db, T0 + timedelta(minutes=10 * i), requests_total=total + i, uptime_s=1000 + 600 * i)
    assert len(json.loads(db.get(PollerState, "citebase").traffic)) <= 144


def test_stored_raw_is_trimmed(db):
    _poll(db, T0, fallback_reasons={"groq_429": 3}, latency_p50_ms=100, questions_today=4,
          documents_by_status={"ready": 9, "failed": 1}, jobs_by_status={"done": 5})
    snap = db.query(Snapshot).one()
    stored = json.loads(snap.raw)
    assert "fallback_reasons" not in stored and "latency_p50_ms" not in stored and "questions_today" not in stored
    assert stored["documents_by_status"] == {"failed": 1} and stored["jobs_by_status"] == {"failed": 0}
    assert set(json.loads(snap.derived)) == {"window"}


def test_state_is_seeded_once_from_existing_snapshots(db):
    from app.collector import poller
    derived = json.dumps({"window": {"requests_5m": 50}})
    for i in range(35):
        db.add(Snapshot(service="citebase", ok=True, ts=T0 - timedelta(minutes=10 * (35 - i)),
                        raw=json.dumps(raw()), derived=derived))
    db.commit()
    _poll(db, T0, requests_total=150)
    state = db.get(PollerState, "citebase")
    assert len(json.loads(state.traffic)) == 36 and json.loads(state.last_raw)["requests_total"] == 150
    assert poller.trim(raw()) == json.loads(state.last_raw) | {"requests_total": 100}


def test_cache_headers(db, monkeypatch):
    c = client(db, monkeypatch)
    shared = "public, s-maxage=60, stale-while-revalidate=300"
    for path in ("/api/status", "/api/stats", "/api/decisions", "/api/incidents", "/api/health/checks", "/api/eval",
                 "/", "/live", "/usage", "/robots.txt"):
        assert c.get(path).headers["cache-control"] == shared, path
    assert c.get("/health", headers={"Accept": "text/html"}).headers["cache-control"] == shared
    assert "s-maxage" not in c.get("/health").headers.get("cache-control", "")  # uptime monitors: always fresh
    assert c.get("/api/usage").headers["cache-control"] == "public, s-maxage=300, stale-while-revalidate=300"
    admin = {"Authorization": "Bearer secret"}
    assert c.get("/api/decisions", headers=admin).headers["cache-control"] == "no-store"
    assert c.get("/api/status", headers={"Authorization": "Bearer wrong"}).headers["cache-control"] == "no-store"
    assert c.get("/api/incidents/nope").headers["cache-control"] == "no-store"
    assert c.post("/api/deploys", json={}, headers=admin).headers["cache-control"] == "no-store"
    assert c.get("/api/cron/poll").headers["cache-control"] == "no-store"


def test_robots_keeps_crawlers_off_the_api(db, monkeypatch):
    assert "Disallow: /api/" in client(db, monkeypatch).get("/robots.txt").text


def test_brief_decisions_count_tools_in_the_database(db, monkeypatch):
    c = client(db, monkeypatch)
    ev = Event(service="citebase", kind="error_spike", detail="x", metrics="{}", source="live")
    db.add(ev)
    db.flush()
    calls = [{"tool": "get_recent_logs", "args": {}, "result_preview": 'a "tool": "x" quoted'},
             {"tool": "get_metric_series", "args": {}}, {"tool": "decide", "args": {}}]
    db.add(Decision(event_id=ev.id, triager="agent", action="notify", summary="s", rationale="r" * 500,
                    tool_calls=json.dumps(calls)))
    db.commit()
    d = c.get("/api/decisions?brief=1").json()[0]
    assert d["tool_count"] == 2 and "rationale" not in d and "tool_calls" not in d
    assert d["event"] == {"kind": "error_spike", "detail": "x"} and d["decided_by"] == "groq"


def _neon(monkeypatch, transfer_bytes, calls):
    from app.routes import ops
    monkeypatch.setattr(ops.settings, "neon_api_key", "k")
    monkeypatch.setattr(ops.settings, "neon_project_id", "p")

    def get(url, **kw):
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url), json={"project": {
            "data_transfer_bytes": transfer_bytes, "compute_time_seconds": 7200,
            "consumption_period_start": "2026-10-01T00:00:00Z"}})
    monkeypatch.setattr(ops.httpx, "get", get)


@pytest.mark.parametrize("gb,status", [(1.0, "pass"), (3.6, "warn"), (4.6, "fail")])
def test_neon_transfer_on_health_and_usage(db, monkeypatch, gb, status):
    calls = []
    _neon(monkeypatch, int(gb * 1e9), calls)
    c = client(db, monkeypatch)
    check = next(x for x in c.get("/api/health/checks").json()["checks"] if x["name"] == "Neon network transfer")
    assert check["status"] == status and f"{gb:.2f} of 5 GB" in check["detail"]
    t = c.get("/api/usage").json()["neon"]["transfer"]
    assert t["limit_gb"] == 5.0 and t["gb"] == pytest.approx(gb)
    assert len(calls) == 1  # both pages share one Neon API call an hour


def test_neon_api_is_cached_for_an_hour(monkeypatch):
    from app.routes import ops
    calls = []
    _neon(monkeypatch, 10, calls)
    clock = [1000.0]
    monkeypatch.setattr(ops.time, "monotonic", lambda: clock[0])
    ops.neon_usage()
    clock[0] += 3599
    ops.neon_usage()
    assert len(calls) == 1
    clock[0] += 2
    ops.neon_usage()
    assert len(calls) == 2


def test_neon_not_configured_is_a_warning(db, monkeypatch):
    from app.routes import ops
    monkeypatch.setattr(ops.settings, "neon_api_key", None)
    checks = client(db, monkeypatch).get("/api/health/checks").json()["checks"]
    neon = next(x for x in checks if x["name"] == "Neon network transfer")
    assert neon["status"] == "warn" and "not configured" in neon["detail"]
