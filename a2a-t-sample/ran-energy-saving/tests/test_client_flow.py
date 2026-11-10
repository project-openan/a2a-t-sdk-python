"""Tests for the multi-round client flow over SSE and webhook transports."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from a2a.client.client import ClientCallContext
from a2a.types import (
    Message,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
    MetadataContent,
    NegotiationContext,
)
from a2a_t.core.standard_templates import (
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    NEGOTIATION_ABORT_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from a2a_t.negotiation.content import (
    FeasibilityEndingContent,
    InformationEndingContent,
    NegotiationConclusion,
    NegotiationEndingData,
    TargetEndingContent,
)
from demo.scenario import (
    FINAL_STATE_NEGOTIATION_ABORTED,
    FINAL_STATE_NEGOTIATION_ENDED,
    FINAL_STATE_TASK_COMPLETED,
    SCENARIO_ABORT_SPEC,
    SCENARIO_FEASIBILITY_SPEC,
    SCENARIO_REJECT_SPEC,
    SCENARIO_TARGET_SPEC,
    SUPPORTED_SCENARIO_NAMES,
    ExpectedStep,
    Scenario,
    get_scenario,
)
from demo.scenario import (
    SCENARIO_INFORMATION_SPEC as _INFORMATION_SPEC_BASE,
)
from google.protobuf.json_format import MessageToDict
from protocol.client_flow import (
    DEFAULT_CALL_TIMEOUT_SECONDS,
    FINAL_STATE_INCOMPLETE,
    FINAL_STATE_TASK_FAILED,
    TASK_REQUEST_LABEL,
    ClientFlowError,
    ClientFlowResult,
    SseClientTransport,
    WebhookClientTransport,
    _final_state,
    normalize_webhook_payload,
    run_client_flow,
)
from protocol.client_policy import ClientPolicy
from protocol.decision_provider import DecisionTimeoutError, ScriptedDecisionProvider
from protocol.messages import (
    END_MESSAGE_TEXT,
    SAMPLE_NEGOTIATION_OPTIONS_METADATA_KEY,
    SAMPLE_PHASE_METADATA_KEY,
    SAMPLE_PROPOSAL_METADATA_KEY,
)
from shared.config import SampleConfig
from shared.models import NegotiationDecision, NegotiationProposeView
from shared.run_constants import FINAL_STATE_INTENT_NOT_CONFIRMED
from shared.trace import (
    PHASE_NEGOTIATION,
    PHASE_RESOLUTION,
    PHASE_TASK_MODIFY,
    PHASE_TASK_PROMPT,
    PHASE_TASK_VALIDATION,
    PHASES,
    set_global_activity_sink,
)

CONTEXT_ID = "ctx-client-flow"

#: Business values the runtime would have parsed from these English requests.
_INTENT_FIELDS: dict[str, object] = {"operationType": "Create", "region": "Region A"}

#: A complete INFORMATION negotiation fixture. The demo ``information`` scenario now blocks the
#: missing target locally, so the flow tests use their own complete request and scripted accept.
INFORMATION_NEGOTIATION_SPEC = replace(
    _INFORMATION_SPEC_BASE,
    expected_sequence=(
        ExpectedStep(actor="server", performative="PROPOSE", round=1),
        ExpectedStep(actor="client", performative="ACCEPT", round=1),
    ),
    expected_final_state=FINAL_STATE_TASK_COMPLETED,
    scripted_client_decisions=(
        NegotiationDecision(
            action="accept",
            rationale="The missing time-segmented throughput target is supplied.",
            filled_params={
                "guaranteedThroughputTarget": (
                    "{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,10Mbps}"
                )
            },
        ),
    ),
)

_PROPOSE_URIS: dict[str, str] = {
    "information": INFORMATION_NEGOTIATION_PROPOSE_URI,
    "target": TARGET_NEGOTIATION_PROPOSE_URI,
    "feasibility": FEASIBILITY_NEGOTIATION_PROPOSE_URI,
}


@dataclass(frozen=True)
class FakePromptClient:
    """Deterministic SDK client double recording every generation call."""

    task_prompts: list[str] = field(default_factory=list)
    accept_texts: list[str] = field(default_factory=list)
    accept_data: list[NegotiationEndingData] = field(default_factory=list)
    reject_texts: list[str] = field(default_factory=list)
    abort_texts: list[str] = field(default_factory=list)
    propose_texts: list[str] = field(default_factory=list)
    refill_data: list[dict[str, object]] = field(default_factory=list)

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> MetadataContent:
        self.task_prompts.append(text)
        return MetadataContent(
            template_uri=template_uri,
            prompt_text=f"Task-T prompt for: {text}",
            extension_uri=TASK_T_EXTENSION_URI,
        )

    def generate_task_prompt_from_data_with_schema(
        self,
        data: Mapping[str, object],
        schema: Mapping[str, object],
        template_uri: str,
    ) -> MetadataContent:
        del schema
        self.refill_data.append(dict(data))
        return MetadataContent(
            template_uri=template_uri,
            prompt_text=f"Refilled Task-T prompt: {json.dumps(dict(data), sort_keys=True)}",
            extension_uri=TASK_T_EXTENSION_URI,
        )

    def generate_negotiation_accept_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent:
        self.accept_texts.append(text or "")
        return _negotiation_content(f"Accept: {text}", context, template_uri)

    def generate_negotiation_accept_prompt_from_data(
        self, data: NegotiationEndingData, template_uri: str
    ) -> MetadataContent:
        self.accept_data.append(data)
        return _negotiation_content(f"Accept from data: {data.content}", data.context, template_uri)

    def generate_negotiation_reject_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent:
        self.reject_texts.append(text or "")
        return _negotiation_content(f"Reject: {text}", context, template_uri)

    def generate_negotiation_abort_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent:
        self.abort_texts.append(text or "")
        return _negotiation_content(f"Abort: {text}", context, template_uri)

    def generate_negotiation_propose_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent:
        self.propose_texts.append(text or "")
        return _negotiation_content(f"Propose: {text}", context, template_uri)


@dataclass(frozen=True)
class SlowPromptClient(FakePromptClient):
    """Fake client that spends wall-clock time in the initial Task-T extraction."""

    delay_seconds: float = 0.3

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> MetadataContent:
        time.sleep(self.delay_seconds)
        return super().generate_task_prompt_from_text(text, template_uri)


@dataclass(frozen=True)
class SlowAcceptPromptClient(FakePromptClient):
    """Fake client that spends wall-clock time building the accept message."""

    delay_seconds: float = 0.3

    def generate_negotiation_accept_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent:
        time.sleep(self.delay_seconds)
        return super().generate_negotiation_accept_prompt_from_text(text, context, template_uri)

    def generate_negotiation_accept_prompt_from_data(
        self, data: NegotiationEndingData, template_uri: str
    ) -> MetadataContent:
        time.sleep(self.delay_seconds)
        return super().generate_negotiation_accept_prompt_from_data(data, template_uri)


async def _ticks_while(awaitable: Awaitable[object]) -> int:
    """Count event-loop turns while ``awaitable`` runs (0 when the call blocks the loop)."""
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        await awaitable
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return ticks


def _negotiation_content(text: str, context: NegotiationContext, template_uri: str) -> MetadataContent:
    return MetadataContent(
        template_uri=template_uri,
        prompt_text=text,
        extension_uri=NEGOTIATION_T_EXTENSION_URI,
        negotiation_context=context,
    )


class ScriptedTransport:
    """Fake transport replaying one event batch per send and recording the requests."""

    def __init__(self, batches: list[list[dict[str, object]]]) -> None:
        self._batches = list(batches)
        self.requests: list[SendMessageRequest] = []
        self.call_contexts: list[ClientCallContext] = []

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        self.requests.append(request)
        self.call_contexts.append(call_context)
        if not self._batches:
            raise AssertionError("The scripted transport ran out of event batches")
        return self._batches.pop(0)


def _negotiation_metadata(
    *,
    performative: str,
    round_number: int,
    template_uri: str,
    content: str = "negotiation content",
    negotiation_id: str = CONTEXT_ID,
) -> dict[str, object]:
    return {
        NEGOTIATION_T_EXTENSION_URI: content,
        TEMPLATE_URI_METADATA_KEY: template_uri,
        NEGOTIATION_CONTEXT_METADATA_KEY: {
            "id": negotiation_id,
            "round": round_number,
            "maxRounds": 3,
            "performative": performative,
        },
    }


def propose_event(
    kind: str,
    round_number: int,
    *,
    content: str | None = None,
    negotiation_id: str = CONTEXT_ID,
) -> dict[str, object]:
    return {
        "kind": "message",
        "text": "Negotiation proposal",
        "context_id": CONTEXT_ID,
        "message_id": f"propose-{round_number}",
        "metadata": _negotiation_metadata(
            performative="PROPOSE",
            round_number=round_number,
            template_uri=_PROPOSE_URIS[kind],
            content=content or f"Server proposal for round {round_number}",
            negotiation_id=negotiation_id,
        ),
    }


def status_propose_event(
    kind: str,
    round_number: int,
    *,
    state: str = "TASK_STATE_INPUT_REQUIRED",
    content: str | None = None,
) -> dict[str, object]:
    """A vendor NMS delivers the Negotiation-T propose inside a status update."""
    text = content or f"Server proposal for round {round_number}"
    return {
        "kind": "status",
        "task_id": "task-1",
        "context_id": CONTEXT_ID,
        "state": state,
        "text": text,
        "metadata": _negotiation_metadata(
            performative="PROPOSE",
            round_number=round_number,
            template_uri=_PROPOSE_URIS[kind],
            content=text,
        ),
    }


def abort_event(round_number: int) -> dict[str, object]:
    return {
        "kind": "message",
        "text": "Negotiation aborted",
        "context_id": CONTEXT_ID,
        "message_id": f"abort-{round_number}",
        "metadata": _negotiation_metadata(
            performative="ABORT",
            round_number=round_number,
            template_uri=NEGOTIATION_ABORT_URI,
            content="The negotiation reached its round limit.",
        ),
    }


def end_event() -> dict[str, object]:
    return {
        "kind": "message",
        "text": END_MESSAGE_TEXT,
        "context_id": CONTEXT_ID,
        "message_id": "end",
    }


def artifact_event() -> dict[str, object]:
    return {
        "kind": "artifact",
        "task_id": "task-1",
        "context_id": CONTEXT_ID,
        "artifact_id": "artifact-1",
        "name": "energy saving intent report",
        "data": {"summary": "Plan applied", "estimated_saving_kwh": 12.5},
    }


def status_event(
    state: str,
    *,
    task_id: str = "task-1",
    context_id: str = CONTEXT_ID,
    text: str | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "kind": "status",
        "task_id": task_id,
        "context_id": context_id,
        "state": state,
    }
    if text is not None:
        event["text"] = text
    return event


def _task_completed_batch() -> list[dict[str, object]]:
    return [
        status_event("TASK_STATE_WORKING"),
        artifact_event(),
        status_event("TASK_STATE_COMPLETED"),
    ]


def test_completed_with_a_refusal_text_is_a_failed_task() -> None:
    """The vendor reports "I could not do it" as COMPLETED plus an error sentence (W-PURE-3)."""
    batch = [
        status_event("TASK_STATE_WORKING"),
        status_event(
            "TASK_STATE_COMPLETED",
            text='The region name "TestRegion" is not recognized. Please provide the correct region name.',
        ),
    ]

    assert _final_state(batch) == FINAL_STATE_TASK_FAILED


def test_completed_with_a_report_keeps_its_success_state() -> None:
    """An artifact is the machine-readable result; refusal wording in a report is not a failure."""
    batch = [
        status_event("TASK_STATE_WORKING"),
        artifact_event(),
        status_event("TASK_STATE_COMPLETED", text="The region is not recognized in one cell."),
    ]

    assert _final_state(batch) == FINAL_STATE_TASK_COMPLETED


def test_benign_completion_text_keeps_its_success_state() -> None:
    batch = [status_event("TASK_STATE_COMPLETED", text="RAN energy-saving task completed.")]

    assert _final_state(batch) == FINAL_STATE_TASK_COMPLETED


def _client_negotiation_steps(requests: list[SendMessageRequest]) -> list[tuple[str, int]]:
    steps: list[tuple[str, int]] = []
    for request in requests:
        metadata = MessageToDict(request.message.metadata)
        context = metadata.get(NEGOTIATION_CONTEXT_METADATA_KEY)
        if isinstance(context, Mapping):
            steps.append((str(context["performative"]), int(context["round"])))
    return steps


async def _run(
    scenario_spec: Scenario,
    batches: list[list[dict[str, object]]],
    decisions: tuple[NegotiationDecision, ...],
    *,
    single_extension_per_message: bool = False,
) -> tuple[ClientFlowResult, ScriptedTransport, FakePromptClient]:
    prompt_client = FakePromptClient()
    transport = ScriptedTransport(batches)
    user_request = scenario_spec.user_request["en-US"]
    result = await run_client_flow(
        prompt_client=prompt_client,
        transport=transport,
        user_request=user_request,
        provider=ScriptedDecisionProvider(decisions),
        language="en-US",
        intent_fields=_INTENT_FIELDS,
        single_extension_per_message=single_extension_per_message,
    )
    return result, transport, prompt_client


async def test_message_sent_span_carries_the_wire_fields() -> None:
    spans: list[dict[str, object]] = []
    prompt_client = FakePromptClient()

    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport([_task_completed_batch()]),
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider(()),
        language="en-US",
        trace_sink=spans.append,
    )

    sent = next(span for span in spans if span["kind"] == "message_sent")
    detail = sent["detail"]
    assert isinstance(detail, dict)
    assert isinstance(detail["messageId"], str) and detail["messageId"]
    assert str(detail["extensionUri"]).endswith("/Task-T/v1")
    assert detail["performative"] == "TASK_T"
    # The wire inspector shows the rendered Task-T, not the natural-language request.
    assert detail["taskT"] == f"Task-T prompt for: {prompt_client.task_prompts[-1]}"


async def test_waiting_for_the_server_publishes_a_peer_activity() -> None:
    """The NMS can stay silent for a long time; the page must still show that it is waiting."""
    activities: list[dict[str, object]] = []
    set_global_activity_sink(activities.append)
    try:
        await run_client_flow(
            prompt_client=FakePromptClient(),
            transport=ScriptedTransport([_task_completed_batch()]),
            user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
            provider=ScriptedDecisionProvider(()),
            language="en-US",
        )
    finally:
        set_global_activity_sink(None)

    peer = [item for item in activities if item["detail"]["activityKind"] == "peer"]
    assert [item["detail"]["status"] for item in peer] == ["running", "done"]
    assert peer[0]["detail"]["labelKey"] == "waitingPeer"


async def test_the_initial_task_t_extraction_does_not_block_the_event_loop() -> None:
    """The yellow zone waited 116.7 s after confirming; a sync SDK call must not freeze the UI."""
    ticks = await _ticks_while(
        run_client_flow(
            prompt_client=SlowPromptClient(),
            transport=ScriptedTransport([_task_completed_batch()]),
            user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
            provider=ScriptedDecisionProvider(()),
            language="en-US",
        )
    )

    # A blocking call would leave the ticker at 0 for the whole extraction.
    assert ticks >= 10


async def test_the_accept_extraction_does_not_block_the_event_loop() -> None:
    """Every prompt-generation call of the flow hands the loop back, not only the first one."""
    ticks = await _ticks_while(
        run_client_flow(
            prompt_client=SlowAcceptPromptClient(),
            transport=ScriptedTransport([[propose_event("information", 1)], _task_completed_batch()]),
            user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
            provider=ScriptedDecisionProvider((INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions[0],)),
            language="en-US",
        )
    )

    assert ticks >= 10


async def test_accept_span_carries_the_refilled_task_t_and_the_negotiation_payload() -> None:
    spans: list[dict[str, object]] = []

    await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport([[propose_event("information", 1)], _task_completed_batch()]),
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions[0],)),
        language="en-US",
        trace_sink=spans.append,
    )

    sent = [span for span in spans if span["kind"] == "message_sent"]
    assert len(sent) == 2
    accept = sent[1]["detail"]
    assert isinstance(accept, dict)
    assert accept["performative"] == "ACCEPT"
    assert str(accept["taskT"]).startswith("Refilled Task-T prompt:")
    assert str(accept["negotiationT"]).startswith("Accept from data:")
    assert str(accept["negotiationUri"]).endswith("/Negotiation-T/v1")


async def test_modify_only_run_continues_the_selected_task() -> None:
    """A Modify-only run sends one Modify Task-T to the selected Task, with no baseline."""
    spans: list[dict[str, object]] = []
    prompt_client = FakePromptClient()
    modify_batch = [
        {
            "kind": "artifact",
            "task_id": "task-9",
            "context_id": "ctx-9",
            "artifact_id": "artifact-1",
            "name": "energy saving intent report",
            "data": {"summary": "Plan modified", "target_rate": 20},
        },
        status_event("TASK_STATE_COMPLETED", task_id="task-9", context_id="ctx-9"),
    ]
    transport = ScriptedTransport([modify_batch])

    result = await run_client_flow(
        prompt_client=prompt_client,
        transport=transport,
        user_request="Modify the running task",
        provider=ScriptedDecisionProvider(()),
        language="en-US",
        trace_sink=spans.append,
        run_mode="modify",
        target_task_id="task-9",
        target_context_id="ctx-9",
    )

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.message.task_id == "task-9"
    assert request.message.context_id == "ctx-9"
    assert request.message.parts[0].text == "modify ran energy saving task"
    metadata = MessageToDict(request.message.metadata)
    # ``samplePhase`` is sample-only bookkeeping, not part of the A2A-T interface: the request to
    # the OSS Agent must not carry it. Modify is identified from the Task-T operation type.
    assert SAMPLE_PHASE_METADATA_KEY not in metadata
    # The Modify Task-T is extracted from the operator's own text, not a canned data map.
    assert prompt_client.task_prompts == ["Modify the running task"]

    sent = [span for span in spans if span["kind"] == "message_sent"]
    assert [span["phase"] for span in sent] == [PHASE_TASK_MODIFY]
    assert [span["title"] for span in sent] == ["Modify Task-T sent"]
    assert result.task_id == "task-9"
    assert result.report is not None and result.report["target_rate"] == 20


async def test_modify_only_run_requires_a_target_task() -> None:
    """Without both target ids the flow rejects the run before any message is sent."""
    transport = ScriptedTransport([])
    with pytest.raises(Exception, match="target Task id"):
        await run_client_flow(
            prompt_client=FakePromptClient(),
            transport=transport,
            user_request="Modify the running task",
            provider=ScriptedDecisionProvider(()),
            language="en-US",
            run_mode="modify",
            target_task_id="task-9",
        )
    assert transport.requests == []


async def test_intent_gate_can_stop_the_run_before_any_task_t_is_sent() -> None:
    prompt_client = FakePromptClient()
    transport = ScriptedTransport([])
    seen: list[str] = []

    async def reject_intent(prompt_text: str) -> bool:
        seen.append(prompt_text)
        return False

    result = await run_client_flow(
        prompt_client=prompt_client,
        transport=transport,
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider(()),
        language="en-US",
        on_intent_ready=reject_intent,
    )

    assert seen and seen[0]
    assert result.final_state == FINAL_STATE_INTENT_NOT_CONFIRMED
    assert result.task_id is None
    assert result.report is None
    assert transport.requests == []


async def test_conversation_context_is_prepended_to_task_t_generation() -> None:
    prompt_client = FakePromptClient()
    transport = ScriptedTransport([[propose_event("information", 1)], _task_completed_batch()])

    await run_client_flow(
        prompt_client=prompt_client,
        transport=transport,
        user_request="current request",
        provider=ScriptedDecisionProvider(INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions),
        language="en-US",
        conversation_context="user: earlier request\nassistant: earlier result",
    )

    assert prompt_client.task_prompts[0].startswith("Conversation context")
    assert "user: earlier request" in prompt_client.task_prompts[0]
    assert "Current request:\ncurrent request" in prompt_client.task_prompts[0]


async def test_information_scenario_accepts_and_completes_a_task() -> None:
    result, transport, prompt_client = await _run(
        INFORMATION_NEGOTIATION_SPEC,
        [[propose_event("information", 1)], _task_completed_batch()],
        INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert result.task_id == "task-1"
    assert result.report == {"summary": "Plan applied", "estimated_saving_kwh": 12.5}
    assert _client_negotiation_steps(transport.requests) == [("ACCEPT", 1)]
    assert prompt_client.refill_data[0]["region"] == "Region A"
    assert prompt_client.refill_data[0]["guaranteedThroughputTarget"] == (
        "{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,10Mbps}"
    )
    accept_metadata = MessageToDict(transport.requests[1].message.metadata)
    assert accept_metadata[TASK_T_EXTENSION_URI].startswith("Refilled Task-T prompt:")
    assert accept_metadata[TEMPLATE_URI_METADATA_KEY] == "Negotiation-T/information-negotiation/accept-reject/v1"


class _UnsupportedActionProvider:
    """Return an action the protocol has no message for."""

    async def respond(self, propose: NegotiationProposeView, session: NegotiationContext) -> object:
        del propose, session
        return SimpleNamespace(
            action="noop",
            rationale="not a protocol decision",
            filled_params=None,
            counter_proposal=None,
            counter_params=None,
            confirmed_intent=None,
            feasibility_summary=None,
        )


class _TimingOutProvider:
    """Report an expired local window instead of a decision."""

    async def respond(self, propose: NegotiationProposeView, session: NegotiationContext) -> object:
        del propose
        raise DecisionTimeoutError(
            round_number=session.round,
            window_seconds=1.0,
            expired_windows=1,
        )


async def test_an_unsupported_decision_action_raises_instead_of_aborting() -> None:
    transport = ScriptedTransport([[propose_event("information", 1)]])
    with pytest.raises(ClientFlowError, match="Unsupported decision action: noop"):
        await run_client_flow(
            prompt_client=FakePromptClient(),
            transport=transport,
            user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
            provider=_UnsupportedActionProvider(),
            language="en-US",
            intent_fields=_INTENT_FIELDS,
        )
    # Only the Task-T went out; the unknown action must never fall back to ABORT.
    assert len(transport.requests) == 1


async def test_a_decision_timeout_from_the_provider_fails_without_sending() -> None:
    transport = ScriptedTransport([[propose_event("information", 1)]])
    with pytest.raises(ClientFlowError, match="stopped waiting without a decision"):
        await run_client_flow(
            prompt_client=FakePromptClient(),
            transport=transport,
            user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
            provider=_TimingOutProvider(),
            language="en-US",
            intent_fields=_INTENT_FIELDS,
        )
    assert len(transport.requests) == 1


async def test_accept_carries_the_proposal_negotiation_id_not_the_context_id() -> None:
    """The vendor rejects a reply whose negotiationContext.id is the A2A context id."""
    result, transport, _prompt_client = await _run(
        INFORMATION_NEGOTIATION_SPEC,
        [[propose_event("information", 1, negotiation_id="negotiation-1")], _task_completed_batch()],
        INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    accept_metadata = MessageToDict(transport.requests[1].message.metadata)
    assert accept_metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["id"] == "negotiation-1"


async def test_accept_continues_when_the_response_stream_carries_a_new_negotiation_id() -> None:
    """After ACCEPT the vendor may open a new negotiation id on the same context id."""
    completion = NegotiationDecision(action="accept", rationale="accept")
    result, transport, _prompt_client = await _run(
        INFORMATION_NEGOTIATION_SPEC,
        [
            [propose_event("information", 1, negotiation_id="negotiation-1")],
            [propose_event("information", 2, negotiation_id="negotiation-2")],
            _task_completed_batch(),
        ],
        (completion, completion),
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert _client_negotiation_steps(transport.requests) == [("ACCEPT", 1), ("ACCEPT", 2)]
    first_accept = MessageToDict(transport.requests[1].message.metadata)
    second_accept = MessageToDict(transport.requests[2].message.metadata)
    assert first_accept[NEGOTIATION_CONTEXT_METADATA_KEY]["id"] == "negotiation-1"
    assert second_accept[NEGOTIATION_CONTEXT_METADATA_KEY]["id"] == "negotiation-2"


async def test_nms_accept_carries_only_the_negotiation_extension_key() -> None:
    """The vendor rejects an accept whose metadata has more than one ``-T`` key."""
    result, transport, prompt_client = await _run(
        INFORMATION_NEGOTIATION_SPEC,
        [[propose_event("information", 1)], _task_completed_batch()],
        INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions,
        single_extension_per_message=True,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    accept_metadata = MessageToDict(transport.requests[1].message.metadata)
    assert TASK_T_EXTENSION_URI not in accept_metadata
    assert NEGOTIATION_T_EXTENSION_URI in accept_metadata
    assert NEGOTIATION_CONTEXT_METADATA_KEY in accept_metadata
    # The refilled Task-T was discarded by the NMS path, so generating it was a pure LLM call.
    assert prompt_client.refill_data == []


async def test_information_accept_uses_the_selected_served_area() -> None:
    decision = NegotiationDecision(
        action="accept",
        rationale="The engineer selected the second served area.",
        filled_params={"region": "Region B"},
    )
    result, _transport, prompt_client = await _run(
        INFORMATION_NEGOTIATION_SPEC,
        [[propose_event("information", 1)], _task_completed_batch()],
        (decision,),
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert prompt_client.refill_data[0]["region"] == "Region B"


async def test_information_accept_discards_an_invalid_model_filled_target() -> None:
    """A live model must not put per-cell SLA floors into the agreed segmented target.

    Nothing is fabricated in its place: the intent layer never invents a missing value, so the
    refill simply carries no target and the server can ask for it again.
    """
    decision = NegotiationDecision(
        action="accept",
        rationale="The dataset SLA floors supply the value.",
        filled_params={
            "guaranteedThroughputTarget": "cell-001: 250 Mbps; cell-002: 150 Mbps",
            "unit": "Mbps",
        },
    )
    result, _transport, prompt_client = await _run(
        INFORMATION_NEGOTIATION_SPEC,
        [[propose_event("information", 1)], _task_completed_batch()],
        (decision,),
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert "guaranteedThroughputTarget" not in prompt_client.refill_data[0]
    assert "unit" not in prompt_client.refill_data[0]


class _DroppingRefillPromptClient(FakePromptClient):
    """Prompt client whose refill renders a Task-T without the agreed throughput target."""

    def generate_task_prompt_from_data_with_schema(
        self,
        data: Mapping[str, object],
        schema: Mapping[str, object],
        template_uri: str,
    ) -> MetadataContent:
        del schema
        self.refill_data.append(dict(data))
        return MetadataContent(
            template_uri=template_uri,
            prompt_text=(
                "## Task Description\n\nCreate the energy saving task.\n\n"
                "## Task Context\n\n\n## Expected Output\n\nThe intent report.\n"
            ),
            extension_uri=TASK_T_EXTENSION_URI,
        )


async def test_accept_rewrites_a_refill_that_dropped_the_agreed_target() -> None:
    """A model-refilled Task-T without the agreed target still carries it when accepted."""
    prompt_client = _DroppingRefillPromptClient()
    transport = ScriptedTransport([[propose_event("information", 1)], _task_completed_batch()])

    result = await run_client_flow(
        prompt_client=prompt_client,
        transport=transport,
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider(INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions),
        language="en-US",
        intent_fields=_INTENT_FIELDS,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    accept_metadata = MessageToDict(transport.requests[1].message.metadata)
    task_text = accept_metadata[TASK_T_EXTENSION_URI]
    assert "{00:00~17:30,10Mbps}" in task_text
    assert "{17:30~23:00,20Mbps}" in task_text
    assert "{23:00~24:00,10Mbps}" in task_text


async def test_target_scenario_counters_in_round_one_then_accepts_round_two() -> None:
    result, transport, prompt_client = await _run(
        SCENARIO_TARGET_SPEC,
        [[propose_event("target", 1)], [propose_event("target", 2)], _task_completed_batch()],
        SCENARIO_TARGET_SPEC.scripted_client_decisions,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert _client_negotiation_steps(transport.requests) == [("PROPOSE", 1), ("ACCEPT", 2)]
    assert len(prompt_client.propose_texts) == 1
    assert len(prompt_client.accept_data) == 1


async def test_feasibility_scenario_accepts_the_alternative() -> None:
    result, transport, prompt_client = await _run(
        SCENARIO_FEASIBILITY_SPEC,
        [[propose_event("feasibility", 1)], _task_completed_batch()],
        SCENARIO_FEASIBILITY_SPEC.scripted_client_decisions,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert _client_negotiation_steps(transport.requests) == [("ACCEPT", 1)]
    assert prompt_client.refill_data[0]["siteList"] == "Station 1,Station 2"


async def test_an_operator_summary_still_names_the_agreed_target() -> None:
    """A hand-written summary must not replace the deterministic agreed-value line."""
    decision = NegotiationDecision(
        action="accept",
        rationale="Accept the reduced rate.",
        filled_params={"guaranteedThroughputTarget": "{08:00-18:00,2Mbps}"},
        feasibility_summary="Accepted the proposal.",
    )
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport([[propose_event("feasibility", 1)], _task_completed_batch()]),
        user_request=SCENARIO_FEASIBILITY_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields=_INTENT_FIELDS,
    )

    content = prompt_client.accept_data[0].content
    assert isinstance(content, FeasibilityEndingContent)
    assert "{08:00~18:00,2Mbps}" in content.feasibility_summary
    assert "Accepted the proposal." in content.feasibility_summary


async def test_a_summary_that_contradicts_the_agreed_target_is_replaced() -> None:
    """A hand-written summary naming a different rate must not go out as the agreement."""
    decision = NegotiationDecision(
        action="accept",
        rationale="Accept the reduced rate.",
        filled_params={"guaranteedThroughputTarget": "{08:00-18:00,2Mbps}"},
        feasibility_summary="Accepted the rate reduction to {08:00~18:00,10Mbps}.",
    )
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport([[propose_event("feasibility", 1)], _task_completed_batch()]),
        user_request=SCENARIO_FEASIBILITY_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields=_INTENT_FIELDS,
    )

    content = prompt_client.accept_data[0].content
    assert isinstance(content, FeasibilityEndingContent)
    assert "10Mbps" not in content.feasibility_summary
    assert "{08:00~18:00,2Mbps}" in content.feasibility_summary


async def test_a_summary_that_already_names_the_target_is_not_duplicated() -> None:
    """The agreed value is added only when the hand-written summary omits it."""
    decision = NegotiationDecision(
        action="accept",
        rationale="Accept the reduced rate.",
        filled_params={"guaranteedThroughputTarget": "{08:00-18:00,2Mbps}"},
        feasibility_summary="Accepted the rate reduction to {08:00~18:00,2Mbps}.",
    )
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport([[propose_event("feasibility", 1)], _task_completed_batch()]),
        user_request=SCENARIO_FEASIBILITY_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields=_INTENT_FIELDS,
    )

    content = prompt_client.accept_data[0].content
    assert isinstance(content, FeasibilityEndingContent)
    assert content.feasibility_summary.count("2Mbps") == 1


async def test_a_feasibility_accept_is_rendered_from_typed_content() -> None:
    """The accept no longer round-trips through an LLM extraction of the client's own text.

    The live failure was: a hyphenated agreed value was dropped, the accept text degenerated to
    bare ``Accept.``, the extraction returned an empty summary and the run died. Building the
    typed ending content removes that whole path instead of special-casing its symptom.
    """
    decision = NegotiationDecision(
        action="accept",
        rationale="Accept the server's reduced rate.",
        filled_params={"guaranteedThroughputTarget": "{08:00-18:00,2Mbps}"},
    )
    result, _transport, prompt_client = await _run(
        SCENARIO_FEASIBILITY_SPEC,
        [[propose_event("feasibility", 1)], _task_completed_batch()],
        (decision,),
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert prompt_client.accept_texts == []
    content = prompt_client.accept_data[0].content
    assert isinstance(content, FeasibilityEndingContent)
    assert content.conclusion is NegotiationConclusion.ACCEPT
    assert "{08:00~18:00,2Mbps}" in content.feasibility_summary


async def test_a_feasibility_accept_without_values_still_carries_a_summary() -> None:
    """A bare confirm click must not produce an accept whose summary is empty."""
    decision = NegotiationDecision(action="accept", rationale="Accept the proposal.")
    _result, _transport, prompt_client = await _run(
        SCENARIO_FEASIBILITY_SPEC,
        [[propose_event("feasibility", 1)], _task_completed_batch()],
        (decision,),
    )

    content = prompt_client.accept_data[0].content
    assert isinstance(content, FeasibilityEndingContent)
    assert content.feasibility_summary.strip()


async def test_a_feasibility_accept_never_echoes_the_pre_negotiation_target() -> None:
    """The baseline target is what the server rejected; it must not be reported as agreed."""
    decision = NegotiationDecision(action="accept", rationale="Accept the proposal.")
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport([[propose_event("feasibility", 1)], _task_completed_batch()]),
        user_request=SCENARIO_FEASIBILITY_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields={**_INTENT_FIELDS, "guaranteedThroughputTarget": "{08:00~18:00,10Mbps}"},
    )

    content = prompt_client.accept_data[0].content
    assert isinstance(content, FeasibilityEndingContent)
    assert "10Mbps" not in content.feasibility_summary


async def test_the_live_task_t_shape_yields_the_canonical_agreed_target() -> None:
    """The vendor Task-T writes ``{08:00-18:00: 10Mbps}``; the accept must still name 2Mbps.

    This is the exact shape of the failing live run: a colon-form baseline target, a proposal
    that offers a rate in prose, and an Accept click with no values of its own.
    """
    decision = NegotiationDecision(action="accept", rationale="Accept the reduced rate.")
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport(
            [
                [
                    propose_event(
                        "feasibility",
                        1,
                        content="Proposal: Reduce rate target to 2Mbps or less.",
                    )
                ],
                _task_completed_batch(),
            ]
        ),
        user_request=SCENARIO_FEASIBILITY_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields={
            "operationType": "Modify",
            "region": "A2ATestRegion",
            "guaranteedThroughputTarget": "{08:00-18:00: 10Mbps}",
        },
    )

    assert prompt_client.refill_data[0]["guaranteedThroughputTarget"] == "{08:00~18:00,2Mbps}"
    content = prompt_client.accept_data[0].content
    assert isinstance(content, FeasibilityEndingContent)
    assert "{08:00~18:00,2Mbps}" in content.feasibility_summary
    assert "10Mbps" not in content.feasibility_summary


async def test_a_feasibility_accept_keeps_an_unchanged_target_in_the_refilled_task_t() -> None:
    """A proposal that names no rate leaves the agreed target untouched, not deleted.

    Withdrawing the baseline target would send a refilled Task-T without the throughput goal the
    task already had; the summary must still not claim it as a new agreement.
    """
    decision = NegotiationDecision(action="accept", rationale="Accept the adjusted outage window.")
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport(
            [
                [
                    propose_event(
                        "feasibility",
                        1,
                        content="Proposal: reduce the power outage duration to 4 hours or less.",
                    )
                ],
                _task_completed_batch(),
            ]
        ),
        user_request=SCENARIO_FEASIBILITY_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields={**_INTENT_FIELDS, "guaranteedThroughputTarget": "{08:00~18:00,10Mbps}"},
    )

    assert prompt_client.refill_data[0]["guaranteedThroughputTarget"] == "{08:00~18:00,10Mbps}"
    content = prompt_client.accept_data[0].content
    assert isinstance(content, FeasibilityEndingContent)
    assert "10Mbps" not in content.feasibility_summary


async def test_a_dropped_information_answer_is_not_replaced_by_the_baseline() -> None:
    """The accept must answer with the decision's value, never with the pre-existing baseline."""
    decision = NegotiationDecision(
        action="accept",
        rationale="The operator supplied the rate in their own wording.",
        filled_params={"guaranteedThroughputTarget": "10 Mbps during the outage"},
    )
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport([[propose_event("information", 1)], _task_completed_batch()]),
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields={**_INTENT_FIELDS, "guaranteedThroughputTarget": "{08:00~18:00,5Mbps}"},
    )

    content = prompt_client.accept_data[0].content
    assert isinstance(content, InformationEndingContent)
    assert [(item.name, item.value) for item in content.items] == [
        ("guaranteedThroughputTarget", "10 Mbps during the outage")
    ]


async def test_a_dropped_accept_value_is_traced() -> None:
    """A value the flow cannot send must appear in the trace, not vanish silently."""
    spans: list[dict[str, object]] = []
    decision = NegotiationDecision(
        action="accept",
        rationale="Accept the server's alternative.",
        filled_params={"guaranteedThroughputTarget": "raise the rate as much as possible"},
    )
    await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport(
            [[propose_event("feasibility", 1)], _task_completed_batch()]
        ),
        user_request=SCENARIO_FEASIBILITY_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields=_INTENT_FIELDS,
        trace_sink=spans.append,
    )

    dropped = [span for span in spans if span["kind"] == "decision_value_dropped"]
    assert dropped, "a dropped accepted value must produce a decision_value_dropped span"
    detail = dropped[0]["detail"]
    assert isinstance(detail, dict)
    assert any("guaranteedThroughputTarget" in item for item in detail["fields"])


async def test_a_feasibility_accept_derives_the_agreed_target_from_the_proposal() -> None:
    """The proposal's alternative is the agreement; the client can name it without a model."""
    decision = NegotiationDecision(action="accept", rationale="Accept the reduced rate.")
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport(
            [
                [
                    propose_event(
                        "feasibility",
                        1,
                        content=(
                            "## Infeasible Evaluation Details and Proposal\n"
                            "Proposal: Reduce rate target to 2Mbps or less."
                        ),
                    )
                ],
                _task_completed_batch(),
            ]
        ),
        user_request=SCENARIO_FEASIBILITY_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields={**_INTENT_FIELDS, "guaranteedThroughputTarget": "{08:00~18:00,10Mbps}"},
    )

    content = prompt_client.accept_data[0].content
    assert isinstance(content, FeasibilityEndingContent)
    assert "{08:00~18:00,2Mbps}" in content.feasibility_summary


async def test_a_target_accept_does_not_re_rate_the_baseline_from_prose() -> None:
    """A target negotiation must not invent a rate the decision never accepted.

    ``agreed_target_value`` is a feasibility derivation: it re-rates the operator's windows with
    the lowest rate the proposal mentions. Applying it to a target negotiation would name a plan
    the client never agreed to.
    """
    decision = NegotiationDecision(action="accept", rationale="Accept the server's adjustment.")
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport(
            [
                [
                    propose_event(
                        "target",
                        1,
                        content="The windows overlap. Reduce the rate to 2Mbps.",
                    )
                ],
                _task_completed_batch(),
            ]
        ),
        user_request=SCENARIO_TARGET_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields={**_INTENT_FIELDS, "guaranteedThroughputTarget": "{00:00~06:00,10Mbps}"},
    )

    content = prompt_client.accept_data[0].content
    assert isinstance(content, TargetEndingContent)
    assert "2Mbps" not in (content.confirmed_intent or "")


async def test_a_feasibility_accept_of_the_outage_alternative_does_not_claim_the_rate_cut() -> None:
    """When the decision takes the non-rate alternative, the summary must not name the rate cut."""
    decision = NegotiationDecision(
        action="accept",
        rationale="Accept the shorter outage.",
        filled_params={"backgroundEvent": "power outage from 08:00 to 12:00"},
    )
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport(
            [
                [
                    propose_event(
                        "feasibility",
                        1,
                        content=(
                            "Proposal: Reduce rate target to 2Mbps or less. If lowering the rate is "
                            "unacceptable, reduce power outage duration to 4 hours or less."
                        ),
                    )
                ],
                _task_completed_batch(),
            ]
        ),
        user_request=SCENARIO_FEASIBILITY_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields={**_INTENT_FIELDS, "guaranteedThroughputTarget": "{08:00~18:00,10Mbps}"},
    )

    content = prompt_client.accept_data[0].content
    assert isinstance(content, FeasibilityEndingContent)
    assert "2Mbps" not in content.feasibility_summary
    assert "power outage from 08:00 to 12:00" in content.feasibility_summary


async def test_a_target_accept_without_values_uses_the_decision_wording() -> None:
    """A plain Accept click on a target proposal must not fall back to a model extraction."""
    decision = NegotiationDecision(
        action="accept",
        rationale="Accept the server's adjusted target.",
    )
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport([[propose_event("target", 1)], _task_completed_batch()]),
        user_request=SCENARIO_TARGET_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields=_INTENT_FIELDS,
    )

    assert prompt_client.accept_texts == []
    content = prompt_client.accept_data[0].content
    assert isinstance(content, TargetEndingContent)
    assert "Accept the server's adjusted target." in (content.confirmed_intent or "")


async def test_a_target_confirmation_still_names_the_agreed_target() -> None:
    """The operator/model intent text must not replace the deterministic agreed-value line."""
    decision = NegotiationDecision(
        action="accept",
        rationale="Confirm the adjusted target.",
        filled_params={"guaranteedThroughputTarget": "{00:00-06:00,2Mbps}"},
        confirmed_intent="The client confirms the target reduction.",
    )
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport([[propose_event("target", 1)], _task_completed_batch()]),
        user_request=SCENARIO_TARGET_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields=_INTENT_FIELDS,
    )

    content = prompt_client.accept_data[0].content
    assert isinstance(content, TargetEndingContent)
    assert "{00:00~06:00,2Mbps}" in (content.confirmed_intent or "")
    assert "The client confirms the target reduction." in (content.confirmed_intent or "")


async def test_a_target_intent_mentioning_the_baseline_rate_is_kept() -> None:
    """Naming the previous rate as context is legitimate; only an invented rate is replaced."""
    decision = NegotiationDecision(
        action="accept",
        rationale="Confirm the adjusted target.",
        filled_params={"guaranteedThroughputTarget": "{00:00-06:00,2Mbps}"},
        confirmed_intent=(
            "Reduced the target from {00:00~06:00,10Mbps} to {00:00~06:00,2Mbps}."
        ),
    )
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport([[propose_event("target", 1)], _task_completed_batch()]),
        user_request=SCENARIO_TARGET_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields={**_INTENT_FIELDS, "guaranteedThroughputTarget": "{00:00~06:00,10Mbps}"},
    )

    content = prompt_client.accept_data[0].content
    assert isinstance(content, TargetEndingContent)
    assert "Reduced the target from" in (content.confirmed_intent or "")
    assert "2Mbps" in (content.confirmed_intent or "")


async def test_a_target_accept_names_a_target_that_did_not_change() -> None:
    """The decision's accepted target is named even when it equals the baseline value."""
    decision = NegotiationDecision(
        action="accept",
        rationale="Confirm the adjusted target.",
        filled_params={"guaranteedThroughputTarget": "{00:00~06:00,2Mbps}"},
    )
    prompt_client = FakePromptClient()
    await run_client_flow(
        prompt_client=prompt_client,
        transport=ScriptedTransport([[propose_event("target", 1)], _task_completed_batch()]),
        user_request=SCENARIO_TARGET_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((decision,)),
        language="en-US",
        intent_fields={**_INTENT_FIELDS, "guaranteedThroughputTarget": "{00:00~06:00,2Mbps}"},
    )

    content = prompt_client.accept_data[0].content
    assert isinstance(content, TargetEndingContent)
    assert "{00:00~06:00,2Mbps}" in (content.confirmed_intent or "")


async def test_a_target_accept_is_rendered_from_typed_content() -> None:
    decision = NegotiationDecision(
        action="accept",
        rationale="Accept the adjusted target.",
        filled_params={"guaranteedThroughputTarget": "{00:00-06:00,2Mbps}"},
    )
    result, _transport, prompt_client = await _run(
        SCENARIO_TARGET_SPEC,
        [[propose_event("target", 1)], _task_completed_batch()],
        (decision,),
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert prompt_client.accept_texts == []
    content = prompt_client.accept_data[0].content
    assert isinstance(content, TargetEndingContent)
    assert "{00:00~06:00,2Mbps}" in (content.confirmed_intent or "")


async def test_an_information_accept_is_rendered_from_typed_content() -> None:
    decision = NegotiationDecision(
        action="accept",
        rationale="The engineer supplied the area.",
        filled_params={"region": "Region B"},
    )
    result, _transport, prompt_client = await _run(
        INFORMATION_NEGOTIATION_SPEC,
        [[propose_event("information", 1)], _task_completed_batch()],
        (decision,),
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert prompt_client.accept_texts == []
    content = prompt_client.accept_data[0].content
    assert isinstance(content, InformationEndingContent)
    assert [(item.name, item.value) for item in content.items] == [("region", "Region B")]


def vendor_propose_event() -> dict[str, object]:
    """A vendor-shaped Negotiation-T message: short key, JSON envelope, no negotiationContext."""
    envelope = json.dumps(
        {
            "message": [
                {
                    "text": (
                        "## Feasibility Negotiation\n"
                        "Proposal: Reduce rate target to 2Mbps or less."
                    ),
                    "metadata": {
                        "responseMetadata": {
                            "performative": "PROPOSE",
                            "templateUri": "Negotiation-T/feasibility-negotiation/propose/v1",
                            "negotiationType": "FEASIBILITY",
                        }
                    },
                }
            ],
            "messageType": "ASK_USER",
        },
        ensure_ascii=False,
    )
    return {
        "kind": "message",
        "text": envelope,
        "context_id": CONTEXT_ID,
        "message_id": "vendor-propose-1",
        "metadata": {"Negotiation-T": envelope},
    }


async def test_a_vendor_frame_without_a_negotiation_context_is_answered() -> None:
    """The vendor may send the short key, a JSON envelope and no negotiationContext."""
    prompt_client = FakePromptClient()
    transport = ScriptedTransport([[vendor_propose_event()], _task_completed_batch()])
    result = await run_client_flow(
        prompt_client=prompt_client,
        transport=transport,
        user_request=SCENARIO_FEASIBILITY_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider(SCENARIO_FEASIBILITY_SPEC.scripted_client_decisions),
        language="en-US",
        intent_fields=_INTENT_FIELDS,
        infer_missing_negotiation_kind=True,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert _client_negotiation_steps(transport.requests) == [("ACCEPT", 1)]
    accept = MessageToDict(transport.requests[1].message.metadata)
    context = accept[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert context["round"] == 1
    assert context["id"] == CONTEXT_ID
    content = prompt_client.accept_data[0].content
    assert isinstance(content, FeasibilityEndingContent)


async def test_an_accept_continues_the_task_the_negotiation_belongs_to() -> None:
    """A negotiation reply names the Task it continues, also when the run created it."""
    result, transport, _ = await _run(
        SCENARIO_FEASIBILITY_SPEC,
        [[status_propose_event("feasibility", 1)], _task_completed_batch()],
        SCENARIO_FEASIBILITY_SPEC.scripted_client_decisions,
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    accept = transport.requests[1]
    assert accept.message.task_id == "task-1"


async def test_a_propose_embedded_in_a_status_update_is_answered() -> None:
    """The vendor NMS delivers the Negotiation-T propose as ``statusUpdate.status.message``."""
    result, transport, _ = await _run(
        SCENARIO_FEASIBILITY_SPEC,
        [[status_propose_event("feasibility", 1)], _task_completed_batch()],
        SCENARIO_FEASIBILITY_SPEC.scripted_client_decisions,
    )

    assert _client_negotiation_steps(transport.requests) == [("ACCEPT", 1)]
    assert result.final_state == FINAL_STATE_TASK_COMPLETED


async def test_a_working_status_carrying_negotiation_metadata_is_not_answered() -> None:
    """Only ``INPUT_REQUIRED`` hands the turn to the client; other states are observations."""
    result, transport, _ = await _run(
        SCENARIO_FEASIBILITY_SPEC,
        [
            [status_propose_event("feasibility", 1, state="TASK_STATE_WORKING")],
            _task_completed_batch(),
        ],
        SCENARIO_FEASIBILITY_SPEC.scripted_client_decisions,
    )

    assert _client_negotiation_steps(transport.requests) == []
    assert result.final_state == FINAL_STATE_INCOMPLETE


async def test_reject_scenario_ends_without_a_task() -> None:
    result, transport, prompt_client = await _run(
        SCENARIO_REJECT_SPEC,
        [
            [
                propose_event(
                    "information",
                    1,
                    content=("Please provide the following missing items: guaranteedThroughputTarget."),
                )
            ],
            [end_event()],
        ],
        SCENARIO_REJECT_SPEC.scripted_client_decisions,
    )

    assert result.final_state == FINAL_STATE_NEGOTIATION_ENDED
    assert result.task_id is None
    assert _client_negotiation_steps(transport.requests) == [("REJECT", 1)]
    assert "guaranteedThroughputTarget:" in prompt_client.reject_texts[0]
    assert prompt_client.refill_data == []


async def test_abort_scenario_keeps_countering_until_the_server_aborts() -> None:
    result, transport, prompt_client = await _run(
        SCENARIO_ABORT_SPEC,
        [
            [propose_event("target", 1)],
            [propose_event("target", 2)],
            [propose_event("target", 3)],
            [abort_event(3)],
        ],
        SCENARIO_ABORT_SPEC.scripted_client_decisions,
    )

    assert result.final_state == FINAL_STATE_NEGOTIATION_ABORTED
    assert result.task_id is None
    assert _client_negotiation_steps(transport.requests) == [
        ("PROPOSE", 1),
        ("PROPOSE", 2),
        ("PROPOSE", 3),
    ]
    assert len(prompt_client.propose_texts) == 3
    assert prompt_client.refill_data == []


async def test_counter_carries_the_sample_proposal() -> None:
    decision = NegotiationDecision(
        action="counter",
        rationale="Move the window.",
        counter_proposal="Use cell-001 00:00~06:00 at 30%.",
        counter_params={"cellId": "cell-001", "window": "00:00~06:00", "targetRate": "30"},
    )
    transport = ScriptedTransport([[propose_event("target", 1)], [abort_event(1)]])

    result = await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=transport,
        user_request="request",
        provider=ScriptedDecisionProvider([decision]),
        language="en-US",
        intent_fields=_INTENT_FIELDS,
    )

    counter_metadata = MessageToDict(transport.requests[1].message.metadata)
    assert counter_metadata[SAMPLE_PROPOSAL_METADATA_KEY] == {
        "cellId": "cell-001",
        "window": "00:00~06:00",
        "targetRate": "30",
    }
    assert counter_metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["round"] == 1
    assert result.final_state == FINAL_STATE_NEGOTIATION_ABORTED


async def test_initial_request_matches_the_old_message_shape() -> None:
    _, transport, _ = await _run(
        INFORMATION_NEGOTIATION_SPEC,
        [[propose_event("information", 1)], _task_completed_batch()],
        INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions,
    )

    request = transport.requests[0]
    metadata = MessageToDict(request.message.metadata)
    assert request.message.parts[0].text == TASK_REQUEST_LABEL
    assert metadata[TASK_T_EXTENSION_URI].startswith("Task-T prompt for:")
    assert metadata[TEMPLATE_URI_METADATA_KEY] == "Task-T/network-layer/ran-energy-saving/v1"
    service_parameters = transport.call_contexts[0].service_parameters
    assert service_parameters is not None
    # Both extensions are declared: without Negotiation-T the vendor answers a missing slot with a
    # bare Message whose empty metadata reads like a negotiation-ended message.
    assert set(service_parameters["A2A-Extensions"].split(",")) == {
        TASK_T_EXTENSION_URI,
        NEGOTIATION_T_EXTENSION_URI,
    }


async def test_every_send_carries_the_configured_call_timeout() -> None:
    """The flow must set ``ClientCallContext.timeout`` on the initial and every follow-up send."""
    transport = ScriptedTransport(
        [
            [propose_event("target", 1)],
            [propose_event("target", 2)],
            _task_completed_batch(),
        ]
    )

    await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=transport,
        user_request=SCENARIO_TARGET_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider(SCENARIO_TARGET_SPEC.scripted_client_decisions),
        language="en-US",
        intent_fields=_INTENT_FIELDS,
        call_timeout_seconds=123.0,
    )

    assert len(transport.call_contexts) == 3  # TASK_T, counter PROPOSE, ACCEPT
    assert [context.timeout for context in transport.call_contexts] == [123.0, 123.0, 123.0]


@pytest.mark.parametrize("terminal", ["reject", "abort"])
async def test_terminal_sends_carry_the_configured_call_timeout(terminal: str) -> None:
    """Reject and abort responses are ordinary sends and must carry the same budget."""
    if terminal == "reject":
        batches = [[propose_event("information", 1)], [end_event()]]
        decisions = SCENARIO_REJECT_SPEC.scripted_client_decisions
    else:
        batches = [[propose_event("target", 1)], [end_event()]]
        decisions = (NegotiationDecision(action="abort", rationale="Stop the negotiation."),)
    transport = ScriptedTransport(batches)

    result = await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=transport,
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider(decisions),
        language="en-US",
        call_timeout_seconds=77.0,
    )

    assert result.final_state == FINAL_STATE_NEGOTIATION_ENDED
    assert len(transport.call_contexts) == 2  # initial TASK_T + terminal REJECT/ABORT
    assert [context.timeout for context in transport.call_contexts] == [77.0, 77.0]


async def test_call_timeout_defaults_to_the_documented_budget() -> None:
    transport = ScriptedTransport([[propose_event("information", 1)], _task_completed_batch()])

    await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=transport,
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider(INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions),
        language="en-US",
        intent_fields=_INTENT_FIELDS,
    )

    assert all(context.timeout == DEFAULT_CALL_TIMEOUT_SECONDS for context in transport.call_contexts)


def test_webhook_payload_tolerates_the_vendor_context_wrapper() -> None:
    """The real NMS appends a top-level contextId, which is not a StreamResponse field."""
    message = Message(message_id="msg-1", role=Role.ROLE_AGENT, context_id=CONTEXT_ID)
    message.parts.add(text="Task modification succeeded")
    payload = MessageToDict(
        StreamResponse(
            status_update=TaskStatusUpdateEvent(
                task_id="task-1",
                context_id=CONTEXT_ID,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED, message=message),
            )
        )
    )
    payload["contextId"] = CONTEXT_ID

    event = normalize_webhook_payload(payload)

    assert event["kind"] == "status"
    assert event["state"] == "TASK_STATE_SUBMITTED"
    assert event["text"] == "Task modification succeeded"


def test_webhook_payload_normalization_matches_sse_shape() -> None:
    message = Message(message_id="msg-1", role=Role.ROLE_AGENT, context_id=CONTEXT_ID)
    message.parts.add(text="hello")
    message.metadata[NEGOTIATION_T_EXTENSION_URI] = "content"
    payload = MessageToDict(StreamResponse(message=message))

    event = normalize_webhook_payload(payload)

    assert event == {
        "kind": "message",
        "text": "hello",
        "context_id": CONTEXT_ID,
        "message_id": "msg-1",
        "metadata": {NEGOTIATION_T_EXTENSION_URI: "content"},
    }


class _FakeA2AClient:
    """Async-generator ``send_message`` double serving pre-built StreamResponse objects."""

    def __init__(self, responses: list[StreamResponse]) -> None:
        self._responses = responses
        self.requests: list[SendMessageRequest] = []

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext,
    ) -> AsyncIterator[StreamResponse]:
        self.requests.append(request)
        del context
        for response in self._responses:
            yield response


async def test_sse_transport_stops_at_the_first_message() -> None:
    message = Message(message_id="msg-1", role=Role.ROLE_AGENT, context_id=CONTEXT_ID)
    message.parts.add(text=END_MESSAGE_TEXT)
    client = _FakeA2AClient([StreamResponse(message=message)])

    events = await SseClientTransport(client).send(SendMessageRequest(), ClientCallContext())

    assert [event["kind"] for event in events] == ["message"]


async def test_webhook_transport_returns_the_rpc_reply_without_draining_the_queue() -> None:
    message = Message(message_id="msg-1", role=Role.ROLE_AGENT, context_id=CONTEXT_ID)
    message.parts.add(text="proposal")
    client = _FakeA2AClient([StreamResponse(message=message)])
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    queue.put_nowait({"message": MessageToDict(message)})

    events = await WebhookClientTransport(
        client,
        queue=queue,
        push_url="http://127.0.0.1:9000/webhook",
        push_token="token",
    ).send(SendMessageRequest(), ClientCallContext())

    assert [event["kind"] for event in events] == ["message"]
    assert queue.qsize() == 1


async def test_webhook_transport_registers_push_on_the_send_that_creates_the_local_task() -> None:
    """Our own server creates the Task when it accepts the negotiation, so that reply registers."""
    message = Message(message_id="msg-1", role=Role.ROLE_AGENT, context_id=CONTEXT_ID)
    message.parts.add(text="proposal")
    client = _FakeA2AClient([StreamResponse(message=message)])
    transport = WebhookClientTransport(
        client,
        queue=asyncio.Queue(),
        push_url="http://127.0.0.1:9000/webhook",
        push_token="token",
    )

    accept = SendMessageRequest()
    accept.message.metadata[NEGOTIATION_T_EXTENSION_URI] = "content"
    await transport.send(accept, ClientCallContext())

    assert client.requests[0].configuration.HasField("task_push_notification_config") is True


async def test_webhook_transport_keeps_asking_for_an_immediate_reply_on_a_continuation() -> None:
    """The local server answers each send, so the local transport keeps the non-blocking reply.

    Only the vendor NMS rejects ``returnImmediately`` on a Modify or a negotiation reply
    (``NmsWebhookTransport``). Our own server leaves a long Task WORKING after its report, so the
    local transport must not adopt the vendor rule: a blocking send would hold the caller.
    """
    message = Message(message_id="msg-1", role=Role.ROLE_AGENT, context_id=CONTEXT_ID)
    message.parts.add(text="proposal")
    client = _FakeA2AClient([StreamResponse(message=message)])
    request = SendMessageRequest()
    request.message.task_id = "task-1"

    await WebhookClientTransport(
        client,
        queue=asyncio.Queue(),
        push_url="http://127.0.0.1:9000/webhook",
        push_token="token",
    ).send(request, ClientCallContext())

    assert client.requests[0].configuration.return_immediately is True


async def test_webhook_transport_stamps_and_binds_its_context_before_sending() -> None:
    """A client-assigned context makes every push routable before the Task reply arrives."""
    message = Message(message_id="msg-1", role=Role.ROLE_AGENT, context_id="ctx-run")
    message.parts.add(text=END_MESSAGE_TEXT)
    client = _FakeA2AClient([StreamResponse(message=message)])
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    bound: list[tuple[str | None, str | None]] = []

    await WebhookClientTransport(
        client,
        queue=queue,
        push_url="http://127.0.0.1:9000/webhook",
        push_token="token",
        context_id="ctx-run",
        bind=lambda task_id, context_id: bound.append((task_id, context_id)),
    ).send(SendMessageRequest(), ClientCallContext())

    assert client.requests[0].message.context_id == "ctx-run"
    assert bound[0] == (None, "ctx-run")


async def test_webhook_transport_binds_the_reply_identity_for_the_router() -> None:
    """The run's queue is only addressable once the transport reports the server identities."""
    message = Message(message_id="msg-1", role=Role.ROLE_AGENT, context_id=CONTEXT_ID)
    message.parts.add(text="proposal")
    client = _FakeA2AClient([StreamResponse(message=message)])
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    bound: list[tuple[str | None, str | None]] = []

    await WebhookClientTransport(
        client,
        queue=queue,
        push_url="http://127.0.0.1:9000/webhook",
        push_token="token",
        bind=lambda task_id, context_id: bound.append((task_id, context_id)),
    ).send(SendMessageRequest(), ClientCallContext())

    assert bound == [(None, CONTEXT_ID)]


async def test_webhook_transport_consumes_pushed_events_until_a_terminal_state() -> None:
    client = _FakeA2AClient([])
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    for event in _task_completed_batch():
        queue.put_nowait(_to_webhook_payload(event))

    events = await WebhookClientTransport(
        client,
        queue=queue,
        push_url="http://127.0.0.1:9000/webhook",
        push_token="token",
        idle_timeout=0.5,
    ).send(SendMessageRequest(), ClientCallContext())

    assert [event["kind"] for event in events] == ["status", "artifact"]
    assert events[0]["state"] == "TASK_STATE_WORKING"
    assert queue.qsize() == 1  # the terminal event after the report artifact is not consumed


async def test_webhook_transport_idle_backstop_starts_at_the_last_event() -> None:
    """The send duration is not charged as silence, and a push does not restart the clock."""

    class _SlowA2AClient:
        async def send_message(
            self,
            request: SendMessageRequest,
            *,
            context: ClientCallContext,
        ) -> AsyncIterator[StreamResponse]:
            del request, context
            await asyncio.sleep(0.45)
            yield StreamResponse(
                task=Task(
                    id="task-1",
                    context_id=CONTEXT_ID,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                )
            )

    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    transport = WebhookClientTransport(
        _SlowA2AClient(),
        queue=queue,
        push_url="http://127.0.0.1:9000/webhook",
        push_token="token",
        idle_timeout=0.6,
    )

    async def push_after_the_reply() -> None:
        await asyncio.sleep(0.75)  # 0.45 s send + 0.3 s of silence after the reply
        queue.put_nowait(_to_webhook_payload(status_event("TASK_STATE_WORKING")))

    pusher = asyncio.create_task(push_after_the_reply())
    started = time.monotonic()
    events = await asyncio.wait_for(
        transport.send(SendMessageRequest(), ClientCallContext()),
        timeout=3.0,
    )
    elapsed = time.monotonic() - started
    await pusher

    assert [event.get("state") for event in events] == ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"]
    # Reply + one budget = 1.05 s; a clock restarted by the 0.3 s-later push ends at 1.35 s.
    assert elapsed < 1.2


class _PerSendFakeA2AClient:
    """Fake a2a client yielding one response turn per ``send_message`` call."""

    def __init__(self, turns: list[list[StreamResponse]]) -> None:
        self._turns = list(turns)

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext,
    ) -> AsyncIterator[StreamResponse]:
        del request, context
        for response in self._turns.pop(0):
            yield response


async def test_webhook_mode_accept_reports_task_completed_after_the_artifact() -> None:
    """The final state must come from the last phase, not from the earlier proposal message."""
    proposal = Message(message_id="propose-1", role=Role.ROLE_AGENT, context_id=CONTEXT_ID)
    proposal.parts.add(text="proposal")
    proposal.metadata[NEGOTIATION_T_EXTENSION_URI] = "Please provide the region."
    proposal.metadata[TEMPLATE_URI_METADATA_KEY] = INFORMATION_NEGOTIATION_PROPOSE_URI
    proposal.metadata[NEGOTIATION_CONTEXT_METADATA_KEY] = {
        "id": CONTEXT_ID,
        "round": 1,
        "maxRounds": 3,
        "performative": "PROPOSE",
    }
    client = _PerSendFakeA2AClient([[StreamResponse(message=proposal)], []])
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    for event in (status_event("TASK_STATE_WORKING"), artifact_event()):
        queue.put_nowait(_to_webhook_payload(event))
    transport = WebhookClientTransport(
        client,
        queue=queue,
        push_url="http://127.0.0.1:9000/webhook",
        push_token="token",
        idle_timeout=0.5,
    )

    result = await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=transport,
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider(INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions),
        language="en-US",
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert result.task_id == "task-1"
    assert result.report is not None
    assert result.report["text"] == "report"


def _to_webhook_payload(event: dict[str, object]) -> dict[str, object]:
    """Convert one fake normalized event into a webhook JSON body via the protobuf types."""
    if event["kind"] == "status":
        from a2a.types import TaskStatus, TaskStatusUpdateEvent

        return MessageToDict(
            StreamResponse(
                status_update=TaskStatusUpdateEvent(
                    task_id=str(event["task_id"]),
                    context_id=str(event["context_id"]),
                    status=TaskStatus(state=str(event["state"])),
                )
            )
        )
    if event["kind"] == "artifact":
        from a2a.types import Artifact, TaskArtifactUpdateEvent

        artifact = Artifact(artifact_id=str(event["artifact_id"]), name=str(event["name"]))
        artifact.parts.add(text="report")
        return MessageToDict(
            StreamResponse(
                artifact_update=TaskArtifactUpdateEvent(
                    task_id=str(event["task_id"]),
                    context_id=str(event["context_id"]),
                    artifact=artifact,
                )
            )
        )
    raise AssertionError(f"Unsupported fake event: {event['kind']}")


async def test_real_sdk_client_runs_the_information_flow_offline(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flow works with the real A2ATClient and the scripted LLM (no network)."""
    from a2a_t.client.a2at_client import A2ATClient
    from demo.composition import install_offline_provider
    from protocol.server_app import prepare_sdk_env_path

    install_offline_provider()
    # The scripted provider resolves its language from the process environment (the demo launcher
    # sets it per process); the test pins it to the language of the offline configuration.
    monkeypatch.setenv("A2AT_LANGUAGE", offline_config.language)
    env_path: Path = prepare_sdk_env_path(offline_config)
    prompt_client = A2ATClient(env_path=env_path)
    transport = ScriptedTransport(
        [
            [propose_event("information", 1)],
            _task_completed_batch(),
        ]
    )

    result = await run_client_flow(
        prompt_client=prompt_client,
        transport=transport,
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider(INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions),
        language="en-US",
    )

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert _client_negotiation_steps(transport.requests) == [("ACCEPT", 1)]
    initial_metadata = MessageToDict(transport.requests[0].message.metadata)
    assert initial_metadata[TASK_T_EXTENSION_URI]
    accept_metadata = MessageToDict(transport.requests[1].message.metadata)
    assert accept_metadata[TASK_T_EXTENSION_URI]


async def test_event_sink_sees_every_event_and_the_decision_hook_the_proposal() -> None:
    seen: list[dict[str, object]] = []
    requested: list[tuple[str, int]] = []

    result = await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport([[propose_event("information", 1)], _task_completed_batch()]),
        user_request="request",
        provider=ScriptedDecisionProvider(INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions),
        language="en-US",
        event_sink=seen.append,
        on_decision_requested=lambda propose, session: requested.append((propose.kind, session.round)),
    )

    assert seen == result.events
    assert requested == [("information", 1)]


async def test_proposal_view_carries_the_sample_area_options() -> None:
    requested: list[NegotiationProposeView] = []
    event = propose_event("information", 1)
    metadata = event["metadata"]
    assert isinstance(metadata, dict)
    metadata[SAMPLE_NEGOTIATION_OPTIONS_METADATA_KEY] = {
        "field": "region",
        "requested": "Region Z",
        "options": [
            {
                "value": "Region A",
                "labels": {"en-US": "Region A"},
                "score": 0.1,
                "recommended": False,
            }
        ],
    }

    await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport([[event], _task_completed_batch()]),
        user_request="request",
        provider=ScriptedDecisionProvider(INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions),
        language="en-US",
        on_decision_requested=lambda propose, session: requested.append(propose),
    )

    assert len(requested) == 1
    options = requested[0].options
    assert options is not None
    assert options.requested == "Region Z"
    assert options.options[0].label("en-US") == "Region A"


async def test_decision_hook_fires_before_the_response_is_sent() -> None:
    order: list[str] = []
    spans: list[dict[str, object]] = []

    class RecordingTransport(ScriptedTransport):
        async def send(
            self,
            request: SendMessageRequest,
            call_context: ClientCallContext,
        ) -> list[dict[str, object]]:
            metadata = MessageToDict(request.message.metadata)
            context = metadata.get(NEGOTIATION_CONTEXT_METADATA_KEY)
            if isinstance(context, Mapping):
                order.append(f"send:{context['performative']}")
            else:
                order.append("send:TASK_T")
            return await super().send(request, call_context)

    def on_decision_made(
        decision: NegotiationDecision,
        propose: NegotiationProposeView,
        session: NegotiationContext,
    ) -> None:
        del propose
        order.append(f"decision:{decision.action}:R{session.round}")

    result = await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=RecordingTransport([[propose_event("information", 1)], _task_completed_batch()]),
        user_request="request",
        provider=ScriptedDecisionProvider(INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions),
        language="en-US",
        on_decision_made=on_decision_made,
        trace_sink=spans.append,
    )

    assert order == ["send:TASK_T", "decision:accept:R1", "send:ACCEPT"]
    assert result.decisions[0].action == "accept"
    # The callback owns the decision row, so the flow must not emit an adjacent duplicate span.
    assert [span["kind"] for span in spans] == [
        "task_prompt_rendered",
        "message_sent",
        "decision_requested",
        "message_sent",
    ]
    assert [span["phase"] for span in spans] == [
        "task_prompt",
        "task_validation",
        "negotiation",
        "resolution",
    ]
    assert spans[1]["direction"] == "client->server"
    assert spans[2]["round"] == 1


async def test_decision_span_is_emitted_only_without_a_consumer_callback() -> None:
    """CLI mode (no callback) still gets one decision row; callback consumers get exactly one."""
    spans: list[dict[str, object]] = []
    decisions: list[NegotiationDecision] = []

    await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport([[propose_event("information", 1)], _task_completed_batch()]),
        user_request="request",
        provider=ScriptedDecisionProvider(INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions),
        language="en-US",
        on_decision_made=lambda decision, propose, session: decisions.append(decision),
        trace_sink=spans.append,
    )

    assert [decision.action for decision in decisions] == ["accept"]
    assert [span["kind"] for span in spans].count("decision_made") == 0

    cli_spans: list[dict[str, object]] = []
    await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport([[propose_event("information", 1)], _task_completed_batch()]),
        user_request="request",
        provider=ScriptedDecisionProvider(INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions),
        language="en-US",
        trace_sink=cli_spans.append,
    )

    decision_spans = [span for span in cli_spans if span["kind"] == "decision_made"]
    assert len(decision_spans) == 1
    assert decision_spans[0]["phase"] == "resolution"
    detail = decision_spans[0]["detail"]
    assert isinstance(detail, dict)
    assert detail["action"] == "accept"


def _scenario_batches(scenario_name: str) -> list[list[dict[str, object]]]:
    """Return the transport batches that replay one scenario's server behavior."""
    if scenario_name in {"night", "outage"}:
        return [_task_completed_batch()]
    if scenario_name in {"information", "unknown_area"}:
        return [[propose_event("information", 1)], _task_completed_batch()]
    if scenario_name == "target":
        return [
            [propose_event("target", 1)],
            [propose_event("target", 2)],
            _task_completed_batch(),
        ]
    if scenario_name == "feasibility":
        return [[propose_event("feasibility", 1)], _task_completed_batch()]
    if scenario_name == "reject":
        return [
            [
                propose_event(
                    "information",
                    1,
                    content="Please provide the following missing items: guaranteedThroughputTarget.",
                )
            ],
            [end_event()],
        ]
    if scenario_name == "abort":
        return [
            [propose_event("target", 1)],
            [propose_event("target", 2)],
            [propose_event("target", 3)],
            [abort_event(3)],
        ]
    raise AssertionError(f"Unsupported scenario: {scenario_name}")


@pytest.mark.parametrize("scenario_name", SUPPORTED_SCENARIO_NAMES)
async def test_trace_phases_and_steps_for_every_scenario(scenario_name: str) -> None:
    """Every scenario exposes ordered phases with contiguous per-phase steps and unique ids."""
    scenario = get_scenario(scenario_name)
    spans: list[dict[str, object]] = []

    result = await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport(_scenario_batches(scenario_name)),
        user_request=scenario.user_request["en-US"],
        provider=ScriptedDecisionProvider(scenario.scripted_client_decisions),
        language="en-US",
        enforce_required_fields=(scenario_name == "information"),
        trace_sink=spans.append,
    )

    assert result.final_state == scenario.expected_final_state
    assert [decision.action for decision in result.decisions] == [
        decision.action for decision in scenario.scripted_client_decisions
    ]
    span_ids = [span["spanId"] for span in spans]
    assert len(span_ids) == len(set(span_ids))
    assert all(str(span_id).startswith("flow_") for span_id in span_ids)

    expected_phases = {PHASE_TASK_PROMPT}
    if scenario.expected_final_state != "input_invalid":
        expected_phases.add(PHASE_TASK_VALIDATION)
    if scenario.expected_sequence:
        expected_phases.add(PHASE_NEGOTIATION)
    if scenario_name != "abort" and scenario.expected_sequence:
        expected_phases.add(PHASE_RESOLUTION)
    assert {str(span["phase"]) for span in spans} == expected_phases

    order: list[str] = []
    for span in spans:
        phase = str(span["phase"])
        if phase not in order:
            order.append(phase)
    assert order == [phase for phase in PHASES if phase in expected_phases]

    grouped: dict[str, list[object]] = {}
    for span in spans:
        grouped.setdefault(str(span["phase"]), []).append(span["phaseStep"])
    for steps in grouped.values():
        assert steps == list(range(1, len(steps) + 1))


async def test_counter_decision_stays_in_the_negotiation_phase() -> None:
    spans: list[dict[str, object]] = []

    await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport([[propose_event("target", 1)], [abort_event(1)]]),
        user_request="request",
        provider=ScriptedDecisionProvider([SCENARIO_TARGET_SPEC.scripted_client_decisions[0]]),
        language="en-US",
        trace_sink=spans.append,
    )

    decision_made = next(span for span in spans if span["kind"] == "decision_made")
    counter_sent = [span for span in spans if span["kind"] == "message_sent"][-1]
    assert decision_made["phase"] == "negotiation"
    assert counter_sent["phase"] == "negotiation"
    counter_detail = counter_sent["detail"]
    assert isinstance(counter_detail, dict)
    assert counter_detail["performative"] == "PROPOSE"


async def test_sse_transport_forwards_events_to_its_sink_as_they_arrive() -> None:
    proposal = Message(message_id="propose-1", role=Role.ROLE_AGENT, context_id=CONTEXT_ID)
    proposal.parts.add(text="proposal")
    proposal.metadata[NEGOTIATION_T_EXTENSION_URI] = "Please provide the region."
    proposal.metadata[TEMPLATE_URI_METADATA_KEY] = INFORMATION_NEGOTIATION_PROPOSE_URI
    proposal.metadata[NEGOTIATION_CONTEXT_METADATA_KEY] = {
        "id": CONTEXT_ID,
        "round": 1,
        "maxRounds": 3,
        "performative": "PROPOSE",
    }
    end = Message(message_id="end", role=Role.ROLE_AGENT, context_id=CONTEXT_ID)
    end.parts.add(text=END_MESSAGE_TEXT)
    seen: list[dict[str, object]] = []
    client = _PerSendFakeA2AClient([[StreamResponse(message=proposal)], [StreamResponse(message=end)]])
    transport = SseClientTransport(client)

    result = await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=transport,
        user_request="request",
        provider=ScriptedDecisionProvider(INFORMATION_NEGOTIATION_SPEC.scripted_client_decisions),
        language="en-US",
        event_sink=seen.append,
    )

    assert seen == result.events
    assert [event["kind"] for event in seen] == ["message", "message"]
    assert result.final_state == FINAL_STATE_NEGOTIATION_ENDED


class _UngroundedProvider:
    """Test double that marks itself as a live automatic decision source."""

    requires_grounding = True

    def __init__(self) -> None:
        self.calls = 0

    async def respond(self, propose: NegotiationProposeView, session: NegotiationContext) -> NegotiationDecision:
        del propose, session
        self.calls += 1
        return NegotiationDecision(
            action="accept",
            rationale="accept",
            filled_params={
                "guaranteedThroughputTarget": "{00:00~07:00,99Mbps}",
            },
        )


async def test_an_ungrounded_auto_accept_is_rejected_instead_of_aborted() -> None:
    """An automatic decision that cannot be grounded refuses; it does not end the negotiation."""
    provider = _UngroundedProvider()
    transport = ScriptedTransport([[propose_event("information", 1)], [end_event()]])

    result = await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=transport,
        user_request="The area is the Songshanhu Administration Committee. {00:00~07:00,2Mbps}",
        provider=provider,
        language="en-US",
        intent_fields={
            "operationType": "Create",
            "region": "Songshanhu Administration Committee",
            "guaranteedThroughputTarget": "{00:00~07:00,2Mbps}",
        },
    )

    assert result.final_state == "decision_ungrounded"
    assert provider.calls == 2
    assert _client_negotiation_steps(transport.requests) == [("REJECT", 1)]
    assert result.error is not None
    assert "guaranteedThroughputTarget" in result.error


class _RegionChangeProvider:
    """A live automatic decision that tries to replace the operator's region."""

    requires_grounding = True

    def __init__(self) -> None:
        self.calls = 0

    async def respond(self, propose: NegotiationProposeView, session: NegotiationContext) -> NegotiationDecision:
        del propose, session
        self.calls += 1
        return NegotiationDecision(
            action="accept",
            rationale="accept",
            filled_params={"region": "A2ATestRegion"},
        )


async def test_a_model_entity_change_is_dropped_and_the_information_accept_is_refused() -> None:
    """The model may not answer a rejected region with a value the server never offered."""
    provider = _RegionChangeProvider()
    transport = ScriptedTransport(
        [
            [propose_event("information", 1, content="Please provide the correct region name.")],
            [end_event()],
        ]
    )

    result = await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=transport,
        user_request="The area is the Songshanhu Administration Committee.",
        provider=provider,
        language="en-US",
        intent_fields={"operationType": "Create", "region": "Songshanhu Administration Committee"},
    )

    assert result.final_state == "decision_ungrounded"
    assert provider.calls == 1
    assert _client_negotiation_steps(transport.requests) == [("REJECT", 1)]
    assert result.error is not None
    assert "region" in result.error


class _RejectingProvider:
    """A live automatic decision that refuses a feasible server alternative."""

    requires_grounding = True

    def __init__(self) -> None:
        self.calls = 0

    async def respond(self, propose: NegotiationProposeView, session: NegotiationContext) -> NegotiationDecision:
        del propose, session
        self.calls += 1
        return NegotiationDecision(
            action="reject",
            rationale="The region cannot be confirmed.",
        )


_SITE_INTENT = {
    "operationType": "Modify",
    "region": "A2ATestRegion",
    "siteList": '["LNR_KPI_7745","NES_27A_321071"]',
    "backgroundEvent": "power outage from 08:00 to 18:00",
    "guaranteedThroughputTarget": "{08:00~18:00,10Mbps}",
}


def _feasibility_transport() -> ScriptedTransport:
    return ScriptedTransport(
        [
            [
                propose_event(
                    "feasibility",
                    1,
                    content=(
                        "Reduce rate target to 2Mbps or less. If lowering the rate is unacceptable, "
                        "reduce power outage duration to 4 hours or less."
                    ),
                )
            ],
            [end_event()],
        ]
    )


async def test_a_feasible_alternative_is_accepted_even_when_the_model_rejects() -> None:
    """Automatic mode accepts the server's feasible alternative by default."""
    provider = _RejectingProvider()
    transport = _feasibility_transport()
    prompt_client = FakePromptClient()

    result = await run_client_flow(
        prompt_client=prompt_client,
        transport=transport,
        user_request="Reduce the guaranteed rate to 10Mbps during the outage.",
        provider=provider,
        language="en-US",
        intent_fields=_SITE_INTENT,
    )

    assert _client_negotiation_steps(transport.requests) == [("ACCEPT", 1)]
    content = prompt_client.accept_data[-1].content
    assert isinstance(content, FeasibilityEndingContent)
    assert "2Mbps" in content.feasibility_summary
    assert result.final_state == FINAL_STATE_NEGOTIATION_ENDED


async def test_a_server_alternative_below_the_configured_floor_is_not_forced_to_accept() -> None:
    provider = _RejectingProvider()
    transport = _feasibility_transport()

    result = await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=transport,
        user_request="Reduce the guaranteed rate to 10Mbps during the outage.",
        provider=provider,
        language="en-US",
        intent_fields=_SITE_INTENT,
        client_policy=ClientPolicy(min_throughput_mbps=5),
    )

    assert _client_negotiation_steps(transport.requests) == [("REJECT", 1)]
    assert result.final_state == FINAL_STATE_NEGOTIATION_ENDED


def _propose_without_template_uri(kind: str, *, content: str) -> dict[str, object]:
    """Build the vendor's continuation propose: negotiation metadata without ``templateUri``."""
    event = propose_event(kind, 1, content=content)
    metadata = event["metadata"]
    assert isinstance(metadata, dict)
    metadata.pop(TEMPLATE_URI_METADATA_KEY)
    assert NEGOTIATION_CONTEXT_METADATA_KEY in metadata
    return event


@pytest.mark.parametrize(
    ("kind", "heading"),
    [
        ("information", "## Information Negotiation\nPlease supplement the relevant content."),
        ("target", "## Target Negotiation\nPlease confirm the clarified target."),
        ("feasibility", "## Feasibility Negotiation\nPlease assess whether the goal is achievable."),
        ("information", "## 信息协商\n请补充相关内容。"),
        ("target", "## 目标协商\n请确认目标。"),
        ("feasibility", "## 可行性协商\n请评估目标是否可达。"),
    ],
)
async def test_a_propose_without_template_uri_is_typed_from_its_heading(
    kind: str, heading: str
) -> None:
    """The vendor omits ``templateUri`` on the proposal that continues the negotiation."""
    requested: list[tuple[str, str | None]] = []
    result = await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport(
            [[_propose_without_template_uri(kind, content=heading)], [end_event()]]
        ),
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((NegotiationDecision(action="reject", rationale="No."),)),
        language="en-US",
        on_decision_requested=lambda propose, session: requested.append(
            (propose.kind, propose.template_uri)
        ),
        infer_missing_negotiation_kind=True,
    )

    assert requested == [(kind, _PROPOSE_URIS[kind])]
    assert result.decisions[0].action == "reject"


async def test_a_propose_without_template_uri_and_without_a_heading_still_fails() -> None:
    transport = ScriptedTransport(
        [[_propose_without_template_uri("information", content="Please provide the region.")]]
    )

    with pytest.raises(ClientFlowError, match="carries no template URI"):
        await run_client_flow(
            prompt_client=FakePromptClient(),
            transport=transport,
            user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
            provider=ScriptedDecisionProvider((NegotiationDecision(action="reject", rationale="No."),)),
            language="en-US",
            infer_missing_negotiation_kind=True,
        )


async def test_a_propose_without_template_uri_fails_when_inference_is_off() -> None:
    transport = ScriptedTransport(
        [[_propose_without_template_uri("information", content="## Information Negotiation")]]
    )

    with pytest.raises(ClientFlowError, match="carries no template URI"):
        await run_client_flow(
            prompt_client=FakePromptClient(),
            transport=transport,
            user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
            provider=ScriptedDecisionProvider((NegotiationDecision(action="reject", rationale="No."),)),
            language="en-US",
        )


def _vendor_propose_content(
    *,
    negotiation_type: str | None = "INFORMATION",
    template_uri: str = "Negotiation-T/network-layer/ran-energy-saving/v1/region-negotiation/propose/v1",
    text: str = "Region 'Region Z' is not recognized. Please provide the correct region name.",
    as_array: bool = False,
) -> str:
    """Build the vendor's Negotiation-T body: one JSON string with a nested ``responseMetadata``.

    ``as_array`` reproduces the envelope the real NMS sends on the proposal that continues a
    negotiation: the document root is a one-element list of message bundles.
    """
    response_metadata: dict[str, object] = {
        "performative": "PROPOSE",
        "templateUri": template_uri,
    }
    if negotiation_type is not None:
        response_metadata["negotiationType"] = negotiation_type
    document: object = {
        "message": [
            {"text": text, "metadata": {"responseMetadata": response_metadata}},
        ],
        "messageType": "ASK_USER",
    }
    return json.dumps([document] if as_array else document)


def _field_feasibility_frame() -> str:
    """Build the frame observed in the field: array root, nested FEASIBILITY typed metadata."""
    return _vendor_propose_content(
        negotiation_type="FEASIBILITY",
        template_uri="Negotiation-T/feasibility-negotiation/propose/v1",
        text=(
            "## Feasibility Negotiation\n"
            "The current rate goal cannot simultaneously satisfy the power outage duration "
            "requirement. Reduce the rate target to 2Mbps or less."
        ),
        as_array=True,
    )


async def _requested_propose(content: str) -> list[tuple[str, str | None]]:
    """Run one round against ``content`` and return what the decision provider was shown."""
    requested: list[tuple[str, str | None]] = []
    await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport(
            [[_propose_without_template_uri("information", content=content)], [end_event()]]
        ),
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((NegotiationDecision(action="reject", rationale="No."),)),
        language="en-US",
        on_decision_requested=lambda propose, session: requested.append(
            (propose.kind, propose.template_uri)
        ),
        infer_missing_negotiation_kind=True,
    )
    return requested


async def test_a_vendor_propose_is_typed_from_its_nested_negotiation_type() -> None:
    """The yellow-zone vendor sends ``negotiationType`` inside the Negotiation-T JSON string."""
    requested = await _requested_propose(_vendor_propose_content())

    assert requested == [("information", INFORMATION_NEGOTIATION_PROPOSE_URI)]


async def test_a_vendor_propose_typed_as_clarification_is_a_target_negotiation() -> None:
    """The vendor spells the target kind ``CLARIFICATION`` internally and maps it to the target URI."""
    requested = await _requested_propose(
        _vendor_propose_content(
            negotiation_type="CLARIFICATION",
            template_uri="Negotiation-T/target-negotiation/propose/v1",
        )
    )

    assert requested == [("target", TARGET_NEGOTIATION_PROPOSE_URI)]


async def test_a_vendor_propose_falls_back_to_its_nested_template_uri() -> None:
    """Without ``negotiationType``, a recognised nested ``templateUri`` still names the kind."""
    requested = await _requested_propose(
        _vendor_propose_content(negotiation_type=None, template_uri="Negotiation-T/info-negotiation/propose/v1")
    )

    assert requested == [("information", INFORMATION_NEGOTIATION_PROPOSE_URI)]


async def test_an_unknown_vendor_propose_still_fails_loudly() -> None:
    """No typed signal means no guess: the wrong decision prompt is worse than a hard failure."""
    transport = ScriptedTransport(
        [
            [
                _propose_without_template_uri(
                    "information",
                    content=_vendor_propose_content(
                        negotiation_type=None,
                        template_uri="Negotiation-T/network-layer/ran-energy-saving/v1/region-negotiation/propose/v1",
                    ),
                )
            ]
        ]
    )

    with pytest.raises(ClientFlowError, match="carries no template URI"):
        await run_client_flow(
            prompt_client=FakePromptClient(),
            transport=transport,
            user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
            provider=ScriptedDecisionProvider((NegotiationDecision(action="reject", rationale="No."),)),
            language="en-US",
            infer_missing_negotiation_kind=True,
        )


async def _seen_propose(content: str) -> NegotiationProposeView:
    """Run one round against ``content`` and return the proposal the decision provider was shown."""
    seen: list[NegotiationProposeView] = []
    await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport(
            [[_propose_without_template_uri("information", content=content)], [end_event()]]
        ),
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((NegotiationDecision(action="reject", rationale="No."),)),
        language="en-US",
        on_decision_requested=lambda propose, session: seen.append(propose),
        infer_missing_negotiation_kind=True,
    )
    return seen[0]


def _propose_with_vendor_template_uri(kind: str, *, content: str) -> dict[str, object]:
    """Build a propose whose outer ``templateUri`` uses the vendor's own template naming."""
    event = propose_event(kind, 1, content=content)
    metadata = event["metadata"]
    assert isinstance(metadata, dict)
    metadata[TEMPLATE_URI_METADATA_KEY] = (
        "Negotiation-T/network-layer/ran-energy-saving/v1/region-negotiation/propose/v1"
    )
    return event


async def test_the_field_feasibility_frame_is_typed_from_its_nested_negotiation_type() -> None:
    """The real NMS wraps the body in a top-level array; the nested type still names the kind."""
    requested = await _requested_propose(_field_feasibility_frame())

    assert requested == [("feasibility", FEASIBILITY_NEGOTIATION_PROPOSE_URI)]


async def test_the_array_envelope_without_negotiation_type_uses_its_nested_template_uri() -> None:
    requested = await _requested_propose(
        _vendor_propose_content(
            negotiation_type=None,
            template_uri="Negotiation-T/info-negotiation/propose/v1",
            as_array=True,
        )
    )

    assert requested == [("information", INFORMATION_NEGOTIATION_PROPOSE_URI)]


async def test_a_truncated_vendor_envelope_is_typed_from_its_raw_fields() -> None:
    """A body the client cannot parse still spells its typed fields verbatim, so they are read.

    The text deliberately carries no negotiation heading: only the scoped raw read of
    ``responseMetadata`` can type this frame, so removing it fails the test.
    """
    truncated = _vendor_propose_content(
        negotiation_type="FEASIBILITY",
        template_uri="Negotiation-T/feasibility-negotiation/propose/v1",
        text="Reduce the rate target to 2Mbps or less.",
    )[:-1]
    with pytest.raises(ValueError):
        json.loads(truncated)

    requested = await _requested_propose(truncated)

    assert requested == [("feasibility", FEASIBILITY_NEGOTIATION_PROPOSE_URI)]


async def test_a_truncated_vendor_envelope_hands_its_escaped_prose_to_the_decision_provider() -> (
    None
):
    """The raw read also unescapes the proposal body, so the provider never sees ``\\n`` literals."""
    text = "## Feasibility Negotiation\nThe current rate goal cannot be met."
    truncated = _vendor_propose_content(negotiation_type="FEASIBILITY", text=text)[:-1]

    propose = await _seen_propose(truncated)

    assert propose.content == text


async def test_a_parseable_body_is_never_typed_from_a_sibling_field() -> None:
    """A readable document is typed only by its own ``responseMetadata``, never by a decoy."""
    content = json.dumps(
        {
            "message": [
                {"text": "## Information Negotiation\nPlease provide the region."},
            ],
            "requestEcho": {"negotiationType": "FEASIBILITY"},
        }
    )

    requested = await _requested_propose(content)

    assert requested == [("information", INFORMATION_NEGOTIATION_PROPOSE_URI)]


async def test_a_truncated_body_is_typed_only_inside_its_response_metadata_block() -> None:
    """A field quoted elsewhere in a truncated body must not outrank the typed block."""
    frame = _vendor_propose_content(
        negotiation_type="FEASIBILITY",
        template_uri="Negotiation-T/feasibility-negotiation/propose/v1",
        text="Reduce the rate target to 2Mbps or less.",
    )
    decoyed = frame.replace(
        '{"message"',
        '{"instruction": {"negotiationType": "TARGET"}, "message"',
        1,
    )
    truncated = decoyed[:-1]
    with pytest.raises(ValueError):
        json.loads(truncated)

    requested = await _requested_propose(truncated)

    assert requested == [("feasibility", FEASIBILITY_NEGOTIATION_PROPOSE_URI)]


async def test_an_empty_typed_block_does_not_shadow_a_later_typed_one() -> None:
    """An array envelope may carry several bundles; an empty one must not hide the typed one."""
    empty = {"message": [{"text": "Please continue.", "metadata": {"responseMetadata": {}}}]}
    typed = {
        "message": [
            {
                "text": "Reduce the rate target to 2Mbps or less.",
                "metadata": {
                    "responseMetadata": {
                        "performative": "PROPOSE",
                        "templateUri": "Negotiation-T/feasibility-negotiation/propose/v1",
                        "negotiationType": "FEASIBILITY",
                    }
                },
            }
        ]
    }

    requested = await _requested_propose(json.dumps([empty, typed]))

    assert requested == [("feasibility", FEASIBILITY_NEGOTIATION_PROPOSE_URI)]


async def test_a_structured_extension_value_is_read_without_json_parsing() -> None:
    """A peer may deliver the value already structured; it is read like the JSON string."""
    event = propose_event("information", 1)
    metadata = event["metadata"]
    assert isinstance(metadata, dict)
    metadata.pop(TEMPLATE_URI_METADATA_KEY)
    metadata[NEGOTIATION_T_EXTENSION_URI] = {
        "message": [
            {
                "text": "Please provide the region.",
                "metadata": {
                    "responseMetadata": {
                        "performative": "PROPOSE",
                        "templateUri": "Negotiation-T/info-negotiation/propose/v1",
                        "negotiationType": "INFORMATION",
                    }
                },
            }
        ]
    }
    seen: list[NegotiationProposeView] = []

    await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport([[event], [end_event()]]),
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((NegotiationDecision(action="reject", rationale="No."),)),
        language="en-US",
        on_decision_requested=lambda propose, session: seen.append(propose),
        infer_missing_negotiation_kind=True,
    )

    assert [(item.kind, item.content) for item in seen] == [
        ("information", "Please provide the region.")
    ]


async def test_prose_that_starts_like_json_keeps_its_body() -> None:
    """Rendered prose may start with ``[`` or ``{``; it must reach the provider unchanged."""
    bodies = [
        "[1]: https://example.com/docs\n## Information Negotiation\nPlease provide the region.",
        "{00:00~07:00,2Mbps} exceeds the cell's capacity. Please provide the correct region name.",
    ]
    for body in bodies:
        seen: list[NegotiationProposeView] = []
        await run_client_flow(
            prompt_client=FakePromptClient(),
            transport=ScriptedTransport(
                [[propose_event("information", 1, content=body)], [end_event()]]
            ),
            user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
            provider=ScriptedDecisionProvider(
                (NegotiationDecision(action="reject", rationale="No."),)
            ),
            language="en-US",
            on_decision_requested=lambda propose, session: seen.append(propose),
        )

        assert seen[0].content == body


async def test_brace_led_prose_quoting_a_typed_field_never_types_the_proposal() -> None:
    """A quoted field in prose is not a typed signal: with no heading the run stays loud."""
    content = (
        '{note} the peer wrote "negotiationType": "TARGET" in prose, '
        "but this is an information request."
    )

    with pytest.raises(ClientFlowError, match="carries no template URI"):
        await _requested_propose(content)


async def test_a_json_envelope_without_any_typed_signal_still_fails_loudly() -> None:
    """Reading fields is not guessing: an envelope carrying none keeps the run loud."""
    content = json.dumps([{"message": [{"text": "Please confirm the request."}]}])

    with pytest.raises(ClientFlowError, match="carries no template URI"):
        await _requested_propose(content)


async def test_the_array_envelope_hands_the_prose_not_the_json_to_the_decision_provider() -> None:
    text = "## Target Negotiation\nThe target rate is ambiguous. Please confirm 2Mbps."
    propose = await _seen_propose(
        _vendor_propose_content(negotiation_type="TARGET", text=text, as_array=True)
    )

    assert propose.content == text
    assert propose.kind == "target"
    assert propose.template_uri == TARGET_NEGOTIATION_PROPOSE_URI


async def test_an_unknown_vendor_template_uri_is_inferred_from_the_body() -> None:
    """A present-but-vendor-named outer URI must not short-circuit the typed body."""
    seen: list[NegotiationProposeView] = []
    await run_client_flow(
        prompt_client=FakePromptClient(),
        transport=ScriptedTransport(
            [
                [
                    _propose_with_vendor_template_uri(
                        "information", content=_field_feasibility_frame()
                    )
                ],
                [end_event()],
            ]
        ),
        user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
        provider=ScriptedDecisionProvider((NegotiationDecision(action="reject", rationale="No."),)),
        language="en-US",
        on_decision_requested=lambda propose, session: seen.append(propose),
        infer_missing_negotiation_kind=True,
    )

    assert [(item.kind, item.template_uri) for item in seen] == [
        ("feasibility", FEASIBILITY_NEGOTIATION_PROPOSE_URI)
    ]


async def test_an_unknown_vendor_template_uri_that_cannot_be_typed_is_reported_as_unknown() -> None:
    """The frame did carry a URI; saying it carried none would misdirect the field report."""
    transport = ScriptedTransport(
        [
            [
                _propose_with_vendor_template_uri(
                    "information",
                    content=json.dumps([{"message": [{"text": "Please continue."}]}]),
                )
            ]
        ]
    )

    with pytest.raises(ClientFlowError, match="not a sample negotiation template") as raised:
        await run_client_flow(
            prompt_client=FakePromptClient(),
            transport=transport,
            user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
            provider=ScriptedDecisionProvider((NegotiationDecision(action="reject", rationale="No."),)),
            language="en-US",
            infer_missing_negotiation_kind=True,
        )

    assert "outer templateUri:" in str(raised.value)


async def test_an_unknown_vendor_template_uri_fails_when_inference_is_off() -> None:
    transport = ScriptedTransport(
        [
            [
                _propose_with_vendor_template_uri(
                    "information", content=_field_feasibility_frame()
                )
            ]
        ]
    )

    with pytest.raises(ClientFlowError, match="not a sample negotiation template") as raised:
        await run_client_flow(
            prompt_client=FakePromptClient(),
            transport=transport,
            user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
            provider=ScriptedDecisionProvider((NegotiationDecision(action="reject", rationale="No."),)),
            language="en-US",
        )

    assert "metadata keys:" in str(raised.value)
    assert "outer templateUri:" in str(raised.value)


async def test_prose_quoting_a_typed_field_does_not_type_the_proposal() -> None:
    """The raw-field read is restricted to JSON bodies; prose keeps its heading as the only signal."""
    content = (
        "## Information Negotiation\n"
        'Example payload: "negotiationType": "TARGET". Please provide the region.'
    )

    requested = await _requested_propose(content)

    assert requested == [("information", INFORMATION_NEGOTIATION_PROPOSE_URI)]


async def test_an_untyped_propose_names_what_the_frame_carried() -> None:
    """The failure must be diagnosable from the run log without the raw wire frame."""
    transport = ScriptedTransport(
        [
            [
                _propose_without_template_uri(
                    "information", content=json.dumps([{"message": [{"text": "Confirm."}]}])
                )
            ]
        ]
    )

    with pytest.raises(ClientFlowError, match="carries no template URI") as raised:
        await run_client_flow(
            prompt_client=FakePromptClient(),
            transport=transport,
            user_request=INFORMATION_NEGOTIATION_SPEC.user_request["en-US"],
            provider=ScriptedDecisionProvider((NegotiationDecision(action="reject", rationale="No."),)),
            language="en-US",
            infer_missing_negotiation_kind=True,
        )

    message = str(raised.value)
    assert "metadata keys:" in message
    assert "Negotiation-T value: json" in message
