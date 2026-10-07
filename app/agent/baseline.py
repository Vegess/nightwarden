"""Rules baseline for incident triage.

WRITE THIS BEFORE THE AGENT. DO NOT MODIFY IT AFTERWARDS.

This is the control arm of the experiment. It deliberately contains NO
fallback-rate rule, because a real team would not have thought to add one
before experiencing a silent failure. Weakening or strengthening this file
after the agent exists invalidates the comparison.
"""

ACTIONS = ("page", "notify", "watch", "suppress")


def rules_triage(event: dict) -> str:
    """Threshold-based triage. Returns one of ACTIONS."""
    m = event["metrics"]

    if m.get("error_rate", 0) > 0.10:
        return "page"
    if m.get("latency_p95_ms", 0) > 5000:
        return "notify"
    if m.get("error_rate", 0) > 0.02:
        return "notify"
    if m.get("traffic_ratio", 1) > 3:
        return "suppress"
    return "suppress"
