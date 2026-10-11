from app.agent.baseline import rules_triage
from harness.scenarios import LABELS, build, cases


def test_sixty_cases_twelve_scenarios_deterministic():
    rows = list(cases())
    assert len(rows) == 60 and len({r["scenario"] for r in rows}) == 12
    assert build(7, 3)[0] == build(7, 3)[0]


def test_no_label_or_scenario_name_leaks_into_what_the_agent_sees():
    import json
    for c in cases():
        ev = c["evidence"]
        visible = json.dumps(c["event"]) + json.dumps(ev.recent_logs(180)) + json.dumps(ev.deploys(168)) \
            + json.dumps(ev.past_incidents("", 50))
        for meta in LABELS.values():
            assert meta["name"] not in visible
        assert "\"expected\"" not in visible and "\"scenario\"" not in visible


def test_baseline_is_frozen_and_misses_silent_failures():
    silent = [c for c in cases() if c["silent"]]
    assert all(rules_triage(c["event"]) != c["expected"] for c in silent)


def test_fixture_tools_filter_like_live_ones():
    _, ev = build(5, 0)
    assert all(r["level"] == "ERROR" for r in ev.recent_logs(60, "ERROR", None))
    assert ev.recent_logs(60, None, "UndefinedColumn")
    assert ev.deploys(1) and ev.deploys(1)[0]["minutes_ago"] <= 9
    assert all(p["minutes_ago"] <= 15 for p in ev.metric_series("error_rate", 15))
