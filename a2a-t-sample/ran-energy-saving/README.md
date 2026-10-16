# RAN Energy Saving (Task-T) Sample

## Table of Contents

- [Overview](#overview)
  - [Project layout](#project-layout)
- [How to Run](#how-to-run)
  - [Run the two sides separately](#run-the-two-sides-separately)
  - [Configuration](#configuration)
- [Using the A2A-T SDK](#using-the-a2a-t-sdk)
  - [Client: generate and send a Task-T prompt](#client-generate-and-send-a-task-t-prompt)
  - [Server: validate, extract, and stream](#server-validate-extract-and-stream)
- [Negotiation scenario (Negotiation-T)](#negotiation-scenario-negotiation-t)
- [Webhook transport (A2A push notifications)](#webhook-transport-a2a-push-notifications)

## Overview

This sample is a complete **Task-T** example of the A2A-T Python SDK. It contains an A2A-T **client**
and **server** for a RAN energy-saving task, and a **registry center** for AgentCard discovery.

The sample runs this step-by-step interaction:

1. **Build the request** — the client calls the A2A-T client SDK's `generate_task_prompt_from_text` to
   turn the raw natural-language input into a Task-T task request (a rendered prompt).
2. **Send the request** — the client sends the task request to the server; the rendered prompt travels
   in `message.metadata`.
3. **Validate** — the server reads the prompt from `message.metadata` and calls the A2A-T
   server SDK's `validate_task_prompt_and_data_filling` to validate it and extract the parameters.
4. **Execute and report** — the server feeds the extracted parameters into its business logic, runs
   the energy-saving task, and generates the status updates and the energy-saving report sent to the
   client.
5. **Receive and present** — the client consumes the streamed events: the progress comes from the
   status update messages and the report body from `artifact.metadata`; it presents both.

### Project layout

| Path | Role |
| --- | --- |
| `src/client/` | client: registry discovery → `generate_task_prompt_from_text` → stream consumption |
| `src/server/` | server: `validate_task_prompt_and_data_filling` → stream progress and the intent report |
| `src/registry/` | mock registry center (AgentCard register/query, port 5001) |
| `src/common/` | shared adapters + the schema-routed mock LLM |
| `resources/mock_llm/{zh-CN,en-US}/` | scripted slot-extraction and content-validation responses |
| `run_demo.py` | cross-platform, non-blocking launcher (background registry/server, auto cleanup) |

## How to Run

> **Prerequisites:** Python (the repository `.venv`) and [`uv`](https://docs.astral.sh/uv/).

Run this once from the repository root and `a2a-t-sample/`:

```bash
# repository root: create .venv and install the SDK
uv sync --dev

# a2a-t-sample/ (the directory that holds .env)
cd a2a-t-sample
cp env.example .env
uv pip install -r requirements.txt
```

Then run the sample with a single cross-platform command:

```bash
cd a2a-t-sample/ran-energy-saving
python run_demo.py        # English (default)
```

`run_demo.py` starts the registry and the server as **background subprocesses** and runs the client
in the foreground. Both sides' output is streamed to the terminal **live and interleaved** as it
happens (their logs are also written to the run directory), then the background processes are
stopped. Use an interpreter that has the sample dependencies (the repository `.venv`), e.g.
`..\..\.venv\Scripts\python.exe run_demo.py` on Windows.

| Flag | Effect |
| --- | --- |
| `--language {en-US,zh-CN}` | Run language (default `en-US`) |
| `--scenario {default,negotiation}` | Scenario variant (default `default`; `negotiation` runs the missing-region information negotiation, see [Negotiation scenario](#negotiation-scenario-negotiation-t)) |
| `--negotiation {information,target,feasibility}` | Negotiation kind selecting the client scenario input (default `information` = missing region; `target` = 100Mbps throughput beyond cell capability; `feasibility` = 30% saving goal conflicting with a 50Mbps throughput target). `information` keeps the `--scenario` switch (only `--scenario negotiation` runs the missing-region flow); `target`/`feasibility` engage without it. The flag only selects the input — the server stays data-driven on the validation error code, see [Negotiation scenario](#negotiation-scenario-negotiation-t) |
| `--channel {fromData,fromText}` | Generation channel of the negotiation propose/accept messages (default `fromData`; `fromText` adds one extraction LLM call per generated message, see [Negotiation scenario](#negotiation-scenario-negotiation-t)) |
| `--transport {sse,webhook}` | Event transport of the client (default `sse`): `sse` consumes the streamed response; `webhook` embeds a client-side push receiver and receives every task event as a webhook POST, see [Webhook transport](#webhook-transport-a2a-push-notifications) |
| `--webhook-port N` | Bind port of the embedded webhook receiver used with `--transport webhook` (default 9000; `0` selects an OS-assigned ephemeral port), see [Webhook transport](#webhook-transport-a2a-push-notifications) |
| `--keep-alive` | Leave registry/server running; stop later with `--stop` |
| `--keep-logs` | Keep the temp run directory (logs / `.env`) under `<temp>/a2at-ran-energy-saving/` |
| `--stop` | Stop a previous `--keep-alive` run and exit |
| `--timeout N` | Seconds to wait for each port (default 30) |

> Server-side progress and report text is fixed English regardless of `--language`; the flag only
> switches the client input, templates, and mock replies.

**Expected output** (trimmed):

```text
[client] a2a-event: {"task": {"id": "...", "status": {"state": "TASK_STATE_SUBMITTED", ...}}}
[client] a2a-event: {"statusUpdate": {"status": {"state": "TASK_STATE_WORKING", ...}}}
...
[client] a2a-event: {"artifactUpdate": {"artifact": {"name": "energy saving intent report", ...}}}
[client] stream-completed: events=12 artifacts=1
```

### Run the two sides separately

To run the server and the client in separate terminals (for example, to host your own server or to
watch each side's log on its own), point `PYTHONPATH` at the case `src/` and start each part. `.env`
is read from the current directory, and a missing `.env` is a **hard error** for the client and
server.

> [!IMPORTANT]
> Start the registry and the server first; the client discovers the server's AgentCard from the
> registry, so it must already be running.

The run language comes from `A2AT_LANGUAGE` in `a2a-t-sample/.env` (`env.example` ships `zh-CN`);
`run_demo.py` overrides it per run via `--language` (default `en-US`).

```bash
# bash / zsh
cd a2a-t-sample
export PYTHONPATH="$PWD/ran-energy-saving/src"
uv run --no-sync python -m registry.registry_main    # terminal 1: registry center (5001)
uv run --no-sync python -m server.server_main         # terminal 2: server (8000)
uv run --no-sync python -m client.client_main         # terminal 3: client
```

```powershell
# PowerShell
cd a2a-t-sample
$env:PYTHONPATH = "$pwd\ran-energy-saving\src"
uv run --no-sync python -m registry.registry_main
uv run --no-sync python -m server.server_main
uv run --no-sync python -m client.client_main
```

### Configuration

Configuration used by this sample (from `a2a-t-sample/.env`):

| Key | Meaning |
| --- | --- |
| `A2AT_LANGUAGE` | `zh-CN` / `en-US`; selects the natural-language input and the mock/template tree |
| `A2AT_SAMPLE_SCENARIO` | `default` / `negotiation`; selects the sample scenario variant (`negotiation` = the missing-region information negotiation, see [Negotiation scenario](#negotiation-scenario-negotiation-t)) |
| `A2AT_SAMPLE_NEGOTIATION_KIND` | `information` / `target` / `feasibility` (default `information`); selects the negotiation scenario input, same semantics as `--negotiation` |
| `A2AT_SAMPLE_CHANNEL` | `data` / `text` (default `data`); generation channel of the negotiation propose/accept messages, same semantics as `--channel` |
| `A2AT_SAMPLE_TRANSPORT` | `sse` / `webhook` (default `sse`); event transport of the client, same semantics as `--transport` |
| `A2AT_SAMPLE_WEBHOOK_PORT` | Bind port of the embedded webhook receiver (default `9000`; `0` = OS-assigned ephemeral port), used when `A2AT_SAMPLE_TRANSPORT=webhook` |
| `A2AT_SAMPLE_WEBHOOK_TOKEN` | Push token the server must carry in the `X-A2A-Notification-Token` header (default `demo-webhook-token`); a mismatch answers `401` |
| `A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT` | Idle backstop in seconds that ends the webhook flow when no push arrives (default `30`) |
| `A2AT_LLM_API_KEY` | Empty ⇒ mock LLM; otherwise an OpenAI-compatible key |
| `A2AT_LLM_PROVIDER` / `A2AT_LLM_MODEL` / `A2AT_LLM_BASE_URL` | LLM endpoint settings |
| `A2AT_LLM_TIMEOUT_SECONDS` | HTTP client timeout in seconds (default 60) |
| `A2AT_SAMPLE_HOST` / `A2AT_SAMPLE_PORT` | Server bind for the manual `python -m` path (default `127.0.0.1:8000`) |
| `REGISTRY_CENTER_HOST` / `REGISTRY_CENTER_PORT` | Registry bind for the manual `python -m` path (default `127.0.0.1:5001`) |
| `A2AT_SAMPLE_DEBUG` | `true` prints full LLM request/response payloads |

Environment variables (read from the process environment, not `.env`):

| Key | Meaning |
| --- | --- |
| `A2AT_SAMPLE_MAX_ARTIFACTS` | Stop after N artifacts (default 0 = no limit) |

Notes:

- `run_demo.py` always uses `5001` / `8000` for its readiness checks and ignores
  `A2AT_SAMPLE_PORT` / `REGISTRY_CENTER_PORT`; set those only for the manual `python -m` path.
- The values above (the `template_uri`, the parameter schema, the AgentCard, and the natural-language
  input) are **specific to this sample**; replace them with your own. The template must belong to the
  `Task-T` extension and use version `v1`.
- Bundled resources and language coverage are limited; remote prompt-resource loading is not part of
  the SDK, and the registry center here is a sample-side mock for AgentCard discovery, not an SDK
  capability.

## Using the A2A-T SDK

### Client: generate and send a Task-T prompt

Render a Task-T prompt from your natural-language input (A2A-T SDK) —
[`client_flow.py:891`](src/client/client_flow.py#L891):

```python
prompt_content = prompt_client.generate_task_prompt_from_text(input_text, ENERGY_SAVING_TEMPLATE_URI)
prompt_text, extension_uri = _require_prompt_text(prompt_content)
```

It returns `MetadataContent` (`prompt_text`, `extension_uri`, `template_uri`,
`build_metadata_content()`) and raises `PromptGenerationError` (codes such as `template.not_found`,
`slot.not_provided`, `llm.not_configured`, `input.text_too_long`).

Put the prompt in the message metadata and send it over `a2a-sdk` (streaming) —
[`client_flow.py:905`](src/client/client_flow.py#L905):

```python
request = SendMessageRequest()
request.message.message_id = str(uuid.uuid4())
request.message.role = Role.ROLE_USER
request.message.parts.add().text = _build_request_metadata(initial_input)
request.message.metadata[extension_uri] = prompt_text
request.message.metadata["templateUri"] = (
    getattr(prompt_content, "template_uri", None) or ENERGY_SAVING_TEMPLATE_URI
)

call_context = ClientCallContext(
    service_parameters={"A2A-Extensions": extension_uri},
)

async for stream_response in a2a_client.send_message(request, context=call_context):
    ...
```

The AgentCard discovery and `a2a-sdk` client construction that precede this are in
[`client_main.py`](src/client/client_main.py).

### Server: validate, extract, and stream

Read the A2A-T payload from the message metadata the client sent —
[`server_flow.py:75`](src/server/server_flow.py#L75):

```python
def _extract_prompt_text(request_context: RequestContext) -> str:
    """Extract the prompt text from metadata under the Task-T extension URI."""
    if request_context.message is None or request_context.message.metadata is None:
        raise ValueError("Expected message metadata for Task-T prompt")
    metadata = MessageToDict(request_context.message.metadata)
    return str(metadata.get(TASK_T_EXTENSION_URI, ""))
```

Validate the prompt and extract the parameters (A2A-T SDK) —
[`server_flow.py:455`](src/server/server_flow.py#L455):

```python
filled_params = prompt_server.validate_task_prompt_and_data_filling(
    prompt=prompt_text,
    schema=TASK_PARAM_SCHEMA,
    template_uri=ENERGY_SAVING_TEMPLATE_URI,
)
```

`schema` is **yours**: it declares the parameters to extract; `filled_params.data` is the merged
parameter map (or a list for array-shaped schemas). A rejected prompt raises `ContentValidationError`
(codes such as `negotiation.semantic_rejected`, `llm.invocation_failed`, `template.not_found`)
carrying `exc.errors` with per-slot `slot_name` / `code` / `message`; the sample maps that to
`TASK_STATE_REJECTED` instead of crashing —
[`server_flow.py:482`](src/server/server_flow.py#L482):

```python
if validation_failure is not None:
    await emit_status_update(
        request_context=request_context,
        event_queue=event_queue,
        context_id=resolved_context_id,
        task_id=resolved_task_id,
        state=TaskState.TASK_STATE_REJECTED,
        text=f"Prompt validation failed: {validation_failure}",
    )
    ...
    return
```

The flow runs inside an `a2a-sdk` `AgentExecutor`, which delegates to `execute_server_flow` —
[`server_main.py:59`](src/server/server_main.py#L59):

```python
async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
    kwargs = {
        "request_context": context,
        "event_queue": event_queue,
        "prompt_server": self._prompt_server,
        "log_sink": self._log_sink,
    }
    await self._execute_flow(**kwargs)
```

The `a2a-sdk` app assembly (routes, task store) is in
[`src/server/server_main.py`](src/server/server_main.py).

## Negotiation scenario (Negotiation-T)

Besides the default flow above, the sample ships negotiation scenarios that demonstrate **Negotiation-T**
negotiations over the same streaming path. Three negotiation kinds are supported, selected data-driven
by the content-validation error code the server produces:

> Integrating negotiation into your own system? See the
> [A2A-T Negotiation Integration Guide](../../docs/en/negotiation_integration_guide.md) for the
> SDK/customer responsibility split, the seven-step integration path, and the demo-to-production
> replacement checklist.

| Validation error code | Negotiation kind | Round 1 propose → round 2 accept |
| --- | --- | --- |
| `content.param_missing` | information | the server asks for the missing parameter (`region`); the client answers with the filled value |
| `content.value_not_allowed` | target | the server proposes the clarified target for confirmation (a confirm-request round); the client accepts with its confirmed intent |
| `content.semantic_conflict` | feasibility | the server requests a feasibility evaluation (`action=REQUEST_FEASIBILITY_EVALUATION`) of the conflicting `contents_to_evaluate`; the client accepts with a feasibility summary |

> This mapping is a demo-level convention. The wire self-describes: each propose carries its own
> `templateUri` (`Negotiation-T/{information,target,feasibility}-negotiation/propose/v1`), and the
> client dispatches its accept on that URI.

```bash
cd a2a-t-sample/ran-energy-saving
python run_demo.py --scenario negotiation                         # missing-region input; information negotiation
python run_demo.py --negotiation target                           # 100Mbps throughput beyond cell capability; target negotiation
python run_demo.py --negotiation feasibility --channel fromText   # 30% saving goal vs 50Mbps throughput; feasibility negotiation
```

The flags' defaults (`default` scenario, `information` kind, `fromData` channel) keep the flow described
above unchanged; the kind and the channel can also be selected with `A2AT_SAMPLE_NEGOTIATION_KIND` /
`A2AT_SAMPLE_CHANNEL` in `.env` (see [Configuration](#configuration)).

**Round 1 — the server proposes.** The client generates the Task-T prompt from its natural-language
input with `generate_task_prompt_from_text` ([`client_flow.py:891`](src/client/client_flow.py#L891)).
The server validates it with `validate_task_prompt_and_data_filling`
([`server_flow.py:455`](src/server/server_flow.py#L455)) and gets a per-slot rejection; the error code
selects the negotiation kind (table above), and the server renders the matching Negotiation-T propose
from typed content (`InformationProposeContent` / `TargetProposeContent` / `FeasibilityProposeContent`
from `a2a_t.negotiation.content.models`) with `generate_negotiation_propose_prompt_from_data`
([`server_flow.py:285`](src/server/server_flow.py#L285)) — on the fromData channel no LLM call is
involved in rendering the propose — and emits `TASK_STATE_INPUT_REQUIRED` with the propose in the
status message metadata (`build_metadata_content()`).

**Round 2 — the client accepts.** The client reads the propose from the INPUT_REQUIRED metadata and
parses the `negotiationContext` session, then dispatches on the propose's `templateUri` and answers with
the matching ending content — information: `InformationEndingContent` with the filled answers; target:
`TargetEndingContent` (`ACCEPT` + `confirmed_intent`); feasibility: `FeasibilityEndingContent`
(`ACCEPT` + `feasibility_summary`). It regenerates the Task-T prompt from the filled parameters with
`generate_task_prompt_from_data_with_schema` ([`client_flow.py:686`](src/client/client_flow.py#L686)),
renders the accept with `generate_negotiation_accept_prompt_from_data`
([`client_flow.py:701`](src/client/client_flow.py#L701)), and re-sends the same task with both prompts
in the message metadata. The server detects the Negotiation-T URI as the continuation, validates the
now-complete prompt, streams the UC1 progress steps and the report artifact, and closes the task with
`TASK_STATE_COMPLETED` — the negotiation run ends here, whereas the default flow stays long-running.

**The fromText channel.** With `--channel fromText` (`A2AT_SAMPLE_CHANNEL=text`) each generated
negotiation message is first rendered as a natural-language text, and the SDK extracts the structured
content back from it: the server calls `generate_negotiation_propose_prompt_from_text`
([`server_flow.py:282`](src/server/server_flow.py#L282)) and the client calls
`generate_negotiation_accept_prompt_from_text` ([`client_flow.py:695`](src/client/client_flow.py#L695))
— one extraction LLM call per generated message (visible as extra `llm-mock` lines with the bundled
mock LLM). The wire format is identical to `fromData`. Known mock artifact: on
`--scenario negotiation --channel fromText` the information propose carries the canned 2-item
extraction list where `fromData` carries the 1-item missing list; accept and wire conventions are
equivalent. Per-type message shapes: the target propose
carries the negotiation summary plus a confirm request, and the accept carries the confirmed intent;
the feasibility propose carries the evaluation request with the `contents_to_evaluate` items, and the
accept carries the feasibility summary.

> **Language behavior:** the negotiation message texts (propose summaries, confirm requests,
> feasibility contents, accept summaries) follow the run language (`--language`); the UC1 progress and
> report text stays fixed English regardless of it.

Wire conventions:

| What | Where |
| --- | --- |
| Propose (round 1) / accept (round 2) text | message `metadata` under the Negotiation-T extension URI (`https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1`) |
| Negotiation session | nested `negotiationContext` metadata map (`id` / `round` / `maxRounds` / `performative`) |
| Round-2 continuation signal | Negotiation-T URI in the round-2 message `metadata`; `task_id` / `context_id` repeat round 1 |
| Round-2 `A2A-Extensions` header | both extension URIs (Task-T **and** Negotiation-T), comma-joined |

**Expected output** (trimmed):

```text
[client] a2a-event: {"task": {"id": "...", "status": {"state": "TASK_STATE_SUBMITTED", ...}}}
[client] a2a-event: {"statusUpdate": {"status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {"metadata": {"negotiationContext": {"round": 1.0, ..., "performative": "PROPOSE"}, ".../extensions/Negotiation-T/v1": "## Information Negotiation ...", "templateUri": "Negotiation-T/information-negotiation/propose/v1"}}}}}
[client] negotiation-accept: generated (round=2) template_uri=Negotiation-T/information-negotiation/accept-reject/v1 items=[('region', 'Songshanhu Administration Committee')]
[client] a2a-send-message: round=2 streaming text_part=create ran energy saving task metadata_keys=[<Task-T/v1>, <Negotiation-T/v1>, templateUri, negotiationContext] header[A2A-Extensions]=<Negotiation-T/v1>,<Task-T/v1>
[client] a2a-event: {"artifactUpdate": {"artifact": {"name": "energy saving intent report", ...}}}
[client] a2a-event: {"statusUpdate": {"status": {"state": "TASK_STATE_COMPLETED", ...}}}
[client] stream-completed: events=14 artifacts=1
```

## Webhook transport (A2A push notifications)

Besides the SSE streaming above, the sample supports the **webhook transport** (`--transport webhook`,
`A2AT_SAMPLE_TRANSPORT=webhook`) — the A2A **push notification** mode for clients that do not hold a
streaming connection open:

- **Pure callback mode.** The client does not consume SSE. It sends a non-blocking `message:send`
  (`return_immediately=True`) and receives **all** task events as webhook POSTs on a receiver embedded
  in the client process: `client_main` starts the Starlette/uvicorn
  [`WebhookReceiver`](src/common/webhook_receiver.py) on the client's asyncio loop before the flow runs
  and stops it when the flow ends ([`client_main.py`](src/client/client_main.py)). Push bodies are
  StreamResponse-shaped JSON — the same shape as the SSE events (a2a-sdk posts
  `to_stream_response(event)`) — so the negotiation detection, artifact counting, and presentation are
  shared with the SSE path.
- **Two registration paths.** Round 1 registers the push config inline on the request
  (`SendMessageConfiguration.task_push_notification_config`, a flat
  `TaskPushNotificationConfig(url, token)` — the task id is still unknown at this point); round 2
  re-registers the same URL/token through the `POST /tasks/{id}/pushNotificationConfigs` REST endpoint
  (a2a-sdk `BaseClient.create_task_push_notification_config`) before the negotiation accept is sent.
  The server assembles the push machinery **unconditionally** — a2a-sdk `DefaultRequestHandler` with
  `InMemoryPushNotificationConfigStore` + `BasePushNotificationSender`
  ([`server_main.py`](src/server/server_main.py)) — so in SSE mode it simply idles; no server-side
  mode switch exists.
- **Token auth.** The server's push sender carries `A2AT_SAMPLE_WEBHOOK_TOKEN` (default
  `demo-webhook-token`) in the `X-A2A-Notification-Token` header on every POST; the receiver validates
  it and answers `401` on a mismatch (the event is dropped); a malformed or unrecognized body answers
  `400` (both rejection paths are part of the receiver contract).
- **End signals.** Any SDK terminal state (`TASK_STATE_COMPLETED` / `FAILED` / `CANCELED` /
  `REJECTED`) ends the flow in both phases — the negotiation scenarios normally end on `COMPLETED`,
  while a failed validation or stream error ends it on `REJECTED` / `FAILED` right away instead of
  hanging to the idle backstop — and the summary line notes a terminal end as `end=<state>`. The
  default scenario still ends on the report artifact; `INPUT_REQUIRED` (a negotiation propose) is a
  phase-1 interrupt, not a flow end. An idle timeout (`A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT`, default
  30 s, reset per received event) is the backstop: it logs a `webhook-idle-timeout` diagnostic line
  and ends the flow normally with what was collected. `A2AT_SAMPLE_MAX_ARTIFACTS` does not apply in
  webhook mode — the artifact end signal itself closes the default flow (no stream-cut cap).

The client embeds the receiver, so webhook mode binds **one extra listening port** —
`A2AT_SAMPLE_WEBHOOK_PORT` (default 9000; `--webhook-port N`, `0` = OS-assigned ephemeral port) —
besides the registry/server ports. The a2a client is built with `ClientConfig(streaming=False)`, which
is required to select the non-blocking `message:send` path (the `return_immediately` flag alone does
not switch the path). Language behavior is inherited from the negotiation section above. Each round's
single RPC reply (the first Task) is logged once as `webhook-rpc-reply` and is not counted again: the
`stream-completed` summary counts the events received over the webhook (SSE parity) and appends
`transport=webhook` (plus `end=<state>` when a terminal state ended the flow, or `idle-timeout=<N>s`
when the idle backstop did).

```bash
cd a2a-t-sample/ran-energy-saving
python run_demo.py --transport webhook                          # default scenario; ends on the report artifact
python run_demo.py --transport webhook --scenario negotiation   # information negotiation; ends on COMPLETED
python run_demo.py --transport webhook --negotiation target     # target negotiation over webhook
```

**Expected output** (trimmed):

```text
[webhook-receiver] listening: url=http://127.0.0.1:9000/webhook
[client] webhook-mode: push_url=http://127.0.0.1:9000/webhook idle_timeout=30.0s (client streaming=false, events via push notifications)
[client] a2a-send-message: round=1 webhook text_part=create ran energy saving task metadata_keys=[<Task-T/v1>, templateUri] return_immediately=true push_registration=inline
[client] webhook-event: {"statusUpdate": {..., "status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {"metadata": {"negotiationContext": {"round": 1.0, ..., "performative": "PROPOSE"}, ".../extensions/Negotiation-T/v1": "## Information Negotiation ...", "templateUri": "Negotiation-T/information-negotiation/propose/v1"}}}}}
[client] negotiation-accept: generated (round=2) template_uri=Negotiation-T/information-negotiation/accept-reject/v1 items=[('region', 'Songshanhu Administration Committee')]
[client] webhook-push-reregister: task_id=... url=http://127.0.0.1:9000/webhook method=create_task_push_notification_config token=***
[client] a2a-send-message: round=2 webhook text_part=create ran energy saving task metadata_keys=[<Task-T/v1>, <Negotiation-T/v1>, templateUri, negotiationContext] header[A2A-Extensions]=<Negotiation-T/v1>,<Task-T/v1> return_immediately=true push_registration=endpoint
[client] webhook-event: {"statusUpdate": {..., "status": {"state": "TASK_STATE_COMPLETED", ...}}}
[client] stream-completed: events=14 artifacts=1 transport=webhook end=TASK_STATE_COMPLETED
[webhook-receiver] stopped: url=http://127.0.0.1:9000/webhook
```
