"""The triage loop: the model gathers evidence through tools until it calls
`decide`.

Hard rules (lessons from Citebase's silent outage):
- Running out of rounds never returns a quiet default: it records an
  inconclusive, low-confidence `notify` that says so.
- Any failure to reach the model raises AgentUnavailable after logging it, so
  the caller switches to the loud degraded path instead of guessing.
- A round where the model writes text instead of calling a tool gets a nudge,
  and the last round forces `decide`, so rounds aren't burned on chatter.
"""
import json
import re
import logging
import time
from dataclasses import dataclass, field

from app.agent.evidence import Evidence
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import ACTIONS, CONFIDENCE, TOOLS, run_tool, summarize_result
from app.core.config import get_settings

logger = logging.getLogger("nightwarden.agent")
settings = get_settings()


class AgentUnavailable(Exception):
    """The model could not be reached or kept failing; use the degraded path."""


@dataclass
class TriageResult:
    action: str
    confidence: str
    summary: str
    rationale: str
    tool_calls: list[dict] = field(default_factory=list)
    rounds: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    inconclusive: bool = False


def format_event(event: dict) -> str:
    m = event.get("metrics", {})
    lines = [f"Signal: {event['kind']} on service '{event.get('service', 'unknown')}'",
             f"Detector says: {event.get('detail', '')}",
             "Readings over the last 5 minutes:"]
    lines += [f"- {k}: {v}" for k, v in m.items()]
    return "\n".join(lines)


def _client():
    if not settings.groq_api_key:
        raise AgentUnavailable("GROQ_API_KEY is not set")
    from groq import Groq
    return Groq(api_key=settings.groq_api_key, timeout=45, max_retries=1)


# On the last round only `decide` is offered: forcing tool_choice while listing every tool lets the
# model still attempt another tool, which Groq rejects with a 400 (tool_use_failed).
DECIDE_ONLY = [t for t in TOOLS if t["function"]["name"] == "decide"]
RATE_LIMIT_RETRIES = 4
MAX_RATE_LIMIT_WAIT_S = 65.0
sleep = time.sleep  # patched in tests


def rate_limit_delay(exc: Exception) -> float | None:
    """Seconds to wait if `exc` is a 429, else None. Long waits mean a daily cap."""
    if getattr(exc, "status_code", None) != 429:
        return None
    resp = getattr(exc, "response", None)
    header = resp.headers.get("retry-after") if resp is not None and hasattr(resp, "headers") else None
    try:
        if header:
            return float(header) + 0.5
    except ValueError:
        pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(exc))
    if m:
        return int(m.group(1) or 0) * 60 + float(m.group(2)) + 0.5
    return 10.0


def _valid_decision(args: dict) -> str | None:
    if args.get("action") not in ACTIONS:
        return f"action must be one of {', '.join(ACTIONS)}"
    if args.get("confidence") not in CONFIDENCE:
        return f"confidence must be one of {', '.join(CONFIDENCE)}"
    if not str(args.get("summary", "")).strip() or not str(args.get("rationale", "")).strip():
        return "summary and rationale are required"
    return None


def triage(event: dict, evidence: Evidence, client=None, model: str | None = None,
           max_rounds: int | None = None) -> TriageResult:
    client = client or _client()
    model = model or settings.groq_model
    max_rounds = max_rounds or settings.agent_max_rounds
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": format_event(event)}]
    calls: list[dict] = []
    tokens_in = tokens_out = 0
    start = time.perf_counter()
    mark = start  # end of the previous step: each step's duration includes the model's thinking before it
    failures = 0

    def close_step(result=None) -> None:
        """Stamp the latest call with its duration and a public-safe result summary."""
        nonlocal mark
        now = time.perf_counter()
        calls[-1]["duration_ms"] = int((now - mark) * 1000)
        mark = now
        if result is not None:
            calls[-1]["result_summary"] = summarize_result(calls[-1]["tool"], result)

    for round_i in range(max_rounds):
        last = round_i == max_rounds - 1
        resp = None
        for waited in range(RATE_LIMIT_RETRIES + 1):
            try:
                resp = client.chat.completions.create(
                    model=model, messages=messages, tools=DECIDE_ONLY if last else TOOLS, temperature=0.2, max_tokens=1200,
                    tool_choice={"type": "function", "function": {"name": "decide"}} if last else "auto",
                )
                break
            except Exception as exc:  # noqa: BLE001 — every provider failure ends here, loudly
                delay = rate_limit_delay(exc)
                if delay is not None and delay <= MAX_RATE_LIMIT_WAIT_S and waited < RATE_LIMIT_RETRIES:
                    # A per-minute limit: wait it out and retry the same round (it isn't a failure).
                    logger.warning("Triage model rate-limited; retrying in %.1fs", delay)
                    sleep(delay)
                    continue
                failures += 1
                logger.warning("Triage model call failed (round %s): %s", round_i + 1, str(exc)[:300])
                if failures >= 2 or last or delay is not None:
                    raise AgentUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
                break
        if resp is None:
            continue
        usage = getattr(resp, "usage", None)
        tokens_in += getattr(usage, "prompt_tokens", 0) or 0
        tokens_out += getattr(usage, "completion_tokens", 0) or 0
        msg = resp.choices[0].message
        tool_calls = msg.tool_calls or []
        messages.append({
            "role": "assistant", "content": msg.content or "",
            **({"tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                               for tc in tool_calls]} if tool_calls else {}),
        })
        if not tool_calls:
            messages.append({"role": "user", "content": "Use a tool to gather evidence, or call `decide` if you have enough."})
            continue

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args, result = {}, {"error": "arguments were not valid JSON"}
            else:
                result = None
            calls.append({"round": round_i + 1, "tool": name, "args": args})

            if name == "decide" and result is None:
                problem = _valid_decision(args)
                if problem is None:
                    close_step()
                    return TriageResult(
                        action=args["action"], confidence=args["confidence"],
                        summary=str(args["summary"])[:300], rationale=str(args["rationale"])[:4000],
                        tool_calls=calls, rounds=round_i + 1, tokens_in=tokens_in, tokens_out=tokens_out,
                        latency_ms=int((time.perf_counter() - start) * 1000),
                    )
                result = {"error": problem}
            elif result is None:
                try:
                    result = run_tool(evidence, name, args)
                except Exception as exc:  # noqa: BLE001 — tell the model, don't crash the triage
                    result = {"error": f"{type(exc).__name__}: {exc}"}
            close_step(result)
            calls[-1]["result_preview"] = json.dumps(result, default=str)[:600]
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)[:6000]})

    return TriageResult(
        action="notify", confidence="low", inconclusive=True,
        summary="Triage inconclusive: the agent did not decide within its round budget",
        rationale=f"No valid decide call in {max_rounds} rounds. A person should look at this signal.",
        tool_calls=calls, rounds=max_rounds, tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=int((time.perf_counter() - start) * 1000),
    )
