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
[`client_flow.py:70`](src/client/client_flow.py#L70):

```python
prompt_content = prompt_client.generate_task_prompt_from_text(input_text, ENERGY_SAVING_TEMPLATE_URI)
prompt_text, extension_uri = _require_prompt_text(prompt_content)
```

It returns `MetadataContent` (`prompt_text`, `extension_uri`, `template_uri`,
`build_metadata_content()`) and raises `PromptGenerationError` (codes such as `template.not_found`,
`slot.not_provided`, `llm.not_configured`, `input.text_too_long`).

Put the prompt in the message metadata and send it over `a2a-sdk` (streaming) —
[`client_flow.py:84`](src/client/client_flow.py#L84):

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
[`server_flow.py:39`](src/server/server_flow.py#L39):

```python
def _extract_prompt_text(request_context: RequestContext) -> str:
    """Extract the prompt text from metadata under the Task-T extension URI."""
    if request_context.message is None or request_context.message.metadata is None:
        raise ValueError("Expected message metadata for Task-T prompt")
    metadata = MessageToDict(request_context.message.metadata)
    return str(metadata.get(TASK_T_EXTENSION_URI, ""))
```

Validate the prompt and extract the parameters (A2A-T SDK) —
[`server_flow.py:101`](src/server/server_flow.py#L101):

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
[`server_flow.py:145`](src/server/server_flow.py#L145):

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
[`server_main.py:55`](src/server/server_main.py#L55):

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
