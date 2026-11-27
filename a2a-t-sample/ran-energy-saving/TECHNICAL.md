# RAN energy saving — technical notes (delivery profile)

This document describes the delivered profile: a live, agent-driven A2A-T client for a real
vendor NMS. It replaces the earlier demo-oriented notes that described offline/scripted modes,
the OSS proxy, MIE approval and the internal observation/review consoles.

## 1. Process topology

| Process | Module | Default port | Role |
|---|---|---:|---|
| Web UI | `demo.web.serve` | 8080 | presentation + `/tester` + `/runs` |
| Inbound gateway | `protocol.client_gateway` | 8001 | A2A caller (Gemini Enterprise) → client runs |
| Local RAN server | `protocol.server_app` | 8000 | reference server for pre-lab runs |
| Callback receiver | inside UI/gateway | 9000 / 9001 | NMS push notifications |

A real-NMS deployment is `UI (+ optional gateway) → vendor NMS`. The gateway is a separate
inbound surface; it does not proxy the Web UI.

## 2. Protocol paths

- **A2A-T**: Task-T + Negotiation-T extension. The SDK extracts the slots and generates the
  Task-T from the operator's original text; a `slot.*` failure blocks before a Task-T exists, and
  the client agent answers information/target/feasibility proposals.
- **standard A2A**: a plain message and `TASK_STATE_INPUT_REQUIRED` continuation. Both paths use
  the same decision agent and the same run/task stores.
- The pure hop is **direct**: `UI → NMS` or `UI → local server`. The deprecated
  `UI → OSS Agent → downstream` proxy is not part of the delivery.

## 3. Decision ownership

1. **Intent layer** (`demo/agents/intent_agent`): display and approval 1 only. It never rewrites
   the request and never produces Task-T values.
2. **SDK slot extraction and validation**: the only source of the Task-T and of the structured
   validation verdict.
3. **Client decision agent** (`demo/agents/client_agent`): authors negotiation replies and the
   accept/reject/abort decision from the server proposal, the original request and the frozen
   negotiation principles.
4. **Deterministic rules** decide protocol correctness: performatives, rounds, Task lifecycle,
   terminal states and precondition checks. The model never owns those.

There is no offline or scripted decision provider in the product. Unit tests use the doubles in
`tests/support/`; acceptance runs use the live endpoint.

## 4. Identity and correlation

- `runId`, `conversationId`, task/context ids and `traceparent` travel end to end.
- Webhook pushes are attributed by task/context id; a push carrying an unknown identity is never
  delivered to "the only run".
- The inbound gateway maps every message to one new Task; the caller identity comes from the
  authenticated A2A call context.
- The gateway card advertises the configured public URL and, when a token is configured, the
  bearer security scheme.

## 5. Long-running Tasks

`A2AT_SAMPLE_LONG_TASK_TRACKING=1` keeps the client observing a Task after the synchronous
window. The OSS hop and its outer-Task bindings were removed; the client tracks the vendor Task
directly. A real-NMS run is not scored against recorded demo sequences: only the terminal state
and report presence are compared.

## 6. Persistence

| File | Content |
|---|---|
| `runs/client-state.sqlite3` | conversations, turns, runs, negotiation events, task snapshots, metrics, ADK sessions |
| `runs/task-status.sqlite3` | local server Tasks |
| `runs/gateway-task-status.sqlite3` | inbound gateway Tasks |
| `runs/<runId>/wire.jsonl` | raw NMS exchange |
| `runs/<runId>/server-trace.jsonl` | local server spans |

## 7. Observability

- `/runs` is the read-only run console (SQLite + wire + trace).
- Langfuse OTLP export is optional and best-effort; without keys it is a no-op.
- Trace, Langfuse and metrics failures never change a protocol result.

## 8. Gateway contract

- Input: a plain A2A message with at least one non-empty text part. Empty text is rejected.
- One message = one inbound Task = one client run; same-Task follow-ups are not supported.
- Downstream protocol: `a2a-t` (default) or `a2a`, configured by
  `A2AT_SAMPLE_GATEWAY_DOWNSTREAM_PROTOCOL`.
- Callback receiver: `A2AT_SAMPLE_GATEWAY_WEBHOOK_PORT`, separate from the UI receiver.
- Public URL: `A2AT_SAMPLE_GATEWAY_PUBLIC_URL`; TLS is terminated by the customer reverse proxy.
- Auth: `A2AT_SAMPLE_AUTH_TOKEN`; the card declares the bearer scheme when it is set.

## 9. Testing

- Unit tests: `pytest tests -q` with the test-only model/SDK doubles.
- Acceptance: `/tester` against the real NMS, plus the eight business scenarios.
- Lint: `ruff check shared protocol demo tests run_demo.py`.
- The sample is not part of CI; the local suite is the only automated gate.
