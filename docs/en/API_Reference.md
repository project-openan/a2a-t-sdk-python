# 1 API Reference

## 1.1 Introduction

The A2A-T SDK exposes only two entry points: the client entry `A2ATClient` (prompt generation, negotiation message generation and validation) and the server entry `A2ATServer` (prompt validation, negotiation message generation and validation). The API category overview is below.

- **API overview**

| Category | API definition | Client/Server | Meaning | Involves an LLM |
| ---- | ------- | -------- | ------- |---------|
| Negotiation-T | `generate_negotiation_propose_prompt_from_text` | A2A-T Client / A2A-T Server | Generates a negotiation propose message from natural-language text | Yes |
| Negotiation-T | `generate_negotiation_accept_prompt_from_text` | A2A-T Client / A2A-T Server | Generates a negotiation accept message from natural-language text | Yes |
| Negotiation-T | `generate_negotiation_reject_prompt_from_text` | A2A-T Client / A2A-T Server | Generates a negotiation reject message from natural-language text | Yes |
| Negotiation-T | `generate_negotiation_abort_prompt_from_text` | A2A-T Client / A2A-T Server | Generates a negotiation abort message from natural-language text | Yes |
| Negotiation-T | `generate_negotiation_propose_prompt_from_data` | A2A-T Client / A2A-T Server | Deterministically generates a negotiation propose message from structured data | No |
| Negotiation-T | `generate_negotiation_accept_prompt_from_data` | A2A-T Client / A2A-T Server | Deterministically generates a negotiation accept message from structured data | No |
| Negotiation-T | `generate_negotiation_reject_prompt_from_data` | A2A-T Client / A2A-T Server | Deterministically generates a negotiation reject message from structured data | No |
| Negotiation-T | `generate_negotiation_abort_prompt_from_data` | A2A-T Client / A2A-T Server | Deterministically generates a negotiation abort message from structured data | No |
| Negotiation-T | `validate_propose_prompt_and_data_filling` | A2A-T Client / A2A-T Server | Validates a negotiation propose message for compliance and extracts parameters per a Schema | Yes |
| Negotiation-T | `validate_accept_prompt_and_data_filling` | A2A-T Client / A2A-T Server | Validates a negotiation accept message for compliance and extracts parameters per a Schema | Yes |
| Negotiation-T | `validate_reject_prompt_and_data_filling` | A2A-T Client / A2A-T Server | Validates a negotiation reject message for compliance and extracts parameters per a Schema | Yes |
| Negotiation-T | `validate_abort_prompt_and_data_filling` | A2A-T Client / A2A-T Server | Validates a negotiation abort message for compliance and extracts parameters per a Schema | Yes |
| Task-T | `generate_task_prompt_from_text` | A2A-T Client | Generates a task prompt from natural-language text with a specified Task-T template (skipping scenario recognition) | Yes |
| Task-T | `generate_task_prompt_from_data_with_schema` | A2A-T Client | Generates a task prompt from structured data + a semantic Schema with a specified Task-T template | Yes |
| Task-T | `validate_task_prompt_and_data_filling` | A2A-T Server | Validates a Task-T task prompt for compliance and extracts parameters per a Schema | Yes |
| Notification-T | `generate_notification_prompt_from_text` | A2A-T Client | Generates a notification subscription prompt from natural-language text with a specified Notification-T template | Yes |
| Notification-T | `generate_notification_prompt_from_data_with_schema` | A2A-T Client | Generates a notification subscription prompt from structured data + a semantic Schema with a specified Notification-T template | Yes |
| Notification-T | `validate_notification_prompt_and_data_filling` | A2A-T Server | Validates a Notification-T prompt for compliance and extracts parameters per a Schema | Yes |
| Authorization-T | `generate_auth_prompt_from_text` | A2A-T Client | Generates an authorization prompt from natural-language text with a specified Authorization-T template | Yes |
| Authorization-T | `generate_auth_prompt_from_data_with_schema` | A2A-T Client | Generates an authorization prompt from structured data + a semantic Schema with a specified Authorization-T template | Yes |
| Authorization-T | `validate_auth_prompt_and_data_filling` | A2A-T Server | Validates an Authorization-T prompt for compliance and extracts parameters per a Schema | Yes |
| General APIs | `generate_task_prompt` | A2A-T Client | Generates a task prompt from natural-language or structured input through scenario recognition | Yes |
| General APIs | `check_task_prompt` | A2A-T Server | Validates a task prompt for scenario, template, and slot compliance | Yes |

**Common data types and conventions**

- **Negotiation session context:** `NegotiationContext(id, round, max_rounds, performative)` (a frozen dataclass; `id` is UUID-shaped, `round` starts at 1, default budget `DEFAULT_MAX_ROUNDS = 5`), travels in the message metadata without involving the LLM; `NegotiationContext.of(id, round, performative)` uses the default budget, `next_round()` advances the round, `with_performative()` derives a context carrying another communicative intent, and `is_exhausted()` reports whether the budget is exceeded. `NegotiationPerformative` takes the values `PROPOSE` / `ACCEPT` / `REJECT` / `ABORT`.
- **Exception system:** every SDK processing failure is a subclass of `A2ATError`; catching `A2ATError` covers all processing failures, and `code_str` gives the machine-readable error code. All messages are rendered by the SDK from error-code templates, with the language following `A2AT_LANGUAGE`. The following business exceptions all extend `A2ATBusinessError`, which additionally provides `facts` returning the structured fact values the rendered message is based on;
  - Template generation failures raise `PromptGenerationError` (Task-T / Notification-T / Authorization-T) or `NegotiationGenerationError` (Negotiation-T).
  - Template validation + parameter extraction failures raise `ContentValidationError` (Task-T / Notification-T / Authorization-T) or `NegotiationParamExtractionError` (Negotiation-T).
  - Infrastructure failures are `ResourceNotFoundError` and `ConfigFileNotFoundError`, which extend `A2ATError` directly; programming errors (None, blank, or malformed arguments) deliberately stay outside the tree and are raised as `TypeError` / `ValueError`.
- **SlotValidationError:** per-slot validation error details, returned with validation-failure exceptions (`errors` / `failed_parameters` in the output descriptions of the respective APIs refer to this definition):

   | Field | Type | Description                                                                                                                       |
   | ---- | ---- |--------------------------------------------------------------------------------------------------------------------------|
   | slot_name | str | Name of the slot that failed                                                                                                                    |
   | code | str | Slot-level error code from the 1.4 error code list, such as `slot.not_provided`, `content.param_missing`, `content.entry_field_missing`, `content.format_error` |
   | message | str | Human-readable error description rendered by the SDK from the code's message template; the language follows `A2AT_LANGUAGE`                                                                         |
   | facts | dict[str, str] \| None | Structured fact values the message is based on (such as `section_label`, `index`, `field_label`); may be None                                                          |

- **Result-track data classes:** `generate_task_prompt` and `check_task_prompt` do not raise business exceptions; failure payloads are returned with the result object (structures in 1.3.22 / 1.3.23).
- **Template URIs:** every method on the `A2ATClient` and `A2ATServer` facades that addresses a template declares the template selection through `template_uri`, accepts raw strings, and recommends passing the `*_URI` string constants of `a2a_t.core.standard_templates` directly (each has a same-named typed twin constant without the `_URI` suffix); strings from external sources can be passed in as-is. The `TemplateUri` type is still used by the underlying service layers and can be parsed with `TemplateUri.parse(str)`, returning `TemplateUri | None` without raising. The currently supported template URI constants (string form) are:

   | Constant name                                                        | Meaning | TemplateUri |
   |-------------------------------------------------------------| ---- | ---- |
   | standard_templates.ENERGY_SAVING_URI                         | Task-T energy-saving task template | Task-T/network-layer/ran-energy-saving/v1 |
   | standard_templates.PRIVATE_LINE_COMPLAINT_URI                | Task-T private-line-complaint task template | Task-T/network-layer/private-line-complaint/v1 |
   | standard_templates.SUBSCRIBE_INCIDENT_URI                    | Notification-T incident subscription notification template | Notification-T/network-layer/subscribe-incident/v1 |
   | standard_templates.SERVICE_RECOVERY_URI                      | Notification-T service recovery notification template | Notification-T/network-layer/service-recovery/v1 |
   | standard_templates.AUTHORIZATION_POLICY_MANAGEMENT_URI       | Authorization-T authorization policy management template | Authorization-T/authorization-policy-management/v1 |
   | standard_templates.INFORMATION_NEGOTIATION_PROPOSE_URI       | Negotiation-T information negotiation propose template | Negotiation-T/information-negotiation/propose/v1 |
   | standard_templates.INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI | Negotiation-T information negotiation accept/reject template | Negotiation-T/information-negotiation/accept-reject/v1 |
   | standard_templates.TARGET_NEGOTIATION_PROPOSE_URI            | Negotiation-T target negotiation propose template | Negotiation-T/target-negotiation/propose/v1 |
   | standard_templates.TARGET_NEGOTIATION_ACCEPT_REJECT_URI      | Negotiation-T target negotiation accept/reject template | Negotiation-T/target-negotiation/accept-reject/v1 |
   | standard_templates.FEASIBILITY_NEGOTIATION_PROPOSE_URI       | Negotiation-T feasibility negotiation propose template | Negotiation-T/feasibility-negotiation/propose/v1 |
   | standard_templates.FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI | Negotiation-T feasibility negotiation accept/reject template | Negotiation-T/feasibility-negotiation/accept-reject/v1 |
   | standard_templates.NEGOTIATION_ABORT_URI                     | Negotiation-T common abort template | Negotiation-T/common/abort/v1 |

- **Template URI validation policy:** all methods with a `template_uri` parameter accept raw URI strings. A `None` template_uri raises `TypeError`; a blank or malformed URI (fewer than three segments, or a segment that is not a simple segment) raises `ValueError` with the message `Unparseable template URI: <input>`.

## 1.2 Constraints and Limitations

- Some APIs involve LLM calls. When using them, control the call frequency and concurrency based on the concurrency the connected model service can provide and your business latency requirements.
- APIs involving LLM calls apply length protection to `text` inputs: when the input exceeds `A2AT_INPUT_TEXT_MAX_CHARS` configured in `.env` (the `package_data/.env` or the file pointed to by `env_path`), the error code `input.text_too_long` is reported; the default of this configuration item is 16384 (16×1024). Structured data that does not involve LLM calls is not limited.
- The SDK is stateless: negotiation session state travels with the A2A-T metadata (`negotiationContext`) of each message and is not stored inside the SDK; the legacy `start_negotiation` / `receive_negotiation` / `continue_negotiation` state-machine negotiation APIs are deprecated since 1.1.0, emit a `DeprecationWarning` when called, and will be removed in the next release.

## 1.3 API Descriptions

### 1.3.1 generate_negotiation_propose_prompt_from_text

**API definition**

```python
def generate_negotiation_propose_prompt_from_text(
    self, text: str | None, context: NegotiationContext,
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: after receiving a Task-T task message with incomplete parameters, the server Agent sends the client Agent a negotiation request in natural language to "supplement the missing information"; it also applies when the client Agent asks the server Agent for target clarification or a feasibility evaluation.

**Function description**: generates the structured negotiation message of the negotiation propose phase from natural-language text. Execution flow: load the template first, then run one LLM content extraction, and finally render the template deterministically. It applies to the initiator of information/target/feasibility negotiation.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| text | str | Yes | Natural-language text describing the propose content (such as the list of missing information to request); the input length is limited by `A2AT_INPUT_TEXT_MAX_CHARS`, default 16384 |
| context | NegotiationContext | Yes | Negotiation session context, injected into the `negotiationContext` metadata of the generated message without any LLM involvement |
| template_uri | str | Yes | Propose template, such as `INFORMATION_NEGOTIATION_PROPOSE_URI` |

**Request sample**

```python
from pathlib import Path
from a2a_t.client.a2at_client import A2ATClient
from a2a_t.core.metadata import MetadataContent, NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import INFORMATION_NEGOTIATION_PROPOSE_URI

client = A2ATClient(env_path=Path("package_data/.env"))

ctx = NegotiationContext.of(
    "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3", 1, NegotiationPerformative.PROPOSE)

propose = client.generate_negotiation_propose_prompt_from_text(
    "Please provide the following missing information: Complaint category: private line "
    "interruption or poor private line quality. Both parameters are required; diagnosis "
    "cannot start without them.",
    ctx,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
)

# the generated metadata travels with the A2A message
metadata = propose.build_metadata_content()
```

**Output description**

On success, returns `MetadataContent`:

| Field/method | Type | Description |
| --------- | ---- | ---- |
| template_uri | str | Template URI used to generate the message, such as `Negotiation-T/information-negotiation/propose/v1` |
| prompt_text | str | Rendered negotiation message text, transferred as the value of the extension URI in the A2A message metadata |
| extension_uri | str | TMF extension URI (`https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1`), i.e. the key of the message in the metadata |
| negotiation_context | NegotiationContext | Negotiation session context (id / round / maxRounds / performative), carried with the message without LLM involvement |
| build_metadata_content() | dict[str, object] | Builds a map that can go directly into `Message.metadata`: extension URI → message text, `templateUri` → template URI, `negotiationContext` → nested context object |

On failure, raises `NegotiationGenerationError` (an `A2ATError` subclass):

| Member | Type | Description |
| ---- | ---- | ---- |
| code_str | str | Machine-readable error code; values below |
| message | str | Human-readable failure description |
| facts | dict[str, str] | Structured fact values the rendered message is based on |

Error codes:

- `template.not_found` (the template or prompt resource is missing)

- `negotiation.content_extract_failed` (structured content could not be extracted from the text, retryable)

- `llm.not_configured` (no LLM client is configured; check the `A2AT_LLM_*` settings)

- `llm.invocation_failed` (LLM transport failure, retryable)

- `llm.response_invalid` (the LLM response does not meet the step requirements, retryable)

- `negotiation.invalid_input` (blank text, extracted content inconsistent with the phase, or a confirm request contradicting other sections)

- `negotiation.field_missing` (a required field is missing)

- `input.text_too_long` (the input exceeds `A2AT_INPUT_TEXT_MAX_CHARS`)

A `None` context or template_uri raises `TypeError`; a blank or malformed template_uri, or a performative segment that is not `propose`, raises `ValueError`; `None` or blank text is not a programming error and raises `NegotiationGenerationError` with `negotiation.invalid_input` (see the error codes above).

**Response sample**

```text
template_uri : Negotiation-T/information-negotiation/propose/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## Information Negotiation
Please supplement the relevant content based on <Required Information Items>.

## Required Information Items
1. Complaint Category: e.g. dedicated-line quality degradation
```

### 1.3.2 generate_negotiation_accept_prompt_from_text

**API definition**

```python
def generate_negotiation_accept_prompt_from_text(
    self, text: str | None, context: NegotiationContext,
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: after receiving the counterpart's information negotiation request, the negotiation responder (usually the client Agent) supplements/delivers the requested information in natural language and generates an accept message to return, e.g. filling in the access port name and complaint category and then confirming that diagnosis can start.

**Function description**: generates a negotiation accept message from natural-language text. One LLM content extraction (the extracted conclusion must be `ACCEPT`, otherwise rejected with `negotiation.conclusion_mismatch`) + deterministic rendering. It applies when the negotiation responder supplements/delivers information.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| text | str | Yes | Natural-language text describing the accept content (such as the list of information to deliver); the input length is limited by `A2AT_INPUT_TEXT_MAX_CHARS`, default 16384 |
| context | NegotiationContext | Yes | Negotiation session context |
| template_uri | str | Yes | accept-reject template, such as `INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI` |

**Request sample**

```python
accept = client.generate_negotiation_accept_prompt_from_text(
    "Agree to supplement the following information: 1. Access Port Name: "
    "P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1; 2. Complaint Category: "
    "poor dedicated-line quality. The information is complete and diagnosis can start.",
    ctx.with_performative(NegotiationPerformative.ACCEPT),
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

**Output description**

On success, returns `MetadataContent` (same structure as [1.3.1](#131-generate_negotiation_propose_prompt_from_text)).

On failure, raises `NegotiationGenerationError` (same structure as 1.3.1). Error codes:

- `template.not_found` (the template or prompt resource is missing)

- `negotiation.content_extract_failed` (structured content could not be extracted from the text, retryable)

- `llm.not_configured` (no LLM client is configured; check the `A2AT_LLM_*` settings)

- `llm.invocation_failed` (LLM transport failure, retryable)

- `llm.response_invalid` (the LLM response does not meet the step requirements, retryable)

- `negotiation.invalid_input` (blank text)

- `negotiation.conclusion_mismatch` (the extracted conclusion is not `ACCEPT`)

- `negotiation.field_missing` (a required field is missing)

- `input.text_too_long` (the input exceeds `A2AT_INPUT_TEXT_MAX_CHARS`)

**Response sample**

```text
template_uri : Negotiation-T/information-negotiation/accept-reject/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## Information Negotiation Result
Accept

## Information Negotiation Result Content
1. Access Port Name: P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1
2. Complaint Category: dedicated-line quality degradation
```

### 1.3.3 generate_negotiation_reject_prompt_from_text

**API definition**

```python
def generate_negotiation_reject_prompt_from_text(
    self, text: str | None, context: NegotiationContext,
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: when the negotiation responder (usually the client Agent) cannot satisfy the counterpart's negotiation request, it generates a reject message in natural language to return and ends this negotiation round, e.g. the access port name cannot be provided because the site list is unavailable.

**Function description**: generates a negotiation reject message from natural-language text. One LLM content extraction (the extracted conclusion must be `REJECT`, otherwise rejected with `negotiation.conclusion_mismatch`) + deterministic rendering.

**Input description**: same as 1.3.2, with text being the natural-language text describing the rejection reason.

**Request sample**

```python
reject = client.generate_negotiation_reject_prompt_from_text(
    "Refuse to supplement the information: the Access Port Name cannot be provided because "
    "the site list is unavailable; this negotiation ends.",
    ctx.with_performative(NegotiationPerformative.REJECT),
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

**Output description**

On success, returns `MetadataContent` (same structure as [1.3.1](#131-generate_negotiation_propose_prompt_from_text)).

On failure, raises `NegotiationGenerationError` (same structure as 1.3.1). Error codes:

- `template.not_found` (the template or prompt resource is missing)

- `negotiation.content_extract_failed` (structured content could not be extracted from the text, retryable)

- `llm.not_configured` (no LLM client is configured; check the `A2AT_LLM_*` settings)

- `llm.invocation_failed` (LLM transport failure, retryable)

- `llm.response_invalid` (the LLM response does not meet the step requirements, retryable)

- `negotiation.invalid_input` (blank text)

- `negotiation.conclusion_mismatch` (the extracted conclusion is not `REJECT`)

- `negotiation.field_missing` (a required field is missing)

- `input.text_too_long` (the input exceeds `A2AT_INPUT_TEXT_MAX_CHARS`)

**Response sample**

```text
template_uri : Negotiation-T/information-negotiation/accept-reject/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## Information Negotiation Result
Reject

## Information Negotiation Result Content
1. Access Port Name: Cannot be provided; the port resource ledger on the workbench side is temporarily unavailable
```

### 1.3.4 generate_negotiation_abort_prompt_from_text

**API definition**

```python
def generate_negotiation_abort_prompt_from_text(
    self, text: str | None, context: NegotiationContext,
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: when the negotiation cannot continue (round budget exhausted, timeout, token budget exhausted, etc.), either negotiation participant generates an abort message in natural language to return and terminates the whole negotiation session.

**Function description**: generates a negotiation abort message from natural-language text. Abort messages are independent of the negotiation type: the addressed template must be the common abort template (`Negotiation-T/common/abort/v1`) and the content carries only the termination reason. The flow is one LLM content extraction + deterministic rendering.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| text | str | Yes | Natural-language text describing the termination reason; the input length is limited by `A2AT_INPUT_TEXT_MAX_CHARS`, default 16384 |
| context | NegotiationContext | Yes | Negotiation session context |
| template_uri | str | Yes | Common abort template: `NEGOTIATION_ABORT_URI` (`Negotiation-T/common/abort/v1`) |

**Request sample**

```python
abort = client.generate_negotiation_abort_prompt_from_text(
    "The negotiation round limit is reached; the missing parameters cannot be provided; "
    "this negotiation is terminated.",
    ctx.with_performative(NegotiationPerformative.ABORT),
    NEGOTIATION_ABORT_URI,
)
```

**Output description**

On success, returns `MetadataContent` (same structure as [1.3.1](#131-generate_negotiation_propose_prompt_from_text)).

On failure, raises `NegotiationGenerationError` (same structure as 1.3.1). Error codes:

- `template.not_found` (the template or prompt resource is missing)

- `negotiation.content_extract_failed` (structured content could not be extracted from the text, retryable)

- `llm.not_configured` (no LLM client is configured; check the `A2AT_LLM_*` settings)

- `llm.invocation_failed` (LLM transport failure, retryable)

- `llm.response_invalid` (the LLM response does not meet the step requirements, retryable)

- `negotiation.invalid_input` (blank text)

- `negotiation.field_missing` (a required field is missing)

- `input.text_too_long` (the input exceeds `A2AT_INPUT_TEXT_MAX_CHARS`)

Programming errors: a `None` context or template_uri raises `TypeError`; a blank or malformed template_uri, or one not addressing the common abort template, raises `ValueError`.

**Response sample**

```text
template_uri : Negotiation-T/common/abort/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## Negotiation Result
Abort

## Negotiation Termination Reason
The negotiation round limit is reached; the missing parameters cannot be provided
```

### 1.3.5 generate_negotiation_propose_prompt_from_data

**API definition**

```python
def generate_negotiation_propose_prompt_from_data(
    self, data: NegotiationProposeData, template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: the same initiation scenario as fromText, but with structured data built by the business system as input (e.g. the server Agent auto-generating negotiation request items from the missing-slot list detected by `validate_task_prompt_and_data_filling`), suitable for scenarios that require deterministic message content and do not want the uncertainty of LLM extraction.

**Function description**: deterministically generates a negotiation propose message from structured data input, **without calling the LLM**. The typed content is validated, dispatched to the generator of the negotiation type addressed by the template URI, and rendered deterministically from the template.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| data | `NegotiationProposeData(context, content)` | Yes | Negotiation context + typed propose content; the content type is selected by negotiation type |
| template_uri | str | Yes | Propose template |

The propose content of the three negotiation types (`a2a_t.negotiation.content.models`):

| Negotiation type | Propose content type | Fields |
| -------- | ---------------- | ---- |
| Information negotiation | `InformationProposeContent` | `items` (the list of missing items), `relationship` (the relationship among the missing items, nullable) |
| Target negotiation | `TargetProposeContent` | `target_negotiation_description` (required), `intent_understanding`, `alignment_and_clarification`, `request_for_clarification` (the three item lists can all be empty/NULL; empty omits the corresponding section), `target_confirm_request` (nullable str; non-empty means this round's message category is "target clarified and requesting confirmation from the counterpart"; when non-empty, `intent_understanding` / `alignment_and_clarification` / `request_for_clarification` must all be empty) |
| Feasibility negotiation | `FeasibilityProposeContent` | `feasibility_negotiation_description` (required), `action` (`NegotiationAction.REQUEST_FEASIBILITY_EVALUATION` / `PROPOSE_ALTERNATIVE_ON_FAILURE`, fixed two values), `contents_to_evaluate`, `infeasibility_details_and_proposal`, `feasibility_confirm_request` (nullable str; non-empty means this round's message category is "assessed as feasible and requesting confirmation": `action` must be `REQUEST_FEASIBILITY_EVALUATION` and both `contents_to_evaluate` / `infeasibility_details_and_proposal` must be empty) |

**Request sample**

```python
from a2a_t.negotiation.content.models import (
    InformationProposeContent, NegotiationItem, NegotiationProposeData,
)

ctx = NegotiationContext.of(
    "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3", 2, NegotiationPerformative.PROPOSE)

propose = client.generate_negotiation_propose_prompt_from_data(
    NegotiationProposeData(
        context=ctx,
        content=InformationProposeContent(
            items=[
                NegotiationItem("Access Port Name", "e.g. P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1"),
                NegotiationItem("Complaint Category", "e.g. dedicated-line quality degradation"),
                NegotiationItem("Private Line Service Identifier", None),
            ],
            relationship="OR",
        ),
    ),
    INFORMATION_NEGOTIATION_PROPOSE_URI,
)
```

**Output description**

On success, returns `MetadataContent` (same structure as [1.3.1](#131-generate_negotiation_propose_prompt_from_text); negotiation messages all carry `negotiation_context`).

On failure, raises `NegotiationGenerationError` (same structure as 1.3.1). Error codes:

- `template.not_found` (the template is missing)

- `negotiation.content_invalid` (a typed content field is invalid, e.g. the required items list is empty or a required description is blank)

- `negotiation.invalid_input` (invalid input scenarios such as a confirm request contradicting other conditional sections)

- `template.render_failed` (template rendering failed)

There are also two kinds of programming errors (outside the `A2ATError` tree, standard Python exceptions): `None` input or its context raises `TypeError`; a blank or malformed template_uri, a content type inconsistent with the template's negotiation type, or a performative segment that is not `propose` raises `ValueError`.

**Response sample**

```text
template_uri : Negotiation-T/information-negotiation/propose/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## Information Negotiation
Please supplement the relevant content based on <Required Information Items>.

## Required Information Items
1. Access Port Name: e.g. P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1
2. Complaint Category: e.g. dedicated-line quality degradation
3. Private Line Service Identifier
Relationship between missing items: OR
```

### 1.3.6 generate_negotiation_accept_prompt_from_data

**API definition**

```python
def generate_negotiation_accept_prompt_from_data(
    self, data: NegotiationEndingData, template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: the negotiation responder (usually the client Agent) programmatically fills the parameters per the slot list requested by the counterpart and generates an accept message with structured items to return.

**Function description**: deterministically generates a negotiation accept message from structured data input, **without calling the LLM**. `content.conclusion` must be `Accept`; any other conclusion (including `Abort`) is rejected with the `negotiation.conclusion_mismatch` business error.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| data | `NegotiationEndingData(context, content)` | Yes | Negotiation context + typed accept content (with the `Accept` conclusion) |
| template_uri | str | Yes | accept-reject template |

The accept content of the three negotiation types: `InformationEndingContent(ACCEPT, items)` (the list of delivered information items), `TargetEndingContent(ACCEPT, confirmed_intent, None)` (the finally confirmed intent), `FeasibilityEndingContent(ACCEPT, feasibility_summary)` (the feasibility evaluation conclusion summary).

**Request sample**

```python
from a2a_t.negotiation.content.enums import NegotiationConclusion
from a2a_t.negotiation.content.models import InformationEndingContent, NegotiationEndingData

accept = client.generate_negotiation_accept_prompt_from_data(
    NegotiationEndingData(
        context=ctx,
        content=InformationEndingContent(
            conclusion=NegotiationConclusion.ACCEPT,
            items=[
                NegotiationItem("Access Port Name", "P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1"),
                NegotiationItem("Complaint Category", "dedicated-line quality degradation"),
            ],
        ),
    ),
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

**Output description**

On success, returns `MetadataContent` (same structure as [1.3.1](#131-generate_negotiation_propose_prompt_from_text)).

On failure, raises `NegotiationGenerationError` (same structure as 1.3.1). Error codes:

- `template.not_found` (the template is missing)

- `negotiation.content_invalid` (a typed content field is invalid, e.g. the required items list is empty or a required description is blank)

- `template.render_failed` (template rendering failed)

Programming errors: `None` input or its context raises `TypeError`; a blank or malformed template_uri, an inconsistent content type, or a performative segment that is not `accept-reject` raises `ValueError`; a `conclusion` other than `ACCEPT` is rejected with the `negotiation.conclusion_mismatch` business error.

**Response sample**

```text
template_uri : Negotiation-T/information-negotiation/accept-reject/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## Information Negotiation Result
Accept

## Information Negotiation Result Content
1. Access Port Name: P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1
2. Complaint Category: dedicated-line quality degradation
```

### 1.3.7 generate_negotiation_reject_prompt_from_data

**API definition**

```python
def generate_negotiation_reject_prompt_from_data(
    self, data: NegotiationEndingData, template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: after programmatically determining that the counterpart's request cannot be satisfied, the negotiation responder generates a reject message with structured items (the items that cannot be provided and the reasons) to return.

**Function description**: deterministically generates a negotiation reject message from structured data input, **without calling the LLM**. `content.conclusion` must be `Reject`; any other conclusion is rejected with the `negotiation.conclusion_mismatch` business error.

**Input description**: same as [1.3.6](#136-generate_negotiation_accept_prompt_from_data), but with the `REJECT` conclusion. Reject content: `InformationEndingContent(REJECT, items)` (the items that cannot be provided and the reasons), `TargetEndingContent(REJECT, None, failure_reason)` (the rejection reason), `FeasibilityEndingContent(REJECT, feasibility_summary)` (the infeasible conclusion summary).

**Request sample**

```python
reject = client.generate_negotiation_reject_prompt_from_data(
    NegotiationEndingData(
        context=ctx,
        content=InformationEndingContent(
            conclusion=NegotiationConclusion.REJECT,
            items=[
                NegotiationItem(
                    "Access Port Name",
                    "Cannot be provided; the port resource ledger on the workbench side is temporarily unavailable",
                ),
            ],
        ),
    ),
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

**Output description**

On success, returns `MetadataContent` (same structure as [1.3.1](#131-generate_negotiation_propose_prompt_from_text)).

On failure, raises `NegotiationGenerationError` (same structure as 1.3.1). Error codes:

- `template.not_found` (the template is missing)

- `negotiation.content_invalid` (a typed content field is invalid, e.g. the required items list is empty or a required description is blank)

- `template.render_failed` (template rendering failed)

Programming errors: `None` input or its context raises `TypeError`; a blank or malformed template_uri, an inconsistent content type, or a performative segment that is not `accept-reject` raises `ValueError`; a `conclusion` other than `REJECT` is rejected with the `negotiation.conclusion_mismatch` business error.

**Response sample**

```text
template_uri : Negotiation-T/information-negotiation/accept-reject/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## Information Negotiation Result
Reject

## Information Negotiation Result Content
1. Access Port Name: Cannot be provided; the port resource ledger on the workbench side is temporarily unavailable
```

### 1.3.8 generate_negotiation_abort_prompt_from_data

**API definition**

```python
def generate_negotiation_abort_prompt_from_data(
    self, data: NegotiationAbortData, template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: either negotiation participant generates an abort message in a structured way and terminates a negotiation session that cannot continue.

**Function description**: deterministically generates a negotiation abort message from structured data input, **without calling the LLM**. Abort messages are type-independent: the addressed template must be the common abort template and `NegotiationAbortContent` carries only the termination reason (the `Abort` conclusion is fixed template text).

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| data | `NegotiationAbortData(context, content)` | Yes | Negotiation context + `NegotiationAbortContent(termination_reason)` |
| template_uri | str | Yes | Common abort template: `NEGOTIATION_ABORT_URI` |

**Request sample**

```python
from a2a_t.negotiation.content.models import NegotiationAbortContent, NegotiationAbortData

abort = client.generate_negotiation_abort_prompt_from_data(
    NegotiationAbortData(
        context=ctx,
        content=NegotiationAbortContent(
            termination_reason="The negotiation round limit is reached; the missing parameters cannot be provided",
        ),
    ),
    NEGOTIATION_ABORT_URI,
)
```

**Output description**

On success, returns `MetadataContent` (same structure as [1.3.1](#131-generate_negotiation_propose_prompt_from_text)).

On failure, raises `NegotiationGenerationError` (same structure as 1.3.1). Error codes:

- `template.not_found` (the template is missing)

- `negotiation.content_invalid` (a typed content field is invalid, e.g. a blank termination reason)

- `template.render_failed` (template rendering failed)

Programming errors: `None` input or its context raises `TypeError`; a blank or malformed template_uri, or one not addressing the common abort template, raises `ValueError`.

**Response sample**

```text
template_uri : Negotiation-T/common/abort/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## Negotiation Result
Abort

## Negotiation Termination Reason
The negotiation round limit is reached; the missing parameters cannot be provided
```

### 1.3.9 validate_propose_prompt_and_data_filling

**API definition**

```python
def validate_propose_prompt_and_data_filling(
    self,
    prompt: str | None,
    context: NegotiationContext | None,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**Typical scenario**: the initiator (the server Agent) self-checks outbound messages before sending a negotiation request; or the receiver (the client Agent) validates the received negotiation request and extracts the list of slots to supplement, driving the subsequent parameter filling.

**Function description**: validates whether a negotiation propose message is a well-formed negotiation message, and extracts parameters from it per the caller-provided JSON Schema. Pipeline order: template URI validation → deterministic rule gate (negotiation context) → template loading → one retryable LLM semantic validation (which also extracts the parameters) → deterministic parameter merging.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| prompt | str | Yes | The negotiation propose message text to validate (`MetadataContent.prompt_text`); the input length is limited by `A2AT_INPUT_TEXT_MAX_CHARS`, default 16384 |
| context | NegotiationContext | Yes | The negotiation context transmitted with the message; `None` reports `negotiation.invalid_input` |
| schema | Mapping[str, object] | Yes | Caller-provided parameter JSON Schema declaring the parameters to extract |
| template_uri | str | Yes | Propose template |

**Request sample**

```python
schema = {
    "type": "object",
    "properties": {
        "Access Port Name": {"type": "string"},
        "Complaint Category": {"type": "string"},
    },
    "required": ["Access Port Name"],
}

# propose_prompt is the negotiation propose message text sent by the counterpart
requested = client.validate_propose_prompt_and_data_filling(
    propose_prompt, ctx, schema, INFORMATION_NEGOTIATION_PROPOSE_URI,
)

# after filtering out the context parameters (id/round/maxRounds), the rest is the list of slots to supplement
needed = {k: v for k, v in requested.data.items() if k not in ("id", "round", "maxRounds")}
```

**Output description**

On success, returns `FilledParamData`:

| Field/method | Type | Description |
| --------- | ---- | ---- |
| data | dict[str, object] | Merged parameters: the negotiation context parameters (`id` / `round` / `maxRounds`, **winning** on key conflicts) + the parameters extracted from the message per the caller's Schema |

On failure, raises `NegotiationParamExtractionError` (an `A2ATError` subclass):

| Member | Type | Description |
| ---- | ---- | ---- |
| code_str | str | Machine-readable error code; values below |
| message | str | Human-readable failure description |
| errors | list[SlotValidationError] | Per-slot error details; structure in the common conventions |

Error codes:

- `negotiation.invalid_input` (the prompt is `None` or blank, the message is not a negotiation message, or the context is `None`)

- `negotiation.rule_violation` (the negotiation context violates the rules; the nested slot error codes in `errors` identify the specific rule, such as `negotiation.invalid_context_id`, `negotiation.round_exceeded`)

- `negotiation.semantic_rejected` (semantic validation rejected; the per-slot details in `errors` use the closed `negotiation.*` code set, such as `negotiation.conclusion_content_mismatch`, `negotiation.missing_result_content`, `negotiation.field_inconsistency`)

- `llm.invocation_failed` / `llm.response_invalid` (LLM failure, retryable)

- `template.not_found` (the validation prompt resource is missing)

Programming errors: a `None` schema / template_uri raises `TypeError`; a blank or malformed template_uri, or a mismatched performative segment, raises `ValueError`; a `None` or blank prompt is not a programming error and raises `NegotiationParamExtractionError` with `negotiation.invalid_input` (see the error codes above).

**Response sample**

```text
requested.data =
{
  'Access Port Name': 'e.g. P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1',
  'Complaint Category': 'e.g. dedicated-line quality degradation',
  'id': '3dbc13b5-bd57-4c2b-b503-24e381b6c8d3',
  'round': 1,
  'maxRounds': 5
}
```

### 1.3.10 validate_accept_prompt_and_data_filling

**API definition**

```python
def validate_accept_prompt_and_data_filling(
    self,
    prompt: str | None,
    context: NegotiationContext | None,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**Typical scenario**: the initiator (the server Agent) validates the accept message returned by the counterpart, extracts the delivered parameter values, checks them against the expected filled values, and continues task execution after confirmation.

**Function description**: validates a negotiation accept message and extracts parameters per a Schema (the pipeline is the same as 1.3.9, with the expected performative fixed to `accept-reject`).

**Input description**: same as [1.3.9](#139-validate_propose_prompt_and_data_filling), with prompt being the accept message text and template_uri being the accept-reject template.

**Request sample**

```python
# accept_prompt is the accept message text returned by the client, accept_context its negotiation context
accept_params = server.validate_accept_prompt_and_data_filling(
    accept_prompt, accept_context, schema, INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

**Output description**

On success, returns `FilledParamData` (same structure as [1.3.9](#139-validate_propose_prompt_and_data_filling)); `data` carries the parameters extracted from the delivered content per the Schema plus the context parameters.

On failure, raises `NegotiationParamExtractionError` (same structure as 1.3.9). Error codes:

- `negotiation.invalid_input` (the prompt is `None` or blank, the message is not an accept negotiation message, or the context is `None`)

- `negotiation.rule_violation` (the negotiation context violates the rules; the nested slot error codes in `errors` identify the specific rule, such as `negotiation.invalid_context_id`, `negotiation.round_exceeded`)

- `negotiation.semantic_rejected` (the conclusion is not Accept or the content does not satisfy the accept-phase constraints)

- `llm.invocation_failed` / `llm.response_invalid` (LLM failure, retryable)

- `template.not_found` (the validation prompt resource is missing)

Programming errors: a `None` schema / template_uri raises `TypeError`; a blank or malformed template_uri, or a performative segment that is not `accept-reject`, raises `ValueError`; a `None` or blank prompt is not a programming error and raises `NegotiationParamExtractionError` with `negotiation.invalid_input` (see the error codes above).

**Response sample**

```text
accept_params.data =
{
  'Access Port Name': 'P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1',
  'Complaint Category': 'dedicated-line quality degradation',
  'id': '3dbc13b5-bd57-4c2b-b503-24e381b6c8d3',
  'round': 1,
  'maxRounds': 5
}
```

### 1.3.11 validate_reject_prompt_and_data_filling

**API definition**

```python
def validate_reject_prompt_and_data_filling(
    self,
    prompt: str | None,
    context: NegotiationContext | None,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**Typical scenario**: the initiator (the server Agent) validates the reject message returned by the counterpart, extracts the rejection reasons, and terminates the task or escalates to manual handling accordingly.

**Function description**: validates a negotiation reject message and extracts parameters per a Schema (the pipeline is the same as 1.3.9, with the expected performative fixed to `accept-reject`).

**Input description**: same as [1.3.9](#139-validate_propose_prompt_and_data_filling), with prompt being the reject message text and template_uri being the accept-reject template.

**Request sample**

```python
reject_params = server.validate_reject_prompt_and_data_filling(
    reject_prompt, ctx, schema, INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

**Output description**

On success, returns `FilledParamData` (same structure as [1.3.9](#139-validate_propose_prompt_and_data_filling)); `data` carries the parameters extracted from the rejection reasons per the Schema plus the context parameters.

On failure, raises `NegotiationParamExtractionError` (same structure as 1.3.9). Error codes:

- `negotiation.invalid_input` (the prompt is `None` or blank, the message is not a reject negotiation message, or the context is `None`)

- `negotiation.rule_violation` (the negotiation context violates the rules; the nested slot error codes in `errors` identify the specific rule, such as `negotiation.invalid_context_id`, `negotiation.round_exceeded`)

- `negotiation.semantic_rejected` (the conclusion is not Reject or the content does not satisfy the reject-phase constraints)

- `llm.invocation_failed` / `llm.response_invalid` (LLM failure, retryable)

- `template.not_found` (the validation prompt resource is missing)

Programming errors: a `None` schema / template_uri raises `TypeError`; a blank or malformed template_uri, or a performative segment that is not `accept-reject`, raises `ValueError`; a `None` or blank prompt is not a programming error and raises `NegotiationParamExtractionError` with `negotiation.invalid_input` (see the error codes above).

**Response sample**

```text
reject_params.data =
{
  'Access Port Name': 'Cannot be provided; the port resource ledger on the workbench side is temporarily unavailable',
  'id': '3dbc13b5-bd57-4c2b-b503-24e381b6c8d3',
  'round': 1,
  'maxRounds': 5
}
```

### 1.3.12 validate_abort_prompt_and_data_filling

**API definition**

```python
def validate_abort_prompt_and_data_filling(
    self,
    prompt: str | None,
    context: NegotiationContext | None,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**Typical scenario**: a negotiation participant validates the abort message sent by the counterpart, extracts the termination reason, and releases the session resources to end the negotiation accordingly.

**Function description**: validates a negotiation abort message and extracts parameters per a Schema (the pipeline is the same as 1.3.9; the template_uri must address the common abort template).

**Input description**: same as [1.3.9](#139-validate_propose_prompt_and_data_filling), with prompt being the abort message text and template_uri being the common abort template (`NEGOTIATION_ABORT_URI`).

**Request sample**

```python
abort_params = server.validate_abort_prompt_and_data_filling(
    abort_prompt, ctx, schema, NEGOTIATION_ABORT_URI,
)
```

**Output description**

On success, returns `FilledParamData` (same structure as [1.3.9](#139-validate_propose_prompt_and_data_filling)); `data` carries the parameters extracted from the termination reason per the Schema plus the context parameters.

On failure, raises `NegotiationParamExtractionError` (same structure as 1.3.9). Error codes:

- `negotiation.invalid_input` (the prompt is `None` or blank, the message is not an abort negotiation message, or the context is `None`)

- `negotiation.rule_violation` (the negotiation context violates the rules; the nested slot error codes in `errors` identify the specific rule, such as `negotiation.invalid_context_id`, `negotiation.round_exceeded`)

- `negotiation.semantic_rejected` (the message does not satisfy the abort-phase constraints)

- `llm.invocation_failed` / `llm.response_invalid` (LLM failure, retryable)

- `template.not_found` (the validation prompt resource is missing)

Programming errors: a `None` schema / template_uri raises `TypeError`; a blank or malformed template_uri, or one not addressing the common abort template, raises `ValueError`; a `None` or blank prompt is not a programming error and raises `NegotiationParamExtractionError` with `negotiation.invalid_input` (see the error codes above).

**Response sample**

```text
abort_params.data =
{
  'id': '3dbc13b5-bd57-4c2b-b503-24e381b6c8d3',
  'round': 1,
  'maxRounds': 5
}
```

### 1.3.13 generate_task_prompt_from_text

**API definition**

```python
def generate_task_prompt_from_text(
    self, text: str, template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: the client Agent converts the user's natural-language task description (such as a private-line complaint diagnosis request) into a Task-T protocol message of the specified scenario; suitable when the target template is already determined and scenario recognition should be skipped.

**Function description**: generates a task prompt message from natural-language text with the specified Task-T template, **skipping scenario recognition** (the template is specified explicitly by the caller). It runs one LLM slot-extraction step and then renders the template deterministically.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| text | str | Yes | Natural-language task description; the input length is limited by `A2AT_INPUT_TEXT_MAX_CHARS`, default 16384 |
| template_uri | str | Yes | Task-T template, such as `PRIVATE_LINE_COMPLAINT_URI` (`Task-T/network-layer/private-line-complaint/v1`) |

**Request sample**

```python
from a2a_t.core.standard_templates import PRIVATE_LINE_COMPLAINT_URI

metadata = client.generate_task_prompt_from_text(
    "Help me create a private-line complaint diagnosis task for the port "
    "P781-Zhujiang New Town-PTN7900-23-TPA1EG24-17. The customer reports poor private line "
    "quality. Starting from 8:30 a.m. on May 11, 2026, the core system access from Shenzhen "
    "to Guangzhou became very slow, with latency jumping from 12ms to 320ms, and the counter "
    "and mobile banking constantly report connection timeouts. The OSS sequence number is "
    "event-id-20260511-09013.",
    PRIVATE_LINE_COMPLAINT_URI,
)
```

**Output description**

On success, returns `MetadataContent`:

| Field/method | Type | Description |
| --------- | ---- | ---- |
| template_uri | str | Template URI used to generate the message, such as `Task-T/network-layer/private-line-complaint/v1` |
| prompt_text | str | Rendered task prompt message text, transferred as the value of the extension URI in the A2A message metadata |
| extension_uri | str | TMF extension URI (`https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1`), i.e. the key of the message in the metadata |
| negotiation_context | NegotiationContext | Always None (not a negotiation message) |
| build_metadata_content() | dict[str, object] | Builds a two-key map that can go directly into `Message.metadata`: extension URI → message text, `templateUri` → template URI |

On failure, raises `PromptGenerationError` (an `A2ATError` subclass):

| Member | Type | Description |
| ---- | ---- | ---- |
| code_str | str | Machine-readable error code; values below |
| message | str | Human-readable failure description |
| failed_parameters | list[SlotValidationError] | Details of the slot validation failures (non-empty for slot-domain failure codes such as `slot.not_provided`); structure in the common conventions |

Error codes:

- `template.not_found` (the template is missing)

- `template.load_failed` (loading a prompt resource failed)

- `slot.schema_not_found` (the slot Schema is missing)

- `llm.not_configured` (no LLM client is configured; check the `A2AT_LLM_*` settings)

- `llm.invocation_failed` / `llm.response_invalid` (LLM call failure, retryable)

- `slot.not_provided` (a required slot is not provided in the input)

- `slot.constraint_violated` (a slot value is outside the allowed range)

- `slot.rule_violation` (fallback code for other slot validation rule violations)

- `template.render_failed` (template rendering failed)

- `input.text_too_long` (the input exceeds `A2AT_INPUT_TEXT_MAX_CHARS`)

Programming errors: a `None` text or template_uri raises `TypeError`; a blank or malformed template_uri raises `ValueError`.

**Response sample**

```text
template_uri : Task-T/network-layer/private-line-complaint/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1
prompt_text  :
## Task Type
Transport private line service complaint diagnosis

## Task Description
Based on <Task Object> and <Task Context>, perform network-side fault root cause diagnosis in the complaint scenario, achieve the complaint diagnosis goal defined in <Task Target>, and return the task processing result in the structure defined in <Expected Output>.

## Task Target
Diagnose network-side faults and return diagnostic result information such as fault root causes and repair suggestions.

## Task Object
Access Port Name: P781-Zhujiang New Town-PTN7900-23-TPA1EG24-17

## Task Context
1. Complaint category: poor private line quality
2. Problem occurrence time: 2026-05-11T08:21:46Z
3. OSS-side event sequence number: event-id-20260511-09013
4. Complaint details: Starting from 8:30 a.m. on May 11, the response latency of accessing Guangzhou from Shenzhen suddenly rose from an average of 12ms to 320ms

## Expected Output
Requirement: The complaint diagnosis task result should include the following information:
1. Diagnosis result. Allowed values: success, failure (required)
2. Diagnosis result details (required)
3. Repair suggestions (optional)
4. Fault root cause list, where each fault root cause includes fault root cause name, detailed description, repair suggestions, fault root cause point location, etc. (optional)

## Terminology Explanation
1. Private line interruption
   - Synonyms: private line interruption, service interruption, network unreachable, service down, service inaccessible
2. Poor private line quality
   - Synonyms: private line poor quality, service stutter, service access timeout, service packet loss, service high latency, service jitter, service congestion, service experience degradation, service high error rate, service optical power abnormal
```

### 1.3.14 generate_task_prompt_from_data_with_schema

**API definition**

```python
def generate_task_prompt_from_data_with_schema(
    self,
    data: Mapping[str, object],
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: the client Agent converts the structured task parameters of an upstream system (field names may differ from the template slots, with the field semantics described by the Schema) into a Task-T protocol message; suitable when the task parameters are already held in structured form.

**Function description**: generates a task prompt from structured data + a semantic Schema with the specified Task-T template, **skipping scenario recognition**. The `schema` describes the business meaning of each input field (description / examples / enum etc.), guiding the slot filling and value constraints; each key of `data` corresponds to one slot value.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| data | Mapping[str, object] | Yes | Structured business field input; keys are business field names and values are field values |
| schema | Mapping[str, object] | Yes (non-empty) | Field-semantics JSON Schema describing the meaning and constraints of each field |
| template_uri | str | Yes | Task-T template |

**Request sample**

```python
data = {
    "portName": "P781-Futian Center-PTN7900-2-TPA1EG24-03",
    "complaintScenario": "poor private line quality",
    "faultStartTime": "2026-05-11T08:21:46Z",
    "ticketNo": "event-id-20260511-09013",
    "faultDetailText": "Starting from 8:30 a.m. on May 11, the response latency of accessing Guangzhou from Shenzhen suddenly rose from an average of 12ms to 320ms",
}

semantics_schema = {
    "type": "object",
    "properties": {
        "portName": {"type": "string", "description": "Business field: access port name, uniquely identifying the complained private line object"},
        "complaintScenario": {
            "type": "string",
            "description": "Business field: complaint category scenario; one of private line interruption and poor private line quality is required",
            "enum": ["private line interruption", "poor private line quality"],
        },
        "faultStartTime": {"type": "string", "description": "Business field: problem occurrence time"},
        "ticketNo": {"type": "string", "description": "Business field: complaint work order or event sequence number accepted on the OSS side"},
        "faultDetailText": {"type": "string", "description": "Business field: the user's free description of the fault symptom"},
    },
    "required": ["portName", "complaintScenario"],
}

metadata = client.generate_task_prompt_from_data_with_schema(
    data, semantics_schema, PRIVATE_LINE_COMPLAINT_URI,
)
```

**Output description**

On success, returns `MetadataContent` (same structure as [1.3.13](#1313-generate_task_prompt_from_text)).

On failure, raises `PromptGenerationError` (same structure as 1.3.13). Programming errors: `None` input raises `TypeError`; a blank or malformed template_uri, or an empty dict schema, raises `ValueError`.

**Response sample** (same template as 1.3.13, with slot values from the structured input; `Complaint details` is a truncated sample value)

```text
template_uri : Task-T/network-layer/private-line-complaint/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1
prompt_text  :
## Task Type
Transport private line service complaint diagnosis

## Task Description
Based on <Task Object> and <Task Context>, perform network-side fault root cause diagnosis in the complaint scenario, achieve the complaint diagnosis goal defined in <Task Target>, and return the task processing result in the structure defined in <Expected Output>.

## Task Target
Diagnose network-side faults and return diagnostic result information such as fault root causes and repair suggestions.

## Task Object
Access Port Name: P781-Futian Center-PTN7900-2-TPA1EG24-03

## Task Context
1. Complaint category: poor private line quality
2. Problem occurrence time: 2026-05-11T08:21:46Z
3. OSS-side event sequence number: event-id-20260511-09013
4. Complaint details: Starting from 8:30 a.m. on May 11, the response latency of accessing Guangzhou from Shenzhen suddenly rose from an average of 12ms to 320ms

## Expected Output
Requirement: The complaint diagnosis task result should include the following information:
1. Diagnosis result. Allowed values: success, failure (required)
2. Diagnosis result details (required)
3. Repair suggestions (optional)
4. Fault root cause list, where each fault root cause includes fault root cause name, detailed description, repair suggestions, fault root cause point location, etc. (optional)

## Terminology Explanation
1. Private line interruption
   - Synonyms: private line interruption, service interruption, network unreachable, service down, service inaccessible
2. Poor private line quality
   - Synonyms: private line poor quality, service stutter, service access timeout, service packet loss, service high latency, service jitter, service congestion, service experience degradation, service high error rate, service optical power abnormal
```

### 1.3.15 validate_task_prompt_and_data_filling

**API definition**

```python
def validate_task_prompt_and_data_filling(
    self,
    prompt: str,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**Typical scenario**: the entry point for parameter validation and extraction after the server Agent receives a Task-T message and before entering business execution; also the decision point of "missing-slot detection" in the negotiation flow — when required parameters are missing, the caller decides whether to initiate negotiation for parameter filling.

**Function description**: validates whether a Task-T task prompt message matches the template and slot constraints, and extracts parameters per the caller-provided JSON Schema. Pipeline order: input gate → rule gate → template loading → one retryable LLM semantic validation (which also extracts the parameters) → deterministic parameter merging.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| prompt | str | Yes (non-empty) | The task prompt message text to validate (`MetadataContent.prompt_text`); the input length is limited by `A2AT_INPUT_TEXT_MAX_CHARS`, default 16384 |
| schema | Mapping[str, object] | Yes | Caller-provided parameter JSON Schema declaring the parameters to extract/validate and the required constraints |
| template_uri | str | Yes | Task-T template (the prefix segment must be `Task-T`) |

**Request sample**

```python
# Server-side parameter schema (the keys are the server's business field names, which may differ
# from the client field names; the SDK performs the cross-field adaptation)
validation_schema = {
    "type": "object",
    "properties": {
        "accessPort": {"type": "string", "description": "Access port name, uniquely identifying the complained private line object"},
        "bizScenario": {
            "type": "string",
            "description": "Complaint category scenario, required; only private line interruption and poor private line quality are allowed",
            "enum": ["private line interruption", "poor private line quality"],
        },
        "faultTime": {"type": "string", "description": "Problem occurrence time"},
        "eventSerialNo": {"type": "string", "description": "Complaint work order or event sequence number accepted on the OSS side"},
        "faultDetail": {"type": "string", "description": "Complaint/fault symptom detail description"},
    },
    "required": ["accessPort", "bizScenario"],
}

extracted = server.validate_task_prompt_and_data_filling(
    metadata.prompt_text, validation_schema, PRIVATE_LINE_COMPLAINT_URI,
).data
```

**Output description**

On success, returns `FilledParamData`:

| Field/method | Type | Description |
| --------- | ---- | ---- |
| data | dict[str, object] | Parameters extracted per the Schema; keys are the parameter names declared in the Schema |

On failure, raises `ContentValidationError` (an `A2ATError` subclass):

| Member | Type | Description |
| ---- | ---- | ---- |
| code_str | str | Machine-readable error code; values below |
| message | str | Human-readable failure description |
| errors | list[SlotValidationError] | Per-slot error details (slot-level error codes such as `content.param_missing`, `content.entry_field_missing`, `content.format_error`); structure in the common conventions |
| params | dict[str, object] | The partially extracted parameters before rejection (slots that could not be extracted have None or missing values) |

Error codes:

- `negotiation.invalid_input` (the prompt is `None` or blank, the schema is `None`, or the templateUri prefix segment/version does not match this API)

- `negotiation.semantic_rejected` (semantic validation rejected, including missing required parameters or illegal values; the per-slot details in `errors` use the `content.*` code set, such as `content.param_missing`, `content.entry_field_missing`, `content.format_error`, `content.value_not_allowed`)

- `llm.invocation_failed` / `llm.response_invalid` (LLM failure, retryable)

- `template.not_found` (the validation prompt resource is missing)

- `input.text_too_long` (the prompt exceeds `A2AT_INPUT_TEXT_MAX_CHARS`)

Programming errors: a `None` template_uri raises `TypeError`, a blank/malformed one raises `ValueError`; a `None` or blank prompt or a `None` schema is not a programming error and raises `ContentValidationError` with `negotiation.invalid_input` (see the error codes above).

**Response sample** (`faultTime` and `faultDetail` are truncated sample values)

```text
extracted =
{
  'accessPort': 'P781-Zhujiang New Town-PTN7900-23-TPA1EG24-17',
  'bizScenario': 'poor private line quality',
  'faultTime': '2026-05-11',
  'eventSerialNo': 'event-id-20260511-09013',
  'faultDetail': '320ms'
}
```

On validation rejection (a negative case with a key slot missing):

```text
ContentValidationError: [negotiation.semantic_rejected] ...
    slot=Task Object code=content.param_missing message=... facts={'section_label': 'Task Object'}
```

### 1.3.16 generate_notification_prompt_from_text

**API definition**

```python
def generate_notification_prompt_from_text(
    self, text: str, template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: the client Agent converts a natural-language subscription requirement (such as a service recovery event subscription) into a Notification-T subscription message of the specified scenario.

**Function description**: generates a notification subscription prompt message from natural-language text with the specified Notification-T template. It runs one LLM slot-extraction step and then renders the template deterministically; the generation phase performs built-in slot Schema validation.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| text | str | Yes | Natural-language subscription description (notification topic, subscription condition, notification data format, etc.); the input length is limited by `A2AT_INPUT_TEXT_MAX_CHARS`, default 16384 |
| template_uri | str | Yes | Notification-T template, such as `SUBSCRIBE_INCIDENT_URI`, `SERVICE_RECOVERY_URI` |

**Request sample**

```python
from a2a_t.core.standard_templates import SERVICE_RECOVERY_URI

result = client.generate_notification_prompt_from_text(
    "I want to subscribe to the service recovery event. The notification data format is: "
    "1. Service recovery plan execution status. Allowed values: not started, ended; "
    "2. Complaint diagnosis task sequence number; 3. OSS-side event sequence number; "
    "4. Access port name; 5. Whether OMC automatic recovery is authorized. Allowed values: "
    "yes, no; 6. Service recovery plan name; 7. Service recovery plan details",
    SERVICE_RECOVERY_URI,
)
```

**Output description**

On success, returns `MetadataContent` (same structure as [1.3.13](#1313-generate_task_prompt_from_text), with `extension_uri` being `https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1`).

On failure, raises `PromptGenerationError` (same structure as 1.3.13). Programming errors: a `None` text or template_uri raises `TypeError`; a blank or malformed template_uri raises `ValueError`.

**Response sample** (rendered from the template; the actual text varies with the LLM slot-extraction result; the sample input specifies no subscription condition, so that slot stays empty)

```text
template_uri : Notification-T/network-layer/service-recovery/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1
prompt_text  :
## Subscription Description
Please complete the network-side service recovery event subscription and reporting task based on the following <Notification Topic>, <Subscribe Condition>, <Notification Data Format>, and <Expected Output> information.

## Notification Topic
Service recovery event

## Subscribe Condition

## Notification Data Format
1. Service recovery plan execution status. Allowed values: not started, ended
2. Complaint diagnosis task sequence number
3. OSS-side event sequence number
4. Access port name
5. Whether OMC automatic recovery is authorized. Allowed values: yes, no
6. Service recovery plan name
7. Service recovery plan details

## Expected Output
1. Subscription result. Allowed values: success
2. After successful subscription, report messages according to <Notification Data Format>
```

### 1.3.17 generate_notification_prompt_from_data_with_schema

**API definition**

```python
def generate_notification_prompt_from_data_with_schema(
    self,
    data: Mapping[str, object],
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: the client Agent converts the structured subscription parameters submitted by an upstream system/UI into a Notification-T subscription message; suitable when the subscription parameters are already held in structured form.

**Function description**: generates a notification subscription prompt from structured data + a semantic Schema with the specified Notification-T template.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| data | Mapping[str, object] | Yes | Structured subscription input (such as the subscription condition and the notification format field list) |
| schema | Mapping[str, object] | Yes (non-empty) | Field-semantics JSON Schema |
| template_uri | str | Yes | Notification-T template |

**Request sample**

```python
data = {
    "condition": "Subnetwork name: xx subnetwork",
    "reportFormat": [
        {"name": "Service recovery plan execution status", "values": ["not started", "ended"], "required": True},
        {"name": "Complaint diagnosis task sequence number", "required": True},
        {"name": "OSS-side event sequence number", "required": True},
        {"name": "Access port name", "required": True},
        {"name": "Whether OMC automatic recovery is authorized", "values": ["yes", "no"], "required": True},
        {"name": "Service recovery plan name", "required": True},
        {"name": "Service recovery plan details", "required": True},
        {"name": "Service recovery plan execution end time", "required": False},
    ],
}

data_schema = {
    "type": "object",
    "properties": {
        "condition": {"type": "string", "description": "Subscription condition, optional. Description of the condition to subscribe to."},
        "reportFormat": {
            "type": "array",
            "description": "Notification data format, required. The field list describing the content to report.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "values": {"type": "array"},
                    "required": {"type": "boolean"},
                },
                "required": ["name"],
            },
        },
    },
    "required": ["reportFormat"],
}

result = client.generate_notification_prompt_from_data_with_schema(
    data, data_schema, SERVICE_RECOVERY_URI,
)
```

**Output description**

On success, returns `MetadataContent` (same structure as [1.3.13](#1313-generate_task_prompt_from_text), with `extension_uri` being `https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1`).

On failure, raises `PromptGenerationError` (same structure as 1.3.13). Programming errors: `None` input raises `TypeError`; a blank or malformed template_uri, or an empty dict schema, raises `ValueError`.

**Response sample** (same template as 1.3.16, with slot values from the structured input: `Subscribe Condition` is filled with `condition`, `Notification Data Format` is rendered from the `reportFormat` list)

```text
template_uri : Notification-T/network-layer/service-recovery/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1
prompt_text  :
## Subscription Description
Please complete the network-side service recovery event subscription and reporting task based on the following <Notification Topic>, <Subscribe Condition>, <Notification Data Format>, and <Expected Output> information.

## Notification Topic
Service recovery event

## Subscribe Condition
Subnetwork name: xx subnetwork

## Notification Data Format
1. Service recovery plan execution status. Allowed values: not started, ended (required)
2. Complaint diagnosis task sequence number (required)
3. OSS-side event sequence number (required)
4. Access port name (required)
5. Whether OMC automatic recovery is authorized. Allowed values: yes, no (required)
6. Service recovery plan name (required)
7. Service recovery plan details (required)
8. Service recovery plan execution end time (optional)

## Expected Output
1. Subscription result. Allowed values: success
2. After successful subscription, report messages according to <Notification Data Format>
```

### 1.3.18 validate_notification_prompt_and_data_filling

**API definition**

```python
def validate_notification_prompt_and_data_filling(
    self,
    prompt: str,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**Typical scenario**: after receiving a Notification-T subscription message, the server Agent validates its compliance and extracts the subscription parameters (topic/condition/reporting format), then establishes the subscription relationship accordingly.

**Function description**: validates whether a Notification-T notification subscription prompt message matches the template and slot constraints, and extracts parameters (subscription topic, subscription condition, notification data format, etc.) per the caller's Schema.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| prompt | str | Yes (non-empty) | The notification subscription prompt message text to validate; the input length is limited by `A2AT_INPUT_TEXT_MAX_CHARS`, default 16384 |
| schema | Mapping[str, object] | Yes | Caller-provided parameter JSON Schema |
| template_uri | str | Yes | Notification-T template (the prefix segment must be `Notification-T`) |

**Request sample**

```python
validation_schema = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "description": "Subscription topic (required). Name of the event topic to subscribe to."},
        "subscriptionCondition": {
            "type": "string", "description": "Subscription condition (optional). Description of the condition to subscribe to.",
        },
        "notificationDataFormat": {
            "type": "string", "description": "Notification data format (required). Description of the notification data format to report.",
        },
    },
    "required": ["topic", "notificationDataFormat"],
}

# prompt_text is the notification subscription prompt message text generated by the client
result = server.validate_notification_prompt_and_data_filling(
    prompt_text, validation_schema, SERVICE_RECOVERY_URI,
)
```

**Output description**

On success, returns `FilledParamData` (same structure as [1.3.15](#1315-validate_task_prompt_and_data_filling)); `data` holds the parameters extracted per the Schema (subscription topic, subscription condition, notification data format, etc.).

On failure, raises `ContentValidationError` (same structure as 1.3.15). Error codes:

- `negotiation.invalid_input` (the prompt is `None` or blank, the schema is `None`, or the templateUri prefix segment/version does not match this API)

- `negotiation.semantic_rejected` (required parameters are missing or have illegal values; the per-slot details in `errors` use the `content.*` code set)

- `llm.invocation_failed` / `llm.response_invalid` (LLM failure, retryable)

- `template.not_found` (the validation prompt resource is missing)

- `input.text_too_long` (the prompt exceeds `A2AT_INPUT_TEXT_MAX_CHARS`)

Programming errors: a `None` template_uri raises `TypeError`, a blank/malformed one raises `ValueError`; a `None` or blank prompt or a `None` schema is not a programming error and raises `ContentValidationError` with `negotiation.invalid_input` (see the error codes above).

**Response sample**

```text
result.data =
{
  'topic': 'Service recovery event',
  'subscriptionCondition': 'Subnetwork name: xx subnetwork',
  'notificationDataFormat': 'Service recovery event data includes: service recovery plan execution status (not started, ended), complaint diagnosis task sequence number, OSS-side event sequence number, access port name, whether OMC automatic recovery is authorized (yes, no), service recovery plan name, service recovery plan details.'
}
```

### 1.3.19 generate_auth_prompt_from_text

**API definition**

```python
def generate_auth_prompt_from_text(
    self, text: str, template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: the client Agent converts a natural-language authorization request (adding/modifying/deleting/querying a network operation authorization policy) into an Authorization-T message.

**Function description**: generates an authorization policy operation prompt message from natural-language text with the specified Authorization-T template. It runs one LLM slot-extraction step and then renders the template deterministically.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| text | str | Yes | Natural-language authorization description (operation type + the authorization policy content of the network operation); the input length is limited by `A2AT_INPUT_TEXT_MAX_CHARS`, default 16384 |
| template_uri | str | Yes | Authorization-T template: `AUTHORIZATION_POLICY_MANAGEMENT_URI` (`Authorization-T/authorization-policy-management/v1`) |

**Request sample**

```python
from a2a_t.core.standard_templates import AUTHORIZATION_POLICY_MANAGEMENT_URI

result = client.generate_auth_prompt_from_text(
    "Add authorization for the campus private network: use service recovery as the handling "
    "type, perform tunnel optimization, and leave the validity period to be filled in later",
    AUTHORIZATION_POLICY_MANAGEMENT_URI,
)
```

**Output description**

On success, returns `MetadataContent` (same structure as [1.3.13](#1313-generate_task_prompt_from_text), with `extension_uri` being `https://projects.tmforum.org/a2aproject/telecommunication/extensions/Authorization-T/v1`).

On failure, raises `PromptGenerationError` (same structure as 1.3.13); for example, an operation type outside "add/modify/delete/query authorization policy" is rejected with `slot.constraint_violated`. Programming errors: a `None` text or template_uri raises `TypeError`; a blank or malformed template_uri raises `ValueError`.

**Response sample**

```text
template_uri : Authorization-T/authorization-policy-management/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Authorization-T/v1
prompt_text  :
## Authorization Policy Operation Type
Add authorization policy

## Authorization Policy Operation Description
Please complete the corresponding authorization operation based on <Authorization Policy Operation Type> and <Network Operation Authorization Policy List>, and return the authorization policy operation execution result in the structure defined in <Expected Output>. <Expected Output> indicates the expected return content.

## Network Operation Authorization Policy List
Campus private network, service recovery, tunnel optimization

## Expected Output
1. Authorization operation execution result. Allowed values: success, failure, partial success
2. When the authorization operation is executed successfully, return the <Network Operation Authorization Policy List> that was executed successfully
3. When the authorization operation fails or is partially successful, return a failure list containing the authorization policies and the failure reasons
```

### 1.3.20 generate_auth_prompt_from_data_with_schema

**API definition**

```python
def generate_auth_prompt_from_data_with_schema(
    self,
    data: Mapping[str, object],
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**Typical scenario**: the client Agent converts the structured authorization policy data submitted field by field from an authorization management UI/system into an Authorization-T message.

**Function description**: generates an authorization policy operation prompt from structured data + a semantic Schema with the specified Authorization-T template, skipping scenario recognition. The semantic constraints are the same as 1.3.14.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| data | Mapping[str, object] | Yes | Structured authorization input (operation type, policy count, policy detail list, etc.) |
| schema | Mapping[str, object] | Yes (non-empty) | Field-semantics JSON Schema |
| template_uri | str | Yes | Authorization-T template |

**Request sample**

```python
data = {
    "operationType": "Add authorization policy",
    "policyCount": 2,
    "details": [
        {"businessScenario": "Campus private network", "handlingType": "Service recovery",
         "operationName": "Tunnel optimization", "validityPeriod": "Permanently valid"},
        {"businessScenario": "Medical private line", "handlingType": "Service restoration",
         "operationName": "Frequency band adjustment", "validityPeriod": "2026-06-01~2030-06-18"},
    ],
}

schema = {
    "type": "object",
    "properties": {
        "operationType": {
            "type": "string",
            "enum": ["Add authorization policy", "Modify authorization policy",
                     "Delete authorization policy", "Query authorization policy"],
        },
        "policyCount": {"type": "integer", "description": "Number of policies to add"},
        "details": {
            "type": "array",
            "description": "Policy detail list",
            "items": {
                "type": "object",
                "properties": {
                    "businessScenario": {"type": "string"},
                    "handlingType": {"type": "string"},
                    "operationName": {"type": "string"},
                    "validityPeriod": {"type": "string"},
                },
            },
        },
    },
}

result = client.generate_auth_prompt_from_data_with_schema(
    data, schema, AUTHORIZATION_POLICY_MANAGEMENT_URI,
)
```

**Output description**

On success, returns `MetadataContent` (same structure as [1.3.13](#1313-generate_task_prompt_from_text), with `extension_uri` being `https://projects.tmforum.org/a2aproject/telecommunication/extensions/Authorization-T/v1`).

On failure, raises `PromptGenerationError` (same structure as 1.3.13). Programming errors: `None` input raises `TypeError`; a blank or malformed template_uri, or an empty dict schema, raises `ValueError`.

**Response sample**

```text
template_uri : Authorization-T/authorization-policy-management/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Authorization-T/v1
prompt_text  :
## Authorization Policy Operation Type
Add authorization policy

## Authorization Policy Operation Description
Please complete the corresponding authorization operation based on <Authorization Policy Operation Type> and <Network Operation Authorization Policy List>, and return the authorization policy operation execution result in the structure defined in <Expected Output>. <Expected Output> indicates the expected return content.

## Network Operation Authorization Policy List
Campus private network, service recovery, tunnel optimization, permanently valid; medical private line, service restoration, frequency band adjustment, 2026-06-01~2030-06-18

## Expected Output
1. Authorization operation execution result. Allowed values: success, failure, partial success
2. When the authorization operation is executed successfully, return the <Network Operation Authorization Policy List> that was executed successfully
3. When the authorization operation fails or is partially successful, return a failure list containing the authorization policies and the failure reasons
```

### 1.3.21 validate_auth_prompt_and_data_filling

**API definition**

```python
def validate_auth_prompt_and_data_filling(
    self,
    prompt: str,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**Typical scenario**: after receiving an Authorization-T message, the server Agent validates its compliance and extracts the operation type and policy list, then executes the authorization policy management action accordingly.

**Function description**: validates whether an Authorization-T authorization prompt message matches the template and slot constraints, and extracts parameters (operation type, policy list, etc.) per the caller's Schema. Differentiated validation is performed per the field requirements of each operation type: added entries must include business scenario/handling type/operation name/validity period; modified entries must include the policy identifier and the new validity period; deleted entries may be the policy identifier or condition fields.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| prompt | str | Yes (non-empty) | The authorization prompt message text to validate; the input length is limited by `A2AT_INPUT_TEXT_MAX_CHARS`, default 16384 |
| schema | Mapping[str, object] | Yes | Caller-provided parameter JSON Schema (including `operationType`, `policies`, etc.) |
| template_uri | str | Yes | Authorization-T template (the prefix segment must be `Authorization-T`) |

**Request sample**

```python
import json

# paramSchema declares: operation type (enum: add/modify/delete/query authorization policy),
# the policy list (array; entries include policy identifier / business scenario / handling
# type / operation name / validity period)
with open("param-schema.json", encoding="utf-8") as f:
    param_schema = json.load(f)

result = server.validate_auth_prompt_and_data_filling(
    metadata.prompt_text, param_schema, AUTHORIZATION_POLICY_MANAGEMENT_URI,
)
```

**Output description**

On success, returns `FilledParamData` (same structure as [1.3.15](#1315-validate_task_prompt_and_data_filling)); `data` holds the parameters extracted per the Schema (operation type, policy list, etc.).

On failure, raises `ContentValidationError` (same structure as 1.3.15). Error codes:

- `negotiation.invalid_input` (the prompt is `None` or blank, the schema is `None`, or the templateUri prefix segment/version does not match this API)

- `negotiation.semantic_rejected` (required parameters are missing or have illegal values, such as an added entry missing required fields (`content.entry_field_missing`) or a malformed validity period (`content.format_error`))

- `llm.invocation_failed` / `llm.response_invalid` (LLM failure, retryable)

- `template.not_found` (the validation prompt resource is missing)

- `input.text_too_long` (the prompt exceeds `A2AT_INPUT_TEXT_MAX_CHARS`)

Programming errors: a `None` template_uri raises `TypeError`, a blank/malformed one raises `ValueError`; a `None` or blank prompt or a `None` schema is not a programming error and raises `ContentValidationError` with `negotiation.invalid_input` (see the error codes above).

**Response sample**

```text
result.data =
{
  'operationType': 'Add authorization policy',
  'policies': [
    {'policyId': None, 'businessScenario': 'Campus private network', 'handlingType': 'Service recovery', 'operationName': 'Tunnel optimization', 'validityPeriod': 'Permanently valid'},
    {'policyId': None, 'businessScenario': 'Medical private line', 'handlingType': 'Service restoration', 'operationName': 'Frequency band adjustment', 'validityPeriod': '2026-06-01~2030-06-18'}
  ]
}
```

### 1.3.22 generate_task_prompt

**API definition**

```python
def generate_task_prompt(self, user_input: str | dict[str, object]) -> PromptGenerationResult
```

**Typical scenario**: the scenario auto-routing entry point of the client Agent: without specifying a template, the SDK recognizes the business scenario of the user input and generates the corresponding message; suitable when the scenario set is known and a simplified integration is preferred.

**Function description**: the general task prompt generation entry point that locates the template automatically through scenario recognition: the natural-language or structured input first goes through LLM-based business scenario recognition, and then slot extraction and rendering are completed per the built-in template of the scenario.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| user_input | str \| dict[str, object] | Yes | Task description: `str` (natural language) or `dict` (structured input), uniformly extracted through the LLM; when the input is a `str`, its length is limited by `A2AT_INPUT_TEXT_MAX_CHARS`, default 16384 |

**Request sample**

```python
result = client.generate_task_prompt(
    "Generate an Incident event subscription task: the notification topic is Incident, "
    "the subscription levels are critical, medium, high, and low, and the notification "
    "data format is DataPart"
)

if result.success:
    processed_prompt = result.prompt_text  # sent as A2A message metadata
else:
    print(result.failure.code, ":", result.failure.message)
```

**Output description**

On success (`success` is `True`; no exception raised; the result is returned with the dataclass):

| Field/method | Type | Description |
| --------- | ---- | ---- |
| success | bool | Always True |
| prompt_text | str | Rendered task prompt message text, sent as A2A message metadata |
| failure | PromptGenerationFailure | Always None |

On failure (`success` is `False`; no exception raised; the failure payload is returned with the result):

| Field/method | Type | Description |
| --------- | ---- | ---- |
| success | bool | Always False |
| prompt_text | str | Always None |
| failure | PromptGenerationFailure | Standardized failure payload; structure in the table below |

`PromptGenerationFailure` structure:

| Field | Type | Description |
| ---- | ---- | ---- |
| code | str | Machine-readable error code from the error code list, such as `scenario.not_matched` (scenario recognition missed), `input.text_too_long` (input length protection), `template.load_failed` (prompt resource loading failed), `template.not_found` (the template is missing), `slot.schema_not_found` (the slot Schema is missing), `slot.not_provided` (a required slot is missing), `template.render_failed` (rendering failed), `llm.invocation_failed` / `llm.response_invalid` (LLM failure) |
| message | str | Human-readable failure description |
| stage | str \| None | Stage where the failure occurred: `input` (input length protection), `scenario` (scenario recognition), `preparation` (template/slot/prompt resource loading), `generation` (generation processes such as LLM slot extraction), `render` (template rendering) |

Both `PromptGenerationResult` and `PromptGenerationFailure` provide `to_dict()` for direct JSON serialization.

**Response sample**

On success (the actual text varies with the LLM slot-extraction result):

```text
result.success = True
result.prompt_text =
## Subscription Description
Based on the following <Notification Topic>, <Subscribe Condition>, <Notification Data Format>, and <Expected Output> information, complete the network-side intelligent fault Incident subscription and reporting task.

## Notification Topic
The name of this topic is "Incident"

## Subscribe Condition
The fault levels are "critical", "medium", "high", "low"

## Notification Data Format
Report Incident data via DataPart

## Expected Output
1. Subscription result, success or failure
2. Reason for subscription failure (optional)
```

On failure (the input matches no built-in scenario):

```text
result.success = False
result.failure =
PromptGenerationFailure(code='scenario.not_matched', message='The input does not match any known scenario: <reason>', stage='scenario')
```

### 1.3.23 check_task_prompt

**API definition**

```python
def check_task_prompt(self, *, processed_prompt_text: str) -> PromptComplianceResult
```

**Typical scenario**: the server Agent runs protocol completeness validation on the received task message (scenario/template/slot compliance); suitable when only a pass/fail conclusion is needed without parameter extraction.

**Function description**: the general task prompt compliance validation entry point (server): runs scenario matching, template compliance validation, and slot validation on the processed task prompt submitted by the client. Difference from `validate_task_prompt_and_data_filling`: this API extracts no parameters and takes no caller Schema; it only returns the standardized pass/fail conclusion.

**Input description**

| Parameter | Type | Required | Description |
| ---- | ---- | ---- | ---- |
| processed_prompt_text | str | Yes | The A2A-T protocol message text submitted by the client (the value keyed by the extension URI in `Message.metadata`); the input length is limited by `A2AT_INPUT_TEXT_MAX_CHARS`, default 16384 |

**Request sample**

```python
result = server.check_task_prompt(processed_prompt_text=processed_prompt)

if result.success:
    print("prompt check passed")
else:
    print(result.failure.message)
```

**Output description**

On success (`success` is `True`; no exception raised; the result is returned with the dataclass):

| Field/method | Type | Description |
| --------- | ---- | ---- |
| success | bool | Always True |
| failure | PromptComplianceFailure | Always None |

On failure (`success` is `False`; no exception raised; the failure payload is returned with the result):

| Field/method | Type | Description |
| --------- | ---- | ---- |
| success | bool | Always False |
| failure | PromptComplianceFailure | Standardized failure payload; structure in the table below |

`PromptComplianceFailure` structure:

| Field | Type | Description |
| ---- | ---- | ---- |
| code | str | Machine-readable error code from the error code list: `scenario.not_matched` (message parsing/scenario recognition failed), `template.not_found` (the template is missing), slot-domain codes such as `slot.not_provided` (a required slot is missing), `slot.constraint_violated` (a value is out of range), `slot.rule_violation` (other slot rule violations), `input.text_too_long` (input length protection) |
| message | str | Human-readable failure description |
| stage | str | Stage where the failure occurred: `input_gate` (input length protection), `prompt_parse` (scenario resolution failure), `preparation` (template/slot/prompt resource loading), `slot_extraction` (LLM slot extraction), `slot_validation` (rule/semantic slot validation) |

Both `PromptComplianceResult` and `PromptComplianceFailure` provide `to_dict()` for direct JSON serialization.

**Response sample**

On success:

```text
result.success = True
```

On failure:

```text
result.success = False
result.failure =
PromptComplianceFailure(code='slot.not_provided', message='\'Task Object\' is not provided in the input.', stage='slot_validation')
```

## 1.4 Error Code List

**Error code categories**: BUSINESS = expected business failures the caller can act on, carried by `A2ATBusinessError` subclasses; INFRA = infrastructure failures, carried by plain `A2ATError`.

| Error code                                 | Category | Message (zh-CN)                                               | Message (en-US)                                                |
| ----------------------------------------- | -------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `template.not_found`                      | BUSINESS | 模板「{template_uri}」不存在 | Template '{template_uri}' does not exist |
| `template.render_failed`                  | BUSINESS | 模板「{template_uri}」渲染失败:{reason}                      | Failed to render template '{template_uri}': {reason}         |
| `template.load_failed`                    | INFRA    | 模板资源「{resource_path}」读取失败                          | Failed to read template resource '{resource_path}'           |
| `slot.schema_not_found`                   | BUSINESS | 模板「{template_uri}」缺少参数定义文件(语言「{language}」)   | Template '{template_uri}' is missing its slot schema (language '{language}') |
| `slot.not_provided`                       | BUSINESS | 输入中未提供「{slot_label}」。                               | '{slot_label}' is not provided in the input.                 |
| `slot.constraint_violated`                | BUSINESS | 「{slot_label}」的取值「{actual}」不在允许范围内             | The value of '{slot_label}' ({actual}) is not within the allowed range |
| `slot.semantic_conflict`                  | BUSINESS | 「{slot_label}」的取值与参数定义冲突:{reason}                | The value of '{slot_label}' conflicts with the slot definition: {reason} |
| `slot.fabricated_value`                   | BUSINESS | 「{slot_label}」的取值「{actual}」是占位内容,不是有效值      | The value of '{slot_label}' ({actual}) is placeholder content, not a valid value |
| `slot.cross_scenario_pollution`           | BUSINESS | 「{slot_label}」的取值混入了其他场景的内容                   | The value of '{slot_label}' contains content from a different scenario |
| `slot.insufficient_grounding`             | BUSINESS | 「{slot_label}」的取值缺少充分依据                           | The value of '{slot_label}' lacks sufficient grounding       |
| `slot.rule_violation`                     | BUSINESS | 「{slot_label}」的取值不符合校验规则。                       | The value of '{slot_label}' violates the validation rules.   |
| `input.text_too_long`                     | BUSINESS | 输入文本长度 {actual_length} 超过上限 {max_chars}(A2AT_INPUT_TEXT_MAX_CHARS) | Input text length {actual_length} exceeds the maximum of {max_chars} (A2AT_INPUT_TEXT_MAX_CHARS) |
| `content.param_missing`                   | BUSINESS | 「{section_label}」未填写,请补充该参数的取值                 | '{section_label}' is empty; please provide a value           |
| `content.entry_field_missing`             | BUSINESS | 「{section_label}」第 {index} 条缺少必填字段「{field_label}」 | Entry {index} of '{section_label}' is missing required field '{field_label}' |
| `content.format_error`                    | BUSINESS | 「{section_label}」的取值格式不符合要求:{reason}             | The format of '{section_label}' is invalid: {reason}         |
| `content.value_not_allowed`               | BUSINESS | 「{section_label}」的取值「{actual}」不在允许范围内          | The value of '{section_label}' ({actual}) is not allowed     |
| `content.semantic_conflict`               | BUSINESS | 「{section_label}」存在语义冲突:{reason}                     | '{section_label}' has a semantic conflict: {reason}          |
| `content.rule_violation`                  | BUSINESS | 「{section_label}」的取值不符合校验规则。                    | The value of '{section_label}' violates the validation rules. |
| `scenario.not_matched`                    | BUSINESS | 输入内容无法匹配任何已知场景:{reason}                        | The input does not match any known scenario: {reason}        |
| `llm.not_configured`                      | BUSINESS | 未配置 LLM 客户端,无法执行该操作(请检查 A2AT_LLM_* 配置)     | No LLM client is configured; check the A2AT_LLM_* settings   |
| `llm.invocation_failed`                   | BUSINESS | LLM 调用失败(提供方 {provider}):{reason}                     | LLM invocation failed (provider {provider}): {reason}        |
| `llm.response_invalid`                    | BUSINESS | LLM 返回内容不符合要求({step} 步骤),请重试                   | The LLM response is invalid (step: {step}); please retry     |
| `negotiation.invalid_input`               | BUSINESS | 输入的协商内容无效:{reason}                                  | The negotiation input is invalid: {reason}                   |
| `negotiation.invalid_context_id`          | BUSINESS | 协商上下文标识「{actual}」不是合法的 UUID                    | The negotiation context id '{actual}' is not a valid UUID    |
| `negotiation.round_exceeded`              | BUSINESS | 协商轮次 {round} 已超过上限 {max_rounds}                     | Negotiation round {round} exceeds the maximum of {max_rounds} |
| `negotiation.type_mismatch`               | BUSINESS | 报文内容属于「{implied}」协商,与声明的模板类型「{declared}」不符 | The message implies '{implied}' negotiation but the declared template type is '{declared}' |
| `negotiation.phase_mismatch`              | BUSINESS | 报文阶段与声明的模板阶段不符({implied} vs {declared})        | The message phase does not match the declared template phase ({implied} vs {declared}) |
| `negotiation.conclusion_mismatch`         | BUSINESS | 报文结论为「{actual}」,与该方法的预期「{expected}」不符      | The message conclusion is '{actual}' but '{expected}' is expected for this method |
| `negotiation.content_invalid`             | BUSINESS | 协商内容字段「{field}」无效:{reason}                         | The negotiation content field '{field}' is invalid: {reason} |
| `negotiation.field_missing`               | BUSINESS | 协商报文缺少必填字段「{field}」                              | The negotiation message is missing required field '{field}'  |
| `negotiation.content_extract_failed`      | BUSINESS | 无法从文本提取协商内容({field}):{reason}                     | Failed to extract negotiation content from text ({field}): {reason} |
| `negotiation.conclusion_content_mismatch` | BUSINESS | 结论为「{conclusion}」,但「{section_label}」未表达该结论应携带的内容 | The conclusion is '{conclusion}' but '{section_label}' does not state the content the conclusion requires |
| `negotiation.missing_result_content`      | BUSINESS | 「{section_label}」板块缺少结论应携带的内容                  | The '{section_label}' section is missing the content required by its conclusion |
| `negotiation.mutually_exclusive_sections` | BUSINESS | 互斥板块同时出现:{sections}                                  | Mutually exclusive sections appear together: {sections}      |
| `negotiation.constraint_conflict`         | BUSINESS | 「{section_label}」与既有约束冲突:{reason}                   | '{section_label}' conflicts with existing constraints: {reason} |
| `negotiation.field_inconsistency`         | BUSINESS | 「{section_label}」内字段取值前后不一致:{reason}             | Fields within '{section_label}' are inconsistent: {reason}   |
| `negotiation.invalid_time_interval`       | BUSINESS | 「{section_label}」的时间区间不合法(开始时间不得晚于结束时间) | The time interval of '{section_label}' is invalid (start must not be later than end) |
| `negotiation.semantic_rejected`           | BUSINESS | 协商报文语义校验未通过                                       | The negotiation message failed semantic validation           |
| `negotiation.rule_violation`              | BUSINESS | 「{section_label}」不符合协商报文的校验规则。                | '{section_label}' violates the negotiation message validation rules. |
| `infra.config_invalid`                    | INFRA    | 配置项「{key}」无效:{reason}                                  | Invalid configuration '{key}': {reason}                      |
| `infra.resource_read_failed`              | INFRA    | 资源「{resource_path}」读取失败                              | Failed to read resource '{resource_path}'                    |
| `infra.internal_error`                    | INFRA    | SDK 内部错误,请联系维护方并提供上下文                        | SDK internal error; contact the maintainer with context      |