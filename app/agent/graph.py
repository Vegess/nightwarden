"""The same triage as a LangGraph state machine (ORCHESTRATOR=langgraph).

    model ──(tool calls)──► tools ──(decided)──► END
      ▲  └─(no reply / text)─┐  │
      └──────────────────────┴──┘ (rounds left)      ──(rounds spent)──► give_up ──► END

Each node delegates to TriageRun, the code the loop runs, so the tools, system
prompt, decide schema, round limit, rate-limit handling and per-step
duration_ms / result_summary are identical; only the control flow differs.
"""
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.evidence import Evidence
from app.agent.orchestrator import TriageResult, TriageRun


class State(TypedDict, total=False):
    round: int            # rounds started so far
    reply: Any            # the model's latest reply, consumed by `tools`
    result: TriageResult  # set once the triage has a decision (or gave up)


def _build(run: TriageRun):
    def model(state: State) -> State:
        return {"round": state["round"] + 1, "reply": run.call_model(state["round"])}

    def tools(state: State) -> State:
        return {"reply": None, "result": run.take_step(state["round"] - 1, state["reply"])}

    def give_up(state: State) -> State:
        return {"result": run.inconclusive()}

    def rounds_left(state: State) -> str:
        return "model" if state["round"] < run.max_rounds else "give_up"

    def after_model(state: State) -> str:
        return "tools" if state.get("reply") is not None else rounds_left(state)

    def after_tools(state: State) -> str:
        return END if state.get("result") is not None else rounds_left(state)

    g = StateGraph(State)
    g.add_node("model", model)
    g.add_node("tools", tools)
    g.add_node("give_up", give_up)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", after_model, ["tools", "model", "give_up"])
    g.add_conditional_edges("tools", after_tools, [END, "model", "give_up"])
    g.add_edge("give_up", END)
    return g.compile()


def triage(event: dict, evidence: Evidence, client=None, model: str | None = None,
           max_rounds: int | None = None, provider: str = "Groq") -> TriageResult:
    run = TriageRun(event, evidence, client=client, model=model, max_rounds=max_rounds, provider=provider)
    out = _build(run).invoke({"round": 0}, {"recursion_limit": 2 * run.max_rounds + 5})
    return out["result"]
