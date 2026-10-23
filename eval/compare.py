"""Loop vs LangGraph on the held-out variations: accuracy, mean latency and mean
tool calls per scenario and in total, from eval/results-loop.json and
eval/results-langgraph.json (one run of each, same model and day). Every metric is
over the cases that actually ran (shown as correct/ran); a "Paired" row compares
both on only the cases both ran, so a run cut short by a rate limit can't look
better or worse than it is."""
import json
import statistics
from pathlib import Path

from eval.run_eval import HERE

FILES = {"loop": HERE / "results-loop.json", "langgraph": HERE / "results-langgraph.json"}

HELD_OUT = (1, 2, 3, 4)


def load(orchestrator: str) -> list[dict]:
    path = FILES[orchestrator]
    if not path.exists():
        return []
    return [r for r in json.loads(path.read_text())["cases"] if r["rep"] in HELD_OUT]


def stats(rows: list[dict]) -> dict:
    ran = [r for r in rows if r.get("agent")]
    return {"n": len(rows), "ran": len(ran),
            "correct": sum(r.get("agent") == r["expected"] for r in ran),
            "latency_s": statistics.mean(r["latency_ms"] for r in ran) / 1000 if ran else None,
            "tool_calls": statistics.mean(r["tool_calls"] for r in ran) if ran else None}


def table() -> tuple[str, dict]:
    data = {o: load(o) for o in FILES}
    scenarios = sorted({(r["scenario"], r["name"]) for rows in data.values() for r in rows})
    lines = ["| # | Scenario | n | Loop accuracy | LangGraph accuracy | Loop latency | LangGraph latency "
             "| Loop tool calls | LangGraph tool calls |", "|---|---|---|---|---|---|---|---|---|"]

    def cells(s: dict) -> tuple[str, str, str]:
        if not s["ran"]:
            return "not run", "—", "—"
        acc = f"{s['correct']}/{s['ran']} ({s['correct'] / s['ran']:.0%})"
        lat = "—" if s["latency_s"] is None else f"{s['latency_s']:.1f} s"
        tools = "—" if s["tool_calls"] is None else f"{s['tool_calls']:.1f}"
        return acc, lat, tools

    def row(label: str, num: str, loop_rows: list[dict], graph_rows: list[dict]) -> str:
        a, b = stats(loop_rows), stats(graph_rows)
        (aa, al, at), (ba, bl, bt) = cells(a), cells(b)
        return f"| {num} | {label} | {max(a['n'], b['n'])} | {aa} | {ba} | {al} | {bl} | {at} | {bt} |"

    for sc, name in scenarios:
        lines.append(row(name, str(sc), [r for r in data["loop"] if r["scenario"] == sc],
                         [r for r in data["langgraph"] if r["scenario"] == sc]))
    lines.append(row("**Total**", "", data["loop"], data["langgraph"]))
    both = {(r["scenario"], r["rep"]) for r in data["loop"] if r.get("agent")} & \
           {(r["scenario"], r["rep"]) for r in data["langgraph"] if r.get("agent")}
    paired = {o: [r for r in rows if (r["scenario"], r["rep"]) in both] for o, rows in data.items()}
    lines.append(row("Paired (cases both ran)", "", paired["loop"], paired["langgraph"]))
    return "\n".join(lines), {o: stats(rows) for o, rows in data.items()}


def main() -> None:
    text, totals = table()
    print(text)
    for o, s in totals.items():
        acc = f"{s['correct'] / s['ran']:.1%}" if s["ran"] else "—"
        print(f"\n{o}: accuracy {acc} ({s['correct']}/{s['ran']} that ran, of {s['n']})", end="")
    print()


if __name__ == "__main__":
    main()
