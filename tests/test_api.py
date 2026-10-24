from fastapi.testclient import TestClient


def client(db, monkeypatch, admin="secret"):
    import app.db.session as session_mod
    from app.core.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "poller_enabled", False)
    monkeypatch.setattr(s, "admin_token", admin)
    from app.main import app
    from app.db.session import get_db

    def _db():
        yield db
    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_public_reads_and_admin_writes(db, monkeypatch):
    c = client(db, monkeypatch)
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/").status_code == 200
    st = c.get("/api/status").json()
    assert st["service"] == "citebase" and st["reachable"] is False
    assert c.post("/api/deploys", json={"commit": "abc"}).status_code == 401
    assert c.post("/api/deploys", json={"commit": "abc"}, headers={"Authorization": "Bearer secret"}).status_code == 201
    d = c.post("/api/events", json={"kind": "error_spike", "detail": "test", "metrics": {"error_rate": 0.5}},
               headers={"Authorization": "Bearer secret"}).json()
    assert d["degraded"] is True and d["action"] == "page"  # rules: error_rate 0.5 -> page
    incs = c.get("/api/incidents").json()
    assert len(incs) == 1
    detail = c.get(f"/api/incidents/{incs[0]['id']}").json()
    assert detail["decisions"][0]["event"]["kind"] == "error_spike"
    assert c.post(f"/api/incidents/{incs[0]['id']}/resolve", headers={"Authorization": "Bearer secret"}).json()["status"] == "resolved"
    assert c.get("/api/eval").status_code == 200


def test_eval_endpoint_returns_a_summary(db, monkeypatch):
    r = client(db, monkeypatch).get("/api/eval").json()
    if r["available"]:
        s = r["summary"]
        assert s["baseline"]["n"] == len(s["per_scenario"]) * len(s["reps"])
        assert {"accuracy", "silent_recall", "page_recall"} <= s["baseline"].keys()


def test_dashboard_reads_the_trace_fields_the_orchestrator_writes():
    """The dashboard renders tool_calls[].tool/args/result_preview; keep both sides in step."""
    import inspect
    from pathlib import Path
    from app.agent import orchestrator
    src = inspect.getsource(orchestrator)
    html = (Path(__file__).parents[1] / "app" / "static" / "app.js").read_text()
    for field in ("tool", "args", "result_preview", "result_summary", "duration_ms"):
        assert f'"{field}"' in src and f"c.{field}" in html


def test_public_views_hide_tool_results_admin_sees_them(db, monkeypatch):
    import json
    from app.db.models import Decision, Event
    c = client(db, monkeypatch)
    ev = Event(service="citebase", kind="error_spike", detail="x", metrics="{}", source="live")
    db.add(ev)
    db.flush()
    db.add(Decision(event_id=ev.id, triager="agent", action="notify", confidence="medium", summary="s", rationale="r",
                    tool_calls=json.dumps([{"round": 1, "tool": "get_recent_logs", "args": {"minutes": 30},
                                            "result_preview": "Unhandled error ..."}])))
    db.commit()
    public = c.get("/api/decisions").json()[0]["tool_calls"][0]
    assert "result_preview" not in public and public["result_hidden"] and public["tool"] == "get_recent_logs"
    admin = c.get("/api/decisions", headers={"Authorization": "Bearer secret"}).json()[0]["tool_calls"][0]
    assert admin["result_preview"] == "Unhandled error ..."
    wrong = c.get("/api/decisions", headers={"Authorization": "Bearer nope"}).json()[0]["tool_calls"][0]
    assert "result_preview" not in wrong


def test_security_headers_rate_limit_and_no_docs(db, monkeypatch):
    c = client(db, monkeypatch)
    r = c.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"] and "unsafe-inline" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    from app.core.security import HITS
    HITS.clear()  # defaults: 120 reads/min, 10 writes/min per client
    statuses = [c.post("/api/deploys", json={}, headers={"Authorization": "Bearer nope"}).status_code for _ in range(12)]
    assert statuses[:10] == [401] * 10 and statuses[-1] == 429
    assert c.get("/health").status_code == 200  # never limited


def test_cron_poll_needs_the_secret_and_runs_one_cycle(db, monkeypatch):
    from app.collector import poller
    from app.core.config import get_settings
    from tests.test_collector import raw
    c = client(db, monkeypatch)
    monkeypatch.setattr(get_settings(), "cron_secret", None)
    assert c.post("/api/cron/poll").status_code == 503
    monkeypatch.setattr(get_settings(), "cron_secret", "cronkey")
    assert c.post("/api/cron/poll", headers={"Authorization": "Bearer nope"}).status_code == 401
    monkeypatch.setattr(poller.target, "fetch_metrics", lambda timeout=None: raw())
    r = c.get("/api/cron/poll", headers={"Authorization": "Bearer cronkey"})
    assert r.status_code == 200 and r.json() == {"reachable": True, "fired": []}


def test_events_left_without_a_decision_are_triaged_on_the_next_poll(db, monkeypatch):
    from datetime import datetime, timezone
    from app.collector import poller
    from app.db.models import Decision, Event
    monkeypatch.setattr(poller, "_safe_fetch_logs", lambda **kw: [])
    now = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)
    db.add(Event(service="citebase", kind="fallback_spike", detail="cut off mid-triage", metrics="{}", source="live", ts=now))
    db.commit()
    assert poller.triage_pending(db, now) == 1
    assert db.query(Decision).one().action == "page"  # degraded (no key in tests) still pages a fallback spike
    assert poller.triage_pending(db, now) == 0


def test_stats_counts_uptime_incidents_and_time_to_resolve(db, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.db.models import Incident, Snapshot
    c = client(db, monkeypatch)
    now = datetime.now(timezone.utc)
    for i, ok in enumerate([True, True, True, False]):
        db.add(Snapshot(service="citebase", ok=ok, ts=now - timedelta(minutes=14 * i), raw="{}", derived="{}"))
    db.add(Incident(service="citebase", kind="error_spike", severity="page", status="resolved", summary="s",
                    opened_at=now - timedelta(hours=2), updated_at=now - timedelta(hours=1),
                    closed_at=now - timedelta(hours=1)))
    db.add(Incident(service="citebase", kind="latency_spike", severity="notify", status="open", summary="s",
                    opened_at=now - timedelta(minutes=30), updated_at=now))
    db.commit()
    s = c.get("/api/stats").json()
    assert s["uptime_24h"] == {"pct": 0.75, "polls": 4}
    assert s["incidents_7d"] == 2 and s["pages_7d"] == 1 and s["open_incidents"] == 1
    assert s["mttr_s"] == 3600 and s["resolved_30d"] == 1


def _incident_with_decision(db, triager="agent", calls=None):
    import json
    from app.db.models import Decision, Event, Incident
    ev = Event(service="citebase", kind="fallback_spike", detail="40% fallbacks", metrics="{}", source="live")
    inc = Incident(service="citebase", kind="fallback_spike", severity="page", summary="Answers are <fallbacks>")
    db.add_all([ev, inc])
    db.flush()
    db.add(Decision(event_id=ev.id, incident_id=inc.id, triager=triager, action="page", confidence="high",
                    summary="s", rationale="r", degraded=triager != "agent", tool_calls=json.dumps(calls or [])))
    db.commit()
    return inc


def test_decisions_say_which_path_decided(db, monkeypatch):
    c = client(db, monkeypatch)
    agent = _incident_with_decision(db)
    rules = _incident_with_decision(db, triager="degraded")
    a = c.get(f"/api/incidents/{agent.id}").json()["decisions"][0]
    r = c.get(f"/api/incidents/{rules.id}").json()["decisions"][0]
    assert (a["decided_by"], a["degraded"]) == ("groq", False)
    assert (r["decided_by"], r["degraded"]) == ("rules", True)


def test_trace_summaries_are_public_and_derived_for_old_decisions(db, monkeypatch):
    import json
    c = client(db, monkeypatch)
    old = {"round": 1, "tool": "get_recent_logs", "args": {"minutes": 30},
           "result_preview": json.dumps({"count": 2, "lines": ["a", "b"]})}
    new = {"round": 2, "tool": "get_deploy_history", "args": {"hours": 6}, "duration_ms": 812,
           "result_summary": "no recent deploys", "result_preview": "{}"}
    inc = _incident_with_decision(db, calls=[old, new])
    steps = c.get(f"/api/incidents/{inc.id}").json()["decisions"][0]["tool_calls"]
    assert steps[0]["result_summary"] == "2 log lines" and "result_preview" not in steps[0]
    assert steps[1]["result_summary"] == "no recent deploys" and steps[1]["duration_ms"] == 812


def test_incident_pages_have_shareable_urls(db, monkeypatch):
    c = client(db, monkeypatch)
    inc = _incident_with_decision(db)
    r = c.get(f"/incidents/{inc.id}")
    assert r.status_code == 200 and "<title>PAGE · Answers are &lt;fallbacks&gt; · Nightwarden</title>" in r.text
    assert c.get("/incidents/does-not-exist").status_code == 404
    for path in ("/incidents", "/eval", "/how", "/live"):
        assert c.get(path).status_code == 200
    fav = c.get("/favicon.ico")
    assert fav.status_code == 200 and fav.headers["content-type"].startswith("image/svg+xml")


def test_alert_links_point_at_the_incident_page():
    import inspect
    from app.services import triage
    assert '/incidents/{incident.id}' in inspect.getsource(triage) and "#incidents" not in inspect.getsource(triage)


def test_alert_delivery_endpoint_is_admin_only_and_skips_triage(db, monkeypatch, quiet_alerts):
    from app.db.models import Decision, Event
    from app.services import notify
    c = client(db, monkeypatch)
    assert c.post("/api/alerts/test").status_code == 401
    assert c.post("/api/alerts/test", headers={"Authorization": "Bearer wrong"}).status_code == 401
    monkeypatch.setattr(notify, "alert", lambda level, title, body, link=None:
                        quiet_alerts.append((level, title, body, link)) or {"webhook": True, "email": False})
    monkeypatch.setattr(notify.settings, "public_url", "https://nw.example/")
    r = c.post("/api/alerts/test", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.json() == {"webhook": True, "email": False, "email_configured": False}
    assert quiet_alerts == [("page", "Test alert", "Delivery test from /api/alerts/test", "https://nw.example/")]
    assert db.query(Event).count() == 0 and db.query(Decision).count() == 0  # no triage


def test_alert_delivery_endpoint_is_rate_limited_like_other_writes(db, monkeypatch):
    c = client(db, monkeypatch)
    codes = [c.post("/api/alerts/test").status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_missing_email_config_is_loud(db, monkeypatch, caplog):
    import logging
    from app.main import app, lifespan
    from app.services import notify
    c = client(db, monkeypatch)
    monkeypatch.setattr(notify.settings, "brevo_api_key", "k")
    monkeypatch.setattr(notify.settings, "alert_email", None)
    assert c.get("/api/status").json()["email_alerts"] == "off"
    with caplog.at_level(logging.WARNING, logger="nightwarden"):
        import asyncio

        async def boot():
            async with lifespan(app):
                pass
        asyncio.run(boot())
    assert any("Email alerts are off: ALERT_EMAIL not set" in r.getMessage() for r in caplog.records)
    monkeypatch.setattr(notify.settings, "alert_email", "me@example.com")
    assert c.get("/api/status").json()["email_alerts"] == "on"
