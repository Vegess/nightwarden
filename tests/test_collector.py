"""Signals, detectors and the poller against a fake target."""
import json
from datetime import datetime, timedelta, timezone

from app.collector import detectors, signals
from app.db.models import Decision, Deploy, Event, Incident, Snapshot


def raw(**kw):
    base = {"uptime_s": 1000, "started_at": "t0", "commit": "aaa", "requests_total": 100,
            "requests_by_status": {"200": 100}, "latency_p95_ms": 1500, "chat_requests_total": 30,
            "fallback_total": 0, "fallback_5m": 0, "fallback_rate_5m": 0.0, "empty_retrievals_total": 0,
            "chunks_total": 200, "documents_total": 10, "documents_by_status": {"ready": 10}, "jobs_by_status": {}}
    base.update(kw)
    return base


def test_delta_handles_restart_counter_reset():
    prev = raw(requests_total=500, uptime_s=5000)
    cur = raw(requests_total=40, uptime_s=60, started_at="t1", requests_by_status={"200": 38, "500": 2})
    d = signals.delta(prev, cur, 60)
    assert d["restarted"] and d["requests"] == 40 and d["s5xx"] == 2


def test_429_is_not_an_error():
    d = signals.delta(raw(), raw(requests_total=140, requests_by_status={"200": 110, "429": 30}), 60)
    w = signals.window([d], raw(), None)
    assert w["error_rate"] == 0.0 and w["rate_429"] == 0.75 and w["rate_4xx"] == 0.75


def test_detectors():
    calm = {"requests_5m": 40, "chat_5m": 10, "error_rate": 0.0, "rate_4xx": 0.02, "rate_429": 0.0, "latency_p95_ms": 1800,
            "fallback_rate": 0.0, "fallback_5m": 0, "empty_retrieval_rate": 0.0, "traffic_ratio": 1.0,
            "chunks_total": 200, "documents_total": 10}
    assert detectors.detect(calm) == []
    assert [s.kind for s in detectors.detect({**calm, "fallback_rate": 0.8, "fallback_5m": 8})] == ["fallback_spike"]
    assert [s.kind for s in detectors.detect({**calm, "chunks_total": 0})] == ["zero_chunks"]
    assert [s.kind for s in detectors.detect({**calm, "requests_5m": 1, "traffic_ratio": 0.03}, previous_requests=40)] == ["traffic_drop"]
    assert [s.kind for s in detectors.detect({**calm, "error_rate": 0.2})] == ["error_spike"]


def _poll(db, payload, t, **kw):
    from app.collector import poller
    fetch = (lambda: (_ for _ in ()).throw(ConnectionError("refused"))) if payload is None else (lambda: payload)
    return poller.poll_once(db, fetch=fetch, now=t, **kw)


def test_poller_detects_deploys_and_silent_fallback(db, monkeypatch):
    from app.collector import poller
    monkeypatch.setattr(poller, "_safe_fetch_logs", lambda **kw: [{"level": "WARNING", "msg": "Answer served by local fallback"}])
    t = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)
    assert _poll(db, raw(), t) == []
    _poll(db, raw(commit="bbb", requests_total=140), t + timedelta(minutes=1))
    assert db.query(Deploy).one().commit_sha == "bbb"
    fired = _poll(db, raw(commit="bbb", requests_total=180, chat_requests_total=40, fallback_total=9,
                          fallback_5m=9, fallback_rate_5m=0.9), t + timedelta(minutes=2))
    assert [e.kind for e in fired] == ["fallback_spike"]
    d = db.query(Decision).one()
    # No key in tests: the degraded path must still page for a silent failure.
    assert d.degraded and d.triager == "degraded" and d.action == "page"
    assert db.query(Incident).one().severity == "page"
    # Cooldown: the same signal a minute later doesn't fire again.
    assert _poll(db, raw(commit="bbb", requests_total=220, chat_requests_total=50, fallback_total=18,
                         fallback_5m=18, fallback_rate_5m=0.95), t + timedelta(minutes=3)) == []


def test_poller_outage_after_three_failures_and_recovery(db, quiet_alerts):
    t = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)
    _poll(db, raw(), t)
    for i in range(1, 3):
        assert _poll(db, None, t + timedelta(minutes=i)) == []
    fired = _poll(db, None, t + timedelta(minutes=3))
    assert [e.kind for e in fired] == ["target_down"]
    assert db.query(Incident).one().status == "open"
    _poll(db, raw(), t + timedelta(minutes=4))
    assert db.query(Incident).one().status == "resolved"
    assert any("reachable again" in title for _, title in quiet_alerts)


def test_agent_decision_through_the_poller(db, scripted, monkeypatch):
    from app.collector import poller
    from tests.conftest import tool_call
    monkeypatch.setattr(poller, "_safe_fetch_logs", lambda **kw: [])
    client = scripted([[tool_call("get_deploy_history", {"hours": 2})],
                       [tool_call("decide", {"action": "notify", "summary": "5xx after deploy", "rationale": "r",
                                             "confidence": "medium"}, 1)]])
    t = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)
    _poll(db, raw(), t)
    _poll(db, raw(requests_total=150, requests_by_status={"200": 120, "500": 30}), t + timedelta(minutes=1),
          triage_client=client)
    d = db.query(Decision).one()
    assert d.triager == "agent" and d.action == "notify" and json.loads(d.tool_calls)[0]["tool"] == "get_deploy_history"


def test_incidents_merge_and_escalate(db, quiet_alerts):
    from app.services import triage
    from app.agent.orchestrator import TriageResult
    e1 = Event(service="citebase", kind="error_spike", detail="x", metrics="{}")
    db.add(e1); db.commit()
    inc, alert = triage._incident_for(db, e1, TriageResult("watch", "low", "s", "r"))
    db.commit()
    assert alert
    inc2, alert2 = triage._incident_for(db, e1, TriageResult("watch", "low", "s", "r"))
    assert inc2.id == inc.id and not alert2 and inc2.occurrences == 2
    inc3, alert3 = triage._incident_for(db, e1, TriageResult("page", "high", "worse", "r"))
    assert inc3.id == inc.id and alert3 and inc3.severity == "page"


def test_redaction():
    from app.collector.target import redact
    assert redact("user 3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90 a@b.com failed") == "user <id> <email> failed"


def test_incidents_stay_open_while_down_and_quiet_ones_resolve(db, monkeypatch):
    from app.collector import poller
    monkeypatch.setattr(poller, "_safe_fetch_logs", lambda **kw: [])
    t = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)
    _poll(db, raw(), t)
    for i in range(1, 4):
        _poll(db, None, t + timedelta(minutes=i))
    # Still down 4 h later: target_down re-fires after its cooldown and keeps the incident open.
    _poll(db, None, t + timedelta(hours=4))
    db.expire_all()
    inc = db.query(Incident).one()
    assert inc.status == "open" and inc.occurrences == 2
    # Back up: recovery closes it.
    _poll(db, raw(), t + timedelta(hours=4, minutes=1))
    db.expire_all()
    assert db.query(Incident).one().status == "resolved"
    # A fallback incident with no recurrence auto-resolves after 3 quiet hours, on the poll clock.
    _poll(db, raw(requests_total=140, chat_requests_total=40, fallback_total=9, fallback_5m=9, fallback_rate_5m=0.9), t + timedelta(hours=4, minutes=2))
    fb = db.query(Incident).filter_by(kind="fallback_spike").one()
    assert fb.status == "open"
    _poll(db, raw(requests_total=150, chat_requests_total=41, fallback_total=9), t + timedelta(hours=7, minutes=30))
    db.expire_all()
    assert db.query(Incident).filter_by(kind="fallback_spike").one().status == "resolved"
