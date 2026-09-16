# 1 a2a-t-sdk-python User Guide

> SDK version: 1.1.0 — this release corresponds to the Java a2a-t-sdk 1.1.0 capability surface.

## 1.1 Feature Introduction
### 1.1.1 What is A2A-T?
A2A-T (Agent-to-Agent Telecom) is a multi-agent interconnection protocol for the telecom domain based on the A2A protocol, designed specifically for complex collaboration scenarios in the telecom domain.
General-purpose agent interconnection protocols in the industry mainly focus on agent interconnection and interaction frameworks, with insufficient attention to business scenarios and specific interaction content, resulting in low success rates for task completion. Business scenarios in the telecom domain are complex and demanding, requiring a dedicated protocol to support the interconnection and collaboration of O&M agents. The A2A-T solution is based on the A2A protocol, focusing on application extensions for enhanced capabilities related to telecom domain business flow information models, task negotiation, and collaboration security.

### 1.1.2 Relationship Between A2A-T SDK and A2A SDK

The A2A-T protocol is an extension based on the A2A protocol. The A2A-T SDK is provided for the protocol extension content, supporting rapid construction of agents for complex collaboration scenarios in the telecom domain. A2A-T SDK is independent of A2A SDK. By integrating both A2A-T SDK and A2A SDK, you can build agents that support the A2A-T protocol, enabling deterministic, highly reliable, efficient, and secure collaboration among multiple agents in the telecom domain.

### 1.1.3 Capability Introduction

a2a-t-sdk-python is a Python SDK designed for telecom agent collaboration scenarios, used to generate, validate, and negotiate task prompts in A2A-T interactions. The SDK is suitable for integration by client Agents, server Agents, and upper-layer orchestration systems.

Main capabilities include:

- **Task prompt generation**: the client generates a processed task prompt from natural language (scenario-recognition flow) or from text/structured input addressed to one explicit template (`from_text` / `from_data_with_schema` entry points for the Task-T, Notification-T, and Authorization-T extensions).
- **Server-side prompt validation and parameter filling**: the server checks whether the processed task prompt matches the template and slot constraints (`check_task_prompt`), and validates a rendered prompt against a caller-provided parameter schema while extracting its parameters (`validate_{task,notification,auth}_prompt_and_data_filling`).
- **Negotiation content API**: twelve methods over the Negotiation-T extension — `generate_{propose,accept,reject,abort}_prompt_from_{data,text}` for message generation and `validate_{propose,accept,reject,abort}_prompt_and_data_filling` for message validation. Session state travels in the A2A-T metadata (`negotiationContext`); the SDK itself stays stateless.
- **Template queries**: `get_prompts` / `get_prompt` list and load the templates available for the configured language across all extensions.
- **Prompt resource management**: built-in scenario, slot, template, vocabulary, and system prompt resources with bilingual (zh-CN / en-US) coverage; business content can be overridden from a local directory.
- **LLM adaptation**: connects to external large language models through OpenAI-compatible APIs, with bounded retries for the retryable failure codes.
- **Structured, bilingual error model**: a closed catalog of 42 machine-readable error codes with fact parameters, rendered messages in the configured language, and structured failure dataclasses on the result-returning APIs.

## 1.2 Application Scenarios

The Python SDK is typically used in the following scenarios:

1. The client Agent receives user intent, generates a structured task prompt, and sends it to the target Agent via the A2A protocol.
2. The server Agent receives the task prompt, performs compliance, template, and slot validation, extracts the parameters it needs, and then proceeds to business processing.
3. When task information is insufficient, task objectives are unclear, or capability feasibility needs confirmation, both parties exchange Negotiation-T messages (propose / accept / reject / abort) generated and validated through the negotiation content API, with the negotiation context carried in the A2A-T metadata of every message.

## 1.3 Environment Requirements

| Item                  | Requirement                                                                        |
|-----------------------|------------------------------------------------------------------------------------|
| Python SDK            | Python 3.12+                                                                       |
| Dependency management | Recommended to use `uv`                                                            |
| LLM                   | Requires an accessible OpenAI-compatible service and API key (not needed for offline tests and the offline sample) |
| Operating system      | Linux, Windows, and macOS are all suitable for development and integration testing |

## 1.4 Installation

### 1.4.1 Install the SDK

From source (development):

```bash
cd {project_path}/a2a-t-sdk-python
uv sync --dev
```

From PyPI (integration):

```bash
pip install a2a-t-sdk
```

Run the test suite to confirm the environment is ready (the suite is offline-deterministic by default; live-LLM tests are skipped when their environment variables are absent):

```bash
uv run pytest -q
```

### 1.4.2 Prepare the SDK Configuration

The SDK reads its configuration from a `.env` file (not from OS environment variables). Copy the template and edit it:

```bash
cd {project_path}/a2a-t-sdk-python
cp env.example package_data/.env
```

`package_data/.env` is the default read location of `A2ATClient` / `A2ATServer`; both facades also accept an explicit `env_path` argument. The repository-root `env.example` carries the full, commented key inventory.

At minimum, configure the LLM access:

```properties
A2AT_LANGUAGE=en-US
A2AT_PROMPT_SOURCE_TYPE=packaged
A2AT_LLM_PROVIDER=openai
A2AT_LLM_MODEL=deepseek-chat
A2AT_LLM_API_KEY={your_api_key}
A2AT_LLM_BASE_URL=https://api.deepseek.com
```

> The SDK connects to external LLMs through OpenAI-compatible APIs. `A2AT_LLM_PROVIDER` currently only supports `openai`. To access DeepSeek or other OpenAI-compatible services, specify the service address via `A2AT_LLM_BASE_URL` and the model name via `A2AT_LLM_MODEL`.

> Since 1.1.0 the default prompt resource source is `packaged`: prompts, templates, and vocabularies are read from the installed package, so the SDK works out of the box without any resource configuration. To keep reading resources from a directory (the pre-1.1.0 default), set `A2AT_PROMPT_SOURCE_TYPE=local_file` and point `A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR` at your resource root; see 1.6.1.

## 1.5 Quickstart

The snippets below use `env_path=Path("package_data/.env")`; omit the argument to use the default location.

### 1.5.1 Generate a Task Prompt on the Client

Generate a processed task prompt from natural language. The scenario-recognition entry point picks the scenario and template automatically:

```python
from pathlib import Path

from a2a_t.client.a2at_client import A2ATClient

client = A2ATClient(env_path=Path("package_data/.env"))
result = client.generate_task_prompt(
    "Generate an Incident event subscription task: the notification topic is Incident, "
    "the subscription levels are critical, medium, high, and low, and the notification data "
    "format is DataPart"
)

if result.success:
    print(result.prompt_text)
else:
    # Structured failure: code / message / stage (see 1.7)
    print(result.failure.code, result.failure.message)
```

When the target template is known, skip scenario recognition and address the template explicitly with its template URI (one LLM slot-extraction step, then deterministic rendering):

```python
metadata = client.generate_notification_prompt_from_text(
    "Subscribe to service recovery events; report the recovery plan execution status and "
    "the complaint diagnosis task serial number",
    "Notification-T/network-layer/subscribe-incident/v1",
)
print(metadata.prompt_text)                # rendered prompt text
print(metadata.template_uri)               # "Notification-T/network-layer/subscribe-incident/v1"
print(metadata.extension_uri)              # Notification-T extension URI for the A2A metadata
print(metadata.build_metadata_content())   # ready-to-use A2A-T metadata map
```

Structured input works the same way through the `*_from_data_with_schema` entry points, which take the input fields together with a schema describing what each field means:

```python
metadata = client.generate_task_prompt_from_data_with_schema(
    {"site": "Songshan Lake campus", "target": "reduce energy consumption by 30%"},
    {"site": "the network site the task applies to", "target": "the optimization goal"},
    "Task-T/network-layer/ran-energy-saving/v1",
)
```

The six addressed-template entry points are `generate_{task,auth,notification}_prompt_from_text` and `generate_{task,auth,notification}_prompt_from_data_with_schema`. Prefer the constants of `a2a_t.core.standard_templates` (for example `ENERGY_SAVING_URI`, `SUBSCRIBE_INCIDENT_URI`, `AUTHORIZATION_POLICY_MANAGEMENT_URI`) over hand-written URI strings.

### 1.5.2 Validate the Prompt on the Server

`check_task_prompt` validates the processed prompt against the scenario, template, and slot constraints and returns a result object instead of raising:

```python
from pathlib import Path

from a2a_t.server.a2at_server import A2ATServer

server = A2ATServer(env_path=Path("package_data/.env"))
result = server.check_task_prompt(processed_prompt_text=prompt_text)

if result.success:
    print("prompt check passed")
else:
    print(result.failure.code, result.failure.stage, result.failure.message)
```

When the server needs the business parameters carried by the prompt, use the parameter-filling validators, which validate the prompt against the addressed template and extract the parameters per a caller-provided JSON schema:

```python
from a2a_t.core.errors.exceptions import ContentValidationError

schema = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "description": "the event topic to subscribe to"},
        "notificationDataFormat": {"type": "string", "description": "the data format to report"},
    },
    "required": ["topic", "notificationDataFormat"],
}

try:
    filled = server.validate_notification_prompt_and_data_filling(
        metadata.prompt_text, schema, "Notification-T/network-layer/subscribe-incident/v1"
    )
    print(filled.data)  # extracted parameters, e.g. {"topic": "Incident", ...}
except ContentValidationError as exc:
    print(exc.code_str, exc.facts)  # catalog code and structured fact values
```

`validate_task_prompt_and_data_filling` and `validate_auth_prompt_and_data_filling` are the counterparts for the Task-T and Authorization-T extensions.

### 1.5.3 One Negotiation Round Trip

A negotiation exchange is a pair of Negotiation-T messages: one side generates a propose message asking for what it needs, the other side validates it and answers with an accept (or reject) message. The round trip below runs fully offline — the from-data generation path never calls an LLM.

```python
import uuid
from pathlib import Path

from a2a_t.client.a2at_client import A2ATClient
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.negotiation.content.enums import NegotiationConclusion
from a2a_t.negotiation.content.models import (
    InformationEndingContent,
    InformationProposeContent,
    NegotiationEndingData,
    NegotiationItem,
    NegotiationProposeData,
)
from a2a_t.server.a2at_server import A2ATServer

PROPOSE_URI = "Negotiation-T/information-negotiation/propose/v1"
ACCEPT_REJECT_URI = "Negotiation-T/information-negotiation/accept-reject/v1"

client = A2ATClient(env_path=Path("package_data/.env"))
server = A2ATServer(env_path=Path("package_data/.env"))

# 1) The server asks for the missing information: a propose message built from typed
#    content (deterministic, zero LLM calls). The session context must carry a UUID id.
context = NegotiationContext.of(str(uuid.uuid4()), 1, NegotiationPerformative.PROPOSE)
propose = server.generate_negotiation_propose_prompt_from_data(
    NegotiationProposeData(
        context=context,
        content=InformationProposeContent(
            items=[NegotiationItem(name="faultLevel", value="the fault level to subscribe to")],
            relationship=None,
        ),
    ),
    PROPOSE_URI,
)
print(propose.prompt_text)
print(propose.build_metadata_content())  # carries negotiationContext: id/round/maxRounds/performative

# 2) The client validates the received message and extracts the requested parameters
filled = client.validate_propose_prompt_and_data_filling(
    propose.prompt_text,
    propose.negotiation_context,
    {"type": "object", "properties": {"faultLevel": {"type": "string"}}, "required": ["faultLevel"]},
    PROPOSE_URI,
)

# 3) The client answers with an accept message carrying the filled information
accept = client.generate_negotiation_accept_prompt_from_data(
    NegotiationEndingData(
        context=propose.negotiation_context.next_round().with_performative(NegotiationPerformative.ACCEPT),
        content=InformationEndingContent(
            conclusion=NegotiationConclusion.ACCEPT,
            items=[NegotiationItem(name="faultLevel", value=str(filled.data["faultLevel"]))],
        ),
    ),
    ACCEPT_REJECT_URI,
)
print(accept.prompt_text)
```

The same exchange can be driven from natural language with the `*_from_text` variants (`generate_negotiation_propose_prompt_from_text` and so on), which run one LLM content-extraction step before the deterministic rendering.

To see the complete four-message closed loop (Task-T prompt with missing parameters → Negotiation-T propose → Task-T prompt with filled parameters + accept → result), run the offline sample; it works without an LLM API key because a scripted mock LLM serves the LLM steps:

```bash
cd {project_path}/a2a-t-sdk-python/a2a-t-sample
cp env.example .env
uv pip install -r requirements.txt
uv run python -m negotiation_demo                 # from-data strategy (zero LLM calls)
uv run python -m negotiation_demo --fromText      # from-text strategy (mock LLM)
uv run python -m negotiation_demo --language zh-CN
```

## 1.6 Configuration Quick Reference

| Configuration item                     | Description                                                                                                              |
|----------------------------------------|--------------------------------------------------------------------------------------------------------------------------|
| `A2AT_LANGUAGE`                        | Prompt resource language; built-in `zh-CN` and `en-US`, default `en-US`                                                  |
| `A2AT_PROMPT_SOURCE_TYPE`              | Prompt resource source: `packaged` (default since 1.1.0) or `local_file`                                                 |
| `A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR`  | Local prompt resource root directory; used only in `local_file` mode (fail-fast when unset or the path does not exist)   |
| `A2AT_PROMPT_COMPLIANCE_ENABLED`       | Whether to enable the server-side prompt compliance check, default `false`                                               |
| `A2AT_INPUT_TEXT_MAX_CHARS`            | Maximum length in characters (`len()`, Unicode code points) accepted for free-text inputs before any LLM step; oversized inputs fail fast with `input.text_too_long` instead of being truncated, default `16384` |
| `A2AT_LLM_PROVIDER`                    | LLM provider; currently only `openai` (OpenAI-compatible)                                                                |
| `A2AT_LLM_MODEL`                       | Model name                                                                                                               |
| `A2AT_LLM_API_KEY`                     | LLM API key                                                                                                              |
| `A2AT_LLM_BASE_URL`                    | LLM service address (OpenAI-compatible)                                                                                  |
| `A2AT_LLM_MAX_TOKENS`                  | Optional maximum number of generated tokens for completion calls; provider default when left empty                       |
| `A2AT_LLM_TEMPERATURE`                 | Sampling temperature; provider default when left empty                                                                   |
| `A2AT_LLM_TIMEOUT_SECONDS`             | LLM request timeout in seconds; provider default when left empty                                                         |
| `A2AT_LLM_HISTORY_WINDOW`              | Number of chat history messages to keep, default `10`                                                                    |
| `A2AT_LLM_REASONING_EFFORT`            | Reasoning effort level; one of `none` / `minimal` / `low` / `medium` / `high` / `xhigh`; not sent when left empty         |
| `A2AT_LLM_SSL_VERIFY`                | Whether to verify the LLM endpoint TLS certificate chain and hostname (per the CA trust configuration used by the HTTPX/OpenAI client); `false` disables both — prefer importing a trusted CA and use `false` only short-term in controlled environments, default `true` |
| `A2AT_LLM_SESSION_MAX_TOTAL`           | Maximum total number of tracked LLM sessions, default `300`                                                              |
| `A2AT_LLM_SESSION_MAX_PER_PROVIDER`    | Maximum number of tracked LLM sessions per provider, default `100`                                                       |
| `A2AT_LLM_MAX_ATTEMPTS`                | Maximum number of attempts for retryable LLM steps; range 1–10 (out-of-range values are clamped with a warning), default `3` |
| `A2AT_LLM_DETAIL_LOG_ENABLED`          | Whether to print the full LLM request/response payloads (no truncation), default `false`; enabling it may expose sensitive information or consume log space — use only in the DEBUG phase and keep disabled in production; timestamp/token/latency summary logs are emitted at DEBUG level on the dedicated logger `a2a_t.llm.call`, see developer guide 1.15 |
| `A2AT_NEGOTIATION_STATE_STORE_TYPE`    | Negotiation state store of the deprecated state-machine negotiation demo (`in_memory`); the 1.1.0 negotiation content API is stateless and ignores this key |

### 1.6.1 Migration: the resource source default changed in 1.1.0

Before 1.1.0 the default value of `A2AT_PROMPT_SOURCE_TYPE` was `local_file`; since 1.1.0 it is `packaged`, mirroring the Java SDK's `classpath` default. Out of the box the SDK now reads the resources bundled in the installed package, which makes a wrong local path fail loudly instead of silently reading unexpected files.

To restore the pre-1.1.0 behavior, set:

```properties
A2AT_PROMPT_SOURCE_TYPE=local_file
A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR=/path/to/your/prompt_resources
```

Only business content (`templates/`, `slots/`, `scenarios/`, `negotiation-vocabulary/`) is read from the local root; the LLM instruction prompts (`prompts/`) and the error message templates (`errors/`) are always loaded from the installed package. The whole local root is captured as a frozen snapshot when the facade is constructed, so file edits take effect only after a process restart.

## 1.7 Error Handling

The SDK uses a two-track error model:

- **Result-returning entry points** (`generate_task_prompt`, `check_task_prompt`) never raise business failures: the failure travels inside the result object as a structured frozen dataclass.
- **Raising entry points** (the six `*_from_text` / `*_from_data_with_schema` methods, the twelve negotiation methods, and the `validate_*` validators) raise typed exceptions carrying a machine-readable catalog code.

### 1.7.1 Failure Dataclasses

`PromptGenerationResult.failure` is a `PromptGenerationFailure` and `PromptComplianceResult.failure` is a `PromptComplianceFailure`; both carry the same three fields:

| Field    | Description                                                        |
|----------|--------------------------------------------------------------------|
| `code`   | Machine-readable catalog code string, for example `slot.not_provided` |
| `message`| Human-readable message rendered from the code's bilingual template |
| `stage`  | Pipeline stage where the failure occurred                         |

Both dataclasses expose `to_dict()` for direct JSON serialization.

### 1.7.2 Error Catalog and Exception Tree

Every business failure carries one code out of a closed catalog of 42 layered `domain.semantic` codes (`template.not_found`, `slot.rule_violation`, `content.param_missing`, `negotiation.conclusion_mismatch`, `llm.invocation_failed`, `input.text_too_long`, `infra.config_invalid`, ...), each with its category (business / infra) and its declared fact parameters. All exceptions derive from the single root `A2ATError`; business failures derive from `A2ATBusinessError`, which carries the code and the structured fact values:

```python
from a2a_t.core.errors.exceptions import A2ATBusinessError, NegotiationGenerationError

try:
    metadata = client.generate_negotiation_propose_prompt_from_data(data, PROPOSE_URI)
except NegotiationGenerationError as exc:
    print(exc.code_str)   # "negotiation.content_invalid" (plain string, JSON-ready)
    print(exc.facts)      # {"field": "...", "reason": "..."} (structured fact values)
    print(exc.message)    # rendered in the configured language
except A2ATBusinessError as exc:
    print(exc.code_str, exc.facts)  # any other business failure
```

Module-specific exception types include `PromptGenerationError`, `ContentValidationError`, `A2ATParamExtractionError`, `NegotiationGenerationError`, and `NegotiationParamExtractionError`; catching `A2ATBusinessError` covers all of them. Programming errors (`None`, blank, or malformed arguments — including a malformed template URI) deliberately stay outside the tree as `TypeError` / `ValueError`. The retryable LLM failure codes (`llm.invocation_failed`, `llm.response_invalid`, `negotiation.content_extract_failed`) are retried internally up to `A2AT_LLM_MAX_ATTEMPTS`; when the attempts are exhausted the original code is re-raised.

### 1.7.3 Bilingual Error Messages

Error messages are rendered from the bundled `prompt_resources/errors/{language}/errors.json` templates following `A2AT_LANGUAGE`, with fact values substituted into `{name}` placeholders. Rendering is never-throw: a template missing in the requested language falls back to `en-US`, a template missing in both languages renders as the bare code, and an unreadable catalog only logs a warning.

## 1.8 Bilingual Support

The SDK ships every user-facing resource in both `zh-CN` and `en-US`:

- prompt templates (`templates/<extension>/<path>/v1/{zh-CN,en-US}/template.md`),
- slot schemas, scenario catalogs, and the negotiation vocabulary,
- LLM instruction prompts,
- the 42 error message templates.

Set `A2AT_LANGUAGE` to switch the whole runtime at once. The negotiation vocabulary is strictly parity-checked between the two languages, and the negotiation templates must stay byte-identical with their Java counterparts, so both languages expose the same capability surface.

## 1.9 Constraints and Limitations

1. The SDK is stateless: the negotiation session state travels in the A2A-T metadata (`negotiationContext`) of each message, never inside the SDK.
2. The legacy state-machine negotiation demo (`start_negotiation` / `receive_negotiation` / `continue_negotiation` and its `in_memory` state store) is deprecated since 1.1.0 and will be removed in the next release; use the negotiation content API instead (see 1.5.3).
3. Prompt resources support two sources (`packaged` and `local_file`); remote resource loading from a registry center is not supported.
4. Free-text inputs are length-limited (`A2AT_INPUT_TEXT_MAX_CHARS`, default 16384) and fail fast instead of being truncated; structured `from_data` input is not subject to the limit.
5. The SDK is responsible for A2A-T prompt generation, validation, and negotiation; it is not responsible for AgentCard registration, authentication, service hosting, or business execution.
