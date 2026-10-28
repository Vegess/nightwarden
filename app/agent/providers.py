"""AI providers for triage, in the order they are tried: Groq, then the backup
(BACKUP_LLM_PROVIDER, Gemini by default, when BACKUP_LLM_API_KEY is set), then the rules.

The orchestrator speaks OpenAI-style chat completions, which Groq serves
natively. The backups get small httpx adapters exposing the same
`client.chat.completions.create(...)` surface and reply shape:
- Gemini through its OpenAI-compatible endpoint;
- Anthropic through the Messages API, translating messages and tools both ways.
Errors keep `status_code` and `response.headers`, so the orchestrator's
rate-limit handling and short_error work the same for every provider.
"""
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

import httpx

from app.agent.orchestrator import _client as groq_client, short_error
from app.core.config import get_settings

BACKUPS = ("gemini", "anthropic")
DEFAULT_MODEL = {"gemini": "gemini-2.5-flash", "anthropic": "claude-haiku-5-5"}
LABEL = {"groq": "Groq", "gemini": "Gemini", "anthropic": "Anthropic"}


@dataclass
class Provider:
    name: str
    model: str | None
    make_client: Callable[[], object]

    @property
    def label(self) -> str:
        return LABEL.get(self.name, self.name)


def backup() -> Provider | None:
    s = get_settings()
    if not s.backup_llm_api_key:
        return None
    name = s.backup_llm_provider
    model = s.backup_llm_model or DEFAULT_MODEL[name]
    make = GeminiClient if name == "gemini" else AnthropicClient
    return Provider(name, model, lambda: make(s.backup_llm_api_key))


def chain(primary_client=None) -> list[Provider]:
    """Providers to try, primary first. `primary_client` stands in for Groq (tests, eval)."""
    s = get_settings()
    providers = [Provider("groq", s.groq_model, (lambda: primary_client) if primary_client else groq_client)]
    b = backup()
    return providers + [b] if b else providers


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


def _post(http: httpx.Client, url: str, headers: dict, body: dict) -> dict:
    r = http.post(url, headers=headers, json=body)
    if r.status_code >= 400:
        raise ProviderError(r.status_code, r, r.text[:300])
    return r.json()


class GeminiClient:
    """Gemini's OpenAI-compatible chat completions endpoint."""
    URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

    def __init__(self, api_key: str, http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=45)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._raw_calls: list[list[dict]] = []  # tool calls as returned, in order (they carry thought signatures)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, *, model, messages, tools=None, tool_choice=None, max_tokens, temperature=None, **_):
        sent, k = [], 0
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                m = {**m, "tool_calls": self._raw_calls[k] if k < len(self._raw_calls) else m["tool_calls"]}
                k += 1
            sent.append(m)
        body = {"model": model, "messages": sent, "max_tokens": max_tokens}
        if tools:
            # Only `decide` is offered when the orchestrator forces it, so "required" is the same constraint.
            body.update(tools=tools, tool_choice="required" if isinstance(tool_choice, dict) else tool_choice or "auto")
        if temperature is not None:
            body["temperature"] = temperature
        data = _post(self._http, self.URL, self._headers, body)
        msg = data["choices"][0]["message"]
        raw = msg.get("tool_calls") or []
        if raw:
            self._raw_calls.append(raw)
        usage = data.get("usage") or {}
        return _reply(msg.get("content"), [_tool_call(c["id"], c["function"]["name"], c["function"].get("arguments") or "{}")
                                           for c in raw],
                      usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))


class AnthropicClient:
    """Claude through the Messages API (plain httpx), behind the chat-completions surface."""
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=45)
        self._headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        self._raw: list[list[dict]] = []  # each reply's content blocks, passed back unchanged (thinking included)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

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
                if out and out[-1]["role"] == "user" and all(b.get("type") == "tool_result" for b in out[-1]["content"]):
                    out[-1]["content"].append(block)  # all results of one turn in one user message
                else:
                    out.append({"role": "user", "content": [block]})
            else:
                out.append({"role": "user", "content": [{"type": "text", "text": m["content"]}]})
        return system, out

    def _create(self, *, model, messages, tools=None, tool_choice=None, max_tokens, **_):
        system, msgs = self._convert(messages)
        if isinstance(tool_choice, dict) and msgs and msgs[-1]["role"] == "user":
            msgs[-1] = {**msgs[-1], "content": [*msgs[-1]["content"],
                                                {"type": "text", "text": "This is the last round: call `decide` now."}]}
        body = {"model": model, "messages": msgs, "max_tokens": max_tokens}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["function"]["name"], "description": t["function"]["description"],
                              "input_schema": t["function"]["parameters"]} for t in tools]
            body["tool_choice"] = {"type": "auto"}
        data = _post(self._http, self.URL, self._headers, body)
        content = data.get("content") or []
        self._raw.append(content)
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        calls = [_tool_call(b["id"], b["name"], json.dumps(b.get("input") or {})) for b in content if b.get("type") == "tool_use"]
        usage = data.get("usage") or {}
        return _reply(text, calls, usage.get("input_tokens", 0), usage.get("output_tokens", 0))


def _json_or_empty(arguments: str) -> dict:
    try:
        return json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {}


def ping(provider: Provider) -> tuple[bool, str | None]:
    """A 1-token call: is the backup reachable with this key? Returns (ok, short error)."""
    try:
        provider.make_client().chat.completions.create(
            model=provider.model, messages=[{"role": "user", "content": "ping"}], max_tokens=1)
        return True, None
    except Exception as exc:  # noqa: BLE001 — any failure is a failed check, said briefly
        return False, short_error(exc, provider.label)
