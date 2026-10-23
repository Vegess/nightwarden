"""Provider chain: Groq -> backup AI (Gemini / Anthropic) -> rules, and the backup adapters."""
import json
from types import SimpleNamespace

import httpx

from app.agent import providers
from app.agent.orchestrator import triage
from app.agent.providers import AnthropicClient, GeminiClient, Provider
from app.db.models import Decision, Event
from app.routes.api import decided_by
from app.services import triage as triage_service
from harness.scenarios import build
from tests.conftest import tool_call

DECIDE = {"action": "page", "summary": "Answers are fallbacks", "rationale": "logs show 401", "confidence": "high"}


def _event(db):
    ev = Event(service="citebase", kind="fallback_spike", detail="80% fallback",
               metrics=json.dumps({"fallback_rate": 0.8, "error_rate": 0.0}), source="manual")
    db.add(ev)
    db.commit()
    return ev


def _chain(monkeypatch, *clients):
    names = ["groq", "gemini", "anthropic"]
    monkeypatch.setattr(triage_service, "chain", lambda client=None: [
        Provider(names[i], None, (lambda c=c: c)) for i, c in enumerate(clients)])


def _evidence():
    return build(1, 0)[1]


def test_primary_decides_not_degraded(db, scripted, monkeypatch):
    _chain(monkeypatch, scripted([[tool_call("decide", DECIDE)]]), scripted([]))
    d = triage_service.run(db, _event(db), _evidence())
    assert (d.triager, d.provider, d.degraded, decided_by(d)) == ("agent", "groq", False, "groq")


def test_backup_provider_decides_when_groq_fails_and_is_marked_degraded(db, scripted, monkeypatch, quiet_alerts):
    groq = scripted([RuntimeError("503"), RuntimeError("503")])
    gemini = scripted([[tool_call("decide", DECIDE)]])
    _chain(monkeypatch, groq, gemini)
    d = triage_service.run(db, _event(db), _evidence())
    assert (d.triager, d.provider, d.degraded, d.action, decided_by(d)) == ("agent", "gemini", True, "page", "gemini")
    assert ("page", "citebase: Answers are fallbacks") in quiet_alerts


def test_rules_decide_when_every_provider_fails(db, scripted, monkeypatch, quiet_alerts):
    _chain(monkeypatch, scripted([RuntimeError("503")] * 2), scripted([RuntimeError("500")] * 2))
    d = triage_service.run(db, _event(db), _evidence())
    assert (d.triager, d.provider, d.degraded, d.action, decided_by(d)) == ("degraded", "rules", True, "page", "rules")
    assert db.get(Decision, d.id).provider == "rules"


def test_chain_order_follows_the_configured_keys(monkeypatch):
    s = providers.get_settings()
    assert [p.name for p in providers.chain()] == ["groq"]
    monkeypatch.setattr(s, "anthropic_api_key", "a")
    monkeypatch.setattr(s, "gemini_api_key", "g")
    assert [p.name for p in providers.chain()] == ["groq", "gemini", "anthropic"]
    assert providers.chain()[2].model == s.anthropic_model


def test_gemini_adapter_speaks_openai_compat_and_echoes_thought_signatures():
    sent = []
    sig = {"google": {"thought_signature": "abc"}}
    replies = [
        {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "extra_content": sig,
             "function": {"name": "get_recent_logs", "arguments": '{"minutes": 30}'}}]}}],
         "usage": {"prompt_tokens": 50, "completion_tokens": 5}},
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c2", "type": "function", "function": {"name": "decide", "arguments": json.dumps(DECIDE)}}]}}]},
    ]

    def handler(request):
        sent.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json=replies[len(sent) - 1])

    client = GeminiClient("k", http=httpx.Client(transport=httpx.MockTransport(handler)))
    event, evidence = build(1, 0)
    r = triage(event, evidence, client=client, model="gemini-test", max_rounds=2)
    assert r.action == "page" and [t["tool"] for t in r.tool_calls] == ["get_recent_logs", "decide"]
    assert sent[1]["tool_choice"] == "required" and len(sent[1]["tools"]) == 1  # last round: decide only
    assistant = [m for m in sent[1]["messages"] if m["role"] == "assistant"][0]
    assert assistant["tool_calls"][0]["extra_content"] == sig


def test_gemini_rate_limit_keeps_status_and_retry_after(monkeypatch):
    from app.agent import orchestrator
    waits = []
    monkeypatch.setattr(orchestrator, "sleep", waits.append)
    calls = iter([httpx.Response(429, headers={"retry-after": "2"}, text="slow down"),
                  httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
                      {"id": "c", "type": "function", "function": {"name": "decide", "arguments": json.dumps(DECIDE)}}]}}]})])
    client = GeminiClient("k", http=httpx.Client(transport=httpx.MockTransport(lambda req: next(calls))))
    event, evidence = build(1, 0)
    assert triage(event, evidence, client=client, max_rounds=3).action == "page" and waits == [2.5]


class FakeAnthropic:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.requests.append(json.loads(json.dumps(kw, default=lambda o: o.__dict__)))
        content = self.replies.pop(0)
        return SimpleNamespace(content=content, usage=SimpleNamespace(input_tokens=70, output_tokens=7))


def _block(**kw):
    return SimpleNamespace(**kw)


def test_anthropic_adapter_translates_tools_results_and_keeps_thinking_blocks():
    thinking = _block(type="thinking", thinking="", signature="sig")
    sdk = FakeAnthropic([
        [thinking, _block(type="tool_use", id="t1", name="get_recent_logs", input={"minutes": 30}),
         _block(type="tool_use", id="t2", name="get_deploy_history", input={"hours": 24})],
        [_block(type="text", text="done"), _block(type="tool_use", id="t3", name="decide", input=DECIDE)],
    ])
    event, evidence = build(1, 0)
    r = triage(event, evidence, client=AnthropicClient(sdk=sdk), model="claude-test", max_rounds=2)
    assert r.action == "page" and r.tokens_in == 140
    first, last = sdk.requests
    assert first["system"].startswith("You are an on-call triage agent")
    assert first["tools"][0]["input_schema"]["required"] == ["minutes"] and first["tool_choice"] == {"type": "auto"}
    assert "temperature" not in first
    assistant = last["messages"][1]
    assert assistant["role"] == "assistant" and assistant["content"][0]["type"] == "thinking"  # passed back unchanged
    results = last["messages"][2]["content"]
    assert [b["tool_use_id"] for b in results if b["type"] == "tool_result"] == ["t1", "t2"]  # one user message
    assert results[-1] == {"type": "text", "text": "This is the last round: call `decide` now."}
    assert [t["name"] for t in last["tools"]] == ["decide"]
