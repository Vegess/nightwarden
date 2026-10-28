"""Shadow baseline, /api/performance, /api/reports, the weekly digest and the month-to-date polls fix."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.models import DailyStat, Decision, Event, Incident, PollerState, Snapshot
from app.services import reports
from tests.test_api import client

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)  # a Wednesday


@pytest.fixture(autouse=True)
def _fresh():
    from app.routes import insights, ops
    insights._cache.clear()
    ops._cache.clear()


def _decision(db, ts, action="notify", triager="agent", provider="groq", degraded=False, rules=None, kind="error_spike",
              tools=2, tokens=1000, ms=1000, summary="s", source="live"):
    ev = Event(service="citebase", kind=kind, detail="x", metrics="{}", source=source, ts=ts)
    db.add(ev)
    db.flush()
    calls = [{"tool": "get_recent_logs", "args": {}}] * tools + [{"tool": "decide", "args": {}}]
    db.add(Decision(event_id=ev.id, triager=triager, provider=provider, action=action, degraded=degraded, ts=ts,
                    rules_action=rules, tool_calls=json.dumps(calls), tokens_in=tokens, tokens_out=0, latency_ms=ms,
                    summary=summary))


def test_shadow_baseline_is_stored_without_an_alert(db, quiet_alerts):
    from app.agent.baseline import rules_triage
    from app.services import triage
    from harness.scenarios import build
    ev = Event(service="citebase", kind="error_spike", detail="x", source="live",
               metrics=json.dumps({"error_rate": 0.02, "requests_5m": 50}))
    db.add(ev)
    db.commit()
    d = triage.run(db, ev, build(1, 0)[1])
    payload = {"kind": "error_spike", "service": "citebase", "detail": "x", "metrics": json.loads(ev.metrics)}
    assert d.rules_action == rules_triage(payload)
    decision_alerts = [t for lvl, t in quiet_alerts if lvl in ("page", "notify") and "without AI" not in t]
    assert len(decision_alerts) <= 1  # only the decision's own alert, never one for the shadow baseline


def test_performance(db, monkeypatch):
    now = datetime.now(timezone.utc)
    _decision(db, now - timedelta(hours=1), "page", rules="page", tools=3, tokens=3000, ms=1000)
    _decision(db, now - timedelta(hours=2), "watch", rules="page", kind="latency_spike", tools=1, tokens=1000, ms=3000)
    _decision(db, now - timedelta(hours=3), "notify", provider="gemini", degraded=True, rules="watch",
              kind="latency_spike", tools=2, tokens=2000, ms=5000, summary="Triage inconclusive: no decide")
    _decision(db, now - timedelta(hours=4), "page", triager="degraded", provider="rules", degraded=True, rules="page",
              tools=0, tokens=0, ms=10)
    _decision(db, now - timedelta(days=10), "page")  # outside 7 days
    _decision(db, now - timedelta(hours=1), "page", source="eval")  # never counted
    db.commit()
    p = client(db, monkeypatch).get("/api/performance?days=7").json()
    assert p["decisions"] == 4 and p["by_action"] == {"page": 2, "watch": 1, "notify": 1}
    assert p["decided_by"] == {"primary": 2, "backup": 1, "rules": 1}
    assert p["degraded_pct"] == 0.5
    assert p["decision_ms"] == {"median": 2000.0, "p95": 5000}
    assert p["tool_calls_median"] == 2 and p["mean_tokens"] == 2000
    assert p["inconclusive_pct"] == pytest.approx(1 / 3, abs=1e-3)
    d = p["disagreement"]
    assert (d["compared"], d["disagree"], d["pct"]) == (3, 2, pytest.approx(0.6667, abs=1e-3))
    assert d["top_kinds"] == [{"kind": "latency_spike", "n": 2, "of": 2}]
    assert client(db, monkeypatch).get("/api/performance?days=30").json()["decisions"] == 5
    assert client(db, monkeypatch).get("/api/performance?days=9").status_code == 422


def test_performance_empty(db, monkeypatch):
    p = client(db, monkeypatch).get("/api/performance").json()
    assert p["decisions"] == 0 and p["degraded_pct"] is None and p["disagreement"]["pct"] is None


def test_period_bounds():
    wed = date(2026, 10, 28)
    assert reports.period_bounds("week", 0, wed) == (date(2026, 10, 26), date(2026, 11, 2))
    assert reports.period_bounds("week", 1, wed) == (date(2026, 10, 19), date(2026, 10, 26))
    assert reports.period_bounds("month", 0, wed) == (date(2026, 10, 1), date(2026, 11, 1))
    assert reports.period_bounds("month", 10, wed) == (date(2025, 12, 1), date(2026, 1, 1))
    assert reports.period_bounds("month", 2, date(2026, 12, 5)) == (date(2026, 10, 1), date(2026, 11, 1))
    assert reports.period_bounds("year", 1, wed) == (date(2025, 1, 1), date(2026, 1, 1))
    assert reports.period_label("week", date(2026, 10, 26)) == "Week of Oct 26, 2026"


def _day(db, d, polls=144, ok=144, pages=0, sev=None, kinds=None, resolved=0, resolve_s=0, decisions=None,
         degraded=0, tokens=0, emails=0):
    db.add(DailyStat(date=d, polls=polls, polls_ok=ok, events=sum((kinds or {}).values()), pages=pages,
                     decisions_by_action=json.dumps(decisions or {}), decisions_by_decided_by="{}",
                     tokens_in=tokens, tokens_out=0, incidents_opened=sum((sev or {}).values()),
                     incidents_resolved=resolved, resolve_s_sum=resolve_s, incidents_by_severity=json.dumps(sev or {}),
                     events_by_kind=json.dumps(kinds or {}), decisions_degraded=degraded, emails_sent=emails,
                     emails_failed=0, version=2))


def test_reports_week_with_delta_and_csv(db, monkeypatch):
    for i in range(7):  # last week (Oct 19-25): one bad day
        _day(db, date(2026, 10, 19) + timedelta(days=i), ok=144 if i else 72)
    _day(db, date(2026, 10, 26), sev={"page": 1, "notify": 2}, kinds={"error_spike": 3, "restart": 1},
         resolved=2, resolve_s=3600, decisions={"page": 1, "notify": 3}, degraded=1, tokens=5000, emails=1, pages=1)
    _day(db, date(2026, 10, 27), kinds={"error_spike": 1})
    db.commit()
    rep = reports.report(db, "week", 0, NOW)
    s = rep["summary"]
    assert rep["label"] == "Week of Oct 26, 2026" and rep["partial"] and not rep["has_next"] and rep["has_prev"]
    assert s["days"] == 3  # Mon, Tue and today (computed live)
    assert s["incidents"] == 3 and s["incidents_by_severity"] == {"page": 1, "notify": 2}
    assert s["mttr_s"] == 1800 and s["pages"] == 1 and s["degraded_pct"] == 0.25 and s["tokens"] == 5000
    assert s["top_kinds"][0] == {"kind": "error_spike", "n": 4}
    prev = reports.report(db, "week", 1, NOW)
    assert prev["summary"]["uptime"] == pytest.approx((144 * 6 + 72) / (144 * 7), abs=1e-5) and prev["has_next"]
    assert rep["delta"]["incidents"] == 3 and rep["delta"]["polls"] == 288 - 1008

    monkeypatch.setattr(reports, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: NOW)}))
    c = client(db, monkeypatch)
    r = c.get("/api/reports?period=week&offset=1&format=csv")
    assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("date,polls,polls_ok,uptime") and len(lines) == 8 and lines[1].startswith("2026-10-19,144,72,0.5")
    assert c.get("/api/reports?period=decade").status_code == 422
    assert c.get("/api/reports?period=month").json()["label"] == "October 2026"


def test_reports_empty(db, monkeypatch):
    r = client(db, monkeypatch).get("/api/reports?period=year").json()
    assert r["summary"]["uptime"] is None and r["summary"]["incidents"] == 0 and not r["has_prev"]


def test_old_rollup_rows_are_upgraded_but_keep_their_polls(db):
    from app.services import rollup
    d = date(2026, 10, 20)
    db.add(DailyStat(date=d, polls=144, polls_ok=140, events=0, decisions_by_action="{}", decisions_by_decided_by="{}",
                     tokens_in=0, tokens_out=0, incidents_opened=0, incidents_resolved=0, pages=0, emails_sent=0,
                     emails_failed=0, version=1))
    opened = datetime(2026, 10, 20, 1, tzinfo=timezone.utc)
    db.add(Incident(service="citebase", kind="restart", severity="notify", summary="s", opened_at=opened,
                    updated_at=opened, closed_at=opened + timedelta(minutes=30), status="resolved"))
    db.commit()
    assert rollup.upgrade_rows(db) == 1
    row = db.get(DailyStat, d)
    assert (row.polls, row.polls_ok, row.version, row.resolve_s_sum) == (144, 140, 2, 1800)
    assert json.loads(row.incidents_by_severity) == {"notify": 1}
    assert rollup.upgrade_rows(db) == 0


def _state(db, week=None):
    st = PollerState(service="citebase", last_raw="{}", deltas="[]", traffic="[]", failures=0, digest_week=week)
    db.add(st)
    db.commit()
    return st


def test_weekly_digest_once_on_monday_after_0330(db, monkeypatch, quiet_alerts):
    monkeypatch.setattr(reports.get_settings(), "notify_webhook_url", "https://discord.com/api/webhooks/1/x")
    st = _state(db)
    monday = datetime(2026, 11, 2, 3, 20, tzinfo=timezone.utc)
    assert not reports.maybe_send_digest(db, st, monday)  # 03:20: too early
    assert reports.maybe_send_digest(db, st, monday + timedelta(minutes=10))
    assert not reports.maybe_send_digest(db, st, monday + timedelta(minutes=20))  # never twice
    assert not reports.maybe_send_digest(db, st, monday + timedelta(days=3))
    assert db.get(PollerState, "citebase").digest_week == "2026-W45"
    sent = [t for lvl, t in quiet_alerts if lvl == "info"]
    assert sent == ["Weekly report · Week of Oct 26, 2026"]
    assert reports.maybe_send_digest(db, st, monday + timedelta(days=7, minutes=10))  # next Monday


def test_digest_catches_up_if_monday_had_no_poll_and_skips_without_a_webhook(db, monkeypatch, quiet_alerts):
    st = _state(db)
    monkeypatch.setattr(reports.get_settings(), "notify_webhook_url", None)
    tuesday = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
    assert not reports.maybe_send_digest(db, st, tuesday) and st.digest_week == "2026-W45" and quiet_alerts == []


def test_digest_text():
    rep = {"summary": {"uptime": 0.9931, "polls": 1008, "incidents": 3, "incidents_by_severity": {"page": 1, "notify": 2},
                       "pages": 1, "mttr_s": 1800, "decisions": 4, "degraded_pct": 0.25, "tokens": 5000,
                       "emails_sent": 1, "emails_failed": 0, "top_kinds": [{"kind": "error_spike", "n": 4}]},
           "delta": {"uptime": -0.004, "incidents": 2}}
    text = reports.digest_text(rep)
    assert "Uptime 99.31% (▼0.4 pt) over 1,008 polls" in text and "1 notify" not in text and "2 notify" in text
    assert "time to resolve 30 min" in text and "error spike ×4" in text


def test_month_to_date_polls_over_20_days(db, monkeypatch):
    """Was: month = snapshots since the 1st, but snapshots older than 7 days were gone, so it read 1,008."""
    from app.routes import ops
    for i in range(20):  # Oct 1-20 rolled up; their snapshots are long deleted
        _day(db, date(2026, 10, 1) + timedelta(days=i))
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    for i in range(73):  # today 00:00-12:00, every 10 minutes
        db.add(Snapshot(service="citebase", ok=True, ts=now - timedelta(minutes=10 * i)))
    db.commit()
    p = ops.poll_counts(db, now)
    assert p["today"] == 73 and p["month"] == 20 * 144 + 73
    assert p["month_limit"] == 20 * 144 + 73 and p["expected_since"].startswith("2026-10-01")


def test_expected_polls_start_at_the_first_poll(db):
    from app.routes import ops
    first = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)
    db.add(Snapshot(service="citebase", ok=True, ts=first))
    db.commit()
    p = ops.poll_counts(db, first + timedelta(hours=1))
    assert p["month_limit"] == 7 and p["expected_since"].startswith("2026-10-15T06:00")
    assert ops.poll_counts(db, datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc))["expected_since"].startswith("2026-11-01")


def test_usage_page_labels():
    from pathlib import Path
    js = (Path(__file__).parents[1] / "app" / "static" / "app.js").read_text()
    assert "today (UTC, resets 05:30 IST)" in js and "tracking since" in js


def test_new_pages_and_old_urls_are_served_and_cached(db, monkeypatch):
    c = client(db, monkeypatch)
    for path in ("/insights", "/performance", "/reports", "/about", "/how", "/eval", "/usage", "/incidents"):
        r = c.get(path)
        assert r.status_code == 200 and 'id="topnav"' in r.text, path
        assert r.headers["cache-control"] == "public, s-maxage=60, stale-while-revalidate=300", path
    for path in ("/api/performance", "/api/reports"):
        assert c.get(path).headers["cache-control"].startswith("public, s-maxage=60"), path


def test_top_bar_has_four_entries_and_every_page_has_a_section():
    from pathlib import Path
    root = Path(__file__).parents[1] / "app" / "static"
    html, js = (root / "index.html").read_text(), (root / "app.js").read_text()
    nav = html[html.index('id="topnav"'):html.index("</nav>", html.index('id="topnav"'))]
    assert [t for t in ("Live", "Incidents", "Insights", "About") if f">{t}</a>" in nav] == ["Live", "Incidents", "Insights", "About"]
    assert nav.count("<a ") == 4
    for section in ("live", "incidents", "insights", "performance", "reports", "health", "usage", "eval", "about"):
        assert f'<section id="{section}"' in html and f"{section}:" in js
    assert "prefers-reduced-motion" in (root / "app.css").read_text()
