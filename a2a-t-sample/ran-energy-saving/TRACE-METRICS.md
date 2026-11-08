# Trace spans and metric collection (phase level)

> Applies to `feat/ran-energy-saving-adk` (after `6a82a12`).
> This guide explains every span that reaches Langfuse under the default
> `A2AT_SAMPLE_TRACE_SPAN_LEVEL=phase`, what it carries, and how it combines with run metrics,
> Prometheus and SQLite into end-to-end statistics.
> See also: `TECHNICAL.md` §8 (observability) and `docs-local/osp-eval-cases/README.md` (OSP
> workbook columns).

## 1. Data flow

```
RunState / ClientFlow / ServerExecutor / ADK / LangGraph
        │  one Span per event (kind + phase + startedAt/endedAt + detail/tokens)
        ▼
  run.traces (in memory, complete) ──► Web UI timeline / transcript.json (complete, diagnosis)
        │
        ├──► LangfuseSpanExporter (filtered by trace_span_level)
        │        └── one span = one Langfuse observation
        │
        └──► build_run_metrics (after the run, 2 s grace for late server spans)
                 ├── runs/client-state.sqlite3::run_metrics
                 └── runs/metrics/run-metrics.jsonl
                          ├── /api/metrics/runs|summary|export.csv, /comparison
                          └── summarize_metrics()
```

Identity keys (never mix them):

| Key | Meaning | Where it goes |
|---|---|---|
| `runId` | one run | spans, SQLite, Langfuse trace metadata |
| `traceId` | 32-hex downstream trace id, defaults to `runId` | Langfuse trace id |
| `conversationId` | conversation | Langfuse `session.id` |
| `caseId` | OSP case 1–51 (tester only) | Langfuse trace metadata |
| `taskId` / `contextId` | A2A task identity | span fields, SQLite, webhook routing |

## 2. Spans exported at `phase`

`phase` is an explicit allow-list, not "first span per phase". Every exported kind is a
time-point carrier or a token/latency source.

| kind | observation name | phase | producer | core content | used for |
|---|---|---|---|---|---|
| `llm` | `LLM call · <stage>` / `Planner turn` | per stage | `shared/traced_llm.py`, `shared/adk_tracing.py`, `server_executor` | request/response, model, `tokens{prompt,cached,completion,total}`, duration | AX/AY/AZ/BA, modelMs |
| `tool` | tool name / `Tool call · <name>` | negotiation / execution | ADK plugin, `server_executor` | name, args, result, duration | toolMs, execution evidence |
| `message_sent` | `Task-T sent` / `Accept sent` / `Counter proposal sent · Rn` / `Pure A2A message sent` | task_prompt / task_validation / negotiation / resolution | `client_flow`, `pure_client_flow`, `plain_client_flow` | performative, templateUri, `taskT` (≤4000 chars), `negotiationT`, messageId | AM, AS; Task-T text (X) |
| `task_prompt_rendered` | `Task-T prompt rendered` / `Modify …` | task_prompt / task_modify | `client_flow` | Task-T text, templateUri, **SDK call start/end/duration** | AL, X |
| `task_validation` | `Task-T validation <verdict>` | task_validation / task_modify | `client_flow` | verdict, errors, negotiationKind, reason | Y (blocked/pass) |
| `intent_processed` | `Intent processed` | task_prompt | `client_runtime` (A2A-T) / `run_state` (pure promotion) | rawRequest, normalizedText, structured intent, missingFields | Z (A2A side) |
| `task_created` | `Task created` / `Task modified` | execution | `run_state._maybe_emit_task_created` | operation, state=`TASK_STATE_SUBMITTED`, taskId/contextId | **AN** |
| `propose` | `Propose · <kind> · Rn` | negotiation | client receives a server PROPOSE (`record_event`) | round, templateUri, body, negotiationContext | **AO/AP**, AW |
| `negotiation_started` | `Negotiation started · <kind> · Rn` | negotiation | `pre_task_negotiation` (local RAN Agent) | round, kind, templateUri, server understanding window | **AT** |
| `accept` / `counter` / `reject` / `abort` | `Decision · <action> · Rn` | negotiation / resolution | `run_state.record_decision` | action, rationale, filled/counter params, confirmedBy | **AQ**, AW, BB |
| `approval` | `Approval requested` / `Approval resolved` | execution | `server_executor` (local gate / MIE) | approvalState, cellId, window, rate, timeout | human wait |
| `finished` | `Run finished` | done | `run_state.emit_finished` | finalState, report, error | end time, outcome |
| root | `run · <scenario> · <protocol>` | — | exporter | trace metadata (§5) | run window, result |
| event | `trace opened` | — | exporter | same trace attributes | early trace naming |

**`full` only** (diagnosis evidence, not in the dashboard by default): `run_started`,
`runtime_ready`, `discovery`, `intent_review_requested`, `task_tracking`, `decision_requested` /
`decision_made` / `decision_ungrounded`, `execution`, `artifact` (execution/report), status rows
(non-terminal and terminal), and `transport` (NMS token/stream/poll/error evidence).

**`minimal`** keeps `llm`, `tool`, `finished` (plus the last status when there is no `finished`).
It cannot support the OSP time points; use it only to inspect model/tool cost.

Filter: `shared/langfuse_tracing.py::LangfuseSpanExporter._should_export`. The Web UI timeline and
the transcript always keep every span; only Langfuse is filtered.

## 3. Mapping to the OSP columns

| Col | Metric | Phase-level source | Computation |
|---|---|---|---|
| AK | intent start | root span start; exact `source.receivedAt` in the tester metadata | start |
| AL | OSS calls the SDK | `task_prompt_rendered.startedAt` | start |
| AM | request dispatched | first `message_sent` of the hop | start |
| AN | task created/modified | `task_created.startedAt` | start |
| AO/AP | negotiation initiated / user notified | each round's `propose.startedAt` | start |
| AQ | user answer | each `Decision · …` `startedAt` (backdated to the submit in manual mode) | start |
| AR | SDK builds the reply | `negotiation_*_extraction` `llm.startedAt` | start |
| AS | reply dispatched | matching `message_sent.startedAt` | start |
| AT | model starts negotiating | `negotiation_started.startedAt` (local server) | start |
| AU | end-to-end | AN − AK | difference |
| AV | negotiation | AQ − AT, summed over rounds | difference |
| AW | rounds | trace metadata `rounds`, or `max(round)` | max |
| AX/AY/AZ | input/cached/output tokens | `usage_details.input/input_cached/output` of every generation | sum |
| BA | total tokens | AX + AZ, or `usage_details.total` | sum |
| BB | negotiated | trace metadata `negotiated` (or `rounds > 0`) | boolean |
| U | traceId | Langfuse trace id = `run.traceId` | direct |

When summing tokens, split by observation metadata `side` (`client` / `server`): an A2A vs A2A-T
comparison must show OSS-side and RAN-side separately, otherwise Task-T overhead and planner cost
are mixed.

## 4. Combining with the other metric layers

### 4.1 Per-run metrics (`protocol/run_metrics.py`)

After the run (plus `metrics_grace_seconds` for late server spans) one JSON document is built:

- `tokens`: `prompt/completion/total/cached`, `cacheHitRate`, `llmCalls`,
  `bySide`/`byComponent` (client/server × stage).
- `latency`: `totalMs` (startedAt→finishedAt), `modelMs` (sum of llm durations), `toolMs`,
  `approvalWaitMs` (requested/resolved pairing), `otherMs`.
- `negotiation`: `rounds`, `messages`, `observedSequence`, `outcome`.
- `accuracy`: final state, rounds, negotiated target, report presence; A2A-T also compares the
  performative sequence.

Persisted in `runs/client-state.sqlite3::run_metrics` and `runs/metrics/run-metrics.jsonl`.
Exposed by `GET /api/runs/{runId}/metrics`, `/api/metrics/runs`, `/api/metrics/summary`,
`/api/metrics/export.csv`, and the `/comparison` page.

**Relation to spans**: run metrics are derived from the same spans, so they should agree with
Langfuse. A mismatch usually means a server trace batch arrived after the grace window.

### 4.2 Operational aggregates (`protocol/metrics_summary.py`)

Grouped by `protocol` / `scenario`: `runs`, `passed`, `successRate`, `accuracy`, token average and
sum, `cacheHitRate`, latency average/p95, `avgRounds`. OSP back-filling simply groups by `caseId`.

### 4.3 Prometheus (`shared/metrics.py`, `/metrics` per process)

| Metric | Meaning |
|---|---|
| `a2at_client_runs_total{status,final_state}` | terminal run count |
| `a2at_client_runs_in_flight` | runs currently executing |
| `a2at_client_run_duration_seconds` | run duration summary |
| `a2at_negotiation_rounds_total{role,round}` | negotiation message count |
| `a2at_server_tasks_total{state}` | server Task outcomes |

Prometheus is for live trends and alerting; per-case OSP values still come from run metrics or
Langfuse.

### 4.4 Langfuse trace metadata

Written on the root span (`_root_attributes`): `runId`, `scenario`, `language`, `mode`, `protocol`,
`transport`, `discovery`, `intentMode`, `modelMode`, `modelLabel`, `runMode`, `sourceKind`,
`caseId`, `caseName`, `batchId`, `receivedAt`, `userRequest`; `phase`/`minimal` also write
`rounds` and `negotiated` at `finish()`. `session.id = conversationId` (a retry of the same
correction chain uses `chainId` and carries `parentRunId`), `user.id = userId`.

In Langfuse you can therefore filter by `protocol` / `caseId` / `modelMode` and sum generation
`usage_details` for AX/AY/AZ/BA, read `rounds`/`negotiated` for AW/BB, and use the
`Propose`/`Decision`/`task_created` observation timestamps for AO/AQ/AN.

## 5. Three end-to-end recipes

### 5.1 Fill one OSP row (recommended)

1. Start the run from the tester so `caseId/batchId/receivedAt` are recorded.
2. On finish read `run_metrics` (complete tokens/accuracy) or the Langfuse trace.
3. Fill AK–BB per §3; put `run.traceId` in column U.
4. Compare A2A and A2A-T on the same `caseId`.

### 5.2 Langfuse batch view

- Filter on `metadata.protocol`, `metadata.caseId`, `metadata.modelMode`, `metadata.sourceKind`.
- Metrics: summed generation usage, observation latency, `rounds`/`negotiated`.
- Limits: offline (scripted) runs are not exported; `minimal` has no time-point carriers.

### 5.3 Local batch statistics

```bash
# appended at the end of every run
runs/metrics/run-metrics.jsonl
# while the service runs
curl localhost:8080/api/metrics/summary
curl localhost:8080/api/metrics/export.csv
```

`summarize_metrics()` / `summarize_protocols()` recompute the aggregates from the JSONL without
the UI.

## 6. Known approximations and boundaries

- **AL/AR** are SDK/LLM call starts; SDK preprocessing is not included.
- **AQ** is exact only for manual/confirm UI submissions; tester decisions are scripted and carry
  no human time.
- **AT** is emitted only by the local RAN Agent. The real a2a-t-server must provide an equivalent
  signal per `docs-local/osp-metrics-a2at-server-handover.md`.
- **AN** uses the first `TASK_STATE_SUBMITTED`; a vendor with a different success signal must
  expose a machine-readable field.
- **pure A2A** has no per-round `propose`/`decision` spans (standard A2A INPUT_REQUIRED rounds);
  use `run_metrics.negotiation.rounds` and `message_sent`.
- **Token double count**: `run_metrics.tokens` contains both client and server; always split by
  `bySide`, and filter Langfuse observations by metadata `side`.
- **offline** runs mark tokens `scripted` and are not exported to Langfuse; they exist for
  deterministic regression only.
- **`transport`** appears only in `full`; real-NMS long polls never reach the dashboard.
