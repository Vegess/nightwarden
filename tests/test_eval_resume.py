"""eval/run_eval.py fits Groq's free tier: resumes, stops on the daily cap or its token budget, Groq only."""
import json
from types import SimpleNamespace

import pytest

from app.agent.orchestrator import AgentUnavailable
from eval import run_eval

HELD_OUT = 48  # 12 scenarios x variations 1-4


def _result(tokens=1000):
    return SimpleNamespace(action="page", confidence="high", rounds=2, tool_calls=[{"tool": "decide"}],
                           tokens_in=tokens, tokens_out=0, latency_ms=10, summary="s", inconclusive=False)


class Model:
    def __init__(self, tokens=1000, fail_after=None, error="Groq rate limit (429, tokens per day)"):
        self.calls, self.tokens, self.fail_after, self.error = [], tokens, fail_after, error

    def __call__(self, event, evidence, client=None, model=None, orchestrator=None):
        self.calls.append({"client": client, "model": model, "orchestrator": orchestrator})
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise AgentUnavailable(self.error)
        return _result(self.tokens)


def _run(tmp_path, model, *extra, out="results-langgraph.json"):
    argv = ["--agent", "--from-rep", "1", "--orchestrator", "langgraph", "--out", str(tmp_path / out),
            "--pause", "0", "--model", "m1", *extra]
    return run_eval.main(argv, run=model, make_client=lambda: "groq-client", sleep=lambda s: None)


def test_resumes_skipping_scored_cases(tmp_path):
    first = Model(fail_after=5)
    s = _run(tmp_path, first)
    assert s["scored"] == 5 and s["stopped"].startswith("daily cap") and len(first.calls) == 6
    second = Model()
    s = _run(tmp_path, second)
    assert len(second.calls) == HELD_OUT - 5 and s["scored"] == HELD_OUT and s["stopped"] is None
    data = json.loads((tmp_path / "results-langgraph.json").read_text())
    assert data["model"] == "m1" and sum(1 for r in data["cases"] if r.get("agent")) == HELD_OUT
    third = Model()
    assert _run(tmp_path, third)["scored"] == HELD_OUT and third.calls == []  # nothing left


def test_daily_cap_stops_cleanly_without_retries(tmp_path):
    m = Model(fail_after=0)
    s = _run(tmp_path, m)  # no decision at all: the file is left alone, and that is not an error
    assert len(m.calls) == 1 and s["scored"] == 0 and "tokens per day" in s["stopped"]
    assert not (tmp_path / "results-langgraph.json").exists()


def test_other_failures_retry_then_move_on(tmp_path):
    m = Model(fail_after=0, error="Groq error (503)")
    with pytest.raises(SystemExit):  # a run that scored nothing for no known reason fails loudly
        _run(tmp_path, m, "--reps", "2")
    assert len(m.calls) == 12 * 3  # 3 attempts per held-out case, no daily-cap stop


def test_token_budget_caps_a_run(tmp_path):
    m = Model(tokens=50_000)
    s = _run(tmp_path, m, "--token-budget", "120000")
    assert len(m.calls) == 2 and s["spent"] == 100_000 and s["stopped"].startswith("token budget")


def test_budget_file_is_shared_across_orchestrators(tmp_path):
    budget = tmp_path / "spent"
    a = Model(tokens=40_000)
    _run(tmp_path, a, "--token-budget", "120000", "--budget-file", str(budget), out="results-loop.json")
    b = Model(tokens=40_000)
    _run(tmp_path, b, "--token-budget", "120000", "--budget-file", str(budget))
    assert len(a.calls) + len(b.calls) == 3 and budget.read_text() == "120000"


def test_same_model_for_both_orchestrators_and_never_the_backup(tmp_path, monkeypatch):
    from app.agent import providers
    monkeypatch.setattr(providers.get_settings(), "backup_llm_api_key", "would-be-backup")
    monkeypatch.setattr(providers, "chain", lambda *a, **k: pytest.fail("the eval must not use the provider chain"))
    monkeypatch.setattr(providers, "backup", lambda: pytest.fail("the eval must not use the backup"))
    loop, graph = Model(), Model()
    _run(tmp_path, loop, "--reps", "2", out="results-loop.json")
    run_eval.main(["--agent", "--from-rep", "1", "--orchestrator", "loop", "--reps", "2", "--pause", "0",
                   "--model", "m1", "--out", str(tmp_path / "results-loop2.json")],
                  run=graph, make_client=lambda: "groq-client", sleep=lambda s: None)
    calls = loop.calls + graph.calls
    assert {c["model"] for c in calls} == {"m1"} and {c["client"] for c in calls} == {"groq-client"}


def test_resume_with_another_model_is_refused(tmp_path):
    _run(tmp_path, Model(), "--reps", "2")
    with pytest.raises(SystemExit, match="scored with m1"):
        run_eval.main(["--agent", "--from-rep", "1", "--orchestrator", "langgraph", "--reps", "2",
                       "--out", str(tmp_path / "results-langgraph.json"), "--model", "m2"],
                      run=Model(), make_client=lambda: "groq-client", sleep=lambda s: None)


def test_agent_run_without_a_groq_key_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval.get_settings(), "groq_api_key", None)
    with pytest.raises(SystemExit):
        run_eval.main(["--agent", "--out", str(tmp_path / "results-x.json")])


def test_daily_cap_detection():
    assert run_eval.daily_cap("Groq rate limit (429, tokens per day)")
    assert run_eval.daily_cap("Groq rate limit (429, requests per day)")
    assert not run_eval.daily_cap("Groq rate limit (429, tokens per minute)")
