# RAN Energy Saving Sample

An A2A-T sample with a client agent, a RAN domain server agent, and a mock registry center for
AgentCard discovery.

**Audience.** This sample is written for the team implementing the **client agent**. The **server
agent** is a runnable reference implementation. In production the two agents usually belong to
different parties, so the sections below name the responsible side rather than address an
unspecified reader.

## Contents

- [1. What this sample demonstrates](#1-what-this-sample-demonstrates)
  - [1.1 Project layout](#11-project-layout)
- [2. Quickstart](#2-quickstart)
- [3. Using the A2A-T SDK to implement the Task-T interaction](#3-using-the-a2a-t-sdk-to-implement-the-task-t-interaction)
  - [3.1 The four steps](#31-the-four-steps)
  - [3.2 Client side](#32-client-side)
  - [3.3 Server side](#33-server-side)
- [4. Using the A2A-T SDK to implement the Negotiation-T interaction](#4-using-the-a2a-t-sdk-to-implement-the-negotiation-t-interaction)
  - [4.1 Negotiation scenarios](#41-negotiation-scenarios)
  - [4.2 How the demo selects the negotiation kind](#42-how-the-demo-selects-the-negotiation-kind)
  - [4.3 Mechanics common to all kinds](#43-mechanics-common-to-all-kinds)
  - [4.4 Information negotiation](#44-information-negotiation)
  - [4.5 Target negotiation](#45-target-negotiation)
  - [4.6 Feasibility negotiation](#46-feasibility-negotiation)
  - [4.7 Reject and abort](#47-reject-and-abort)
- [5. Notes for Custom Agent Development](#5-notes-for-custom-agent-development)
- [6. Appendix](#6-appendix)
  - [6.1 Webhook transport](#61-webhook-transport)
  - [6.2 Configuration (a2a-t-sample/.env)](#62-configuration-a2a-t-sampleenv)
  - [6.3 Run the two sides separately](#63-run-the-two-sides-separately)
  - [6.4 Mock LLM mechanism](#64-mock-llm-mechanism)
  - [6.5 Supported flags (`run_demo.py`)](#65-supported-flags-run_demopy)

## 1. What this sample demonstrates

This sample demonstrates A2A-T agent collaboration, where a client agent interacts with a RAN
(Radio Access Network) energy-saving agent to execute an energy-saving task. It uses both Task-T and
Negotiation-T.

Key workflows:

- **Task-T (without negotiation):**

  ```text
  natural language ──> task prompt ──> validation ──> task execution
  ```

- **Task-T + Negotiation-T (with negotiation):**

  ```text
  natural language (incomplete / ambiguous / infeasible)
    ──> task prompt ──> validation ──> negotiation ──> agreement ──> task execution
  ```

### 1.1 Project layout

| Path | Core responsibility |
| --- | --- |
| [`src/client/client_main.py`](src/client/client_main.py) | Assembly root: reads `.env`, builds the runtime (`build_client_runtime`), discovers the server's AgentCard from the registry, starts/stops the webhook receiver, and assembles the demo `ScriptedNegotiationResponseProvider`. The client-side `INTEGRATION POINT` comment sits in `main()`. |
| [`src/client/client_flow.py`](src/client/client_flow.py) | Orchestration: the message sequence (generate → send → consume), the message handling (metadata keys, `A2A-Extensions` header), the event pipeline, and the two transport drivers (SSE streaming / webhook queue). It calls the negotiation decision integration point but owns no decision. |
| [`src/client/negotiation_response.py`](src/client/negotiation_response.py) | The negotiation decision integration point: `NegotiationProposeView`, `NegotiationDecision`, the `NegotiationResponseProvider` Protocol, and the demo `ScriptedNegotiationResponseProvider`. |
| [`src/client/scenario_data.py`](src/client/scenario_data.py) | The sample's inputs and scripted answers — it feeds **both** sides (see [§5](#5-notes-for-custom-agent-development)). |
| [`src/server/server_main.py`](src/server/server_main.py) | The A2A SDK app assembly includes the push-notification module by default. For server-side integration, see the `INTEGRATION POINT` comment in `build_server_runtime`. |
| [`src/server/server_flow.py`](src/server/server_flow.py) | The server flow: validate → negotiate-or-execute → stream progress and the report. |
| [`src/server/task_execution.py`](src/server/task_execution.py) | Integrates `TaskExecutionService` as the execution entry point, along with a demo execution service that simulates task progress and generates intent reports. |
| [`src/common/`](src/common) | Shared adapters, the schema-routed mock LLM, the `WebhookReceiver`, and the shared `TASK_PARAM_SCHEMA`. |
| [`src/registry/`](src/registry) | Mock registry center (AgentCard register/query, port 5001). |
| [`resources/mock_llm/`](resources/mock_llm) | Scripted slot-extraction and content-validation replies. |
| [`run_demo.py`](run_demo.py) | Non-blocking launcher: starts the registry and the server in the background and runs the client in the foreground. |

## 2. Quickstart

Prerequisites: Python 3.12 or newer and [`uv`](https://docs.astral.sh/uv/).

**Step 1 — create the repository `.venv` and install the SDK.** Run this from the repository root.

```bash
uv sync --dev
```

**Step 2 — configure the sample.** `a2a-t-sample/` holds the `.env` the sample reads.

```bash
cd a2a-t-sample
cp env.example .env
uv pip install -r requirements.txt
```

**Step 3 — run the sample.** The launcher starts the registry and the server as background
subprocesses and runs the client in the foreground. Add `--language zh-CN` for the Chinese run.

```bash
cd ran-energy-saving
python run_demo.py
```

Use the interpreter from the repository `.venv` (activate it or call it directly).

> **Windows tip:** call the interpreter explicitly, e.g.
> `..\..\.venv\Scripts\python.exe run_demo.py`.

Both client and server logs stream to the terminal live and interleaved, with background processes
automatically terminating when the client exits.

Add `--scenario negotiation` to run the information negotiation (missing region), or
`--negotiation target` / `--negotiation feasibility` to run the other two kinds. If the client
cannot keep a stream open, add `--transport webhook` — see [§6.1](#61-webhook-transport).

## 3. Using the A2A-T SDK to implement the Task-T interaction

### 3.1 The four steps

1. **Generate** — the client turns its natural-language input into a Task-T task request (a
   rendered prompt) with the A2A-T client SDK's `generate_task_prompt_from_text`.
2. **Send** — the prompt travels to the server in the message `metadata`.
3. **Validate** — the server reads the prompt from the metadata and calls the A2A-T server SDK's
   `validate_task_prompt_and_data_filling`, which validates it and extracts the parameters
   (`operationType`, `region`, `energySavingGoal`, `guaranteedThroughputTarget`, plus the optional
   site-level `siteList` and `backgroundEvent`). The caller schema `TASK_PARAM_SCHEMA`, shared by
   both sides, lives in [`src/common/task_schema.py`](src/common/task_schema.py).
4. **Execute and report** — the extracted parameters feed the business execution; progress comes
   back as status updates and the result as a report artifact, which the client presents.

### 3.2 Client side

The core of the exchange is shown below, simplified from
[`client_flow.py`](src/client/client_flow.py):

```python
prompt_content = prompt_client.generate_task_prompt_from_text(input_text, ENERGY_SAVING_TEMPLATE_URI)
prompt_text, extension_uri = _require_prompt_text(prompt_content)
request = SendMessageRequest()
...  # message_id / role omitted
request.message.parts.add().text = _build_request_text(initial_input)   # the scenario text part
request.message.metadata[extension_uri] = prompt_text   # the prompt travels in the metadata
request.message.metadata["templateUri"] = getattr(prompt_content, "template_uri", None) or ENERGY_SAVING_TEMPLATE_URI
call_context = ClientCallContext(service_parameters={"A2A-Extensions": extension_uri})
async for stream_response in a2a_client.send_message(request, context=call_context):
    ...  # consume the status updates and the report artifact
```

### 3.3 Server side

`_extract_prompt_text` reads the metadata and `execute_server_flow` calls
`validate_task_prompt_and_data_filling`
([`server_flow.py`](src/server/server_flow.py)). A rejected prompt is mapped to
`TASK_STATE_REJECTED` instead of crashing the server; the validated parameters are passed to the
execution integration point (`TaskExecutionService`).

The template, the parameter schema and the natural-language input are sample-specific;
[§5](#5-notes-for-custom-agent-development) lists what each side replaces.

## 4. Using the A2A-T SDK to implement the Negotiation-T interaction

The SDKs handle the protocol details; the two agents own the business decisions.

- The **A2A-T SDK** generates and validates the negotiation messages (propose, accept, reject and
  abort): it renders the prompt text and carries the caller-supplied `negotiationContext` in the
  generated message. It keeps no session state and has no transport layer.
- The **A2A SDK** carries the messages, so a deployment always pairs the two.
- In this sample, the server agent decides when a request cannot be carried out as received and a
  negotiation could settle it; the client agent decides how to answer the propose. The protocol
  itself lets either side propose (see [§4.3](#43-mechanics-common-to-all-kinds)).

### 4.1 Negotiation scenarios

In this sample the server opens a negotiation when it cannot execute the request as received, and
the issue can be resolved through negotiation instead of immediate rejection. The sample covers
three such situations:

**Information — the request is incomplete.** The client asks to run energy saving and names a goal,
a time window and a throughput target, but never says which region. The server cannot start without
it, so it says which piece is missing; the client supplies it and the task proceeds.

**Target — the target is ambiguous.** The client sends a segmented guaranteed throughput target in
which two time windows overlap: `{17:30~23:00,20Mbps}` and `{20:00~00:00,10Mbps}`. The request is
well-formed, but the guaranteed rate for the overlapping period cannot be determined. The server
restates the intent it understood and asks which time window should be adjusted; the client answers
with the corrected segments.

**Feasibility — the request cannot be satisfied under current conditions.** The client asks two
stations to guarantee `{00:00~06:00,2Mbps}, {06:00~18:00,10Mbps}` during a mains power outage that
lasts from 00:00 to 18:00, while an existing constraint requires at least 18 hours of battery
autonomy. At that rate the batteries would run out before the outage ends. The server reports that
the target is infeasible and proposes an alternative (lower the 06:00~18:00 rate from 10 Mbps to
2 Mbps); the client accepts the adjusted target.

### 4.2 How the demo selects the negotiation kind

Whether to negotiate at all, and which kind applies, is **the server agent's business decision; the
SDKs do not dictate it**, and the client only reacts to the propose it receives. In this demo the
choice is data-driven: the code of the first content-validation error selects the kind through the
`_NEGOTIATION_TYPE_BY_ERROR_CODE` table
([`server_flow.py`](src/server/server_flow.py)). That table is a **sample convention, not an SDK
contract**.

The message format does not depend on that table. Each propose records its `templateUri` — the prompt
template its message text was rendered from, such as
`Negotiation-T/information-negotiation/propose/v1`. That URI also tells the client which kind of
propose it received, so the client picks its answer — accept, reject or abort — from the message it
got, not from any error code.

### 4.3 Mechanics common to all kinds

These apply to all three kinds; the per-kind sections below do not repeat them.

**Round.** A round consists of one propose and the response to that propose (accept, reject or
abort). The session context carries a 1-based `round`: a response keeps the round of the propose it
answers, and only a *new* propose advances the round. The client's initial request is not a
negotiation round — the first round begins with the server's propose. `maxRounds` (default 5) bounds
the session.

**Either side can propose.** Negotiation is symmetric: the A2A-T client SDK and the A2A-T server SDK
expose the same generation methods (propose / accept / reject / abort) and the same validation
methods, so either agent can open a negotiation and either can answer one. Only the sender of the
propose differs; message forms, session handling and transport are identical. In this demo the
**server** proposes, because it is the side that discovers the request cannot be executed as
received. A client could equally open a negotiation, for example when it cannot meet a constraint
the server announced; the server would then answer with accept, reject or abort. The demo implements
only the client-side decision (the provider integration point); a client-initiated negotiation would
need the equivalent decision on the server side.

**The closed loop, in four messages:**

1. client → server: the energy-saving request. In the demo it is deliberately flawed in one of three
   ways: a missing region, overlapping time windows in the target, or a target the batteries cannot
   sustain during the outage.
2. server → client: the propose — what is missing, what needs clarification, or which target is
   infeasible and what alternative is offered.
3. client → server: a single message that carries both the accept and the re-sent request with the
   missing or corrected content filled in.
4. server → client: the result — progress updates, then the report.

The session (`id` / `round` / `maxRounds` / performative) travels with every negotiation message in
the `negotiationContext` metadata map, so the A2A-T SDK itself stays stateless.

**The negotiation runs before the task is created.** The propose in message 2 is a standalone A2A
`Message` (role `ROLE_AGENT`, same context id), not a Task status event. The server creates the task
(`SUBMITTED`, then `WORKING`) only when the accept's Task-T prompt validates; the interaction then
continues as ordinary Task-T. The energy-saving task is a long-running duty task: it stays `WORKING`
after the report and has no `COMPLETED` state — it ends only if the task is cancelled.

**Where the client's decision plugs in.** When message 2 arrives, the client flow builds a lightweight
`NegotiationProposeView` (kind, `templateUri`, rendered text) and calls the provider
([`negotiation_response.py`](src/client/negotiation_response.py)):

```python
@runtime_checkable
class NegotiationResponseProvider(Protocol):
    def respond(
        self, propose: NegotiationProposeView, session: NegotiationContext
    ) -> NegotiationDecision: ...
```

`NegotiationDecision` carries a `conclusion` (`accept` / `reject` / `abort`) plus the payload for
it: for `accept`, `filled_params` (information, name → value), `confirmed_intent` (target) or
`feasibility_summary` (feasibility); for `reject` / `abort`, a `reason`. **`accept` is implemented
end to end**: from the client's decision the flow regenerates the Task-T prompt, builds the accept
message (same round, performative `ACCEPT`) and sends it over the configured transport. `respond` is
synchronous, so it blocks the flow until it returns; an implementation that needs to wait (human
confirmation, an upstream call) handles that inside `respond`.

> The provider replaces only the scripted **answer** to a propose — for information, the values
> returned in `filled_params`. The Task-T refill parameters (`negotiation_params`) remain
> caller-supplied inputs: the flow uses them to regenerate the Task-T prompt in the accept message,
> and without them the flow leaves the propose unanswered. In the demo both come from
> `scenario_data`, so they agree.

### 4.4 Information negotiation

**① Trigger condition.** The request is incomplete — it lacks a parameter the server needs
(in the demo, the region). The demo's trigger is a `content.param_missing` validation error, but
that code is the sample's choice, not part of the protocol.

**② Reference implementation.**

| Step | SDK call | Reference code |
| --- | --- | --- |
| Server propose | `generate_negotiation_propose_prompt_from_text` | [`server_flow.py`](src/server/server_flow.py) (`_emit_negotiation_propose`) |
| Client decision | `NegotiationResponseProvider.respond` | [`negotiation_response.py`](src/client/negotiation_response.py), [`client_flow.py`](src/client/client_flow.py) (`_plan_negotiation_accept`) |
| Client accept | `generate_negotiation_accept_prompt_from_text` | [`client_flow.py`](src/client/client_flow.py) (`_build_negotiation_accept_request`) |

```python
# Server propose: write the business message, then render it through the from-text API
propose = server.generate_negotiation_propose_prompt_from_text(
    propose_text,   # the missing parameters, written as a numbered list
    context,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
)

# Client decision: ask the provider how to answer the propose
decision = provider.respond(propose_view, session)   # filled_params = the values to deliver

# Client accept: write the answer as a business message and render it through the from-text API
accept_text = f"Accept. {_numbered_items(decision.filled_params.items())}"
accept = client.generate_negotiation_accept_prompt_from_text(
    accept_text,
    context,
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

The propose lists the missing parameters; the accept text lists the filled values.

**③ Reusable client code.** The prompt rendering, the `templateUri`/`negotiationContext`
metadata assembly, the session handling, and the transport — everything in `client_flow.py` except
the decision itself.

**④ Client-specific code.** The answer source behind the provider
integration point.

```python
class MyProvider:
    def respond(self, propose: NegotiationProposeView, session: NegotiationContext) -> NegotiationDecision:
        values = my_system.look_up(propose.kind, propose.prompt_text)   # the client's lookup
        return NegotiationDecision(conclusion="accept", filled_params=values)
```

### 4.5 Target negotiation

**① Trigger condition.** The target is ambiguous: two time windows of the segmented guaranteed
throughput target overlap, so the guaranteed rate for the overlapping period cannot be determined.
The demo's trigger is a `content.value_not_allowed` validation error.

**② Reference implementation.**

| Step | SDK call | Reference code |
| --- | --- | --- |
| Server propose | `generate_negotiation_propose_prompt_from_text` | [`server_flow.py`](src/server/server_flow.py) (`_emit_negotiation_propose`) |
| Client decision | `NegotiationResponseProvider.respond` | [`negotiation_response.py`](src/client/negotiation_response.py), [`client_flow.py`](src/client/client_flow.py) (`_plan_negotiation_accept`) |
| Client accept | `generate_negotiation_accept_prompt_from_text` | [`client_flow.py`](src/client/client_flow.py) (`_build_negotiation_accept_request`) |

```python
# Server propose: write the business message, then render it through the from-text API
propose = server.generate_negotiation_propose_prompt_from_text(
    propose_text,   # intent restatement + the clarification request
    context,
    TARGET_NEGOTIATION_PROPOSE_URI,
)

# Client decision: ask the provider how to answer the propose
decision = provider.respond(propose_view, session)   # confirmed_intent = the corrected target

# Client accept: write the answer as a business message and render it through the from-text API
accept_text = f"Accept. {decision.confirmed_intent}"
accept = client.generate_negotiation_accept_prompt_from_text(
    accept_text,
    context,
    TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

The demo's propose asks for clarification: the server restates the intent it understood
(`intent_understanding`) and asks which time window to adjust (`request_for_clarification`). The
accept carries the corrected target as `confirmed_intent`.

**③ Reusable client code.** The same as information: rendering, metadata assembly, session
handling, transport.

**④ Client-specific code.** How the client derives `confirmed_intent`: the demo reads it from
`scenario_data`, while a client implementation obtains it from the component that resolves the
ambiguity.

### 4.6 Feasibility negotiation

**① Trigger condition.** The request cannot be satisfied under current conditions: the guaranteed
rate cannot sustain the declared outage duration for the stations named in the request. The demo's
trigger is a `content.rule_violation` validation error.

**② Reference implementation.**

| Step | SDK call | Reference code |
| --- | --- | --- |
| Server propose | `generate_negotiation_propose_prompt_from_text` | [`server_flow.py`](src/server/server_flow.py) (`_emit_negotiation_propose`) |
| Client decision | `NegotiationResponseProvider.respond` | [`negotiation_response.py`](src/client/negotiation_response.py), [`client_flow.py`](src/client/client_flow.py) (`_plan_negotiation_accept`) |
| Client accept | `generate_negotiation_accept_prompt_from_text` | [`client_flow.py`](src/client/client_flow.py) (`_build_negotiation_accept_request`) |

```python
# Server propose: write the business message, then render it through the from-text API
propose = server.generate_negotiation_propose_prompt_from_text(
    propose_text,   # infeasibility conclusion + the alternative proposal
    context,
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
)

# Client decision: ask the provider how to answer the propose
decision = provider.respond(propose_view, session)   # feasibility_summary = the answer

# Client accept: write the answer as a business message and render it through the from-text API
accept_text = f"Accept. {decision.feasibility_summary}"
accept = client.generate_negotiation_accept_prompt_from_text(
    accept_text,
    context,
    FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

The demo's propose states that the assessment concluded the target is infeasible and attaches an
alternative (`action=PROPOSE_ALTERNATIVE_ON_FAILURE` with `infeasibility_details_and_proposal`); the
accept states the adjustment the client agrees to.

**③ Reusable client code.** The same as information: rendering, metadata assembly, session
handling, transport.

**④ Client-specific code.** The answer carried in `feasibility_summary`, i.e. the client's response
to the server's proposal.

### 4.7 Reject and abort

The client can answer a propose with `reject` (decline the propose) or `abort` (terminate the
negotiation) instead of `accept`; both are terminal in the protocol. This demo implements only the
accept path: for a reject/abort decision it logs the decision and ends the client flow without
sending a message, so the server is not notified. A production client sends the corresponding
message; the SDK provides the matching generation methods
(`generate_negotiation_reject_prompt_from_data` / `..._from_text`, and
`generate_negotiation_abort_prompt_from_data` / `..._from_text`).

## 5. Notes for Custom Agent Development

The **Side** column identifies the agent responsible for each component. The client team is
responsible for the rows marked `client` and `both`; the server agent is responsible for the rows
marked `server`.

| Demo piece | Side | What it does in the demo | Keep or replace |
| --- | --- | --- | --- |
| task template and parameter schema — `ENERGY_SAVING_TEMPLATE_URI` ([`scenario_data.py`](src/client/scenario_data.py)), [`src/common/task_schema.py`](src/common/task_schema.py) | both | the contract the client renders its request from and the server validates against | **REPLACE** with the template and schema agreed by both parties |
| mock LLM — [`resources/mock_llm/`](resources/mock_llm) + the schema-routed mock in `src/common/` | both | scripted slot-extraction and content-validation replies, selected when `A2AT_LLM_API_KEY` is empty | **KEEP** for CI / offline runs. In a real deployment, each side replaces it with a real OpenAI-compatible endpoint |
| scripted execution data — [`src/server/constants_data.py`](src/server/constants_data.py) | server | the scripted progress steps and intent report the demo execution streams | **REPLACE** by the server agent via the `TaskExecutionService` integration point ([`src/server/task_execution.py`](src/server/task_execution.py)); once its service is assembled, the demo service and data can be deleted |
| sample input and scripted answers — [`src/client/scenario_data.py`](src/client/scenario_data.py) | both (see below) | the Task-T natural-language input, the negotiation answers, and the canned propose texts | **REPLACE** on both sides: the client supplies its own task intent and answers through its own `NegotiationResponseProvider`; the server replaces the canned propose texts in `server_flow._negotiation_propose_text` with its own business wording |
| mock registry — [`src/registry/`](src/registry) (port 5001) | both | AgentCard registration (server) and query for discovery (client) | **REPLACE** with the deployment's registry center, or bypass discovery and construct the `a2a-sdk` client from a known AgentCard |
| static webhook token — `A2AT_SAMPLE_WEBHOOK_TOKEN` (default `demo-webhook-token`) | both | shared static token in the `X-A2A-Notification-Token` header | **REPLACE** with production auth (per-client secrets, rotation, TLS); treat the receiver as an exposed HTTP endpoint |

> **`scenario_data` feeds BOTH sides** — the client's Task-T input and scripted negotiation answers
> (filled parameters, confirmed intent, feasibility summary) go through the provider integration
> point, and the server's propose text is built from the same module: `_negotiation_propose_text`
> ([`server_flow.py`](src/server/server_flow.py)) fills the target/feasibility propose texts from
> its accessors. Replacing only the provider and the execution service still leaves the server
> proposing the demo's canned texts, so `scenario_data.py` cannot be deleted until both sides have
> also been replaced.

## 6. Appendix

### 6.1 Webhook transport

Both transports deliver the same task events; they differ in how the client receives them. SSE
streaming is the default: the client keeps the streaming `message:stream` call open and consumes
the events as they arrive. Webhook push notifications are for clients that cannot keep a streaming
connection open (long-running tasks, intermittently connected clients). Enable them with
`--transport webhook` or `A2AT_SAMPLE_TRANSPORT=webhook`.

What changes under webhook: the client sends non-blocking (`return_immediately`) and every task
event arrives as a webhook POST on a receiver embedded in the client process (`WebhookReceiver` in
[`src/common/webhook_receiver.py`](src/common/webhook_receiver.py)), which binds one extra
listening port (`--webhook-port`, default 9000). The server's pushes carry
`A2AT_SAMPLE_WEBHOOK_TOKEN` (default `demo-webhook-token`) in the `X-A2A-Notification-Token`
header. The flow ends on the report artifact, with a 30 s idle backstop. The negotiation round
trip behaves identically on both transports; a pre-task propose Message is not a task event, so the
client reads it from the non-blocking RPC reply.

- **Two registration paths.** The initial request registers the push config inline
  (`SendMessageConfiguration.task_push_notification_config`, a flat
  `TaskPushNotificationConfig(url, token)`) because the task id does not exist yet. Before sending
  the accept, the flow re-registers the same URL/token through
  `POST /tasks/{id}/pushNotificationConfigs` (a2a-sdk `BaseClient.create_task_push_notification_config`)
  — the endpoint path that becomes available once the task id is known.
- **No server-side mode switch.** The server assembles the push machinery unconditionally
  (a2a-sdk `DefaultRequestHandler` with `InMemoryPushNotificationConfigStore` +
  `BasePushNotificationSender`, [`server_main.py`](src/server/server_main.py)); in SSE mode it
  simply idles.
- **Non-blocking path.** The a2a-sdk client is built with `ClientConfig(streaming=False)`, required
  to select the non-blocking `message:send` path (the `return_immediately` flag alone does not
  switch it). Push bodies are StreamResponse-shaped JSON — the same shape as the SSE events — so
  negotiation detection, artifact counting, and presentation are shared with SSE.
- **Token auth.** The receiver validates `X-A2A-Notification-Token` on every POST: a mismatch
  answers `401` (the event is dropped), a malformed or unrecognized body answers `400`. The demo's
  static shared token is demo-grade — for production use per-client secrets with rotation over TLS.
- **End signals.** Every scenario ends on the report artifact: the energy-saving task is a
  long-running duty task and stays `WORKING`, so the server emits no terminal state after the
  report. A failed validation (a `REJECTED` task) or a stream error (`FAILED` / `CANCELED`) still
  ends the flow immediately, and the summary records it as `end=<state>`. A pre-task propose is a
  Message, never a pushed task event: in webhook mode the client answers it from the non-blocking
  RPC reply and registers its own inline push config on the accept. The idle backstop
  (`A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT`, default 30 s, reset per received event) logs
  `webhook-idle-timeout` and ends the flow with what was collected. `A2AT_SAMPLE_MAX_ARTIFACTS`
  does not apply in webhook mode. Each phase's single RPC reply is logged once as
  `webhook-rpc-reply` and is not counted again. The `stream-completed` summary counts the
  webhook-received events and appends `transport=webhook`.

### 6.2 Configuration (a2a-t-sample/.env)

| Key | Meaning |
| --- | --- |
| `A2AT_LANGUAGE` | `zh-CN` / `en-US`; selects the natural-language input and the mock/template tree |
| `A2AT_SAMPLE_SCENARIO` | `default` / `negotiation`; selects the sample scenario variant (same semantics as `--scenario`) |
| `A2AT_SAMPLE_NEGOTIATION_KIND` | `information` / `target` / `feasibility` (default `information`); negotiation scenario input, same semantics as `--negotiation` |
| `A2AT_SAMPLE_TRANSPORT` | `sse` / `webhook` (default `sse`); client event transport, same semantics as `--transport` |
| `A2AT_SAMPLE_WEBHOOK_PORT` | Bind port of the embedded webhook receiver (default `9000`; `0` = OS-assigned ephemeral port) |
| `A2AT_SAMPLE_WEBHOOK_TOKEN` | Push token the server must carry in the `X-A2A-Notification-Token` header (default `demo-webhook-token`); a mismatch answers `401` |
| `A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT` | Idle backstop in seconds that ends the webhook flow when no push arrives (default `30`) |
| `A2AT_LLM_API_KEY` | Empty ⇒ mock LLM; otherwise an OpenAI-compatible key |
| `A2AT_LLM_PROVIDER` / `A2AT_LLM_MODEL` / `A2AT_LLM_BASE_URL` | LLM endpoint settings |
| `A2AT_LLM_TIMEOUT_SECONDS` | HTTP client timeout in seconds (default 60) |
| `A2AT_SAMPLE_HOST` / `A2AT_SAMPLE_PORT` | Server bind for the manual `python -m` path (default `127.0.0.1:8000`) |
| `REGISTRY_CENTER_HOST` / `REGISTRY_CENTER_PORT` | Registry bind for the manual `python -m` path (default `127.0.0.1:5001`) |
| `A2AT_SAMPLE_DEBUG` | `true` prints full LLM request/response payloads |
| `A2AT_SAMPLE_MAX_ARTIFACTS` (process env, not `.env`) | Stop after N artifacts (default 0 = no limit) |

Notes: `run_demo.py` always uses `5001` / `8000` for its readiness checks and ignores
`A2AT_SAMPLE_PORT` / `REGISTRY_CENTER_PORT` — set those only for the manual `python -m` path.
Server-side progress and report text is always English, regardless of `--language`. The flag
switches the client input, the templates, the mock replies and the negotiation message texts. The
template, parameter schema, AgentCard and natural-language input are sample-specific; a replaced
template must belong to the `Task-T` extension and use version `v1`. The SDK does not load remote
prompt resources; the registry center in this sample is a sample-only mock used for AgentCard
discovery, not an SDK capability.

### 6.3 Run the two sides separately

Point `PYTHONPATH` at the sample's `src/` directory and start each part in its own terminal. `.env`
is read from the current directory, and a missing `.env` is a **hard error** for the client and
server. Start the registry first, then the server, then the client (the client discovers the
server's AgentCard from the registry).

From the repository root:

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

The run language comes from `A2AT_LANGUAGE` in `a2a-t-sample/.env` (`env.example` ships `zh-CN`);
`run_demo.py` overrides it per run via `--language` (default `en-US`).

### 6.4 Mock LLM mechanism

With an empty `A2AT_LLM_API_KEY` the sample installs the bundled mock LLM
([`src/common/mock_llm.py`](src/common/mock_llm.py)): it routes every structured call by the
**output schema signature** (`route_structured_call`) and replays the scripted slot-extraction and
content-validation responses of [`resources/mock_llm/{zh-CN,en-US}/`](resources/mock_llm).

### 6.5 Supported flags (`run_demo.py`)

| Flag | Effect |
| --- | --- |
| `--language {en-US,zh-CN}` | Run language (default `en-US`) |
| `--scenario {default,negotiation}` | Scenario variant (default: `default`; `negotiation` runs the missing-region information negotiation) |
| `--negotiation {information,target,feasibility}` | Negotiation kind: `information` (default; missing region), `target` (two overlapping time segments in the guaranteed throughput target) or `feasibility` (the guaranteed rate cannot sustain the 18-hour power outage). `target` and `feasibility` run a negotiation on their own; `information` additionally needs `--scenario negotiation`. The flag only selects the client input; the server decides the kind from the validation error code |
| `--transport {sse,webhook}` | Event transport of the client (default `sse`); see [§6.1](#61-webhook-transport) |
| `--webhook-port N` | Bind port of the embedded webhook receiver used with `--transport webhook` (default 9000; `0` selects an OS-assigned ephemeral port) |
| `--keep-alive` | Leave registry/server running; stop later with `--stop` |
| `--keep-logs` | Keep the temp run directory (logs / `.env`) under `<temp>/a2at-ran-energy-saving/` |
| `--stop` | Stop a previous `--keep-alive` run and exit |
| `--timeout N` | Seconds to wait for each port (default 30) |
