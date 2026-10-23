"""AI providers for triage, in the order they are tried: Groq, then a backup
(Gemini or Anthropic, whichever has a key; both if both do), then the rules.

The orchestrator speaks OpenAI-style chat completions, which Groq serves
natively. The backups get small adapters exposing the same
`client.chat.completions.create(...)` surface and reply shape:
- Gemini through its OpenAI-compatible endpoint (plain httpx);
- Anthropic through the official SDK's Messages API, translating messages
  and tools both ways.
Errors keep `status_code` and `response.headers`, so the orchestrator's
rate-limit handling works the same for every provider.
"""
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

import httpx

from app.agent.orchestrator import _client as groq_client
from app.core.config import get_settings


@dataclass
class Provider:
    name: str
    model: str | None
    make_client: Callable[[], object]


def chain(primary_client=None) -> list[Provider]:
    """Providers to try, primary first. `primary_client` stands in for Groq (tests, eval)."""
    s = get_settings()
    providers = [Provider("groq", s.groq_model, (lambda: primary_client) if primary_client else groq_client)]
    if s.gemini_api_key:
        providers.append(Provider("gemini", s.gemini_model, lambda: GeminiClient(s.gemini_api_key)))
    if s.anthropic_api_key:
        providers.append(Provider("anthropic", s.anthropic_model, lambda: AnthropicClient(s.anthropic_api_key)))
    return providers


class ProviderError(Exception):
    def __init__(self, status_code: int, response: httpx.Response, message: str):
        super().__init__(f"Error code: {status_code} - {message}")
        self.status_code, self.response = status_code, response


def _reply(content: str | None, tool_calls: list, prompt_tokens: int, completion_tokens: int):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                           usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens))


def _tool_call(id_: str, name: str, arguments: str):
    return SimpleNamespace(id=id_, type="function", function=SimpleNamespace(name=name, arguments=arguments))


class _Completions:
    def __init__(self, create):
        self.create = create


class GeminiClient:
    """Gemini's OpenAI-compatible chat completions endpoint."""
    URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

    def __init__(self, api_key: str, http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=45)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._raw_calls: list[list[dict]] = []  # tool calls as returned, in order (they carry thought signatures)
        self.chat = SimpleNamespace(completions=_Completions(self._create))

    def _create(self, *, model, messages, tools, tool_choice, max_tokens, temperature=None, **_):
        sent, k = [], 0
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                m = {**m, "tool_calls": self._raw_calls[k] if k < len(self._raw_calls) else m["tool_calls"]}
                k += 1
            sent.append(m)
        body = {"model": model, "messages": sent, "tools": tools, "max_tokens": max_tokens,
                # Only `decide` is offered when the orchestrator forces it, so "required" is the same constraint.
                "tool_choice": "required" if isinstance(tool_choice, dict) else tool_choice}
        if temperature is not None:
            body["temperature"] = temperature
        r = self._http.post(self.URL, headers=self._headers, json=body)
        if r.status_code >= 400:
            raise ProviderError(r.status_code, r, r.text[:300])
        data = r.json()
        msg = data["choices"][0]["message"]
        raw = msg.get("tool_calls") or []
        if raw:
            self._raw_calls.append(raw)
        usage = data.get("usage") or {}
        return _reply(msg.get("content"), [_tool_call(c["id"], c["function"]["name"], c["function"].get("arguments") or "{}")
                                           for c in raw],
                      usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))


class AnthropicClient:
    """Claude through the Messages API, behind the chat-completions surface the orchestrator uses."""

    def __init__(self, api_key: str | None = None, sdk=None):
        if sdk is None:
            import anthropic
            sdk = anthropic.Anthropic(api_key=api_key, timeout=45, max_retries=1)
        self._sdk = sdk
        self._raw: list = []  # each reply's content blocks, in order: passed back unchanged (thinking included)
        self.chat = SimpleNamespace(completions=_Completions(self._create))

    def _convert(self, messages: list[dict]) -> tuple[str, list[dict]]:
        system, out, k = "", [], 0
        for m in messages:
            role = m["role"]
            if role == "system":
                system = m["content"]
            elif role == "assistant":
                if k < len(self._raw):
                    content = self._raw[k]
                else:
                    content = ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                        {"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                         "input": _json_or_empty(c["function"]["arguments"])} for c in m.get("tool_calls", [])]
                k += 1
                out.append({"role": "assistant", "content": content})
            elif role == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and all(b.get("type") == "tool_result" for b in out[-1]["content"]):
                    out[-1]["content"].append(block)  # all results of one turn in one user message
                else:
                    out.append({"role": "user", "content": [block]})
            else:
                out.append({"role": "user", "content": [{"type": "text", "text": m["content"]}]})
        return system, out

    def _create(self, *, model, messages, tools, tool_choice, max_tokens, **_):
        system, msgs = self._convert(messages)
        if isinstance(tool_choice, dict) and msgs and msgs[-1]["role"] == "user":
            # Forced tool choice is not accepted by current Claude models: offer only `decide` and ask for it.
            msgs[-1] = {**msgs[-1], "content": [*msgs[-1]["content"],
                                                {"type": "text", "text": "This is the last round: call `decide` now."}]}
        resp = self._sdk.messages.create(
            model=model, system=system, messages=msgs, max_tokens=max(max_tokens, 8000),
            tools=[{"name": t["function"]["name"], "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"]} for t in tools],
            tool_choice={"type": "auto"}, output_config={"effort": "low"},
        )
        self._raw.append(resp.content)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [_tool_call(b.id, b.name, json.dumps(b.input)) for b in resp.content if b.type == "tool_use"]
        return _reply(text, calls, resp.usage.input_tokens, resp.usage.output_tokens)


def _json_or_empty(arguments: str) -> dict:
    try:
        return json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {}

