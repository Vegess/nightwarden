# Vigil — Incident Triage Agent

An LLM agent that triages production signals by gathering evidence through tools
before deciding **page / notify / watch / suppress**.

Its focus is **silent degradation**: failures where the service returns HTTP 200,
the error rate stays at 0%, and the output is quietly wrong.

## Why

This project exists because of a real outage. An LLM provider decommissioned the
model a RAG service depended on. The exception was caught and converted into a
fallback that returned plausible-looking text. For two days:

| Metric | Reading |
|---|---|
| Uptime | 100% |
| HTTP error rate | 0% |
| p95 latency | Normal |
| Actual user experience | Every answer wrong |

No conventional monitor could see it. Vigil is built to catch that class of failure.

## Design

- `decide` is a **tool**, not parsed text — forces structured terminal output
- The system prompt contains a **cost model, not a signal→action mapping**
- A **rules baseline is written before the agent**, so it cannot be tuned to lose
- Ground truth comes from **deliberate fault injection**, not a static dataset
- Expected actions are labelled **before** the agent runs and never revised after

## Results

_(populate after eval — agent vs baseline over 60 fault-injected events)_

| Metric | Baseline | Agent |
|---|---|---|
| Action accuracy | | |
| Page precision | | |
| Silent-degradation recall | | |
| Median tool calls | — | |
| Mean tokens/event | — | |
| p95 decision latency | | |

## Limitations

- Faults are synthetic and injected, not observed in production traffic
- Single service; real incident correlation spans many services
- One LLM call per event does not scale to high-volume alerting — this belongs at
  the aggregation layer, not per-request
- Ground truth is self-labelled, n=60, by the same person who wants it to succeed
- No on-call rotation to validate against; "correct" is my judgment, not a team's
  post-incident review
- The rules baseline is one I wrote; a mature team's rules would be stronger

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # fill in values
alembic upgrade head
uvicorn app.main:app --reload --port 8001
```

## Build order

1. `app/agent/baseline.py` + `harness/labels.yaml` — **before anything else**
2. `app/agent/tools.py`
3. `app/agent/orchestrator.py`
4. `harness/scenarios.py`
5. `eval/run_eval.py` + `eval/report.py`

See `docs/VIGIL.md` for the full plan.
