# a2a-t-sample

`a2a-t-sample` is the sample case collection of the A2A-T Python SDK, organized as one independent directory per case, with runnable client and server entry points.

The current samples run a real A2A `HTTP+JSON/REST` chain based on the official Python A2A SDK (`a2a-sdk`):
- the `a2a-t-sdk` client only generates structured prompts
- the `a2a-t-sdk` server only validates structured prompts

## Case List

| Directory | Description |
| --- | --- |
| [subscribe-incident/](subscribe-incident/) | Event subscription case — client generates a Notification-T prompt → server validates it → streams Incident artifacts (includes the registry center) |
| [negotiation/](negotiation/) | Negotiation closed-loop case — offline propose → accept round trips (scripted mock LLM, both languages) |

## Resources in This Module

- Shared environment configuration template: `env.example` (copy to `a2a-t-sample/.env` before use)
- Shared dependencies: `requirements.txt`
- Subscription case client/server/registry entry points: `subscribe-incident/src/{client_example,server_example,agentcard_example}/`
- Subscription case mock LLM response data: `subscribe-incident/resources/mock_responses/` (zh-CN / en-US)
- Negotiation case entry point: `negotiation/src/negotiation_demo/` (demo runtime and generation strategies)
- Negotiation case scenario data and scripted mock LLM responses: `negotiation/resources/`

Add new cases directly under `a2a-t-sample/`, as sibling directories of `subscribe-incident/`.

## Quick Start (Shared)

```bash
cd a2a-t-sample
cp env.example .env
uv pip install -r requirements.txt
```

> If `A2AT_LLM_API_KEY` is left empty, both cases automatically use their own scripted mock LLM responses, so the full flows run without a real API. With the mock in use, a standalone log line `[llm] llm-mock: using canned mock LLM response` is printed before each response; this line distinguishes the mock from a real LLM.

## Negotiation Closed-Loop Sample

The negotiation sample is an **offline** negotiation closed-loop demo (the Python counterpart of the Java `a2a-t-sample` NegotiationDemoApp, with the embedded HTTP server replaced by an **in-process runtime**). Without an LLM API key it uses a scripted mock LLM, so the whole round trip runs completely offline.

The 4-message flow:

| Message | Direction | Content | Task status |
| --- | --- | --- | --- |
| 1 | client→server | Task-T (with missing parameters) | → |
| 2 | server→client | Negotiation-T information negotiation request (dynamically lists the missing parameters) | INPUT_REQUIRED |
| 3 | client→server | Task-T (parameters filled) + Negotiation-T accept | → |
| 4 | server→client | Diagnosis result (dynamically generated from the extracted parameters) | COMPLETED |

**LLM involvement conventions**: under the `fromData` strategy the negotiation message generation is purely deterministic rendering (no LLM calls), while Task-T slot extraction, semantic validation, and fromText negotiation extraction still call the LLM (answered by the scripted mock LLM when the key is absent, so the sample runs offline). With a real API key configured, the same flow calls a real LLM.

### Negotiation Sample Structure

| Directory | Role |
| --- | --- |
| `negotiation/src/negotiation_demo/` | Entry point `__main__` / `demo_app`: parses `--fromText` / `--language` and drives the 4-message round trip |
| `negotiation/src/negotiation_demo/client_runtime.py` | Client runtime: Task-T prompt generation + accept generation (`generate_task_prompt_from_data_with_schema` + `generate_negotiation_accept_prompt_from_data`) |
| `negotiation/src/negotiation_demo/server_runtime.py` | Server runtime: missing-parameter detection driven by `validate_task_prompt_and_data_filling` → propose generation → diagnosis rendering |
| `negotiation/src/negotiation_demo/shared/` | Strategy layer (fromData / fromText), the mock LLM stub, and scenario data loading |
| `negotiation/resources/` | Scenario data (e.g. `scenario.json`) + bilingual mock LLM responses |

### Starting the Negotiation Sample

```bash
# Run from the a2a-t-sample directory; PYTHONPATH must point at the case src
$env:PYTHONPATH = "$pwd\negotiation\src"     # PowerShell; use export under bash

uv run python -m negotiation_demo                 # fromData strategy (deterministic negotiation messages)
uv run python -m negotiation_demo --fromText      # fromText strategy (one LLM extraction per negotiation message)
uv run python -m negotiation_demo --language zh-CN
```

This sample is an in-process runtime (no HTTP server), so transport endpoint switches do not apply. If the Windows console shows garbled Chinese characters, run `chcp 65001` first.

## Event Subscription (subscribe-incident) Sample

A minimal end-to-end case based on the real HTTP+JSON chain of `a2a-sdk`, demonstrating the fault subscription scenario: the client generates a prompt → the server validates it → Incident artifacts are streamed. The flow covers registry center interaction, client prompt generation, server validation, and streaming artifact delivery, and keeps the LLM mock capability.

| Stage | Who calls the SDK | What the SDK does | LLM calls |
| --- | --- | --- | --- |
| Startup | client + server | A2ATClient / A2ATServer initialization | 0 |
| Prompt generation | client | scenario recognition + slot extraction + template rendering | 2 |
| Prompt validation | server | scenario recognition → slot extraction → semantic validation | 3 |
| Streaming push | client | normalizes the stream events | 0 |

### Message Body Conventions

| Location | Content |
| --- | --- |
| text part | scenario name (`"create incident subscription"`) |
| `metadata[Notification-T/NL/v1]` | the generated promptText |
| header `A2A-Extensions` | `https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/NL/v1` |

- The prompt generation input is **hard-coded natural language**, selected automatically by `A2AT_LANGUAGE`:
  - `zh-CN`: `"请生成一个Incident事件订阅任务：通知主题为Incident，订阅条件为订阅级别为critical的ETH-LOS的故障，上报通知数据格式为DataPart"`
  - `en-US`: `"Generate an Incident event subscription task: notification topic is Incident, subscription condition is a critical ETH-LOS fault, and the notification data format is DataPart"`

### Server Validation Flow

The state machine of `execute_server_flow`:

1. **Validate the `A2A-Extensions` header**: must contain the Notification-T/NL extension URI, otherwise raise `ValueError("a2a client extensions is not exist.")`
2. **Extract promptText from `metadata[Notification-T/NL/v1]`** (no longer read from `parts[0].text`)
3. **`SUBMITTED`** → validate with `A2ATServer.check_task_prompt`
   - validation failed → emit **`REJECTED`** status (no exception raised)
   - validation passed → emit **`WORKING`** status
4. **Push Incident artifacts in a loop** (every `ARTIFACT_SEND_INTERVAL_SECONDS = 5.0s`; unlimited by default, may be truncated by `max_artifacts`)
5. exception during pushing → emit **`FAILED`** status

### AgentCard Data

- name: `SPN Domain Agent`, provider: `Huawei`
- declares only the `Notification-T/NL/v1` extension (the subscribe case does not involve Task-T)

### Starting the Event Subscription Sample

> The modules live under `subscribe-incident/src/` while `.env` lives under `a2a-t-sample/`; therefore **set `PYTHONPATH` to point at `subscribe-incident/src` while working in the `a2a-t-sample` directory**.

```powershell
# enter the sample directory (where .env lives)
cd a2a-t-sample

# set the module search path (repeat in every terminal)
$env:PYTHONPATH = "$pwd\subscribe-incident\src"
```

```bash
# Terminal 1: start the registry center (port 5001)
uv run python -m agentcard_example.registry_main

# Terminal 2: start the server (port 8000)
uv run python -m server_example.server_main

# Terminal 3: start the client (keeps receiving artifacts, Ctrl+C to stop)
uv run python -m client_example.client_main
```

### Limit the Number of Received Artifacts (Optional)

```powershell
$env:A2AT_SAMPLE_MAX_ARTIFACTS = "5"
uv run python -m client_example.client_main
```

### Key Points

- **The SDK is the middle layer**: the client/server never call the LLM directly; they call it indirectly through A2ATClient/A2ATServer
- **The LLM is used only in the prompt stages**: pushing artifacts does not call the LLM
- **No negotiation**: the client submits complete input once, and the server pushes directly after validation passes
- **Three-layer decoupling**: client (discovery + consumption) → server (registration + push) → registry (registry center)
- **Mock capability retained**: `common/mock_llm.py` + `resources/mock_responses/` are enabled automatically when the key is empty; the full flow needs no real API

## Running the Tests

```bash
# run the tests of all cases (from the a2a-t-sample directory)
uv run pytest subscribe-incident/test/ -v
uv run pytest negotiation/test/ -v
```