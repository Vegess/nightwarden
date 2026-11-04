"""The LangGraph orchestrator behaves exactly like the loop on the same scripted model."""
import pytest

from app.agent import graph, orchestrator, runner
from app.agent.orchestrator import AgentUnavailable
from harness.scenarios import build
from tests.conftest import tool_call

DECIDE = {"action": "page", "summary": "Answers are fallbacks", "rationale": "logs show 401", "confidence": "high"}

SCRIPTS = {
    "evidence_then_decide": (1, 6, lambda: [
        [tool_call("get_recent_logs", {"minutes": 30})],
        [tool_call("get_metrics", {"metric": "nope", "minutes": 60}, 1)],
        [tool_call("decide", DECIDE, 2)]]),
    "nudge_then_forced_decide": (9, 3, lambda: [
        ("I think it's fine.", None), ("Still fine.", None), [tool_call("decide", {**DECIDE, "action": "suppress"})]]),
    "bad_args_then_decide": (5, 6, lambda: [
        [tool_call("explode", {}, 1), tool_call("get_recent_logs", "{not json", 2)],
        [tool_call("decide", {"action": "panic", "summary": "x", "rationale": "y", "confidence": "high"}, 3)],
        [tool_call("decide", DECIDE, 4)]]),
    "out_of_rounds": (12, 3, lambda: [[tool_call("get_deploy_history", {"hours": 24}, i)] for i in range(3)]),
    "one_blip_retried": (1, 6, lambda: [RuntimeError("blip"), [tool_call("decide", DECIDE)]]),
}


def _strip(calls):
    return [{k: v for k, v in c.items() if k != "duration_ms"} for c in calls]


@pytest.mark.parametrize("name", list(SCRIPTS))
def test_langgraph_matches_the_loop(name, scripted):
    scenario, rounds, script = SCRIPTS[name]
    event, evidence = build(scenario, 0)
    a_client, b_client = scripted(script()), scripted(script())
    a = orchestrator.triage(event, evidence, client=a_client, max_rounds=rounds)
    b = graph.triage(event, evidence, client=b_client, max_rounds=rounds)
    assert (a.action, a.confidence, a.rounds, a.inconclusive, a.tokens_in, a.tokens_out) == \
           (b.action, b.confidence, b.rounds, b.inconclusive, b.tokens_in, b.tokens_out)
    assert _strip(a.tool_calls) == _strip(b.tool_calls)
    assert all(isinstance(c["duration_ms"], int) for c in b.tool_calls)
    assert [r["messages"] for r in a_client.requests] == [r["messages"] for r in b_client.requests]
    assert [r["tool_choice"] for r in a_client.requests] == [r["tool_choice"] for r in b_client.requests]


def test_langgraph_raises_for_the_degraded_path(scripted):
    event, evidence = build(1, 0)
    with pytest.raises(AgentUnavailable):
        graph.triage(event, evidence, client=scripted([RuntimeError("503"), RuntimeError("503")]), max_rounds=6)


def test_orchestrator_setting_picks_the_implementation(scripted, monkeypatch):
    event, evidence = build(1, 0)
    used = []
    monkeypatch.setattr(graph, "triage", lambda *a, **k: used.append("langgraph"))
    monkeypatch.setattr(orchestrator, "triage", lambda *a, **k: used.append("loop"))
    runner.triage(event, evidence, client=None)
    monkeypatch.setattr(runner.get_settings(), "orchestrator", "langgraph")
    runner.triage(event, evidence, client=None)
    runner.triage(event, evidence, client=None, orchestrator="loop")
    assert used == ["loop", "langgraph", "loop"]
    with pytest.raises(ValueError):
        runner.triage(event, evidence, orchestrator="crew")
