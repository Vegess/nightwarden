"""The agent's five tools (OpenAI function-calling format, which Groq speaks).

Four gather evidence; `decide` ends the run and is never executed here: the
orchestrator intercepts it. Tool errors are returned to the model as data,
so a bad argument costs one round, not the whole triage.
"""
from app.agent.evidence import METRICS, Evidence

ACTIONS = ("page", "notify", "watch", "suppress")
CONFIDENCE = ("low", "medium", "high")

TOOLS = [
    {"type": "function", "function": {
        "name": "get_recent_logs",
        "description": "Warning and error log lines from the service. Use to see what actually went wrong "
                       "(exceptions, provider errors, fallbacks) behind a signal.",
        "parameters": {"type": "object", "properties": {
            "minutes": {"type": "integer", "minimum": 1, "maximum": 180, "description": "How far back to look."},
            "level": {"type": "string", "enum": ["WARNING", "ERROR"], "description": "Minimum level (optional)."},
            "contains": {"type": "string", "description": "Only lines containing this text (optional)."},
        }, "required": ["minutes"]}}},
    {"type": "function", "function": {
        "name": "get_metrics",
        "description": "A metric over time, oldest first, one point per poll. error_rate counts 5xx only; "
                       "rate_4xx includes 429; fallback_rate is the share of answers produced by the degraded "
                       "fallback instead of the language model; traffic_ratio compares traffic with a typical day. "
                       "Also returns the window's median, so the latest value can be judged against this "
                       "service's own normal rather than an absolute threshold.",
        "parameters": {"type": "object", "properties": {
            "metric": {"type": "string", "enum": list(METRICS)},
            "minutes": {"type": "integer", "minimum": 5, "maximum": 1440},
        }, "required": ["metric", "minutes"]}}},
    {"type": "function", "function": {
        "name": "get_deploy_history",
        "description": "Deploys of the service in the last N hours, newest first.",
        "parameters": {"type": "object", "properties": {
            "hours": {"type": "integer", "minimum": 1, "maximum": 168},
        }, "required": ["hours"]}}},
    {"type": "function", "function": {
        "name": "search_past_incidents",
        "description": "Earlier incidents matching words in the query, newest first. Use to tell a new "
                       "problem from a recurring one.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "decide",
        "description": "Your final triage decision. Call exactly once, after gathering evidence.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "summary": {"type": "string", "description": "One line a human reads first (max 200 characters)."},
            "rationale": {"type": "string", "description": "Cite the specific tool outputs behind the action."},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        }, "required": ["action", "summary", "rationale", "confidence"]}}},
]


def _stats(points: list[dict]) -> dict:
    """Context for judging the latest value against this service's own recent normal."""
    vals = sorted(p["value"] for p in points if isinstance(p.get("value"), (int, float)))
    if not vals:
        return {}
    latest = next(p["value"] for p in reversed(points) if isinstance(p.get("value"), (int, float)))
    median = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    return {"latest": latest, "window_median": round(median, 4), "window_min": vals[0], "window_max": vals[-1],
            "latest_vs_median": round(latest / median, 2) if median else None}


def run_tool(evidence: Evidence, name: str, args: dict):
    if name == "get_recent_logs":
        minutes = int(args.get("minutes", 30))
        if not 1 <= minutes <= 180:
            raise ValueError("minutes must be between 1 and 180")
        rows = evidence.recent_logs(minutes, args.get("level"), args.get("contains"))
        return {"count": len(rows), "lines": rows[-25:]}
    if name == "get_metrics":
        metric = args.get("metric")
        if metric not in METRICS:
            raise ValueError(f"unknown metric {metric!r}; choose from {', '.join(METRICS)}")
        points = evidence.metric_series(metric, int(args.get("minutes", 60)))
        return {"metric": metric, "points": points, **_stats(points)}
    if name == "get_deploy_history":
        return {"deploys": evidence.deploys(int(args.get("hours", 24)))}
    if name == "search_past_incidents":
        return {"incidents": evidence.past_incidents(str(args.get("query", "")), int(args.get("limit", 5)))}
    raise ValueError(f"unknown tool {name!r}")


def summarize_result(name: str, result) -> str:
    """One short, public-safe line about a tool result: counts and numbers, never log text."""
    if not isinstance(result, dict):
        return "no result"
    if "error" in result:
        return "error: " + str(result["error"])[:80]
    if name == "get_recent_logs":
        n = result.get("count", len(result.get("lines") or []))
        return f"{n} log line{'' if n == 1 else 's'}"
    if name == "get_metrics":
        n = len(result.get("points") or [])
        if not n:
            return f"no {result.get('metric', 'metric')} data"
        out = f"{n} point{'' if n == 1 else 's'} · latest {result.get('latest')}"
        if result.get("window_median") is not None:
            out += f" (median {result['window_median']})"
        return out
    if name == "get_deploy_history":
        n = len(result.get("deploys") or [])
        return f"{n} deploy{'' if n == 1 else 's'}" if n else "no recent deploys"
    if name == "search_past_incidents":
        n = len(result.get("incidents") or [])
        return f"{n} past incident{'' if n == 1 else 's'}" if n else "no matching past incidents"
    return "done"
