# Trace and metrics (delivery profile)

## 1. Where spans come from

| Producer | Module | Scope |
|---|---|---|
| Client flow | `protocol/client_flow.py` | send, negotiation, decision, Task lifecycle |
| Client agent | `shared/adk_tracing.py` | ADK decision and intent turns |
| SDK structured calls | `shared/traced_llm.py` | Task-T slot extraction |
| Local server | `protocol/server_executor.py` | planner turns, tool calls, Task states |
| Gateway | `protocol/client_gateway.py` | inbound Task mapping |

Every run writes `runs/<runId>/wire.jsonl` (raw A2A/NMS frames) and, for the local server,
`runs/<runId>/server-trace.jsonl`. The Web UI can also ingest spans over HTTP when the server and
UI run as separate processes.

## 2. Identity

Four identities must not be mixed:

- `runId`: the client run;
- vendor `traceId` / `taskId` / `contextId`: the NMS Task;
- Langfuse trace id: the export;
- OTel span / parent span id: the trace tree.

`traceparent` is propagated on every hop. The UI correlates spans by run id; the vendor path keeps
the run id in the correlation header as well.

## 3. Negotiation stages

A2A-T runs publish `propose` / `accept` / `reject` / `abort` decisions and their rounds. Standard
A2A runs publish the `INPUT_REQUIRED` question and the client's answer. In both cases the
decision agent is the author; the deterministic flow owns the performative and the round count.

## 4. Metrics

`protocol/run_metrics.py` builds one document per run:

- tokens and call counts, split by prompt / completion / cache hit;
- latency split into model, tool and the remaining orchestration time;
- `negotiation`: rounds, messages, observed sequence, outcome;
- `accuracy`: terminal state and report presence. For a real-NMS run the recorded demo sequence
  and target are **not** compared; only the terminal state and report presence remain ground truth.

The document is stored in `runs/client-state.sqlite3` and exported as CSV/xlsx by the tester.
There is no separate metrics console or comparison page in this delivery.

## 5. Langfuse

The OTLP exporter is optional: with `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` it exports
the run's spans; without them it is a no-op. Export failures are logged and never change a
protocol result.

## 6. Retention

`runs/<runId>/` files are the durable evidence. SQLite keeps queryable state and the negotiation
audit; it does not store full prompts. Terminal Task retention follows
`A2AT_SAMPLE_TASK_RETENTION_DAYS`.
