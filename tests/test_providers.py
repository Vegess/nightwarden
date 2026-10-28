"""Provider chain: Groq -> backup AI (BACKUP_LLM_PROVIDER) -> rules; the httpx adapters; the backup check."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.agent import providers
from app.agent.orchestrator import short_error, triage
from app.agent.providers import AnthropicClient, GeminiClient, Provider
from app.db.models import Decision, Event, PollerState
from app.routes.api import decided_by
from app.services import triage as triage_service
from harness.scenarios import build
from tests.conftest import tool_call

DECIDE = {"action": "page", "summary": "Answers are fallbacks", "rationale": "logs show 401", "confidence": "high"}
T0 = datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)


class StatusError(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status_code, self.response = status, None


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    from app.agent import orchestrator
    monkeypatch.setattr(orchestrator, "sleep", lambda s: None)


def _event(db):
    ev = Event(service="citebase", kind="fallback_spike", detail="80% fallback",
               metrics=json.dumps({"fallback_rate": 0.8, "error_rate": 0.0}), source="manual")
    db.add(ev)
    db.commit()
    return ev


def _chain(monkeypatch, *clients):
    names = ["groq", "gemini"]
    monkeypatch.setattr(triage_service, "chain", lambda client=None: [
        Provider(names[i], None, (lambda c=c: c)) for i, c in enumerate(clients)])


def _backup_settings(monkeypatch, provider="gemini", key="bk"):
    s = providers.get_settings()
    monkeypatch.setattr(s, "backup_llm_api_key", key)
    monkeypatch.setattr(s, "backup_llm_provider", provider)
    monkeypatch.setattr(s, "backup_llm_model", None)


def test_primary_decides_not_degraded(db, scripted, monkeypatch):
    _chain(monkeypatch, scripted([[tool_call("decide", DECIDE)]]), scripted([]))
    d = triage_service.run(db, _event(db), build(1, 0)[1])
    assert (d.triager, d.provider, d.degraded, decided_by(d)) == ("agent", "groq", False, "groq")


def test_backup_decides_when_groq_fails_and_is_marked_degraded(db, scripted, monkeypatch, quiet_alerts):
    groq = scripted([StatusError(429, "Rate limit reached in organization `org_01kz9` on tokens per day (TPD). "
                                      "Please try again in 7m12.5s.")])
    _chain(monkeypatch, groq, scripted([[tool_call("decide", DECIDE)]]))
    d = triage_service.run(db, _event(db), build(1, 0)[1])
    assert (d.triager, d.provider, d.degraded, d.action, decided_by(d)) == ("agent", "gemini", True, "page", "gemini")
    assert ("page", "citebase: Answers are fallbacks") in quiet_alerts


def test_rules_decide_when_every_provider_fails_with_short_errors(db, scripted, monkeypatch, quiet_alerts):
    monkeypatch.setattr(triage_service, "_last_degraded_notice", None)
    groq = scripted([StatusError(429, "Rate limit reached in organization `org_01kz912k7peb` on tokens per day "
                                      "(TPD). Please try again in 7m12.5s.")])
    gemini = scripted([StatusError(503, "upstream busy, request id req_abc123")] * 2)
    _chain(monkeypatch, groq, gemini)
    d = triage_service.run(db, _event(db), build(1, 0)[1])
    assert (d.triager, d.provider, d.degraded, d.action, decided_by(d)) == ("degraded", "rules", True, "page", "rules")
    assert "org_" not in d.rationale and "req_" not in d.rationale
    assert "Groq rate limit (429, tokens per day)" in d.rationale and "Gemini error (503)" in d.rationale
    assert db.get(Decision, d.id).provider == "rules"
    notice = [t for lvl, t in quiet_alerts if lvl == "notify"]
    assert notice == ["Nightwarden is triaging without AI"]


def test_missing_groq_key_falls_through_to_the_backup(db, scripted, monkeypatch):
    s = providers.get_settings()
    monkeypatch.setattr(s, "groq_api_key", None)
    _backup_settings(monkeypatch)
    backup = scripted([[tool_call("decide", DECIDE)]])
    monkeypatch.setattr(providers, "GeminiClient", lambda key: backup)
    d = triage_service.run(db, _event(db), build(1, 0)[1])
    assert (d.provider, d.degraded) == ("gemini", True)


def test_chain_uses_backup_settings(monkeypatch):
    assert [p.name for p in providers.chain()] == ["groq"]
    _backup_settings(monkeypatch)
    assert [(p.name, p.model) for p in providers.chain()] == [("groq", providers.get_settings().groq_model),
                                                              ("gemini", "gemini-2.5-flash")]
    _backup_settings(monkeypatch, provider="anthropic")
    b = providers.chain()[1]
    assert (b.name, b.model, b.label) == ("anthropic", "claude-haiku-5-5", "Anthropic")


def test_short_error_names_the_provider_and_drops_ids():
    assert short_error(StatusError(401, "Invalid API Key for org_123"), "Gemini") == "Gemini auth error (401)"
    assert short_error(TimeoutError(), "Anthropic") == "Anthropic timed out"


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
    r = triage(event, evidence, client=client, model="gemini-test", max_rounds=2, provider="Gemini")
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


def test_anthropic_adapter_translates_tools_results_and_keeps_thinking_blocks():
    sent = []
    replies = [
        {"content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                     {"type": "tool_use", "id": "t1", "name": "get_recent_logs", "input": {"minutes": 30}},
                     {"type": "tool_use", "id": "t2", "name": "get_deploy_history", "input": {"hours": 24}}],
         "usage": {"input_tokens": 70, "output_tokens": 7}},
        {"content": [{"type": "text", "text": "done"}, {"type": "tool_use", "id": "t3", "name": "decide", "input": DECIDE}],
         "usage": {"input_tokens": 70, "output_tokens": 7}},
    ]

    def handler(request):
        sent.append(json.loads(request.content))
        assert request.headers["x-api-key"] == "k" and request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(200, json=replies[len(sent) - 1])

    client = AnthropicClient("k", http=httpx.Client(transport=httpx.MockTransport(handler)))
    event, evidence = build(1, 0)
    r = triage(event, evidence, client=client, model="claude-test", max_rounds=2, provider="Anthropic")
    assert r.action == "page" and r.tokens_in == 140
    first, last = sent
    assert first["system"].startswith("You are an on-call triage agent")
    assert first["tools"][0]["input_schema"]["required"] == ["minutes"] and first["tool_choice"] == {"type": "auto"}
    assistant = last["messages"][1]
    assert assistant["role"] == "assistant" and assistant["content"][0]["type"] == "thinking"  # passed back unchanged
    results = last["messages"][2]["content"]
    assert [b["tool_use_id"] for b in results if b["type"] == "tool_result"] == ["t1", "t2"]  # one user message
    assert results[-1] == {"type": "text", "text": "This is the last round: call `decide` now."}
    assert [t["name"] for t in last["tools"]] == ["decide"]


@pytest.mark.parametrize("provider,url", [("gemini", GeminiClient.URL), ("anthropic", AnthropicClient.URL)])
def test_ping_is_a_one_token_call(monkeypatch, provider, url):
    seen = []

    def handler(request):
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"choices": [{"message": {"content": "p"}}]} if provider == "gemini"
                              else {"content": [{"type": "text", "text": "p"}]})
    real = httpx.Client
    monkeypatch.setattr(providers.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    _backup_settings(monkeypatch, provider=provider)
    assert providers.ping(providers.backup()) == (True, None)
    assert seen[0][0] == url and seen[0][1]["max_tokens"] == 1 and "tools" not in seen[0][1]


def _poll(db, t):
    from app.collector import poller
    from tests.test_collector import raw
    return poller.poll_once(db, fetch=lambda: raw(), now=t)


def _health(db, monkeypatch):
    from app.routes import ops
    from tests.test_api import client
    ops._cache.clear()
    checks = client(db, monkeypatch).get("/api/health/checks").json()["checks"]
    return next(c for c in checks if c["name"] == "Backup AI provider")


def test_backup_check_runs_from_the_poll_twice_a_day_and_drives_health(db, monkeypatch):
    pings = []
    result = [(True, None)]
    monkeypatch.setattr(providers, "ping", lambda p: pings.append(p.name) or result[0])
    assert _health(db, monkeypatch)["status"] == "warn"  # no key
    _backup_settings(monkeypatch)
    _poll(db, T0)
    _poll(db, T0 + timedelta(hours=1))
    assert pings == ["gemini"]  # not every poll
    state = db.get(PollerState, "citebase")
    assert state.backup_ok_at is not None and state.backup_error is None
    with monkeypatch.context() as m:  # the health check compares against wall-clock now
        m.setattr(state, "backup_ok_at", datetime.now(timezone.utc) - timedelta(hours=2))
        db.commit()
        h = _health(db, monkeypatch)
        assert h["status"] == "pass" and "gemini" in h["detail"]
    result[0] = (False, "Gemini auth error (401)")
    _poll(db, T0 + timedelta(hours=13))
    assert pings == ["gemini", "gemini"]
    state = db.get(PollerState, "citebase")
    state.backup_ok_at = datetime.now(timezone.utc) - timedelta(hours=25)
    db.commit()
    h = _health(db, monkeypatch)
    assert h["status"] == "fail" and "Gemini auth error (401)" in h["detail"]
    _poll(db, T0 + timedelta(hours=13, minutes=30))
    _poll(db, T0 + timedelta(hours=14, minutes=10))  # after a failure it retries hourly
    assert pings == ["gemini", "gemini", "gemini"]


def test_backup_key_set_but_never_tested_is_a_warning(db, monkeypatch):
    _backup_settings(monkeypatch)
    h = _health(db, monkeypatch)
    assert h["status"] == "warn" and "not tested yet" in h["detail"]
