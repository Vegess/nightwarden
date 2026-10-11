"""Turn raw /metrics readings into rates.

Citebase's counters are cumulative and reset when its process restarts, so
every rate is computed from the difference between two readings, and a drop
in uptime (or a new start time) means "restarted: count from zero".

Definitions used everywhere (baseline, detectors, agent tools):
  error_rate     5xx responses / all responses        (429s are NOT errors)
  rate_4xx       4xx responses / all responses        (includes 429)
  rate_429       429 responses / all responses
  fallback_rate  answers from the degraded fallback / chat answers, last 5 min
  traffic_ratio  requests in the window / typical requests in a window
"""
import statistics


def _num(v) -> float:
    return float(v or 0)


def restarted(prev: dict | None, cur: dict) -> bool:
    if not prev:
        return False
    return cur.get("started_at") != prev.get("started_at") or _num(cur.get("uptime_s")) < _num(prev.get("uptime_s"))


def delta(prev: dict | None, cur: dict, interval_s: float) -> dict:
    """Counts between two readings (since the restart, if there was one)."""
    base = {} if (prev is None or restarted(prev, cur)) else prev
    sc, sp = cur.get("requests_by_status") or {}, base.get("requests_by_status") or {}
    status = {k: max(0.0, _num(sc.get(k)) - _num(sp.get(k))) for k in set(sc) | set(sp)}
    d = {
        "interval_s": interval_s,
        "requests": max(0.0, _num(cur.get("requests_total")) - _num(base.get("requests_total"))),
        "chat": max(0.0, _num(cur.get("chat_requests_total")) - _num(base.get("chat_requests_total"))),
        "fallback": max(0.0, _num(cur.get("fallback_total")) - _num(base.get("fallback_total"))),
        "empty": max(0.0, _num(cur.get("empty_retrievals_total")) - _num(base.get("empty_retrievals_total"))),
        "s5xx": sum(v for k, v in status.items() if k.startswith("5")),
        "s4xx": sum(v for k, v in status.items() if k.startswith("4")),
        "s429": status.get("429", 0.0),
        "status": status,
        "restarted": restarted(prev, cur),
    }
    return d


def window(deltas: list[dict], latest: dict, typical_requests: float | None) -> dict:
    """Aggregate several intervals (normally the last ~5 minutes) into the
    metrics a detector, the baseline and the agent all read."""
    req = sum(d["requests"] for d in deltas)
    chat = sum(d["chat"] for d in deltas)
    docs = latest.get("documents_by_status") or {}
    jobs = latest.get("jobs_by_status") or {}
    return {
        "requests_5m": int(req),
        "chat_5m": int(chat),
        "error_rate": round(sum(d["s5xx"] for d in deltas) / req, 3) if req else 0.0,
        "rate_4xx": round(sum(d["s4xx"] for d in deltas) / req, 3) if req else 0.0,
        "rate_429": round(sum(d["s429"] for d in deltas) / req, 3) if req else 0.0,
        "latency_p95_ms": int(_num(latest.get("latency_p95_ms"))),
        "fallback_rate": round(_num(latest.get("fallback_rate_5m")), 3),
        "fallback_5m": int(_num(latest.get("fallback_5m"))),
        "empty_retrieval_rate": round(sum(d["empty"] for d in deltas) / chat, 3) if chat else 0.0,
        "traffic_ratio": round(req / typical_requests, 2) if typical_requests else 1.0,
        "chunks_total": int(_num(latest.get("chunks_total"))),
        "documents_total": int(_num(latest.get("documents_total"))),
        "documents_failed": int(_num(docs.get("failed"))),
        "jobs_failed": int(_num(jobs.get("failed"))),
        "llm_key_loaded": bool(latest.get("llm_key_loaded", True)),
    }


def typical(request_counts: list[float], floor: float = 10.0) -> float:
    """Typical requests per window: the median of the last day's windows,
    never below `floor` so a quiet night doesn't make 12 requests a "spike"."""
    return max(floor, statistics.median(request_counts)) if request_counts else floor
