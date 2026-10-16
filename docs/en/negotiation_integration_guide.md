# A2A-T Negotiation Integration Guide

This guide explains how to integrate the A2A-T negotiation capability (**Negotiation-T**) into your
own client and server agents: what the SDK provides, what your business system provides, a
seven-step integration path, and what to replace when moving from the demo to production. The
[ran-energy-saving sample](../../a2a-t-sample/ran-energy-saving/README.md) serves as the worked
example — every code citation below points at files you can read in the repository today.

> Prerequisite: the SDK integration basics (installation, LLM configuration, prompt generation and
> validation) are covered in [developer_guide.md](developer_guide.md); this guide assumes them and
> focuses on the negotiation layer. For custom business templates see §1.5 of that guide, referenced
> in step 1 below.

## 1. Overview and architecture

A negotiation is a two-message exchange the server opens when a Task-T validation fails with a
content error it prefers to resolve by talking instead of rejecting:

```text
client                                        server
  |  Task-T task request (round 1)             |
  |------------------------------------------->|
  |              content validation fails; the error code selects a negotiation type
  |  TASK_STATE_INPUT_REQUIRED                 |
  |  + Negotiation-T propose                   |
  |<-------------------------------------------|   round 1: the server proposes
  |  your NegotiationResponseProvider decides (accept / reject / abort)
  |  Task-T task request + Negotiation-T accept (round 2)
  |------------------------------------------->|
  |              prompt now valid: the negotiated task executes
  |  TASK_STATE_WORKING ... / report artifact  |
  |  TASK_STATE_COMPLETED                      |
  |<-------------------------------------------|
```

The responsibility split:

| Concern | The SDK provides | You provide |
| --- | --- | --- |
| Facades | `A2ATClient` / `A2ATServer` ([src/a2a_t/client/a2at_client.py](../../src/a2a_t/client/a2at_client.py), [src/a2a_t/server/a2at_server.py](../../src/a2a_t/server/a2at_server.py)) | construction with your `.env` (`env_path`) |
| Task-T prompt generation | `generate_task_prompt_from_text` / `generate_task_prompt_from_data_with_schema` | the natural-language input and the template choice |
| Templates and schemas | the template pipeline: built-in resources plus `local_file` loading (developer_guide §1.5) | your business template, slot definitions, and the caller parameter schema |
| Task validation | `validate_task_prompt_and_data_filling` and the structured per-slot error model (`ContentValidationError` carrying `exc.errors` with `slot_name` / `code` / `message`) | what a failing error code means for your business — negotiate or reject (step 5) |
| Negotiation messages | 12 methods per facade — `generate_negotiation_{propose,accept,reject,abort}_prompt_from_data` / `_from_text` plus `validate_{propose,accept,reject,abort}_prompt_and_data_filling` — over the typed content models; 42 fixed error codes; bilingual message templates | when to open a negotiation and which content to put into it |
| Negotiation session | the `negotiationContext` metadata map (`id` / `round` / `max_rounds` / `performative`); the SDK is stateless | nothing — the session state travels with the messages |
| Task execution | the A2A emission semantics only (each progress line becomes a `TASK_STATE_WORKING` update; the result becomes the report artifact; exceptions become `TASK_STATE_FAILED`) | the actual business execution — the `TaskExecutionService` seam (step 3) |
| Negotiation answering | the round-2 wire mechanics: rebuilding the Task-T prompt, building the typed accept, advancing the session, sending over the configured transport | the decision of how to answer — the `NegotiationResponseProvider` seam (step 4) |
| Transport | A2A streaming (SSE) and push notifications (webhook) through the official `a2a-sdk` | the deployment and security of the chosen transport |
| LLM | the OpenAI-compatible call chain (provider, model, retries, timeouts) | the LLM endpoint and API key — or the sample's mock for offline runs |
| Agent discovery | — | a registry center (the sample ships a mock one) |

**The boundary in one sentence:** the SDK owns the wire — message rendering, validation, the
negotiation session map, and the transport mechanics — while your system owns the business: which
template and parameters describe your task, when a validation failure turns into a negotiation, how
the negotiated task executes in your EMS/NMS, and how your side decides to answer a propose. The
ran-energy-saving sample pins this boundary with two named integration seams in its code — the
server-side `TaskExecutionService` and the client-side `NegotiationResponseProvider`, each with an
`INTEGRATION POINT` annotation at its assembly site — so the split is discoverable in the IDE, not
only in documentation.

## 2. The seven-step integration path

### Step 1 — Define your Task-T template and parameter schema

Your negotiation talks about *your* task, so the Task-T template (the rendered prompt), its slot
definitions, and the caller parameter schema are the foundation everything else builds on. The
authoring steps, the `local_file` resource layout, and the `templateUri` mapping rules are covered
in [developer_guide.md §1.5 Loading Custom Templates](developer_guide.md#15-loading-custom-templates)
— follow it rather than reinventing the layout.

Worked example: the sample's template is `Task-T/network-layer/ran-energy-saving/v1`
(`ENERGY_SAVING_TEMPLATE_URI` in
[src/client/scenario_data.py](../../a2a-t-sample/ran-energy-saving/src/client/scenario_data.py)),
and the caller parameter schema shared by both sides is `TASK_PARAM_SCHEMA` in
[src/common/task_schema.py](../../a2a-t-sample/ran-energy-saving/src/common/task_schema.py)
(`operationType`, `region`, `energySavingGoal`, `guaranteedThroughputTarget`). The same schema is
what the negotiation round 2 refills: the client regenerates the Task-T prompt from the filled
parameters with `generate_task_prompt_from_data_with_schema`.

### Step 2 — Configure the LLM

The SDK reaches external LLMs through OpenAI-compatible call chains; the configuration items
(`A2AT_LLM_API_KEY`, `A2AT_LLM_PROVIDER`, `A2AT_LLM_MODEL`, `A2AT_LLM_BASE_URL`,
`A2AT_LLM_TIMEOUT_SECONDS`, `A2AT_LLM_MAX_ATTEMPTS`, ...) are listed in developer_guide §1.7.

How the LLM is actually used along the negotiation path:

- Slot extraction (`generate_task_prompt_from_text`) always runs one LLM call.
- The **fromData** negotiation channels (`generate_negotiation_*_prompt_from_data`) are
  deterministic — they render the typed content against the template and never call an LLM.
- The **fromText** channels (`generate_negotiation_*_prompt_from_text`) add one extraction LLM call
  per generated message.

A fromData-only integration therefore needs the LLM mainly for the initial prompt generation.
Offline/CI runs can skip the LLM entirely: in the sample an empty `A2AT_LLM_API_KEY` selects the
bundled schema-routed mock LLM
([resources/mock_llm/{zh-CN,en-US}/](../../a2a-t-sample/ran-energy-saving/resources/mock_llm)
served from `src/common/`), which replays scripted slot-extraction and content-validation responses.

### Step 3 — Implement the server `TaskExecutionService`

The server-side seam is the `TaskExecutionService` Protocol in
[src/server/task_execution.py](../../a2a-t-sample/ran-energy-saving/src/server/task_execution.py)
of the sample:

```python
@runtime_checkable
class TaskExecutionService(Protocol):
    def execute(
        self, params: dict[str, object], emit_progress: Callable[[str], Awaitable[None]]
    ) -> Awaitable[TaskExecutionResult]: ...

@dataclass(slots=True, frozen=True)
class TaskExecutionResult:
    report_name: str       # becomes the artifact name
    report_text: str       # becomes the artifact's label text part
    report_metadata: str   # the report body carried in artifact.metadata[Task-T/v1]
```

The division of labor is fixed by the flow ([server_flow.py](../../a2a-t-sample/ran-energy-saving/src/server/server_flow.py)):

- `params` carries the filled Task-T parameters extracted by
  `validate_task_prompt_and_data_filling` (the sample schema is object-shaped, so a
  `dict[str, object]`).
- each `emit_progress(text)` call becomes a `TASK_STATE_WORKING` status update on the wire.
- the returned `TaskExecutionResult` becomes the report artifact (one `TaskArtifactUpdateEvent`).
- an exception raised by your implementation is caught by the flow, which emits `TASK_STATE_FAILED`
  with the generic text `Task execution failed: {exc}` and re-raises — you get the streaming and
  terminal-state semantics for free and only produce progress texts and the result.
- `execute` is a synchronous method returning an awaitable; an `async def` implementation conforms
  (`Coroutine` is an `Awaitable`). If your decision needs asynchrony internally (waiting for a
  remote system), own it inside the implementation.

A minimal realistic skeleton — an EMS/NMS northbound call with the progress mapping:

```python
from collections.abc import Awaitable, Callable

from server.task_execution import TaskExecutionResult, TaskExecutionService


class EmsEnergySavingExecutionService:
    """Executes the negotiated energy-saving task through the EMS northbound interface."""

    async def execute(
        self, params: dict[str, object], emit_progress: Callable[[str], Awaitable[None]]
    ) -> TaskExecutionResult:
        region = str(params["region"])
        await emit_progress(f"Connecting to the EMS for region {region}")
        plan = await self._ems.create_energy_saving_plan(**params)  # your EMS/NMS call
        await emit_progress(f"Energy-saving plan {plan.id} activated")
        return TaskExecutionResult(
            report_name="energy saving intent report",
            report_text=plan.summary_text,     # artifact label text part
            report_metadata=plan.report_json,  # report body in artifact.metadata[Task-T/v1]
        )
```

and the assembly — [src/server/server_main.py](../../a2a-t-sample/ran-energy-saving/src/server/server_main.py),
`build_server_runtime`:

```python
# INTEGRATION POINT: replace Uc1DemoExecutionService with your business execution service
# (the server flow depends only on the TaskExecutionService Protocol it implements).
execution_service: TaskExecutionService = EmsEnergySavingExecutionService()
```

The demo implementation in the same file, `Uc1DemoExecutionService`, delegates to the static UC1
data in [src/server/constants_data.py](../../a2a-t-sample/ran-energy-saving/src/server/constants_data.py)
(scripted progress steps + intent report) — that is the piece your implementation replaces.

### Step 4 — Implement the client `NegotiationResponseProvider`

The client-side seam is the `NegotiationResponseProvider` Protocol in
[src/client/negotiation_response.py](../../a2a-t-sample/ran-energy-saving/src/client/negotiation_response.py):

```python
@runtime_checkable
class NegotiationResponseProvider(Protocol):
    def respond(
        self, propose: NegotiationProposeView, session: NegotiationContext
    ) -> NegotiationDecision: ...

@dataclass(frozen=True, slots=True)
class NegotiationProposeView:
    kind: str           # negotiation type the flow resolved (flag, else the propose's templateUri)
    template_uri: str   # the propose's templateUri
    prompt_text: str    # the propose's rendered text, verbatim (never parsed by the flow)

@dataclass(frozen=True, slots=True)
class NegotiationDecision:
    conclusion: Literal["accept", "reject", "abort"] # accept / reject / abort
    filled_params: dict[str, object] | None = None   # information accept: the name->value items
    confirmed_intent: str | None = None              # target accept
    feasibility_summary: str | None = None           # feasibility accept
    reason: str | None = None                        # reject / abort
```

When the server answers round 1 with an `INPUT_REQUIRED` Negotiation-T propose, the client flow
builds the view from the wire metadata, calls `respond`, and acts on the decision
([client_flow.py](../../a2a-t-sample/ran-energy-saving/src/client/client_flow.py)):

- `accept` → the flow keeps the wire mechanics: it regenerates the Task-T prompt from the
  caller-supplied refill parameters, builds the typed accept from your decision field of the
  proposed type (`filled_params` / `confirmed_intent` / `feasibility_summary`), advances the
  session, and sends round 2 over the configured transport.
- `reject` / `abort` → **not wired in this demo**: the flow logs
  `negotiation-decision: {conclusion} (not wired in this demo; see the integration guide)` and ends
  the flow without sending anything. The real extension point is the reject/abort branch of
  `_plan_negotiation_accept` in
  [client_flow.py](../../a2a-t-sample/ran-energy-saving/src/client/client_flow.py) — the decision
  point (`respond`) only returns a `NegotiationDecision` and cannot send wire messages. To emit
  them, extend that branch to mirror `_run_negotiation_accept_round`: advance the session with
  `session.next_round().with_performative(NegotiationPerformative.REJECT)`, build the proposed
  type's ending content with conclusion `Reject` (the decision's `reason` becomes the ending
  content's `failure_reason`), and generate the round-2 reject with the SDK's
  `generate_negotiation_reject_prompt_from_data` / `..._from_text` (per channel) — see §5.2.
- `respond` is synchronous — the flow blocks at the decision point. A customer implementation that
  needs asynchrony (a human confirmation UI, an upstream system call) owns that internally.

A policy-engine-style skeleton, including a reject decision:

```python
from a2a_t.core.metadata import NegotiationContext

from client.negotiation_response import NegotiationDecision, NegotiationProposeView


class PolicyNegotiationResponseProvider:
    """Answers negotiation proposes from the operator's policy engine."""

    def respond(
        self, propose: NegotiationProposeView, session: NegotiationContext
    ) -> NegotiationDecision:
        if propose.kind == "target":
            intent = self._policy.align_target(propose.prompt_text)
            if intent is None:
                return NegotiationDecision(conclusion="reject", reason="target outside the operator policy")
            return NegotiationDecision(conclusion="accept", confirmed_intent=intent)
        if propose.kind == "feasibility":
            return NegotiationDecision(
                conclusion="accept", feasibility_summary=self._assess_feasibility(propose)
            )
        return NegotiationDecision(
            conclusion="accept", filled_params=self._cmdb.lookup_missing_items(propose.kind)
        )
```

and the assembly — [src/client/client_main.py](../../a2a-t-sample/ran-energy-saving/src/client/client_main.py),
`main()`:

```python
# INTEGRATION POINT: replace ScriptedNegotiationResponseProvider with your negotiation
# response provider (human confirmation, policy engine, upstream system...)
negotiation_response_provider = PolicyNegotiationResponseProvider(...)
```

The demo implementation in the same file, `ScriptedNegotiationResponseProvider`, wraps the scripted
answers of [src/client/scenario_data.py](../../a2a-t-sample/ran-energy-saving/src/client/scenario_data.py)
— that is the piece your implementation replaces.

> **What the provider replaces — and what it does not.** The provider replaces only the scripted
> *answer* to a propose. The Task-T prompt refill parameters (`negotiation_params` in the sample)
> remain caller input to the flow in every case: the flow needs them to regenerate the round-2
> Task-T prompt and will not engage a negotiation without them. They are a flow-owned wire
> mechanic, not provider input and not scripted-answer data.

### Step 5 — Map validation error codes to negotiation types

The sample decides *when to negotiate* with a module-level constant in
[server_flow.py](../../a2a-t-sample/ran-energy-saving/src/server/server_flow.py):

```python
_NEGOTIATION_TYPE_BY_ERROR_CODE: dict[str, str] = {
    CONTENT_ERROR_CODE_INFORMATION: NEGOTIATION_TYPE_INFORMATION,   # content.param_missing    -> information
    CONTENT_ERROR_CODE_TARGET: NEGOTIATION_TYPE_TARGET,             # content.value_not_allowed -> target
    CONTENT_ERROR_CODE_FEASIBILITY: NEGOTIATION_TYPE_FEASIBILITY,   # content.semantic_conflict -> feasibility
}
```

`_resolve_negotiation_type` looks the first validation error's code up in this table to select the
propose type; any other code — or a codeless error — selects no negotiation and the task is
rejected as usual (`TASK_STATE_REJECTED`). One built-in rule stays: an information propose opens
only when the errors actually name missing slots.

This mapping is a **sample-level convention, not an SDK contract**. The wire is self-describing —
each propose carries its own `templateUri`
(`Negotiation-T/{information,target,feasibility}-negotiation/propose/v1`), and the client
dispatches its accept on that URI — so your copy of the sample can edit the constant freely: remap
codes, add entries for your own error codes, or leave codes unmapped to keep rejecting them.

### Step 6 — Choose the transport (SSE or webhook)

Both transports deliver the same task events; the difference is how the client receives them:

- **SSE streaming** (default): the client holds the streaming `message:send` open and consumes
  events as they arrive — the path all sample flows use by default.
- **Webhook push notifications**: the client sends non-blocking (`return_immediately`) and receives
  every event as a webhook POST on a receiver embedded in the client process. Round 1 registers the
  push config inline on the request; round 2 re-registers it through the
  `POST /tasks/{id}/pushNotificationConfigs` REST endpoint before the accept is sent. The server
  assembles the push machinery unconditionally, so no server-side mode switch exists.

Selection and details — flags, configuration keys, registration flow, token auth, end signals, idle
backstop — are documented in the sample README:
[How to Run](../../a2a-t-sample/ran-energy-saving/README.md#how-to-run) (`--transport`,
`--webhook-port`) and
[Webhook transport (A2A push notifications)](../../a2a-t-sample/ran-energy-saving/README.md#webhook-transport-a2a-push-notifications).
The negotiation round trip is statement-identical on both transports.

### Step 7 — Validate with the mock, then go live

The sample runs fully offline: mock LLM (empty `A2AT_LLM_API_KEY`), mock registry
([src/registry/](../../a2a-t-sample/ran-energy-saving/src/registry), port 5001), and the two
scripted seams. Use it to validate your template, schema, error-code mapping, and transport choice
end to end before touching any real system — then walk the
[demo → production replacement checklist](#4-demo--production-replacement-checklist) piece by
piece, re-running the demo commands after each replacement.

## 3. The three negotiation types in business terms

Negotiation-T fixes three propose types plus a type-independent abort; the SDK carries their
content in the typed models of
[src/a2a_t/negotiation/content/models.py](../../src/a2a_t/negotiation/content/models.py). The
propose of each type is rendered by the server, and the matching accept by the client, from the
models below.

### information — the server is missing data

**Business pattern:** the task request lacks a parameter the server needs to execute. The server
names the missing items; the client supplies their values and the task proceeds unchanged.

| | |
| --- | --- |
| Propose content | `InformationProposeContent(items, relationship)` — `items` are the missing entries as `NegotiationItem(name, value)` |
| Ending content | `InformationEndingContent(items)` — the supplied items |
| Client decision field | `NegotiationDecision.filled_params` (name → value) |
| Template URIs | `Negotiation-T/information-negotiation/propose/v1`, `.../accept-reject/v1` |

**Business example:** the energy-saving task arrives without the target region — the server
proposes an information negotiation naming the missing `region`; the client accepts with
`{"region": "Songshanhu Administration Committee"}` and the task runs.
Demo input: `run_demo.py --scenario negotiation`.

### target — the two sides align on the target's meaning

**Business pattern:** the requested target is well-formed but beyond or ambiguous for the server's
capability. The server restates its understanding of the counterpart's intent and asks for
confirmation of the clarified target — a confirm-request round that carries only the summary and
the confirm request.

| | |
| --- | --- |
| Propose content | `TargetProposeContent(target_negotiation_description, intent_understanding, alignment_and_clarification, request_for_clarification, target_confirm_request)` |
| Ending content | `TargetEndingContent(confirmed_intent, failure_reason)` — exactly one is meaningful, selected by the conclusion (accept → confirmed intent, reject → failure reason) |
| Client decision field | `NegotiationDecision.confirmed_intent` |
| Template URIs | `Negotiation-T/target-negotiation/propose/v1`, `.../accept-reject/v1` |

**Business example:** the client requests 100 Mbps guaranteed throughput for a cell whose
capability tops out lower — the server restates the intent and asks the client to confirm a
capability-conformant target; the client confirms its intent.
Demo input: `run_demo.py --negotiation target`.

### feasibility — evaluate whether the target is achievable

**Business pattern:** the requested goal may not be achievable in the current network state. The
server requests a feasibility evaluation of the conflicting contents
(`action=REQUEST_FEASIBILITY_EVALUATION`); when it assesses infeasibility it can instead propose an
alternative (`action=PROPOSE_ALTERNATIVE_ON_FAILURE` with `infeasibility_details_and_proposal`); a
confirm-request round asks the counterpart to confirm an assessed-as-feasible outcome.

| | |
| --- | --- |
| Propose content | `FeasibilityProposeContent(feasibility_negotiation_description, action, contents_to_evaluate, infeasibility_details_and_proposal, feasibility_confirm_request)` — `action` is a `NegotiationAction` value ([src/a2a_t/negotiation/content/enums.py](../../src/a2a_t/negotiation/content/enums.py)) |
| Ending content | `FeasibilityEndingContent(feasibility_summary)` — the evaluation result |
| Client decision field | `NegotiationDecision.feasibility_summary` |
| Template URIs | `Negotiation-T/feasibility-negotiation/propose/v1`, `.../accept-reject/v1` |

**Business example:** the 30% energy-saving goal conflicts with the 50 Mbps guaranteed-throughput
target — the server requests a feasibility evaluation of the two conflicting contents; the client
answers with a feasibility summary.
Demo input: `run_demo.py --negotiation feasibility --channel fromText`.

Reject and abort endings exist for every type (the reject shares the type's `accept-reject`
template; abort uses the common `Negotiation-T/common/abort/v1` template with
`NegotiationAbortContent(termination_reason)`) — see §5.2 for their demo status.

## 4. Demo → production replacement checklist

| Demo piece (ran-energy-saving) | What it does in the demo | Keep or replace in production |
| --- | --- | --- |
| mock LLM — [resources/mock_llm/](../../a2a-t-sample/ran-energy-saving/resources/mock_llm) + the schema-routed mock in `src/common/` | scripted slot-extraction and content-validation replies, selected when `A2AT_LLM_API_KEY` is empty | **KEEP** for CI/offline runs; **REMOVE** from production deployments and configure a real OpenAI-compatible endpoint (step 2) |
| scripted execution data — [src/server/constants_data.py](../../a2a-t-sample/ran-energy-saving/src/server/constants_data.py) | the UC1 progress steps and intent report the demo execution service streams | **REPLACE** via the `TaskExecutionService` seam (step 3); once your service is assembled, `Uc1DemoExecutionService` and the demo data can be deleted |
| scripted answers — [src/client/scenario_data.py](../../a2a-t-sample/ran-energy-saving/src/client/scenario_data.py) | feeds BOTH sides: the client answers (filled parameters, confirmed intent, feasibility summary) via the provider seam, and the server's propose content — `server_flow._negotiation_propose_data` builds the target/feasibility propose texts from its accessors | **REPLACE** on both sides: the client answers via the `NegotiationResponseProvider` seam (step 4), and the accessor-backed propose texts in your copy of `server_flow._negotiation_propose_data` with your business phrasing — replacing only provider and execution service still leaves the server proposing the demo's canned texts, so the module is not removable |
| mock registry — [src/registry/](../../a2a-t-sample/ran-energy-saving/src/registry) (port 5001) | AgentCard register/query mock for discovery | **REPLACE** with your registry center, or bypass discovery and construct the `a2a-sdk` client from a known AgentCard |
| static webhook token — `A2AT_SAMPLE_WEBHOOK_TOKEN` (default `demo-webhook-token`) | shared static token carried in the `X-A2A-Notification-Token` header | **REPLACE** with production auth (per-pair secrets, rotation, TLS) — see §5.3 |

## 5. Production notes

### 5.1 Terminal states and error paths

- **Validation failure** → the server emits `TASK_STATE_REJECTED` with
  `Prompt validation failed: {validation_failure}` instead of crashing — this is the path for error
  codes your mapping (step 5) does not turn into a negotiation.
- **Execution failure** → an exception from your `TaskExecutionService` becomes
  `TASK_STATE_FAILED` with the generic text `Task execution failed: {exc}` and the flow re-raises.
- **Normal end** → a negotiation run closes with `TASK_STATE_COMPLETED` after the round-2 accept is
  validated and the task executes; the sample's default (non-negotiation) flow stays long-running
  with no terminal state. Webhook clients end their flow on any SDK terminal state
  (`COMPLETED` / `FAILED` / `CANCELED` / `REJECTED`) — or on the idle backstop when nothing arrives
  (`A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT`, default 30 s).

### 5.2 Negotiation round budget and abort

- The session context `NegotiationContext(id, round, max_rounds, performative)`
  ([src/a2a_t/core/metadata.py](../../src/a2a_t/core/metadata.py)) bounds how many rounds a
  negotiation may run (`DEFAULT_MAX_ROUNDS = 5`). It is immutable — advancing to the next round
  produces a new instance — and travels in the `negotiationContext` metadata map of every message.
- **Abort is type-independent**: `NegotiationAbortContent(termination_reason)` renders against the
  common `Negotiation-T/common/abort/v1` template (typical reasons: round limit reached, timeout,
  budget exhausted).
- **Reject/abort in the demo:** a `reject`/`abort` decision from the provider is *not* wired to the
  wire — the flow logs
  `negotiation-decision: {conclusion} (not wired in this demo; see the integration guide)` and ends
  the flow without sending (same end-of-flow semantics as an unhandled propose). There is no other
  seam: the decision point (`respond`) only returns a `NegotiationDecision` and cannot send wire
  messages. To actually emit them, extend the reject/abort branch of `_plan_negotiation_accept` in
  client_flow.py to mirror `_run_negotiation_accept_round`: advance the session with
  `session.next_round().with_performative(NegotiationPerformative.REJECT)`, build the proposed
  type's ending content with conclusion `Reject` (`TargetEndingContent.failure_reason` carries the
  decision's `reason`; abort uses `NegotiationAbortContent(termination_reason)` instead), and call
  the SDK's `generate_negotiation_reject_prompt_from_data` (the reject shares the type's
  `accept-reject` template, e.g. `Negotiation-T/feasibility-negotiation/accept-reject/v1`) or
  `generate_negotiation_abort_prompt_from_data` (the common `Negotiation-T/common/abort/v1`
  template); both exist on `A2ATClient` and `A2ATServer`, with `_from_text` variants that add one
  extraction LLM call.
- **Diagnosing your own provider:** two further `negotiation-decision` problem logs tell you when
  the flow could not use a decision —
  `unsupported conclusion {c!r}; staying on round 1` for an unknown conclusion, and
  `the accept decision is missing <field> for the <kind> negotiation; staying on round 1` when the
  accept field of the proposed type is `None`. Both end the flow like an unhandled propose.

### 5.3 Webhook security

The push receiver validates the `X-A2A-Notification-Token` header on every POST: a mismatch
answers `401` (the event is dropped) and a malformed or unrecognized body answers `400`. The
demo's static shared token (`A2AT_SAMPLE_WEBHOOK_TOKEN`, default `demo-webhook-token`) is
**demo-grade** — for production use per-client secrets with rotation over TLS, and treat the
receiver as an exposed HTTP endpoint. The idle backstop (`A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT`,
default 30 s, reset per received event) keeps a stalled flow from hanging forever.

### 5.4 Stateless negotiationContext across rounds

The SDK stores no negotiation state (developer_guide §1.2): the session travels in the
`negotiationContext` metadata map (`id` / `round` / `max_rounds` / `performative`) of every
message, and round 2 repeats round 1's `task_id` / `context_id`. Your provider receives the parsed
`NegotiationContext` each round; any cross-round memory you want — counting proposes before
auto-rejecting, correlating with an upstream ticket, remembering what was already offered — is your
implementation's concern, keyed by the session `id`.

## 6. Running example

The [ran-energy-saving sample](../../a2a-t-sample/ran-energy-saving/README.md) is the worked
example for everything above. Its README documents the transport / negotiation-type / generation
channel matrix with all run flags
([How to Run](../../a2a-t-sample/ran-energy-saving/README.md#how-to-run)), the negotiation round
trip on the wire ([Negotiation scenario](../../a2a-t-sample/ran-energy-saving/README.md#negotiation-scenario-negotiation-t)),
and the webhook variant
([Webhook transport](../../a2a-t-sample/ran-energy-saving/README.md#webhook-transport-a2a-push-notifications)).
Quick start:

```bash
cd a2a-t-sample/ran-energy-saving
python run_demo.py --scenario negotiation                         # information negotiation (missing region)
python run_demo.py --negotiation target                           # target negotiation (100 Mbps beyond cell capability)
python run_demo.py --negotiation feasibility --channel fromText   # feasibility negotiation (30% saving vs 50 Mbps)
python run_demo.py --transport webhook                            # webhook transport, default scenario
```
