# RAN energy saving — A2A-T client for real-NMS integration

This sample is the client side of a RAN energy-saving integration: an ADK agent reads the
operator's request, the A2A-T SDK produces and validates the Task-T, and the client negotiates
with the vendor NMS (or with the local reference RAN server) until the Task is accepted and
executed. Every decision is model-driven; there is no scripted/offline decision mode.

The same client serves two inbound surfaces:

- a **Web UI** (presentation or run explorer) operated by an engineer;
- an **A2A gateway** for callers such as Gemini Enterprise.

It also ships a **local RAN reference server** so the client can be exercised without a vendor.

```
engineer ── Web UI ──┐
                     ├── A2A-T / standard A2A client ── real NMS (message:send + webhook)
Gemini Enterprise ── gateway ──┘
                     └── local reference server (optional)
```

## 1. Quick start (real NMS)

1. Copy `env.example` to `.env` and fill in:
   - `ADK_MODEL_*` (required) and, when different, `A2AT_LLM_*` for the SDK structured calls;
   - `A2AT_SAMPLE_NMS_*` (A2A surface, token endpoint, user/password, served region and sites);
   - `A2AT_SAMPLE_WEBHOOK_PUBLIC_URL` when the NMS reaches this host through NAT/proxy;
   - `A2AT_SAMPLE_GATEWAY_PUBLIC_URL` when the gateway is used.
2. Start the client against the vendor:

```bash
python run_demo.py --nms --ui --protocol both
```

A real `--nms` run always uses the webhook transport and requires
`A2AT_SAMPLE_LONG_TASK_TRACKING=1` in the delivered `.env` template.

3. Open `http://127.0.0.1:8080/`, choose the protocol (A2A-T or standard A2A), enter the
   operator request, and review the evidence in `/runs`.

### Gateway for Gemini Enterprise

```bash
python run_demo.py --nms --ui --gateway \
  --gateway-public-url https://ran-client.example/gateway \
  --gateway-port 8001 --gateway-webhook-port 9001 \
  --gateway-downstream-protocol a2a-t
```

- The gateway AgentCard advertises `A2AT_SAMPLE_GATEWAY_PUBLIC_URL`; when
  `A2AT_SAMPLE_AUTH_TOKEN` is set the card declares the bearer scheme and the gateway enforces it.
- One inbound message maps to one client run and one new inbound Task; a follow-up on the same
  Task is not supported.
- The caller identity comes from the authenticated A2A call context (`user_name`), not a fixed
  placeholder.
- The UI and the gateway bind **separate** callback receivers; both must be reachable by the NMS.

### Local reference server

```bash
python run_demo.py --ui            # local RAN server + UI, direct AgentCard discovery
python run_demo.py --stop          # stop the launcher-owned processes
```

## 2. What the client does

1. Reads the operator request with the ADK intent layer (display and approval 1 only).
2. Lets the SDK produce the Task-T from the operator's **original text**; the sample never
   rewrites the request or guesses slots.
3. Validates the Task-T before sending (`A2AT_SAMPLE_PRE_SEND_VALIDATION`).
4. Negotiates with the NMS: information / target / feasibility rounds, with the client ADK agent
   producing every reply and decision.
5. Sends the accepted request, follows the Task to its terminal state (including long tasks),
   and publishes the report.

Business approval 1 (`--mode confirm`) is available in the Web UI. Execution approval belongs to
the vendor NMS; the sample has no local approval/MML mock.

## 3. Tester console

`/tester` runs the OSP case corpus (`eval/osp/cases`) against the configured endpoint and exports
an xlsx report with per-stage evidence: Task-T, validation, send, negotiation, accepted, working,
final. The client always uses the live decision agent; Q/P columns are kept as expectation
reference data and are not replayed.

```bash
# Start the UI first, then open /tester.
A2AT_SAMPLE_MODEL_MODE=live python run_demo.py --nms --ui
```

The acceptance run must target the real NMS; the local reference server is for pre-lab rehearsal
only.

## 4. Observability

- `/runs` is the read-only run console: persisted runs, wire captures, transcript, trace timeline.
- Every run writes `runs/<runId>/wire.jsonl` and `runs/<runId>/server-trace.jsonl`.
- Langfuse export is optional (`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`); without keys it is a
  no-op. It never changes a protocol result.

## 5. Configuration

All keys are documented in `env.example`. Precedence:
CLI arguments > shell environment > per-run `.env` > source `.env` > defaults.

Delivery-critical keys:

| Key | Meaning |
|---|---|
| `ADK_MODEL_*` / `A2AT_LLM_*` | model endpoint of the ADK agents and the SDK structured calls |
| `A2AT_SAMPLE_NMS_*` | vendor A2A surface, token endpoint and served inventory |
| `A2AT_SAMPLE_WEBHOOK_*` | UI callback receiver and its public URL |
| `A2AT_SAMPLE_GATEWAY_*` | gateway public URL, callback port and downstream protocol |
| `A2AT_SAMPLE_LONG_TASK_TRACKING` | keep tracking a Task after the observation window |
| `A2AT_SAMPLE_AUTH_TOKEN` / `SERVER_AUTH_TOKEN` | bearer tokens of the UI/gateway and the local server |

## 6. Tests

```bash
PYTHONPATH=. ../../.venv/Scripts/python.exe -m pytest tests -q
../../.venv/Scripts/python.exe -m ruff check shared protocol demo tests run_demo.py
```

Unit tests replace the model and the SDK structured calls with test-only doubles under
`tests/support/`; integration and acceptance runs use the configured live endpoint and the real
NMS. The suite is the only automated gate — CI does not run this sample.
