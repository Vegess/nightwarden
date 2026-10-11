"""Thresholds that turn window metrics into events.

Detectors only decide that something is worth a look. Whether it matters,
and what a human should do, is the triage step's job (agent or baseline).
Each kind has a cooldown so one problem doesn't fire every minute.
"""
from dataclasses import dataclass

COOLDOWN_MIN = 30


@dataclass
class Signal:
    kind: str
    detail: str


def detect(m: dict, previous_requests: float | None = None) -> list[Signal]:
    out: list[Signal] = []
    req = m["requests_5m"]
    if req >= 5 and m["error_rate"] >= 0.05:
        out.append(Signal("error_spike", f"{m['error_rate']:.0%} of {req} requests failed with 5xx in 5 min"))
    if req >= 20 and m["rate_4xx"] >= 0.3:
        out.append(Signal("status_storm", f"{m['rate_4xx']:.0%} of {req} requests got 4xx (429: {m['rate_429']:.0%})"))
    if m["latency_p95_ms"] >= 5000:
        out.append(Signal("latency_spike", f"p95 latency {m['latency_p95_ms']} ms"))
    if m["fallback_5m"] >= 2 and m["fallback_rate"] >= 0.2:
        out.append(Signal("fallback_spike", f"{m['fallback_rate']:.0%} of answers in 5 min came from the degraded fallback"))
    if m["documents_total"] > 0 and m["chunks_total"] == 0:
        out.append(Signal("zero_chunks", f"{m['documents_total']} documents but 0 searchable passages"))
    elif m["chat_5m"] >= 3 and m["empty_retrieval_rate"] >= 0.5:
        out.append(Signal("zero_chunks", f"{m['empty_retrieval_rate']:.0%} of questions found no passages at all"))
    if m["traffic_ratio"] >= 3 and req >= 30:
        out.append(Signal("traffic_spike", f"{req} requests in 5 min, {m['traffic_ratio']}x typical"))
    if previous_requests is not None and previous_requests >= 20 and m["traffic_ratio"] <= 0.1:
        out.append(Signal("traffic_drop", f"requests fell to {req} in 5 min, {m['traffic_ratio']}x typical"))
    return out
