"""The 12 fault scenarios (docs/NIGHTWARDEN.md), each generated 5 times with
seeded variation -> 60 labelled cases.

Each case is what Nightwarden would hold at the moment a detector fires:
the event (kind, detector text, 5-minute readings) plus the evidence its
tools can reach (an hour of metrics, warning/error logs, deploys, past
incidents). Scenario names and labels never appear in that data.

Expected actions live in labels.yaml, written before the agent existed and
never revised.
"""
import random
from pathlib import Path

import yaml

from harness.fixture import FixtureEvidence

LABELS = yaml.safe_load((Path(__file__).parent / "labels.yaml").read_text())["scenarios"]
POINTS = 12  # one hour at 5-minute resolution
NOISE = [
    ("WARNING", "app.routes.documents", "Slow upload: 2.4 MB PDF took 3100 ms to extract"),
    ("WARNING", "app.services.jobs", "Job summarize_document retry 1/3 scheduled in 30s"),
    ("WARNING", "app.services.conflicts", "Conflict check skipped for one document: nothing comparable"),
    ("WARNING", "app.main", "GET /favicon.ico returned 404"),
    ("WARNING", "app.routes.auth", "Failed login for an existing account (wrong password)"),
]


def _normal(rng: random.Random) -> dict:
    return {
        "requests_5m": [rng.randint(28, 52) for _ in range(POINTS)],
        "chat_5m": [rng.randint(8, 16) for _ in range(POINTS)],
        "error_rate": [round(rng.choice([0, 0, 0, 0.01]), 3) for _ in range(POINTS)],
        "rate_4xx": [round(rng.uniform(0.01, 0.05), 3) for _ in range(POINTS)],
        "rate_429": [0.0] * POINTS,
        "latency_p95_ms": [rng.randint(1400, 2600) for _ in range(POINTS)],
        "fallback_rate": [0.0] * POINTS,
        "empty_retrieval_rate": [round(rng.uniform(0.0, 0.1), 2) for _ in range(POINTS)],
        "traffic_ratio": [round(rng.uniform(0.8, 1.2), 2) for _ in range(POINTS)],
        "chunks_total": [240 + rng.randint(0, 30)] * POINTS,
        "documents_total": [18] * POINTS,
        "documents_failed": [0] * POINTS,
    }


def _set_tail(series: dict, metric: str, values: list):
    series[metric] = series[metric][: POINTS - len(values)] + values


def _noise(rng: random.Random, n: int) -> list[dict]:
    out = []
    for _ in range(n):
        level, logger, msg = rng.choice(NOISE)
        out.append({"minutes_ago": rng.randint(1, 55), "level": level, "logger": logger, "msg": msg})
    return out


def _old_incidents(rng: random.Random) -> list[dict]:
    pool = [
        {"days_ago": 6.2, "kind": "latency_spike", "severity": "notify", "status": "resolved",
         "summary": "p95 latency high during a large PDF batch upload", "occurrences": 2},
        {"days_ago": 12.5, "kind": "restart", "severity": "watch", "status": "resolved",
         "summary": "Service restarted after a deploy", "occurrences": 1},
        {"days_ago": 20.0, "kind": "status_storm", "severity": "notify", "status": "resolved",
         "summary": "404s from a crawler hitting old URLs", "occurrences": 1},
    ]
    return rng.sample(pool, rng.randint(0, 2))


def _event(kind: str, detail: str, series: dict) -> dict:
    return {"kind": kind, "service": "citebase", "detail": detail,
            "metrics": {k: v[-1] for k, v in series.items()}
            | {"fallback_5m": round(series["fallback_rate"][-1] * series["chat_5m"][-1])}}


def build(scenario: int, rep: int) -> tuple[dict, FixtureEvidence]:
    rng = random.Random(scenario * 1000 + rep)
    s = _normal(rng)
    logs = _noise(rng, rng.randint(1, 4))
    deploys = [{"minutes_ago": rng.randint(600, 2800), "commit": f"{rng.getrandbits(40):010x}", "note": "new commit seen in /metrics"}]
    incidents = _old_incidents(rng)

    def fallback_logs(reason: str, exc: str | None, count: int):
        for _ in range(count):
            row = {"minutes_ago": rng.randint(0, 9), "level": "WARNING", "logger": "app.services.generation",
                   "msg": f"Answer served by local fallback (reason={reason})"}
            if exc:
                row["exc"] = exc
            logs.append(row)

    if scenario == 1:  # invalid API key
        rate = round(rng.uniform(0.9, 1.0), 2)
        _set_tail(s, "fallback_rate", [rate] * rng.randint(2, 3))
        fallback_logs("llm_error", "groq.AuthenticationError: Error code: 401 - {'error': {'message': 'Invalid API Key'}}", rng.randint(4, 8))
        if rep % 2:
            deploys.insert(0, {"minutes_ago": rng.randint(15, 40), "commit": f"{rng.getrandbits(40):010x}", "note": "new commit seen in /metrics"})
        event = _event("fallback_spike", f"{rate:.0%} of answers in 5 min came from the degraded fallback", s)
    elif scenario == 2:  # model decommissioned
        rate = round(rng.uniform(0.85, 1.0), 2)
        _set_tail(s, "fallback_rate", [rate] * rng.randint(3, 5))
        model = rng.choice(["llama-3.3-70b-versatile", "mixtral-8x7b-32768"])
        fallback_logs("llm_error", f"groq.BadRequestError: Error code: 400 - The model `{model}` has been decommissioned and is no longer supported", rng.randint(5, 9))
        event = _event("fallback_spike", f"{rate:.0%} of answers in 5 min came from the degraded fallback", s)
    elif scenario == 3:  # LLM slow / timeouts
        p95 = rng.randint(9000, 16000)
        _set_tail(s, "latency_p95_ms", [rng.randint(6000, 9000), p95])
        _set_tail(s, "fallback_rate", [round(rng.uniform(0.05, 0.15), 2)])
        for _ in range(rng.randint(2, 4)):
            logs.append({"minutes_ago": rng.randint(0, 8), "level": "WARNING", "logger": "app.services.generation",
                         "msg": "LLM call slow; retrying once", "exc": "groq.APITimeoutError: Request timed out."})
        event = _event("latency_spike", f"p95 latency {p95} ms", s)
    elif scenario == 4:  # search index emptied
        _set_tail(s, "chunks_total", [0, 0])
        _set_tail(s, "empty_retrieval_rate", [1.0, 1.0])
        for _ in range(rng.randint(3, 6)):
            logs.append({"minutes_ago": rng.randint(0, 9), "level": "WARNING", "logger": "app.routes.chat",
                         "msg": "Question answered with 0 retrieved passages"})
        event = _event("zero_chunks", "18 documents but 0 searchable passages", s)
    elif scenario == 5:  # migration missing after deploy
        rate = round(rng.uniform(0.3, 0.6), 2)
        _set_tail(s, "error_rate", [round(rate * 0.6, 2), rate])
        deploys.insert(0, {"minutes_ago": rng.randint(3, 9), "commit": f"{rng.getrandbits(40):010x}", "note": "new commit seen in /metrics"})
        col = rng.choice(["documents.version", "users.email_verified_at", "documents.collection_id"])
        for _ in range(rng.randint(4, 8)):
            logs.append({"minutes_ago": rng.randint(0, 6), "level": "ERROR", "logger": "app.main",
                         "msg": f"Unhandled server error for GET {rng.choice(['/documents', '/home', '/me'])}",
                         "exc": f"sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedColumn) column {col} does not exist"})
        event = _event("error_spike", f"{rate:.0%} of {s['requests_5m'][-1]} requests failed with 5xx in 5 min", s)
    elif scenario == 6:  # frontend pointed at the wrong API base
        _set_tail(s, "requests_5m", [rng.randint(0, 2), rng.randint(0, 2), rng.randint(0, 1)])
        _set_tail(s, "chat_5m", [0, 0, 0])
        _set_tail(s, "traffic_ratio", [round(rng.uniform(0.0, 0.05), 2)] * 3)
        logs = [l for l in logs if l["minutes_ago"] > 15]  # the backend has nothing to say: requests never arrive
        event = _event("traffic_drop", f"requests fell to {s['requests_5m'][-1]} in 5 min, {s['traffic_ratio'][-1]}x typical", s)
    elif scenario == 7:  # silent fallback, exception swallowed
        rate = round(rng.uniform(0.6, 1.0), 2)
        _set_tail(s, "fallback_rate", [round(rate * rng.uniform(0.5, 0.9), 2), rate])
        fallback_logs("llm_error", None, rng.randint(3, 7))
        event = _event("fallback_spike", f"{rate:.0%} of answers in 5 min came from the degraded fallback", s)
    elif scenario == 8:  # rate limiter misconfigured
        r429 = round(rng.uniform(0.45, 0.75), 2)
        _set_tail(s, "rate_429", [round(r429 * 0.7, 2), r429])
        _set_tail(s, "rate_4xx", [round(r429 * 0.7 + 0.03, 2), round(r429 + 0.03, 2)])
        if rep % 2 == 0:
            deploys.insert(0, {"minutes_ago": rng.randint(10, 30), "commit": f"{rng.getrandbits(40):010x}", "note": "new commit seen in /metrics"})
        for _ in range(rng.randint(5, 9)):
            logs.append({"minutes_ago": rng.randint(0, 8), "level": "WARNING", "logger": "slowapi",
                         "msg": f"ratelimit 20 per 1 minute exceeded at endpoint: {rng.choice(['/chat/ask/stream', '/documents', '/home'])}"})
        event = _event("status_storm", f"{r429 + 0.03:.0%} of {s['requests_5m'][-1]} requests got 4xx (429: {r429:.0%})", s)
    elif scenario == 9:  # clean traffic spike
        req = rng.randint(140, 220)
        ratio = round(req / 40, 2)
        _set_tail(s, "requests_5m", [int(req * 0.7), req])
        _set_tail(s, "traffic_ratio", [round(ratio * 0.7, 2), ratio])
        _set_tail(s, "latency_p95_ms", [rng.randint(2400, 3600)])
        event = _event("traffic_spike", f"{req} requests in 5 min, {ratio}x typical", s)
    elif scenario in (10, 11):  # one transient 500 / the same blip, third time today
        total = rng.randint(12, 18)
        rate = round(1 / total, 3)
        _set_tail(s, "requests_5m", [total])
        _set_tail(s, "error_rate", [rate])
        logs.append({"minutes_ago": rng.randint(1, 4), "level": "ERROR", "logger": "app.main",
                     "msg": "Unhandled server error for POST /chat/ask/stream",
                     "exc": "sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) server closed the connection unexpectedly"})
        if scenario == 11:
            for hours in sorted(rng.sample(range(2, 11), 2)):
                incidents.append({"days_ago": round(hours / 24, 2), "kind": "error_spike", "severity": "watch",
                                  "status": "resolved", "occurrences": 1,
                                  "summary": "Single 500 on POST /chat/ask/stream: database connection closed unexpectedly"})
        event = _event("error_spike", f"{rate:.0%} of {total} requests failed with 5xx in 5 min", s)
    elif scenario == 12:  # control: routine review of a healthy service
        event = _event("routine_check", "scheduled review of the last hour (no detector fired)", s)
    else:
        raise ValueError(scenario)

    return event, FixtureEvidence(logs, s, deploys, incidents)


def cases(reps: int = 5):
    for scenario, meta in LABELS.items():
        for rep in range(reps):
            event, evidence = build(int(scenario), rep)
            yield {"scenario": int(scenario), "rep": rep, "name": meta["name"], "expected": meta["expected"],
                   "silent": meta["silent"], "event": event, "evidence": evidence}
