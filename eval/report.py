"""Print the agent-vs-baseline comparison from eval/results.json (or --file).
Numbers are reported as measured: no rounding in anyone's favour."""
import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from eval.run_eval import OUT

ACTIONS = ["page", "notify", "watch", "suppress"]


def metrics(rows: list[dict], key: str) -> dict:
    scored = [r for r in rows if r.get(key)]
    if not scored:
        return {}
    acc = sum(r[key] == r["expected"] for r in scored) / len(scored)
    pages = [r for r in scored if r[key] == "page"]
    page_precision = sum(r["expected"] == "page" for r in pages) / len(pages) if pages else None
    real = [r for r in scored if r["expected"] == "page"]
    page_recall = sum(r[key] == "page" for r in real) / len(real) if real else None
    silent = [r for r in scored if r["silent"]]
    silent_recall = sum(r[key] == r["expected"] for r in silent) / len(silent) if silent else None
    loud = [r for r in scored if not r["silent"]]
    loud_acc = sum(r[key] == r["expected"] for r in loud) / len(loud) if loud else None
    return {"n": len(scored), "accuracy": acc, "page_precision": page_precision, "page_recall": page_recall,
            "silent_recall": silent_recall, "loud_accuracy": loud_acc,
            "confusion": {e: dict(Counter(r[key] for r in scored if r["expected"] == e)) for e in ACTIONS}}


def summarize(data: dict) -> dict:
    rows = data["cases"]
    agent_rows = [r for r in rows if r.get("agent")]
    # Compare on the same cases: if the agent ran on a subset, score the baseline on that subset too.
    paired = agent_rows or rows
    out = {"baseline": metrics(paired, "baseline"), "agent": metrics(rows, "agent"),
           "baseline_all": metrics(rows, "baseline"), "reps": sorted({r["rep"] for r in paired})}
    if agent_rows:
        lat = sorted(r["latency_ms"] for r in agent_rows)
        out["agent_cost"] = {
            "median_tool_calls": statistics.median(r["tool_calls"] for r in agent_rows),
            "mean_tokens": round(statistics.mean(r["tokens"] for r in agent_rows)),
            "p95_latency_ms": lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))],
            "inconclusive": sum(bool(r.get("inconclusive")) for r in agent_rows),
            "errors": sum(1 for r in rows if r.get("error") and not r.get("agent")),
        }
    out["per_scenario"] = []
    for sc in sorted({r["scenario"] for r in paired}):
        rs = [r for r in paired if r["scenario"] == sc]
        out["per_scenario"].append({
            "scenario": sc, "name": rs[0]["name"], "expected": rs[0]["expected"], "silent": rs[0]["silent"],
            "baseline_correct": sum(r["baseline"] == r["expected"] for r in rs),
            "agent_correct": sum(r.get("agent") == r["expected"] for r in rs) if agent_rows else None, "n": len(rs),
            "agent_actions": dict(Counter(r.get("agent") for r in rs)) if agent_rows else None,
        })
    return out


def _pct(v):
    return "—" if v is None else f"{v:.1%}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, default=OUT)
    data = json.loads(ap.parse_args().file.read_text())
    s = summarize(data)
    b, a = s["baseline"], s.get("agent") or {}
    print(f"Model: {data.get('model') or '(baseline only)'} · generated {data['generated_at']} · "
          f"{s['baseline']['n']} cases (variations {', '.join(map(str, s['reps']))})\n")
    print("| Metric | Rules baseline | Agent |\n|---|---|---|")
    for key, label in [("accuracy", "Action accuracy"), ("page_precision", "Page precision"),
                       ("page_recall", "Page recall"), ("silent_recall", "Silent-degradation recall"),
                       ("loud_accuracy", "Accuracy on loud failures")]:
        print(f"| {label} | {_pct(b.get(key))} | {_pct(a.get(key))} |")
    if "agent_cost" in s:
        c = s["agent_cost"]
        print(f"| Median tool calls | — | {c['median_tool_calls']} |\n| Mean tokens / event | — | {c['mean_tokens']} |"
              f"\n| p95 decision latency | ~0 ms | {c['p95_latency_ms']} ms |")
    print("\n| # | Scenario | Expected | Baseline | Agent |\n|---|---|---|---|---|")
    for r in s["per_scenario"]:
        agent = f"{r['agent_correct']}/{r['n']}" if r["agent_correct"] is not None else "—"
        print(f"| {r['scenario']} | {r['name']}{' (silent)' if r['silent'] else ''} | {r['expected']} | "
              f"{r['baseline_correct']}/{r['n']} | {agent} |")
    for who in ("baseline", "agent"):
        if s.get(who):
            print(f"\nConfusion ({who}), rows = expected:")
            for e, got in s[who]["confusion"].items():
                print(f"  {e:<8} {got}")


if __name__ == "__main__":
    main()
