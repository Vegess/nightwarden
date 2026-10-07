# Vigil — Incident Triage Agent
**K Anirudh · MTech AI & DS, SASTRA · August 2026**

*Paste PART 1 into a new chat to resume at full speed.*

---

## PART 1 — CONTEXT PROMPT (paste this first in a new chat)

> I'm K Anirudh, MTech AI & DS at SASTRA, targeting placements Nov 2026–Jan 2027.
>
> **Project: Vigil** — an agentic incident triage service. It ingests signals from a running production service (DocuMind, my deployed RAG SaaS), gathers evidence using tools, and decides what a human should do: page, notify, watch, or suppress. Its focus is **silent degradation** — failures where the service returns HTTP 200, error rate stays at 0%, and the output is quietly wrong.
>
> **Origin:** my own outage. Groq decommissioned the model DocuMind used, the exception was swallowed by a broken logging format, and the app returned plausible-looking fallback text for two days. Uptime 100%, error rate 0%, completely broken. Conventional monitoring cannot see this. Vigil is built to catch it.
>
> **Stack:** FastAPI · SQLite (SQLAlchemy + Alembic) · Groq (tool calling, openai/gpt-oss-120b) · pytest · Docker. Auth and migration patterns reused from DocuMind.
>
> **Environment:** Windows 11, PowerShell, Python 3.12, venv at `.venv`, 8GB RAM, no Docker locally.
>
> **Key design decisions already made:**
> - `decide` is a tool, not parsed text — forces structured terminal output
> - The system prompt contains a cost model, NOT a signal→action mapping
> - A rules baseline is written BEFORE the agent, so it can't be tuned to lose
> - Ground truth comes from deliberate fault injection, not a static dataset
> - Expected actions are labelled BEFORE running the agent and never revised after
>
> Current task: [state what you're doing]

---

## PART 2 — THE IDEA

### The problem

Production systems fail in two ways.

**Loud failures** — crashes, 500s, timeouts — are already solved. Every monitoring
tool catches them.

**Silent degradation** — the service returns 200, latency looks normal, and the
output is wrong — is not solved. Nothing alerts, because by every conventional
metric the system is healthy.

Concrete example, from my own deployment:

| Metric | Reading during a 2-day total failure |
|---|---|
| Uptime | 100% |
| HTTP error rate | 0% |
| p95 latency | Normal |
| Actual user experience | Every answer wrong |

The LLM provider had decommissioned the model. The exception was caught and
converted into a fallback that returned real-looking text. No monitor could see it.

### What Vigil does

Ingests signals → gathers evidence with tools → decides what a human needs to see.

The value is not detection. It is **triage under alert fatigue**: deciding what
deserves a human's attention, and specifically catching the failures that don't
announce themselves.

### Why an agent and not a rules engine

For loud failures, rules are fine and Vigil should not beat them by much. The agent
earns its place because **the same signal means different things in different
contexts**, and knowing which requires gathering evidence first.

| Signal | Context the agent must fetch | Correct action |
|---|---|---|
| 500 rate spike | Deploy 4 min ago | page — likely regression |
| Same 500 spike | No deploy, matches incident #12, self-resolved twice | watch |
| Latency +300ms | Traffic up 4x | suppress — expected under load |
| Fallback counter rising, 0 errors | Provider returning 400s upstream | **page — silent degradation** |
| 404 storm on frontend | Zero backend logs at all | notify — config error, not outage |

Rows 1 and 2 are the same signal with different answers. Row 4 is invisible to
conventional monitoring. Row 5 is a client misconfiguration where the backend has
nothing in its logs because requests never arrived.

**The agency is in choosing what evidence to gather, in what order, and when it has
enough to act.** Not in the final if-statement.

### The thesis (state before running the eval)

> Vigil will roughly tie the rules baseline on loud failures and beat it decisively
> on silent degradation, because a rules baseline written before experiencing a
> silent failure will not contain a rule for it.

If the data disagrees, report that. A disproved thesis honestly reported is worth
more than an unmeasured claim.

---

## PART 3 — REPOSITORY STRUCTURE

```
vigil/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app, logging config, routes
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py            # pydantic-settings (NEVER os.getenv)
│   │   └── logging.py           # JsonFormatter
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py              # SQLAlchemy declarative base
│   │   ├── models.py            # events, deploys, incidents, decisions
│   │   └── session.py
│   ├── collector/
│   │   ├── __init__.py
│   │   ├── poller.py            # polls target /metrics, writes events
│   │   └── detectors.py         # threshold logic → event kinds
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── orchestrator.py      # tool-calling loop
│   │   ├── tools.py             # TOOLS schema + TOOL_IMPL dispatch
│   │   ├── prompts.py           # SYSTEM_PROMPT
│   │   └── baseline.py          # rules_triage() — write this FIRST
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── events.py            # POST /events, GET /events
│   │   ├── incidents.py         # GET /incidents, GET /incidents/{id}
│   │   └── health.py            # GET /health, GET /metrics
│   └── services/
│       ├── __init__.py
│       └── notify.py            # page/notify sinks (logged, not real SMS)
├── harness/
│   ├── __init__.py
│   ├── scenarios.py             # the 12 fault scenarios
│   ├── inject.py                # runs a scenario against the target
│   └── labels.yaml              # expected action per scenario — WRITE FIRST
├── eval/
│   ├── run_eval.py              # agent vs baseline over labelled events
│   └── report.py                # prints the comparison table
├── migrations/                  # alembic
├── tests/
│   ├── test_tools.py
│   ├── test_orchestrator.py
│   └── test_baseline.py
├── .env.example
├── .gitignore
├── requirements.txt
├── Dockerfile
├── README.md                    # includes the LIMITATIONS section
└── VIGIL.md                     # this file
```

**Rule:** `baseline.py` and `labels.yaml` are written before `orchestrator.py`.
This is not a style preference — it is what makes the evaluation credible.

---

## PART 4 — DATA MODEL

```sql
-- raw signals from the collector
events(
  id, ts, service, kind, payload_json,
  window_start, window_end
)
-- kind ∈ {error_spike, latency_spike, fallback_spike, status_storm,
--         traffic_spike, zero_chunks, heartbeat}

-- deploys, so the agent can correlate
deploys(id, ts, service, commit_sha, note)

-- incidents the agent opened
incidents(
  id, opened_at, closed_at, service,
  severity, summary, status
)
-- severity ∈ {page, notify, watch}

-- one row per triage run — audit trail AND eval dataset
decisions(
  id, event_id, ts, action, severity, rationale,
  tool_calls_json, rounds, tokens_in, tokens_out, latency_ms,
  expected_action, correct
)
```

`decisions` does double duty: auditable trace for a human, and the evaluation table.
Scoring becomes a single SQL query.

---

## PART 5 — TOOLS

Groq is OpenAI-compatible. Five tools; four gather evidence, one terminates.

| Tool | Purpose |
|---|---|
| `get_recent_logs(service, minutes, level?, contains?)` | Error messages, tracebacks, warnings behind a signal |
| `get_metrics(service, metric, minutes)` | error_rate, latency_p95, fallback_rate, requests_total, chunks_total |
| `get_deploy_history(service, hours)` | Recent deploy makes regression more likely |
| `search_past_incidents(query, limit?)` | Is this novel or recurring? |
| `decide(action, summary, rationale, confidence)` | **Terminal.** Called exactly once. |

**Design decision to defend in interviews:** `decide` is a tool rather than a parsed
text response. It forces structured output, makes the terminal step explicit, and
gives a clean loop exit. Everything before it is evidence gathering.

`action` ∈ {page, notify, watch, suppress}
`confidence` ∈ {low, medium, high}

---

## PART 6 — THE SYSTEM PROMPT

Note what it does **not** contain: any mapping from signal to action.

```
You are an on-call triage agent for a production service. A signal has fired.
Your job is to decide what a human should do about it.

COSTS, and they are asymmetric:
- Paging a human at 3am for a non-issue is expensive. It burns trust, and an
  engineer who stops trusting pages will eventually ignore a real one.
- Missing a genuine user-facing failure is more expensive still.
- When evidence is thin, gathering more is cheap. Prefer it to guessing.

METHOD:
- Do not decide from the signal alone. Gather evidence first.
- A signal's meaning depends on context: what deployed recently, whether this
  has happened before, whether traffic changed, what the logs actually say.
- Note that a service can fail while returning HTTP 200 and reporting a zero
  error rate. Healthy-looking top-line metrics are not proof that users are
  being served correctly.
- Stop gathering when more evidence would not change your action.

ACTIONS:
- page      wake someone now
- notify    send to a channel for working hours
- watch     record and re-evaluate if it recurs
- suppress  expected or benign; no human action

OUTPUT:
Call `decide` exactly once. In `rationale`, cite the specific tool outputs that
support your action. If you are unsure, say so and set confidence accordingly —
an honest low-confidence notify is better than a confident wrong page.
```

The HTTP-200 line is doing real work: it points at silent degradation **without
telling the agent what to do about it**. That is the difference between supplying
context and hardcoding a decision tree — and it is the evidence to cite when an
interviewer asks how you avoided baking in the answer.

---

## PART 7 — ORCHESTRATOR LOOP

```python
def triage(event, max_rounds=6):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": format_event(event)},
    ]
    calls = []

    for round_i in range(max_rounds):
        resp = client.chat.completions.create(
            model=settings.groq_model, messages=messages,
            tools=TOOLS, tool_choice="auto",
        )
        msg = resp.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            continue

        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            calls.append({"round": round_i, "tool": name, "args": args})

            if name == "decide":
                return record_decision(event, args, calls, rounds=round_i + 1)

            result = TOOL_IMPL[name](**args)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result)})

    # ran out of rounds without deciding — fail LOUD, never silent
    return record_decision(event, {
        "action": "notify", "confidence": "low",
        "summary": "Triage inconclusive after max rounds",
        "rationale": "Agent did not reach a decision within the round budget.",
    }, calls, rounds=max_rounds)
```

The final fallback matters, and it is the direct lesson from the DocuMind outage:
**when the system cannot do its job, it must say so loudly rather than return
something that looks like an answer.**

---

## PART 8 — RULES BASELINE (write before the agent)

```python
def rules_triage(event):
    m = event["metrics"]
    if m["error_rate"] > 0.10:          return "page"
    if m["latency_p95_ms"] > 5000:      return "notify"
    if m["error_rate"] > 0.02:          return "notify"
    if m["traffic_ratio"] > 3:          return "suppress"
    return "suppress"
```

Deliberately contains **no fallback-rate rule** — because a real team would not have
thought to add one before experiencing a silent failure. That is the honest baseline.

Do not touch this file after the agent exists. If you tune the baseline down, the
comparison is worthless and you will know it.

---

## PART 9 — FAULT INJECTION HARNESS

Ground truth comes from deliberately breaking DocuMind and recording the telemetry.
Better than a static dataset because you control the labels.

| # | Fault | Expected action |
|---|---|---|
| 1 | Invalid LLM API key | page |
| 2 | Model decommissioned (400 from provider) | page |
| 3 | LLM timeout / very slow responses | notify |
| 4 | Chroma collection emptied | page |
| 5 | Migration not applied → 500s | page |
| 6 | Frontend pointed at wrong API base | notify (config, not outage) |
| 7 | **Silent fallback firing, all 200s** | **page** |
| 8 | Rate limiter misconfigured, 429 storm | notify |
| 9 | Traffic spike, no errors | suppress |
| 10 | Single transient 500, self-resolved | suppress |
| 11 | Repeated transient at same endpoint, 3rd today | notify — pattern |
| 12 | Normal traffic (control) | suppress |

Run each 5 times with variation → **60 labelled events**.

**Discipline:** write `harness/labels.yaml` before running the agent even once,
and never revise a label after seeing a result. Note in the README that you are
both the labeller and the person hoping the system does well — naming that bias
is what makes the number believable.

Scenarios 6 and 7 are the ones that matter. Both were real bugs in DocuMind.

---

## PART 10 — EVALUATION

Metrics, computed for agent and baseline over the same 60 events:

| Metric | Why it matters |
|---|---|
| **Action accuracy** | Exact match vs expected |
| **Page precision** | Of everything paged, how much was real. This is alert fatigue, quantified. False pages are the expensive error. |
| **Silent-degradation recall** | Scenarios 6 and 7 only. Expect agent ≫ baseline. |
| **Median tool calls per decision** | Is it gathering evidence or guessing? |
| **Tokens / cost per event** | Honest scaling answer |
| **p95 decision latency** | Triage that takes 40s is not triage |

Report the table regardless of outcome. "Agent tied on loud failures, caught 2/2
silent failures the baseline missed at 1.4x token cost" is a real engineering
result and a far better interview answer than a win with no numbers.

---

## PART 11 — PHASES

| Phase | Days | Output |
|---|---|---|
| **0 — Instrument DocuMind** | 2 | JSON logs, `/metrics`, fallback counter |
| **1 — Fault harness** | 3 | 12 scenarios runnable, `labels.yaml` written |
| **2 — Agent** | 6 | Orchestrator + 5 tools, decisions persisted |
| **3 — State & correlation** | 3 | Incident history, repeat detection, past-incident search |
| **4 — Baseline & eval** | 4 | Comparison table |
| **5 — Dashboard & deploy** | 2 | Incident feed with expandable decision trace; HF Space |

≈3 weeks part-time alongside DSA. **Phase 0 happens inside the DocuMind repo**, and
requires DocuMind to be deployed first — there is nothing to instrument otherwise.

### Phase 0 detail — instrumenting DocuMind

**1. JSON log formatter** (reintroduces `extra={}` fields safely — `hasattr` check
is what prevents the ValueError that broke logging the first time):

```python
import json, logging

class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "org_id", "latency_ms", "status_code",
                    "endpoint", "provider", "fallback"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)
```

**2. Fallback counter.** Every time `_build_local_answer` fires from an exception
path, increment a counter and log at WARNING with `fallback=True`. **This is the
single most important line of instrumentation in the project** — it is the only
signal that would have caught the original outage.

**3. `/metrics` endpoint** (plain JSON, in-memory counters, token-protected):

```json
{
  "uptime_s": 4821,
  "requests_total": 143,
  "requests_by_status": {"200": 139, "404": 3, "500": 1},
  "chat_requests_total": 47,
  "fallback_total": 0,
  "fallback_rate_5m": 0.0,
  "latency_p50_ms": 890,
  "latency_p95_ms": 2400,
  "llm_provider": "groq",
  "llm_model": "openai/gpt-oss-120b",
  "documents_total": 3,
  "chunks_total": 24
}
```

`chunks_total` matters more than it looks: if Chroma empties, requests still return
200 with "I couldn't find anything relevant" — another silent failure. The agent
needs to see chunks drop to zero while traffic continues.

---

## PART 12 — LIMITATIONS (write these yourself, in the README, before anyone asks)

- Faults are synthetic and injected, not observed in production traffic
- Single service; real incident correlation spans many services
- One LLM call per event does not scale to high-volume alerting — this belongs at
  the aggregation layer, not per-request
- Ground truth is self-labelled, n=60, by the same person who wants it to succeed
- No real on-call rotation to validate against; "correct" is my judgment, not a
  team's post-incident review
- The rules baseline is one I wrote; a mature team's rules would be stronger

Naming your own limits before being asked is the clearest MTech-vs-BTech signal
there is. Most candidates claim their project works. Few can say precisely how it
fails.

---

## PART 13 — AGENT EXECUTION PROMPTS

Use these with a coding agent. Tight scope, named files, one phase at a time.
**Review every diff** — you will be asked to explain this code in interviews.

### Prompt A — scaffold

```
Create the Vigil project skeleton. FastAPI + SQLAlchemy + Alembic + SQLite,
Python 3.12, Windows/PowerShell environment.

Create exactly this structure (empty modules with docstrings where noted):
[paste the tree from PART 3]

Requirements:
- app/core/config.py uses pydantic-settings. NEVER use os.getenv anywhere.
  Settings must include: groq_api_key, groq_model, database_url, target_service_url,
  target_metrics_token, debug (bool, default False).
- app/core/logging.py contains the JsonFormatter from PART 11.
- logging.basicConfig must use force=True and a format string that does NOT
  reference custom fields.
- .gitignore covers: .env, *.db, .venv/, __pycache__/, .pytest_cache/, .pytest-tmp/
- .env.example with all keys present and BLANK values.
- requirements.txt with pinned versions.

Do not implement the agent yet. Structure only. Show me the tree when done.
```

### Prompt B — baseline and labels FIRST

```
Implement app/agent/baseline.py exactly as specified:
[paste the rules_triage function from PART 8]

Then create harness/labels.yaml mapping each of the 12 scenario ids to its
expected action:
[paste the table from PART 9]

Do NOT add a fallback-rate rule to the baseline. Its absence is intentional and
is part of the experiment. Do not modify labels.yaml in any later task.
```

### Prompt C — tools

```
Implement app/agent/tools.py with the five tool schemas in OpenAI function-calling
format: get_recent_logs, get_metrics, get_deploy_history, search_past_incidents,
decide. Signatures and descriptions as specified in PART 5 of VIGIL.md.

Also implement TOOL_IMPL, a dict mapping tool name to its Python implementation.
The four evidence tools query the local SQLite tables. `decide` has no
implementation — the orchestrator intercepts it as the terminal action.

Add tests in tests/test_tools.py: each evidence tool returns correctly shaped
data on an empty DB without raising.
```

### Prompt D — orchestrator

```
Implement app/agent/orchestrator.py with the triage() loop from PART 7 of VIGIL.md
and the SYSTEM_PROMPT from PART 6 in app/agent/prompts.py.

Requirements:
- max_rounds default 6
- Every tool call recorded with round number, name, and arguments
- Timing, token counts, and full tool chain persisted to the decisions table
- If max_rounds is exhausted without a decide call, record action="notify",
  confidence="low" and a rationale saying triage was inconclusive.
  It must NEVER silently return a default.
- Wrap the Groq call in try/except that logs the exception with logger.exception
  before re-raising. No silent failures anywhere in this file.

Do not add any signal→action logic to the prompt or the code. The decision must
come from the model.
```

### Prompt E — eval

```
Implement eval/run_eval.py and eval/report.py.

run_eval.py loads all labelled events, runs both rules_triage and the agent over
each, and writes results to the decisions table with expected_action populated.

report.py prints a comparison table with: action accuracy, page precision,
silent-degradation recall (scenarios 6 and 7 only), median tool calls per
decision, mean tokens per event, and p95 decision latency — for agent and
baseline side by side.

Print the raw confusion matrix too. Do not round numbers to flatter either side.
```

---

## PART 14 — HARD-WON LESSONS (carried from DocuMind, do not relearn)

- **Never let a fallback impersonate a success.** Two days lost to exactly this.
  Every degraded path must be visibly labelled as degraded.
- **A broken logging format hides the error that would have saved you.** Fix
  observability before debugging anything else.
- **Pin model IDs in config and watch provider deprecation pages.**
  `llama-3.3-70b-versatile` was decommissioned mid-project with no warning to me.
- **Config that silently defaults is a bug.** `VITE_API_URL` falling back to `""`
  cost hours. Missing config should raise, not degrade.
- **Client state keyed to server rows must be validated on load.** Stale
  localStorage IDs outlive the rows they point to.
- **PowerShell:** `&&` doesn't chain — use `;`. Nested quotes in `python -c` are a
  losing battle; write a temp file. Pytest temp cleanup fails — use
  `--basetemp=.pytest-tmp`.
- **Settings cached with `@lru_cache` need a full restart**, not `--reload`.
- **Never paste `.env` into a chat or screenshot it.** Any key seen on a shared
  screen is burned — rotate it.
- **Review every agent diff.** Watch for the failure mode where an agent weakens
  production code to satisfy a stale test. The test should model reality.

---

## PART 15 — CV LINE (target form, once evals exist)

> **Vigil — Agentic Incident Triage** (live link · GitHub)
> FastAPI · Groq tool calling · SQLite · Docker
> LLM agent that triages production signals by gathering evidence through 5 tools
> before deciding page/notify/watch/suppress. Evaluated against a rules baseline
> over 60 fault-injected events: matched on conventional failures, caught N/N
> silent-degradation cases the baseline missed. Full decision traces persisted for
> audit.

The numbers are what make it MTech-level. Without the eval it is a tool-calling demo.

---

*Created August 2026. Prerequisite: DocuMind deployed and instrumented (Phase 0).*
*Next action: deploy DocuMind, then Phase 0.*
