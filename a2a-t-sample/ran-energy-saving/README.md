# ran-energy-saving (Task-T)

A minimal **Task-T** end-to-end sample demonstrating a RAN energy-saving task:

- the client generates a prompt with the template-directed
  `A2ATClient.generate_task_prompt_from_text(text, "Task-T/network-layer/ran-energy-saving/v1")` API
- the server (A2A server) validates it with the A2A-T server SDK API
  `A2ATServer.validate_task_prompt_and_data_filling(prompt, schema, template_uri)`
- the server **streams** the execution of the RAN energy-saving task over a real A2A `HTTP+JSON`
  chain: plan → candidate cell selection → energy saving activation → intent report → `COMPLETED`

The flow runs fully offline: with an empty `A2AT_LLM_API_KEY` the sample serves scripted mock LLM
responses (`resources/mock_responses/{zh-CN,en-US}/`). The language is read from `A2AT_LANGUAGE`
in `.env` (the shared `env.example` ships `zh-CN`); both the natural-language input and the mock
responses follow it, so `en-US` and `zh-CN` are both fully supported. `run_demo.py` defaults to
`en-US` and accepts `--language`. A missing `.env` is a hard error (copy `env.example` to `.env`
first).

## Message Body Conventions

| Location | Content |
| --- | --- |
| text part | task name (`"create ran energy saving task"`) |
| `metadata[Task-T/v1]` | the generated prompt text |
| `metadata[templateUri]` | `Task-T/network-layer/ran-energy-saving/v1` |
| header `A2A-Extensions` | `https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1` |

## Streaming

The client calls the A2A SDK `Client.send_message(...)` and iterates the returned **stream**; the
A2A server registers its handler on the streaming route (`POST /message:stream`). The server
streams `TaskStatusUpdateEvent` (`SUBMITTED` → `WORKING` → `COMPLETED`) and one
`TaskArtifactUpdateEvent` per task step, so the client sees the energy-saving task progress
incrementally instead of one final blob.

Streamed task steps (artifact names):

| # | Artifact | Shows |
| --- | --- | --- |
| 1 | `energySaving.TaskPlan` | operation type, area, cell mode, time window, target |
| 2 | `energySaving.CellSelection` | candidate/selected cell counts + policy |
| 3 | `energySaving.EnergySavingActivated` | cells activated, power saving mode, time |
| 4 | `energySaving.IntentReport` | intent/energy/rate goal achievement |

## Layout

| Path | Role |
| --- | --- |
| `src/client_example/` | client: registry discovery → `generate_task_prompt_from_text` → stream consumption |
| `src/server_example/` | server: `validate_task_prompt_and_data_filling` → stream energy-saving task steps |
| `src/agentcard_example/` | mock registry center (AgentCard register/query, port 5001) |
| `src/common/` | shared adapters + the schema-routed mock LLM |
| `resources/mock_responses/{zh-CN,en-US}/` | scripted slot-extraction and content-validation responses |
| `run_demo.py` | cross-platform, non-blocking launcher (background registry/server, auto cleanup) |

## Prerequisites

From `a2a-t-sample/` (the directory holding `.env`):

```powershell
cp env.example .env      # keep A2AT_LLM_API_KEY empty for the offline mock
uv pip install -r requirements.txt
```

`A2AT_LANGUAGE` in that `.env` selects the language of the manual (three-terminal) flow;
`run_demo.py` overrides it per run via `--language` (default `en-US`).

## Run (non-blocking, recommended)

`run_demo.py` is cross-platform (Windows / macOS / Linux). It starts the registry and the server as
**background subprocesses** (so the terminal is never blocked), waits for their ports, runs the
client, then stops the background processes.

```bash
cd a2a-t-sample/ran-energy-saving
python run_demo.py                   # English (default); streams the 4 task steps, then COMPLETED
python run_demo.py --language zh-CN  # Chinese run
python run_demo.py --max-artifacts 2 # stop the client after 2 artifacts
python run_demo.py --keep-alive      # leave registry/server running (stop with --stop)
python run_demo.py --stop            # stop a previous --keep-alive run
```

The client runs in the foreground; after it exits, the launcher prints the key **server-side
A2A-T SDK activity** (`sdk-call` / `sdk-result` / statuses) read from the server log, so the whole
story is visible from one command. Full LLM request/response payloads are printed only when
`A2AT_SAMPLE_DEBUG=true`.

> Use an interpreter that has the sample dependencies installed (the repository `.venv`), e.g.
> `..\..\.venv\Scripts\python.exe run_demo.py` on Windows or `../../.venv/bin/python run_demo.py`
> on macOS/Linux.
> 
> **Temporary files**: everything the launcher writes lives in
> `<temp>/a2at-ran-energy-saving/` (a per-run `.env` with the overridden language, the subprocess
> logs, and `pids.json`) and is **removed on exit**. Use `--keep-logs` to keep it for inspection;
> `--keep-alive` also keeps it (with `pids.json`) so a later `--stop` can find the processes.

## Run (manual, three terminals)

From `a2a-t-sample/` (the directory holding `.env`), point `PYTHONPATH` at the case `src/`:

```bash
# bash / zsh
cd a2a-t-sample
export PYTHONPATH="$PWD/ran-energy-saving/src"

# Terminal 1: registry center (5001)   # Terminal 2: server (8000)
uv run python -m agentcard_example.registry_main
uv run python -m server_example.server_main

# Terminal 3: client (Ctrl+C to stop; or cap the artifacts)
A2AT_SAMPLE_MAX_ARTIFACTS=5 uv run python -m client_example.client_main
```

```powershell
# PowerShell
cd a2a-t-sample
$env:PYTHONPATH = "$pwd\ran-energy-saving\src"

uv run python -m agentcard_example.registry_main
uv run python -m server_example.server_main
$env:A2AT_SAMPLE_MAX_ARTIFACTS = "5"
uv run python -m client_example.client_main
```

> If `uv run` prunes the sample dependencies, use the repository venv directly
> (`.venv/Scripts/python.exe` on Windows, `.venv/bin/python` on macOS/Linux), or `uv run --no-sync ...`.

## Expected client output (trimmed)

The client logs the raw input, the A2A-T SDK prompt, and then the A2A streaming exchange:

```
[client] input-raw: Create a RAN energy saving task in the Songshanhu Administration Committee area: ...
[client] sdk-call: A2ATClient.generate_task_prompt_from_text(text=..., template_uri=Task-T/network-layer/ran-energy-saving/v1)
[client] llm-mock: using canned mock LLM response
[client] sdk-output-prompt: ## Operation Type

Create

## Task Type

Wireless network energy saving
...
[client] sdk-output-meta: template_uri=Task-T/network-layer/ran-energy-saving/v1 extension_uri=.../Task-T/v1
[client] a2a-send-message: streaming text_part=create ran energy saving task metadata_keys=[.../Task-T/v1, templateUri] header[A2A-Extensions]=.../Task-T/v1
[client] response-inbound: stream started
[client] status-received: state=TASK_STATE_SUBMITTED text=
[client] status-received: state=TASK_STATE_WORKING text=RAN energy saving task in progress
[client] artifact-received: name=energySaving.TaskPlan artifact_id=...
[client] artifact-received: name=energySaving.CellSelection artifact_id=...
[client] artifact-received: name=energySaving.EnergySavingActivated artifact_id=...
[client] artifact-received: name=energySaving.IntentReport artifact_id=...
[client] status-received: state=TASK_STATE_COMPLETED text=RAN energy saving task completed
[client] stream-completed: events=7 artifacts=4
```

The server side shows the A2A-T SDK call and its result:

```
[server] request-inbound: {"prompt_text": "## Operation Type ..."}
[server] sdk-call: A2ATServer.validate_task_prompt_and_data_filling(template_uri=..., prompt_text=...)
[server] sdk-result: success extracted_params={'operationType': 'create', 'region': 'Songshanhu Administration Committee', 'energyTarget': '30%', 'energyTargetDirection': 'reduce', 'cellMode': 'NR', 'startTime': '16:00:00Z', 'endTime': '04:00:00Z'}
[server] task-status: TASK_STATE_SUBMITTED
[server] task-status: TASK_STATE_WORKING
[server] artifact-pushed: count=1 name=energySaving.TaskPlan step=energy saving plan built
...
[server] task-status: TASK_STATE_COMPLETED
```

## Where the SDKs are called

| Step | Caller | API | Transport |
| --- | --- | --- | --- |
| prompt generation | client | A2A-T **client** SDK `A2ATClient.generate_task_prompt_from_text` | in-process |
| task request | client → server | A2A SDK `Client.send_message` (streaming) | A2A `HTTP+JSON` `/message:stream` |
| prompt validation + param extraction | server | A2A-T **server** SDK `A2ATServer.validate_task_prompt_and_data_filling` | in-process (inside the A2A handler) |
| result streaming | server → client | A2A SDK `EventQueue` (`TaskArtifactUpdateEvent`) | A2A `HTTP+JSON` stream |

So yes — the A2A server **does** call the A2A-T server SDK to validate the prompt and extract the
parameters; the extracted `params` are logged as `[server] sdk-result`.
