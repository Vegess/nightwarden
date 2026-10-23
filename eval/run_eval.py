"""Run the rules baseline and (optionally) the agent over the 60 labelled cases.

    python -m eval.run_eval                                   # baseline only (no API key needed)
    python -m eval.run_eval --agent                           # baseline + agent loop (needs GROQ_API_KEY)
    python -m eval.run_eval --agent --orchestrator langgraph  # baseline + LangGraph orchestrator

Agent runs write eval/results.json (loop) or eval/results-langgraph.json, or
--out; the loop-vs-LangGraph comparison (`python -m eval.compare`) reads
eval/results-loop.json and eval/results-langgraph.json. A baseline-only run
writes eval/results-baseline.json and never touches agent results.
`python -m eval.report` prints the agent-vs-baseline comparison. Labels come from harness/labels.yaml and are never
edited after a run.
"""
import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from app.agent.baseline import rules_triage
from app.agent.orchestrator import AgentUnavailable
from app.agent.runner import ORCHESTRATORS, triage
from app.core.config import get_settings
from harness.scenarios import cases

HERE = Path(__file__).parent
OUT = HERE / "results.json"                   # agent loop (also what the dashboard shows)
RESULTS = {"loop": OUT, "langgraph": HERE / "results-langgraph.json"}  # default --out per orchestrator
BASELINE_OUT = HERE / "results-baseline.json"


def _key(row: dict) -> tuple:
    return row["scenario"], row["rep"]


def _save(out: Path, rows: list[dict], model: str | None, orchestrator: str | None) -> bool:
    """Write results. Agent results files are never replaced by rows without any agent decision."""
    if out != BASELINE_OUT and not any(r.get("agent") for r in rows):
        return False
    out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "orchestrator": orchestrator,
        "cases": sorted(rows, key=_key),
    }, indent=1))
    return True


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
    ap.add_argument("--orchestrator", choices=ORCHESTRATORS, default="loop")
    ap.add_argument("--out", type=Path, help="results file (default depends on --agent and --orchestrator)")
    args = ap.parse_args()
    settings = get_settings()
    model = settings.groq_model if args.agent else None
    orchestrator = args.orchestrator if args.agent else None
    out = args.out or (RESULTS[args.orchestrator] if args.agent else BASELINE_OUT)
    if not args.agent and out.resolve() != BASELINE_OUT.resolve() and out.name.startswith("results"):
        ap.error(f"a baseline-only run must not overwrite {out}; drop --out or add --agent")

    previous = {}
    if args.only_missing and out.exists():
        previous = {_key(r): r for r in json.loads(out.read_text())["cases"] if r.get("agent")}

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
                    r = triage(c["event"], c["evidence"], orchestrator=args.orchestrator)
                    row.update(orchestrator=args.orchestrator, agent=r.action, confidence=r.confidence, rounds=r.rounds,
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
            _save(out, rows + [r for k, r in previous.items() if k not in {_key(x) for x in rows}], model, orchestrator)

    done = sum(1 for r in rows if r.get("agent"))
    if _save(out, rows, model, orchestrator):
        print(f"Wrote {out} ({len(rows)} cases, {done} with an agent decision)")
    else:
        print(f"No agent decisions; left {out} untouched")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
