# Nightwarden: design notes

## The problem

Services fail in two ways. **Loud** failures (crashes, 500s, timeouts) are solved: every monitor catches them.
**Silent** failures are not. The service answers 200, latency is normal, and the output is wrong.

Citebase had one. Its LLM provider retired the model it called; the exception was caught and turned into a
fallback answer; a broken log format hid the error. For two days:

| Signal | Reading |
|---|---|
| Uptime | 100% |
| HTTP error rate | 0% |
| p95 latency | normal |
| What users got | a fallback instead of every answer |

Nightwarden's job is not detection alone. It is **triage**: deciding which signals deserve a human, and when.

## Decisions

| Decision | Why |
|---|---|
| `decide` is a tool, not parsed text | Forces a structured, validated end to every investigation. Invalid arguments go back to the model as an error. |
| Prompt has a cost model, no signal→action table | A table would just be the rules baseline with extra latency. The model has to reason from evidence. |
| Rules baseline and labels frozen before the agent | Neither can be tuned after seeing agent results. |
| Fixtures replay real Citebase log shapes | The eval runs offline, deterministically (seeded), with no live traffic. |
| Fail loud when the model is down | Rules result raised to at least notify; silent-failure kinds always page. A monitor that goes quiet when its AI breaks is worse than none. |
| Out of rounds → notify, low confidence, marked inconclusive | An honest "I don't know" reaches a human in working hours. |
| Incidents merge for 6 h, alert only on open or escalate | One page per problem, not one per poll. |
| Read-only tools, no remediation | The agent can't restart, roll back or change anything. |
| Logs redacted at collection (UUIDs, emails) | Nothing identifying is stored or sent to the model. |
| Separate host from Citebase | If Citebase's host dies, the watcher still runs. Plus Render's free hours are already used by Citebase. |

## Signals

Counters from `/metrics` become per-poll deltas, then a 5-minute window:

- `error_rate` (5xx / requests), `rate_4xx`, `rate_429`
- `latency_p95_ms`
- `fallback_rate`, `fallback_5m` (answers that fell back instead of using the LLM)
- `empty_retrieval_rate` (questions where search found nothing), `chunks_total`
- `traffic_ratio` (requests vs. the typical 5-minute count over 24 h)

Detectors (cooldown 30 min per kind):

| Kind | Fires when |
|---|---|
| error_spike | ≥5 requests and error rate ≥5% |
| status_storm | ≥20 requests and 4xx rate ≥30% |
| latency_spike | p95 ≥5 s |
| fallback_spike | ≥2 fallbacks and fallback rate ≥20% |
| zero_chunks | no chunks while documents exist, or ≥3 chats with ≥50% empty retrieval |
| traffic_spike / traffic_drop | ≥3× typical with ≥30 requests / ≤10% of a typical ≥20 |
| target_down | 3 failed polls in a row |
| restart | uptime reset without a new commit (after >10 min up) |

## Evaluation design

12 scenarios × 5 seeded variations = 60 cases. Scenarios 4 (vector store emptied), 6 (wrong API base) and 7
(silent fallback) are silent. 6 and 7 were real Citebase bugs. Controls (9, 10, 12) check that the agent doesn't
page for benign noise; 10 and 11 are the same transient 500, once versus repeating, to test whether the agent uses
incident history.

Reported metrics: action accuracy, page precision and recall, silent-degradation recall, accuracy on loud failures,
plus cost (tool calls, tokens, p95 decision time) and the number of inconclusive runs. A test checks that no label
or scenario name leaks into anything the agent can see.

## Known limits

- The labels were written by the person who built the agent.
- 60 cases is small; one scenario swinging changes accuracy by about 8 points.
- Fixtures are modelled on Citebase's logs, not recorded from real incidents.
- One target service, one model. Groq free-tier rate limits slow the eval (see `--pause`).
- The dashboard is public and read-only. It shows Citebase's health and redacted log excerpts, never credentials.
