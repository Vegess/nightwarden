"""The triage loop with a scripted model: evidence first, then exactly one decision."""
import pytest

from app.agent.orchestrator import AgentUnavailable, triage
from harness.scenarios import build
from tests.conftest import tool_call

DECIDE = {"action": "page", "summary": "Answers are fallbacks", "rationale": "logs show 401", "confidence": "high"}


def test_gathers_evidence_then_decides(scripted):
    event, evidence = build(1, 0)
    client = scripted([
        [tool_call("get_recent_logs", {"minutes": 30})],
        [tool_call("get_metrics", {"metric": "fallback_rate", "minutes": 60}, 1)],
        [tool_call("decide", DECIDE)],
    ])
    r = triage(event, evidence, client=client, max_rounds=6)
    assert r.action == "page" and r.rounds == 3 and [t["tool"] for t in r.tool_calls] == ["get_recent_logs", "get_metrics", "decide"]
    tool_msgs = [m for m in client.requests[-1]["messages"] if m["role"] == "tool"]
    assert "Invalid API Key" in tool_msgs[0]["content"]  # the model really saw the evidence
    assert r.tokens_in == 300 and r.tokens_out == 60


def test_text_without_tools_gets_a_nudge_and_last_round_forces_decide(scripted):
    event, evidence = build(9, 0)
    client = scripted([("I think it's fine.", None), ("Still fine.", None), [tool_call("decide", {**DECIDE, "action": "suppress"})]])
    r = triage(event, evidence, client=client, max_rounds=3)
    assert r.action == "suppress"
    assert client.requests[1]["messages"][-1]["content"].startswith("Use a tool")
    assert client.requests[2]["tool_choice"] == {"type": "function", "function": {"name": "decide"}}


def test_bad_arguments_and_unknown_tools_are_reported_to_the_model(scripted):
    event, evidence = build(5, 0)
    client = scripted([
        [tool_call("get_metrics", {"metric": "cpu", "minutes": 30})],
        [tool_call("explode", {}, 1), tool_call("get_recent_logs", "{not json", 2)],
        [tool_call("decide", {"action": "panic", "summary": "x", "rationale": "y", "confidence": "high"}, 3)],
        [tool_call("decide", DECIDE, 4)],
    ])
    r = triage(event, evidence, client=client, max_rounds=6)
    assert r.action == "page"
    contents = [m["content"] for m in client.requests[-1]["messages"] if m["role"] == "tool"]
    assert "unknown metric" in contents[0] and "unknown tool" in contents[1] and "not valid JSON" in contents[2]
    assert "action must be one of" in contents[3]


def test_running_out_of_rounds_is_loud_not_silent(scripted):
    event, evidence = build(12, 0)
    client = scripted([[tool_call("get_deploy_history", {"hours": 24}, i)] for i in range(3)])
    r = triage(event, evidence, client=client, max_rounds=3)
    assert r.inconclusive and r.action == "notify" and r.confidence == "low" and "inconclusive" in r.summary.lower()


def test_provider_failures_raise_for_the_degraded_path(scripted):
    event, evidence = build(1, 0)
    with pytest.raises(AgentUnavailable):
        triage(event, evidence, client=scripted([RuntimeError("503"), RuntimeError("503")]), max_rounds=6)
    # one blip is retried
    r = triage(event, evidence, client=scripted([RuntimeError("blip"), [tool_call("decide", DECIDE)]]), max_rounds=6)
    assert r.action == "page"


def test_no_key_means_unavailable(monkeypatch):
    from app.agent import orchestrator
    monkeypatch.setattr(orchestrator.settings, "groq_api_key", None)
    event, evidence = build(1, 0)
    with pytest.raises(AgentUnavailable):
        triage(event, evidence)


def test_prompt_contains_no_signal_to_action_mapping():
    from app.agent.prompts import SYSTEM_PROMPT
    for kind in ("fallback", "zero_chunks", "429", "traffic_drop", "error_spike"):
        assert kind not in SYSTEM_PROMPT


class _RateLimited(Exception):
    status_code = 429

    def __init__(self, msg="Rate limit reached ... Please try again in 2.385s."):
        super().__init__(msg)
        self.response = None


def test_per_minute_rate_limit_waits_and_keeps_the_round(scripted, monkeypatch):
    from app.agent import orchestrator
    from tests.conftest import tool_call
    waits = []
    monkeypatch.setattr(orchestrator, "sleep", waits.append)
    client = scripted([_RateLimited(), _RateLimited(),
                       [tool_call("decide", {"action": "notify", "confidence": "medium",
                                             "summary": "s", "rationale": "r"})]])
    r = orchestrator.triage({"kind": "error_spike", "detail": "x", "metrics": {}}, build(1, 0)[1], client=client, max_rounds=2)
    assert r.action == "notify" and r.rounds == 1
    assert [round(w, 1) for w in waits] == [2.9, 2.9]


def test_daily_cap_is_not_waited_out(scripted, monkeypatch):
    import pytest
    from app.agent import orchestrator
    monkeypatch.setattr(orchestrator, "sleep", lambda s: pytest.fail("should not sleep"))
    client = scripted([_RateLimited("tokens per day (TPD) ... Please try again in 12m30.5s.")])
    with pytest.raises(orchestrator.AgentUnavailable):
        orchestrator.triage({"kind": "error_spike", "detail": "x", "metrics": {}}, build(1, 0)[1], client=client)


def test_get_metrics_adds_the_window_median_for_context():
    from app.agent.tools import run_tool
    _, evidence = build(12, 0)
    out = run_tool(evidence, "get_metrics", {"metric": "latency_p95_ms", "minutes": 60})
    vals = sorted(p["value"] for p in out["points"])
    assert out["window_min"] == vals[0] and out["window_max"] == vals[-1]
    assert out["latest"] == out["points"][-1]["value"] and out["latest_vs_median"] > 0


def test_last_round_offers_only_decide(scripted):
    event, evidence = build(1, 0)
    client = scripted([[tool_call("get_recent_logs", {"minutes": 30})], [tool_call("decide", DECIDE, 1)]])
    triage(event, evidence, client=client, max_rounds=2)
    assert len(client.requests[0]["tools"]) == 5
    assert [t["function"]["name"] for t in client.requests[-1]["tools"]] == ["decide"]


def test_discord_alert_is_an_embed_and_only_pages_mention(monkeypatch):
    from app.services import notify
    posted = []

    class R:
        def raise_for_status(self): pass
    monkeypatch.setattr(notify.settings, "notify_webhook_url", "https://discord.com/api/webhooks/1/x")
    monkeypatch.setattr(notify.httpx, "post", lambda url, json, timeout: posted.append(json) or R())
    notify._webhook("t", "page", "citebase: fallbacks", "logs show 401", "https://nw/#incidents/1")
    notify._webhook("t", "notify", "citebase: 429s", "rate limited", None)
    page, note = posted
    assert page["embeds"][0]["url"].endswith("/#incidents/1") and page["content"] == "@here"
    assert note["content"] is None and note["allowed_mentions"] == {"parse": []}
