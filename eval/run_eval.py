"""Run the rules baseline and (optionally) the agent over the 60 labelled cases.

    python -m eval.run_eval              # baseline only (no API key needed)
    python -m eval.run_eval --agent      # baseline + agent (needs GROQ_API_KEY)

Writes eval/results.json; `python -m eval.report` prints the comparison.
Labels come from harness/labels.yaml and are never edited after a run.
"""
import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from app.agent.baseline import rules_triage
from app.agent.orchestrator import AgentUnavailable, triage
from app.core.config import get_settings
from harness.scenarios import cases

OUT = Path(__file__).parent / "results.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", action="store_true")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between agent runs (free-tier rate limits)")
    args = ap.parse_args()
    settings = get_settings()

    rows = []
    for c in cases(args.reps):
        row = {"scenario": c["scenario"], "rep": c["rep"], "name": c["name"], "silent": c["silent"],
               "kind": c["event"]["kind"], "expected": c["expected"], "baseline": rules_triage(c["event"])}
        if args.agent:
            for attempt in range(3):
                try:
                    r = triage(c["event"], c["evidence"])
                    row.update(agent=r.action, confidence=r.confidence, rounds=r.rounds,
                               tool_calls=sum(1 for t in r.tool_calls if t["tool"] != "decide"),
                               tools=[t["tool"] for t in r.tool_calls], tokens=r.tokens_in + r.tokens_out,
                               latency_ms=r.latency_ms, summary=r.summary, inconclusive=r.inconclusive)
                    break
                except AgentUnavailable as exc:
                    row.update(agent=None, error=str(exc)[:200])
                    time.sleep(20 * (attempt + 1))
            time.sleep(args.pause)
            print(f"S{row['scenario']:>2} r{row['rep']} expected={row['expected']:<8} baseline={row['baseline']:<8} "
                  f"agent={row.get('agent')}", flush=True)
        rows.append(row)

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": settings.groq_model if args.agent else None,
        "cases": rows,
    }, indent=1))
    print(f"Wrote {OUT} ({len(rows)} cases)")


if __name__ == "__main__":
    main()
