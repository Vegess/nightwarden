"""Pick the orchestrator: the plain loop (default) or the LangGraph graph."""
from app.agent.evidence import Evidence
from app.agent.orchestrator import TriageResult
from app.core.config import get_settings

ORCHESTRATORS = ("loop", "langgraph")


def triage(event: dict, evidence: Evidence, client=None, model: str | None = None,
           max_rounds: int | None = None, orchestrator: str | None = None) -> TriageResult:
    kind = orchestrator or get_settings().orchestrator
    if kind == "langgraph":
        from app.agent.graph import triage as run  # imported lazily: the loop never needs langgraph
    elif kind == "loop":
        from app.agent.orchestrator import triage as run
    else:
        raise ValueError(f"ORCHESTRATOR must be one of {', '.join(ORCHESTRATORS)}, not {kind!r}")
    return run(event, evidence, client=client, model=model, max_rounds=max_rounds)
