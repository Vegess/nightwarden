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
                       "fallback instead of the language model; traffic_ratio compares traffic with a typical day.",
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
        return {"metric": metric, "points": evidence.metric_series(metric, int(args.get("minutes", 60)))}
    if name == "get_deploy_history":
        return {"deploys": evidence.deploys(int(args.get("hours", 24)))}
    if name == "search_past_incidents":
        return {"incidents": evidence.past_incidents(str(args.get("query", "")), int(args.get("limit", 5)))}
    raise ValueError(f"unknown tool {name!r}")
