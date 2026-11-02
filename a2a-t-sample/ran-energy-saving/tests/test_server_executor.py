"""Tests for the deterministic server executor and the negotiation wrapper."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext, UnauthenticatedUser
from a2a.server.events import Event as A2AEvent
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Message,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import ContentValidationError, SlotValidationError
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
)
from demo import ran_data
from demo.agents.server_agent.factory import build_server_agent, server_agent_factory
from demo.agents.server_agent.planner import ScriptedChatModel
from demo.composition import build_demo_negotiation_policy
from dotenv import dotenv_values
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct
from protocol.approval_gate import ApprovalRegistry
from protocol.messages import SAMPLE_PROPOSAL_METADATA_KEY, TASK_TEMPLATE_URI
from protocol.pre_task_negotiation import (
    NegotiationContentServer,
    NegotiationOutcome,
    PreTaskNegotiationHandler,
)
from protocol.server_app import (
    build_agent_card,
    build_registration_payload,
    build_server_runtime,
    prepare_sdk_env_path,
)
from protocol.server_executor import (
    REPORT_ARTIFACT_NAME,
    TOOL_ARTIFACT_NAME,
    LangGraphTaskExecutor,
    NegotiationAgentExecutor,
    build_negotiation_executor,
)
from shared.config import MODEL_MODE_LIVE, SampleConfig
from shared.metrics import render_prometheus, reset_metrics

CONTEXT_ID = "ctx-executor"
TASK_PROMPT = "Create a RAN energy saving task in the Songshanhu area."


def _metadata(metadata: Struct) -> dict[str, Any]:
    """Decode a protobuf Struct into a plain dict (the stubs model no dynamic keys)."""
    return MessageToDict(metadata)


class RecordingEventQueue(EventQueue):
    """Collect events enqueued by an executor."""

    def __init__(self) -> None:
        self.events: list[object] = []

    async def enqueue_event(self, event: A2AEvent) -> None:
        self.events.append(event)


class RecordingExecutor(AgentExecutor):
    """Inner executor test double recording delegation."""

    def __init__(self) -> None:
        self.contexts: list[RequestContext] = []
        self.cancelled: list[RequestContext] = []

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        del event_queue
        self.contexts.append(context)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        del event_queue
        self.cancelled.append(context)


class FixedGate:
    """Gate test double returning one fixed outcome."""

    def __init__(self, outcome: NegotiationOutcome) -> None:
        self.outcome = outcome
        self.contexts: list[RequestContext] = []

    async def handle(self, context: RequestContext) -> NegotiationOutcome:
        self.contexts.append(context)
        return self.outcome


class RejectingPromptServer:
    """Prompt-server double whose Task-T validation fails with an unmapped error code."""

    def validate_task_prompt_and_data_filling(
        self, prompt: str, schema: object, template_uri: str
    ) -> object:
        del prompt, schema, template_uri
        raise ContentValidationError(
            ErrorCatalog.CONTENT_SEMANTIC_CONFLICT,
            {"section_label": "task_context", "reason": "rejected"},
            errors=[SlotValidationError(slot_name="region", code="content.format_error", message="bad")],
        )


def _make_context(
    *,
    task_prompt: str = TASK_PROMPT,
    negotiation: dict[str, object] | None = None,
    sample: dict[str, object] | None = None,
    context_id: str = CONTEXT_ID,
    add_label_part: bool = True,
) -> RequestContext:
    message = Message(message_id="message-1", role=Role.ROLE_USER, context_id=context_id)
    if add_label_part:
        message.parts.add(text="short label")
    message.metadata[TASK_T_EXTENSION_URI] = task_prompt
    message.metadata[TEMPLATE_URI_METADATA_KEY] = TASK_TEMPLATE_URI
    if negotiation is not None:
        message.metadata[NEGOTIATION_CONTEXT_METADATA_KEY] = negotiation  # ty: ignore[invalid-assignment]
    if sample is not None:
        message.metadata[SAMPLE_PROPOSAL_METADATA_KEY] = sample  # ty: ignore[invalid-assignment]
    request = SendMessageRequest(message=message)
    call_context = ServerCallContext(
        state={},
        user=UnauthenticatedUser(),
        requested_extensions={TASK_T_EXTENSION_URI},
    )
    return RequestContext(call_context=call_context, request=request)


async def test_executor_short_circuits_without_calling_the_inner_executor() -> None:
    message = Message(message_id="propose", role=Role.ROLE_AGENT, context_id=CONTEXT_ID)
    message.parts.add(text="propose")
    gate = FixedGate(NegotiationOutcome.short(message))
    inner = RecordingExecutor()
    executor = NegotiationAgentExecutor(handler=cast(PreTaskNegotiationHandler, gate), inner=inner)
    queue = RecordingEventQueue()

    await executor.execute(_make_context(), queue)

    assert queue.events == [message]
    assert inner.contexts == []


async def test_executor_delegates_the_gate_approved_request() -> None:
    context = _make_context()
    gate = FixedGate(NegotiationOutcome.execution(context))
    inner = RecordingExecutor()
    executor = NegotiationAgentExecutor(handler=cast(PreTaskNegotiationHandler, gate), inner=inner)
    queue = RecordingEventQueue()

    await executor.execute(context, queue)

    assert queue.events == []
    assert inner.contexts == [context]


async def test_executor_enqueues_a_rejected_task_for_an_unmapped_validation_failure() -> None:
    server = cast(NegotiationContentServer, RejectingPromptServer())
    gate = PreTaskNegotiationHandler(server)
    inner = RecordingExecutor()
    executor = NegotiationAgentExecutor(handler=gate, inner=inner)
    context = _make_context()
    queue = RecordingEventQueue()

    await executor.execute(context, queue)

    assert len(queue.events) == 1
    task = queue.events[0]
    assert isinstance(task, Task)
    assert task.status.state == TaskState.TASK_STATE_REJECTED
    assert task.context_id == CONTEXT_ID
    assert task.status.message.parts[0].text
    assert inner.contexts == []


async def test_executor_cancel_delegates_to_the_inner_executor() -> None:
    context = _make_context()
    gate = FixedGate(NegotiationOutcome.execution(context))
    inner = RecordingExecutor()
    executor = NegotiationAgentExecutor(handler=cast(PreTaskNegotiationHandler, gate), inner=inner)

    await executor.cancel(context, RecordingEventQueue())

    assert inner.cancelled == [context]


async def test_langgraph_executor_publishes_task_artifacts_and_report(
    offline_config: SampleConfig,
) -> None:
    ran_data.reset()
    reset_metrics()
    context = _make_context(
        negotiation={"id": CONTEXT_ID, "round": 2, "maxRounds": 3, "performative": "ACCEPT"}
    )
    executor = LangGraphTaskExecutor(
        agent_factory=build_server_agent, config=offline_config
    )
    queue = RecordingEventQueue()

    try:
        await executor.execute(context, queue)
    finally:
        ran_data.reset()

    assert 'a2at_server_tasks_total{state="completed"} 1' in render_prometheus()

    tasks = [event for event in queue.events if isinstance(event, Task)]
    artifacts = [event for event in queue.events if isinstance(event, TaskArtifactUpdateEvent)]
    final_statuses = [
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.status.state == TaskState.TASK_STATE_COMPLETED
    ]
    tool_artifacts = [
        event for event in artifacts if event.artifact.name == TOOL_ARTIFACT_NAME
    ]
    reports = [event for event in artifacts if event.artifact.name == REPORT_ARTIFACT_NAME]

    assert len(tasks) == 1
    assert tasks[0].id == context.task_id
    assert tasks[0].context_id == CONTEXT_ID
    assert len(tool_artifacts) == 3
    assert final_statuses
    assert len(reports) == 1
    report = reports[0]
    assert report.artifact.metadata[TEMPLATE_URI_METADATA_KEY] == TASK_TEMPLATE_URI
    assert _metadata(report.artifact.metadata)[NEGOTIATION_CONTEXT_METADATA_KEY]["round"] == 2
    task_t_report = str(report.artifact.metadata[TASK_T_EXTENSION_URI])
    assert "1. Basic information about the intent execution result" in task_t_report
    assert "Energy saving:" in task_t_report
    assert report.artifact.parts[0].HasField("data")
    assert report.artifact.parts[1].text


async def test_langgraph_executor_fails_when_the_planner_cannot_generate_a_report(
    offline_config: SampleConfig,
) -> None:
    reset_metrics()
    context = _make_context()
    executor = LangGraphTaskExecutor(
        agent_factory=server_agent_factory(
            model_factory=lambda: ScriptedChatModel(dataset_date="not-a-date")
        ),
        config=offline_config,
    )
    queue = RecordingEventQueue()

    await executor.execute(context, queue)

    statuses = [event for event in queue.events if isinstance(event, TaskStatusUpdateEvent)]
    assert statuses
    assert statuses[-1].status.state == TaskState.TASK_STATE_FAILED
    assert not any(
        isinstance(event, TaskArtifactUpdateEvent) and event.artifact.name == REPORT_ARTIFACT_NAME
        for event in queue.events
    )
    assert 'a2at_server_tasks_total{state="failed"} 1' in render_prometheus()


def test_build_negotiation_executor_wraps_the_langgraph_executor(
    offline_config: SampleConfig,
) -> None:
    gate = cast(PreTaskNegotiationHandler, FixedGate(NegotiationOutcome.execution(_make_context())))

    executor = build_negotiation_executor(
        handler=gate, agent_factory=build_server_agent, config=offline_config
    )

    assert isinstance(executor, NegotiationAgentExecutor)
    assert isinstance(executor.inner, LangGraphTaskExecutor)
    assert executor.handler is gate


def test_agent_card_declares_the_task_and_negotiation_extensions() -> None:
    card = build_agent_card(host="127.0.0.1", port=8000)

    assert card.name
    assert card.supported_interfaces[0].url == "http://127.0.0.1:8000"
    assert str(card.supported_interfaces[0].protocol_binding) == "HTTP+JSON"
    extension_uris = {extension.uri for extension in card.capabilities.extensions}
    assert TASK_T_EXTENSION_URI in extension_uris
    assert NEGOTIATION_T_EXTENSION_URI in extension_uris


def test_registration_payload_is_a_registry_card_list() -> None:
    payload = build_registration_payload(build_agent_card(host="127.0.0.1", port=8000))

    cards = cast(list[dict[str, object]], payload["agentCards"])
    assert len(cards) == 1
    assert cards[0]["name"] == "RAN NMS Agent"
    assert "supportedInterfaces" in cards[0]


def test_live_sdk_env_fills_empty_values_without_mutating_the_source(
    offline_config: SampleConfig, tmp_path: Path
) -> None:
    config = replace(offline_config, model_mode=MODEL_MODE_LIVE)
    source = tmp_path / ".env"
    source_text = (
        "A2AT_LLM_PROVIDER=\n"
        "A2AT_LLM_MODEL=\n"
        "A2AT_LLM_API_KEY=\n"
        "A2AT_LLM_BASE_URL=\n"
        "A2AT_LANGUAGE=en-US\n"
    )
    source.write_text(source_text, encoding="utf-8")

    sdk_env_path = prepare_sdk_env_path(config, source)

    values = dotenv_values(sdk_env_path)
    assert sdk_env_path != source
    assert values["A2AT_LLM_PROVIDER"] == "openai_traced"
    assert values["A2AT_LLM_MODEL"] == "mass/volcengine/deepseek-v4.1-flash"
    assert values["A2AT_LLM_API_KEY"] == "not-needed"
    assert values["A2AT_LLM_BASE_URL"] == "http://127.0.0.1:31943/v1"
    assert source.read_text(encoding="utf-8") == source_text


def test_live_sdk_env_preserves_an_explicit_api_key(
    offline_config: SampleConfig, tmp_path: Path
) -> None:
    config = replace(offline_config, model_mode=MODEL_MODE_LIVE)
    source = tmp_path / ".env"
    source.write_text("A2AT_LLM_API_KEY=explicit-key\n", encoding="utf-8")

    values = dotenv_values(prepare_sdk_env_path(config, source))

    assert values["A2AT_LLM_API_KEY"] == "explicit-key"
    assert source.read_text(encoding="utf-8") == "A2AT_LLM_API_KEY=explicit-key\n"


def test_live_server_constructs_from_the_example_env_with_an_empty_key(
    offline_config: SampleConfig, tmp_path: Path
) -> None:
    source = Path(__file__).resolve().parents[1] / "env.example"
    # Reproduce the reported startup failure: the example profile with the proxy key blanked out.
    live_source = tmp_path / ".env"
    live_source.write_text(
        source.read_text(encoding="utf-8").replace("A2AT_LLM_API_KEY=not-needed", "A2AT_LLM_API_KEY="),
        encoding="utf-8",
    )
    config = replace(offline_config, model_mode=MODEL_MODE_LIVE)

    runtime = build_server_runtime(
        agent_factory=build_server_agent,
        policy=build_demo_negotiation_policy(),
        config=config,
        env_path=live_source,
    )

    assert runtime.prompt_server is not None
    assert runtime.config.is_offline is False


async def _wait_for_approval(
    executor: LangGraphTaskExecutor,
    context: RequestContext,
    queue: RecordingEventQueue,
    registry: ApprovalRegistry,
    *,
    approved: bool,
) -> list[object]:
    """Start one execution, wait until the gate is pending, resolve it, return the events."""
    task = asyncio.create_task(executor.execute(context, queue))
    task_id = ""
    for _ in range(300):
        tasks = [event for event in queue.events if isinstance(event, Task)]
        if tasks and registry.pending(tasks[0].id):
            task_id = tasks[0].id
            break
        await asyncio.sleep(0.01)
    assert task_id, "the approval gate never became pending"
    assert registry.resolve(task_id, approved) is True
    await asyncio.wait_for(task, 15)
    return list(queue.events)


async def test_langgraph_executor_applies_only_after_approval(
    offline_config: SampleConfig,
) -> None:
    ran_data.reset()
    reset_metrics()
    context = _make_context(
        negotiation={"id": CONTEXT_ID, "round": 2, "maxRounds": 3, "performative": "ACCEPT"}
    )
    registry = ApprovalRegistry()
    executor = LangGraphTaskExecutor(
        agent_factory=build_server_agent,
        config=offline_config,
        approval_registry=registry,
        approval_timeout_seconds=5.0,
    )
    queue = RecordingEventQueue()

    try:
        events = await _wait_for_approval(
            executor, context, queue, registry, approved=True
        )
        assert len(ran_data.list_plans()) == 1
    finally:
        ran_data.reset()

    states = [
        event.status.state
        for event in events
        if isinstance(event, TaskStatusUpdateEvent)
    ]
    assert TaskState.TASK_STATE_INPUT_REQUIRED in states
    assert states[-1] == TaskState.TASK_STATE_COMPLETED
    status_texts = [
        event.status.message.parts[0].text
        for event in events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.status.HasField("message")
        and event.status.message.parts
    ]
    assert any("Data collection started" in text for text in status_texts)
    assert any("Data collection in progress" in text for text in status_texts)
    assert sum("Data collection completed" in text for text in status_texts) == 1
    # Apply and report are reported as A2A notifications, not only as tool artifacts.
    assert any(
        "Progress: plan " in text and "was applied to cell cell-001" in text
        for text in status_texts
    )
    assert any("Progress: the energy-saving report is ready." in text for text in status_texts)
    artifacts = [event for event in events if isinstance(event, TaskArtifactUpdateEvent)]
    assert any(event.artifact.name == REPORT_ARTIFACT_NAME for event in artifacts)


async def test_langgraph_executor_writes_nothing_when_approval_is_rejected(
    offline_config: SampleConfig,
) -> None:
    ran_data.reset()
    reset_metrics()
    context = _make_context(
        negotiation={"id": CONTEXT_ID, "round": 2, "maxRounds": 3, "performative": "ACCEPT"}
    )
    registry = ApprovalRegistry()
    executor = LangGraphTaskExecutor(
        agent_factory=build_server_agent,
        config=offline_config,
        approval_registry=registry,
        approval_timeout_seconds=5.0,
    )
    queue = RecordingEventQueue()

    try:
        events = await _wait_for_approval(
            executor, context, queue, registry, approved=False
        )
        assert ran_data.list_plans() == ()
    finally:
        ran_data.reset()

    states = [
        event.status.state
        for event in events
        if isinstance(event, TaskStatusUpdateEvent)
    ]
    assert TaskState.TASK_STATE_INPUT_REQUIRED in states
    assert states[-1] == TaskState.TASK_STATE_REJECTED
    artifacts = [event for event in events if isinstance(event, TaskArtifactUpdateEvent)]
    assert not any(event.artifact.name == REPORT_ARTIFACT_NAME for event in artifacts)
