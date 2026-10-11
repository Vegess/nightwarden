---
title: Nightwarden
sdk: docker
app_port: 7860
pinned: false
---

# Nightwarden

An AI on-call agent that watches [Citebase](https://documind-beta-lac.vercel.app) (my multi-tenant RAG app) and
decides what a human should do when something looks wrong: **page**, **notify**, **watch** or **suppress**. Every
decision shows the evidence it gathered and why.

It exists because of a real outage. Citebase's LLM provider retired the model it used, the exception was swallowed,
and the app served polite fallback answers for two days. Uptime was 100%, error rate 0%, and every answer was useless.
Ordinary monitoring can't see that kind of failure. Nightwarden is built to.

## How it works

```
Citebase /metrics, /metrics/logs
        │  every 60 s
        ▼
  Poller ──► snapshot ──► 5-min window ──► detectors ──► event
                                                          │
                       ┌──────────────────────────────────┘
                       ▼
               Triage agent (Groq tool calling)
               get_recent_logs · get_metrics · get_deploy_history · search_past_incidents
                       │
                       ▼  decide(action, summary, rationale, confidence)
               incident (merged / escalated) ──► Discord or Slack · email for pages
```

- **Collector.** Polls Citebase's token-protected metrics and log endpoints, turns counters into rates over a
  5-minute window, and notices deploys (commit changed), restarts (uptime reset) and outages (3 failed polls).
- **Detectors** fire on server errors, 4xx storms, latency, **fallback answers**, **empty retrieval**, and traffic
  spikes or drops. Each kind has a 30-minute cooldown.
- **Agent.** The model investigates with four read-only tools and ends by calling `decide`. The system prompt
  describes the costs of paging versus missing a failure. It contains no "signal X means action Y" rules.
- **Fail loud.** If the model is unavailable, Nightwarden falls back to the rules and raises the result: silent-
  failure signals (fallback spike, zero chunks, target down) always page. It never goes quiet because its own AI broke.
- **Incidents.** Repeats of the same kind within 6 hours merge into one incident; alerts go out only when an incident
  opens or gets more severe. Incidents resolve themselves after 3 quiet hours.
- **Watching the watcher.** UptimeRobot checks `/health`; every poll pings a healthchecks.io dead-man switch.

## Evaluation

60 labelled cases: 12 failure scenarios × 5 seeded variations, replayed from fixtures (no live traffic). The
expected actions (`harness/labels.yaml`) and the rules baseline (`app/agent/baseline.py`) were written and frozen
**before** the agent existed, so neither could be tuned to make the agent look good.

**Development run** (variation 0 of each scenario, 12 cases, `openai/gpt-oss-120b`, 11 Oct 2026). These cases
have been looked at, so they're a development set, not the headline result:

| Metric | Rules baseline | Agent |
|---|---|---|
| Action accuracy | 41.7% | 41.7% |
| Page precision | 100.0% | 66.7% |
| Page recall | 20.0% | 80.0% |
| Silent-degradation recall | 0.0% | 33.3% |
| Accuracy on loud failures | 55.6% | 44.4% |
| Median tool calls / mean tokens | — | 3 / 5,919 |

What it showed: the agent catches the real outages the rules miss (4 of 5 page-worthy cases, including the emptied
vector store) but over-escalates: it paged for an LLM timeout and for a traffic drop that warranted a notify, and it
called normal latency "high" on quiet controls because it had nothing to compare against. One change was made after
this run, and it is general rather than scenario-specific: `get_metrics` now also returns the window's median, so
the model judges a value against the service's own normal. Labels and the baseline were not touched.

**Held-out result** (variations 1–4, 48 cases never inspected): *pending*. Run with
`python -m eval.run_eval --agent --from-rep 1`.

Limits, stated plainly: the labels were written by the person who built the agent; 60 cases is small; fixtures
approximate Citebase's real logs but aren't real incidents.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill TARGET_URL / TARGET_METRICS_TOKEN / GROQ_API_KEY
alembic upgrade head
uvicorn app.main:app --reload # dashboard on http://localhost:8000
```

```bash
pytest -q                          # no network, no key needed
python -m eval.run_eval            # baseline only
python -m eval.run_eval --agent    # agent + baseline (uses Groq quota)
python -m eval.report              # comparison tables
```

Deploying (Hugging Face Space + Neon) is covered in [docs/DEPLOY.md](docs/DEPLOY.md); design notes and
decisions are in [docs/NIGHTWARDEN.md](docs/NIGHTWARDEN.md).

## API

Public, read-only: `GET /health`, `/api/status`, `/api/incidents`, `/api/incidents/{id}`, `/api/decisions`,
`/api/eval`.
Admin (`Authorization: Bearer $ADMIN_TOKEN`): `POST /api/deploys`, `POST /api/events` (fire a test event end to
end), `POST /api/incidents/{id}/resolve`.

## Stack

FastAPI · SQLAlchemy 2 + Alembic · Postgres (Neon) or SQLite · Groq tool calling (`openai/gpt-oss-120b`) ·
httpx · vanilla JS dashboard · pytest · Docker.
