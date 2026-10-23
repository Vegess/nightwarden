"""A baseline-only eval run never replaces agent results."""
import sys

import pytest

from eval import run_eval


def test_baseline_only_run_refuses_to_overwrite_agent_results(monkeypatch, tmp_path):
    for target in ("eval/results.json", "eval/results-loop.json", "eval/results-langgraph.json"):
        monkeypatch.setattr(sys, "argv", ["run_eval", "--out", target])
        with pytest.raises(SystemExit):
            run_eval.main()


def test_agent_results_are_not_saved_without_any_agent_decision(tmp_path):
    out = tmp_path / "results-loop.json"
    assert run_eval._save(out, [{"scenario": 1, "rep": 1, "agent": None}], "m", "loop") is False
    assert not out.exists()
    assert run_eval._save(out, [{"scenario": 1, "rep": 1, "agent": "page"}], "m", "loop") and out.exists()
