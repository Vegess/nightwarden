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

Agent runs are resumable and fit Groq's free tier (200k tokens/day):
- cases already scored in the results file are skipped (`--fresh` starts over);
- a run stops cleanly, saving what it has, at --token-budget (default 120k tokens)
  or on a daily-cap 429; run it again after the cap resets to continue;
- every case, for both orchestrators, uses the same Groq model (--model, recorded in
  the file; a resume with a different model is refused) and never the backup provider.
"""
import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from app.agent.baseline import rules_triage
from app.agent.orchestrator import AgentUnavailable, _client as groq_client
from app.agent.runner import ORCHESTRATORS, triage
from app.core.config import get_settings
from harness.scenarios import cases

HERE = Path(__file__).parent
OUT = HERE / "results.json"                   # agent loop (also what the dashboard shows)
RESULTS = {"loop": OUT, "langgraph": HERE / "results-langgraph.json"}  # default --out per orchestrator
BASELINE_OUT = HERE / "results-baseline.json"
TOKEN_BUDGET = 120_000
FIRST_CASE_ESTIMATE = 7_000                   # tokens, until this run has its own average


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


def daily_cap(error: str) -> bool:
    """A 429 that waiting a minute won't fix (short_error names the limit kind)."""
    return "per day" in error or "TPD" in error or "RPD" in error


def load_previous(out: Path, model: str, fresh: bool) -> dict:
    if fresh or not out.exists():
        return {}
    data = json.loads(out.read_text())
    if data.get("model") and data["model"] != model:
        raise SystemExit(f"{out} was scored with {data['model']}; resume with --model {data['model']} or use --fresh")
    return {_key(r): r for r in data["cases"] if r.get("agent")}


def main(argv: list[str] | None = None, run=triage, make_client=groq_client, sleep=time.sleep) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", action="store_true")
    ap.add_argument("--reps", type=int, default=5, help="variations per scenario (seeds 0..reps-1)")
    ap.add_argument("--from-rep", type=int, default=0,
                    help="run the agent only on variations >= this (rep 0 is the development set)")
    ap.add_argument("--fresh", action="store_true", help="ignore cases already scored in the results file")
    ap.add_argument("--only-missing", action="store_true", help=argparse.SUPPRESS)  # resuming is the default now
    ap.add_argument("--token-budget", type=int, default=TOKEN_BUDGET, help="stop before spending more Groq tokens")
    ap.add_argument("--budget-file", type=Path,
                    help="share one budget across several runs (the eval workflow runs both orchestrators)")
    ap.add_argument("--model", help="Groq model for every case (default: GROQ_MODEL)")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between agent runs (free-tier rate limits)")
    ap.add_argument("--orchestrator", choices=ORCHESTRATORS, default="loop")
    ap.add_argument("--out", type=Path, help="results file (default depends on --agent and --orchestrator)")
    args = ap.parse_args(argv)
    model = (args.model or get_settings().groq_model) if args.agent else None
    orchestrator = args.orchestrator if args.agent else None
    out = args.out or (RESULTS[args.orchestrator] if args.agent else BASELINE_OUT)
    if not args.agent and out.resolve() != BASELINE_OUT.resolve() and out.name.startswith("results"):
        ap.error(f"a baseline-only run must not overwrite {out}; drop --out or add --agent")

    if args.agent and make_client is groq_client and not get_settings().groq_api_key:
        ap.error("--agent needs GROQ_API_KEY (the eval never uses the backup provider)")

    previous = load_previous(out, model, args.fresh) if args.agent else {}
    rows: list[dict] = []
    before = int(args.budget_file.read_text() or 0) if args.budget_file and args.budget_file.exists() else 0
    spent, ran, stopped = 0, [], None
    for c in cases(args.reps):
        row = {"scenario": c["scenario"], "rep": c["rep"], "name": c["name"], "silent": c["silent"],
               "kind": c["event"]["kind"], "expected": c["expected"], "baseline": rules_triage(c["event"])}
        wanted = args.agent and row["rep"] >= args.from_rep
        if _key(row) in previous:
            row = previous[_key(row)]
        elif wanted and stopped is None:
            estimate = statistics.mean(ran) if ran else FIRST_CASE_ESTIMATE
            if before + spent + estimate > args.token_budget:
                stopped = f"token budget: {before + spent:,} of {args.token_budget:,} spent"
            else:
                for attempt in range(3):
                    try:
                        # Groq only, by construction: the client is Groq's, never the provider chain.
                        r = run(c["event"], c["evidence"], client=make_client(), model=model,
                                orchestrator=args.orchestrator)
                        row.update(orchestrator=args.orchestrator, agent=r.action, confidence=r.confidence,
                                   rounds=r.rounds, tool_calls=sum(1 for t in r.tool_calls if t["tool"] != "decide"),
                                   tools=[t["tool"] for t in r.tool_calls], tokens=r.tokens_in + r.tokens_out,
                                   latency_ms=r.latency_ms, summary=r.summary, inconclusive=r.inconclusive)
                        row.pop("error", None)
                        spent += row["tokens"]
                        ran.append(row["tokens"])
                        break
                    except AgentUnavailable as exc:
                        row.update(agent=None, error=str(exc)[:200])
                        if daily_cap(str(exc)):
                            stopped = f"daily cap: {exc}"
                            break
                        sleep(20 * (attempt + 1))
                sleep(args.pause)
                print(f"S{row['scenario']:>2} r{row['rep']} expected={row['expected']:<8} baseline={row['baseline']:<8} "
                      f"agent={row.get('agent')} tokens={row.get('tokens')}", flush=True)
        rows.append(row)
        if args.agent:
            _save(out, rows + [r for k, r in previous.items() if k not in {_key(x) for x in rows}], model, orchestrator)

    if args.budget_file:
        args.budget_file.write_text(str(before + spent))
    wanted_rows = [r for r in rows if r["rep"] >= args.from_rep] if args.agent else []
    scored = sum(1 for r in wanted_rows if r.get("agent"))
    summary = {"scored": scored, "of": len(wanted_rows), "spent": spent, "stopped": stopped}
    if not args.agent:
        _save(out, rows, None, None)
        print(f"Wrote {out} ({len(rows)} cases, baseline only)")
    elif _save(out, rows, model, orchestrator):
        print(f"Wrote {out}: {scored}/{len(wanted_rows)} cases scored ({orchestrator}, {model}); "
              f"this run spent {spent:,} tokens" + (f"; stopped on {stopped}. Run again to continue." if stopped else ""))
    else:
        print(f"No agent decisions; left {out} untouched" + (f" ({stopped})" if stopped else ""))
        if not stopped:
            raise SystemExit(1)
    return summary


if __name__ == "__main__":
    main()
