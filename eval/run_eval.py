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


def _key(row: dict) -> tuple:
    return row["scenario"], row["rep"]


def _save(rows: list[dict], model: str | None) -> None:
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "cases": sorted(rows, key=_key),
    }, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", action="store_true")
    ap.add_argument("--reps", type=int, default=5, help="variations per scenario (seeds 0..reps-1)")
    ap.add_argument("--from-rep", type=int, default=0,
                    help="run the agent only on variations >= this (rep 0 is the development set)")
    ap.add_argument("--only-missing", action="store_true",
                    help="keep agent results already in results.json and run only the cases without one "
                         "(for finishing a run across days on a free-tier daily token cap)")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between agent runs (free-tier rate limits)")
    args = ap.parse_args()
    settings = get_settings()
    model = settings.groq_model if args.agent else None

    previous = {}
    if args.only_missing and OUT.exists():
        previous = {_key(r): r for r in json.loads(OUT.read_text())["cases"] if r.get("agent")}

    rows: list[dict] = []
    capped = False
    for c in cases(args.reps):
        row = {"scenario": c["scenario"], "rep": c["rep"], "name": c["name"], "silent": c["silent"],
               "kind": c["event"]["kind"], "expected": c["expected"], "baseline": rules_triage(c["event"])}
        if _key(row) in previous:
            row = previous[_key(row)]
        elif args.agent and not capped and row["rep"] >= args.from_rep:
            for attempt in range(3):
                try:
                    r = triage(c["event"], c["evidence"])
                    row.update(agent=r.action, confidence=r.confidence, rounds=r.rounds,
                               tool_calls=sum(1 for t in r.tool_calls if t["tool"] != "decide"),
                               tools=[t["tool"] for t in r.tool_calls], tokens=r.tokens_in + r.tokens_out,
                               latency_ms=r.latency_ms, summary=r.summary, inconclusive=r.inconclusive)
                    row.pop("error", None)
                    break
                except AgentUnavailable as exc:
                    row.update(agent=None, error=str(exc)[:200])
                    if "per day" in str(exc) or "TPD" in str(exc) or "RPD" in str(exc):
                        capped = True  # daily cap: stop spending retries, keep what we have
                        print("Daily rate limit reached; saving partial results. "
                              "Re-run later with --only-missing to finish.", flush=True)
                        break
                    time.sleep(20 * (attempt + 1))
            time.sleep(args.pause)
            print(f"S{row['scenario']:>2} r{row['rep']} expected={row['expected']:<8} baseline={row['baseline']:<8} "
                  f"agent={row.get('agent')} tokens={row.get('tokens')}", flush=True)
        rows.append(row)
        if args.agent:
            _save(rows + [r for k, r in previous.items() if k not in {_key(x) for x in rows}], model)

    _save(rows, model)
    done = sum(1 for r in rows if r.get("agent"))
    print(f"Wrote {OUT} ({len(rows)} cases, {done} with an agent decision)")


if __name__ == "__main__":
    main()
