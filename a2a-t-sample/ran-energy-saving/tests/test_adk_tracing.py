"""Tests of the ADK model/tool tracing (``shared.adk_tracing``)."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import shared.adk_tracing as adk_tracing
from demo.adk_fake_model import FakeScript, FakeToolCall, FakeTurn, build_fake_model
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from shared.adk_tracing import (
    SHARED_LINKER,
    SpanLinker,
    TracePlugin,
    TracingLlm,
    json_safe,
    serialize_llm_request,
    serialize_llm_response,
)
from shared.models import NegotiationDecision
from shared.trace import (
    PHASE_EXECUTION,
    emit_span,
    set_global_activity_sink,
    set_global_sink,
    trace_scope,
)

#: Model id of the deterministic fake model.
_FAKE_MODEL_NAME = "fake-ran-energy-saving"

#: ADK application name of the tool-plugin test.
_APP_NAME = "ran_energy_tracing_test"


class _NullContentModel(BaseLlm):
    """Model returning a response without content (the ``None`` tolerance case)."""

    model: str = "null-content-model"

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities()

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        del llm_request, stream
        yield LlmResponse(content=None, partial=False)


class _FailingModel(BaseLlm):
    """Model raising on every call."""

    model: str = "failing-model"

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities()

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        del llm_request, stream
        raise RuntimeError("provider exploded")
        yield LlmResponse()  # pragma: no cover - makes this an async generator


@pytest.fixture
def spans() -> list[dict[str, object]]:
    """Spans emitted by the producer hook during one test."""
    return []


@pytest.fixture(autouse=True)
def _sink(spans: list[dict[str, object]]) -> Any:
    """Attach one recording sink and clear it afterwards."""
    set_global_sink(spans.append)
    try:
        yield
    finally:
        set_global_sink(None)


def _request(system_instruction: str = "You are an agent.") -> LlmRequest:
    """Build one request routed to the fake model's default text."""
    return LlmRequest(
        model=_FAKE_MODEL_NAME,
        contents=[types.Content(role="user", parts=[types.Part(text="Hello.")])],
        config=types.GenerateContentConfig(system_instruction=system_instruction),
    )


async def _collect(model: BaseLlm, request: LlmRequest) -> list[LlmResponse]:
    """Collect the responses of one model call."""
    return [response async for response in model.generate_content_async(request)]


def test_wrap_delegates_model_and_capabilities() -> None:
    inner = build_fake_model()
    traced = TracingLlm.wrap(inner)

    assert isinstance(traced, TracingLlm)
    assert traced.model == inner.model
    assert traced.capabilities == inner.capabilities
    assert traced.inner is inner


@pytest.mark.asyncio
async def test_llm_span_carries_the_full_request_and_response(spans: list[dict[str, object]]) -> None:
    traced = TracingLlm.wrap(build_fake_model())

    with trace_scope(run_id="run-adk", role="server", context_id="ctx-adk", stage="server_execution_agent"):
        responses = await _collect(traced, _request())

    assert responses and responses[0].content is not None
    assert len(spans) == 1
    span = spans[0]
    assert span["kind"] == "llm"
    assert span["runId"] == "run-adk"
    assert span["side"] == "server"
    assert span["contextId"] == "ctx-adk"
    assert span["stage"] == "server_execution_agent"
    assert span["phase"] == PHASE_EXECUTION
    assert str(span["spanId"]).startswith("llm_server_adk_")
    detail = span["detail"]
    assert isinstance(detail, dict)
    request = detail["request"]
    assert isinstance(request, dict)
    assert request["systemInstruction"] == "You are an agent."
    assert request["contents"][0]["parts"][0]["text"] == "Hello."
    response = detail["response"]
    assert isinstance(response, dict)
    assert "RAN energy-saving task completed." in str(response["text"])


@pytest.mark.asyncio
async def test_a_model_turn_publishes_its_wait_state_before_and_after_the_call() -> None:
    """The span only exists after the model answers; the wait state must exist before it."""
    activities: list[dict[str, object]] = []
    set_global_activity_sink(activities.append)
    try:
        traced = TracingLlm.wrap(build_fake_model())
        with trace_scope(run_id="run-adk", role="server", stage="client_decision_agent"):
            await _collect(traced, _request())
    finally:
        set_global_activity_sink(None)

    assert [item["detail"]["status"] for item in activities] == ["running", "done"]
    assert all(item["detail"]["activityKind"] == "model" for item in activities)
    assert all(item["detail"]["labelKey"] == "waitingModel" for item in activities)
    assert len({item["detail"]["activityId"] for item in activities}) == 1
    assert activities[0]["runId"] == "run-adk"
    assert activities[0]["stage"] == "client_decision_agent"


@pytest.mark.asyncio
async def test_llm_span_tolerates_a_none_content_response(spans: list[dict[str, object]]) -> None:
    traced = TracingLlm.wrap(_NullContentModel())

    with trace_scope(run_id="run-null", role="client", stage="client_decision_agent"):
        responses = await _collect(traced, _request())

    assert len(responses) == 1
    assert len(spans) == 1
    span = spans[0]
    assert span["status"] == "ok"
    detail = span["detail"]
    assert isinstance(detail, dict)
    assert detail["response"]["text"] is None
    assert detail["response"]["functionCalls"] == []
    assert detail["response"]["usage"] is None


@pytest.mark.asyncio
async def test_llm_span_captures_an_error_and_reraises(spans: list[dict[str, object]]) -> None:
    traced = TracingLlm.wrap(_FailingModel())

    with trace_scope(run_id="run-err", role="server", stage="server_execution_agent"):
        with pytest.raises(RuntimeError, match="provider exploded"):
            await _collect(traced, _request())

    assert len(spans) == 1
    span = spans[0]
    assert span["status"] == "error"
    assert "RuntimeError" in str(span["error"])


@pytest.mark.asyncio
async def test_request_serializer_failure_drops_the_span_without_changing_the_model(
    monkeypatch: pytest.MonkeyPatch,
    spans: list[dict[str, object]],
) -> None:
    """A broken request serializer must not raise into ADK: the span is dropped, the model runs."""

    def explode(_request: LlmRequest) -> dict[str, object]:
        raise RuntimeError("serializer exploded")

    monkeypatch.setattr(adk_tracing, "serialize_llm_request", explode)
    traced = TracingLlm.wrap(build_fake_model())

    with trace_scope(run_id="run-ser", role="server", stage="server_execution_agent"):
        responses = await _collect(traced, _request())

    assert responses and responses[0].content is not None
    assert spans == []


@pytest.mark.asyncio
async def test_response_serializer_failure_drops_the_span_without_changing_the_model(
    monkeypatch: pytest.MonkeyPatch,
    spans: list[dict[str, object]],
) -> None:
    """The guarded emit also covers the final response (and error) serialization."""

    def explode(_response: LlmResponse | None) -> dict[str, object] | None:
        raise RuntimeError("response serializer exploded")

    monkeypatch.setattr(adk_tracing, "serialize_llm_response", explode)
    traced = TracingLlm.wrap(build_fake_model())

    with trace_scope(run_id="run-ser-2", role="server", stage="server_execution_agent"):
        responses = await _collect(traced, _request())

    assert responses and responses[0].content is not None
    assert spans == []


def test_serializers_tolerate_none_and_json_safe_objects() -> None:
    assert serialize_llm_response(None) is None
    assert json_safe(None) is None
    nested = json_safe({"value": [1, None, {"a": "b"}]})
    assert nested == {"value": [1, None, {"a": "b"}]}

    request = _request()
    serialized = serialize_llm_request(request)
    assert serialized["tools"] == []
    assert serialized["responseSchema"] is None


def test_response_schema_class_is_rendered_as_its_json_schema() -> None:
    """``output_schema`` keeps a pydantic class on the request config, never a ``<class ...>``."""
    request = _request()
    request.config.response_schema = NegotiationDecision

    schema = serialize_llm_request(request)["responseSchema"]

    assert isinstance(schema, dict)
    assert schema["title"] == "NegotiationDecision"
    assert "action" in schema["properties"]
    assert "<class" not in json.dumps(schema)


@pytest.mark.asyncio
async def test_tool_plugin_emits_spans_parented_to_the_llm_call(spans: list[dict[str, object]]) -> None:
    """A real ADK runner over a local tool script produces LLM and tool spans."""
    linker = SpanLinker()
    script = FakeScript(
        marker="trace-tool-agent",
        turns=(
            FakeTurn(tool_calls=(FakeToolCall(name="echo_tool", args={"value": "x"}),)),
            FakeTurn(text="done"),
        ),
    )
    model = TracingLlm.wrap(build_fake_model((script,)), linker=linker)

    def echo_tool(value: str) -> dict[str, object]:
        """Echo one value back to the model."""
        return {"ok": True, "value": value}

    agent = LlmAgent(
        name="trace_tool_agent",
        model=model,
        instruction="trace-tool-agent",
        tools=[echo_tool],
    )
    runner = InMemoryRunner(
        app=App(name=_APP_NAME, root_agent=agent, plugins=[TracePlugin(linker=linker)]),
        app_name=_APP_NAME,
    )
    await runner.session_service.create_session(app_name=_APP_NAME, user_id="user-1", session_id="ctx-tools")

    with trace_scope(run_id="run-tools", role="server", context_id="ctx-tools", stage="server_execution_agent"):
        async for _ in runner.run_async(
            user_id="user-1",
            session_id="ctx-tools",
            new_message=types.Content(role="user", parts=[types.Part(text="Call the tool.")]),
        ):
            continue

    llm_spans = [span for span in spans if span["kind"] == "llm"]
    tool_spans = [span for span in spans if span["kind"] == "tool"]
    assert llm_spans
    tool_names: list[object] = []
    for span in tool_spans:
        detail = span["detail"]
        assert isinstance(detail, dict)
        tool_names.append(detail["toolName"])
    assert tool_names == ["echo_tool"]
    llm_ids = {str(span["spanId"]) for span in llm_spans}
    for span in tool_spans:
        assert span["runId"] == "run-tools"
        assert span["contextId"] == "ctx-tools"
        assert span["side"] == "server"
        assert span["status"] == "ok"
        assert isinstance(span["durationMs"], float)
        assert span["parentSpanId"] in llm_ids
        detail = span["detail"]
        assert isinstance(detail, dict)
        assert detail["args"] == {"value": "x"}
        assert detail["result"] == {"ok": True, "value": "x"}
    assert linker.last_llm("ctx-tools") in llm_ids


@pytest.mark.asyncio
async def test_tool_plugin_reports_a_failing_tool(spans: list[dict[str, object]]) -> None:
    """A tool that raises is reported as an error span and its error still propagates."""

    def broken_tool(value: str) -> dict[str, object]:
        raise ValueError(f"cannot handle {value}")

    from google.adk.agents import LlmAgent

    class _CallingModel(BaseLlm):
        model: str = "calling-model"

        @property
        def capabilities(self) -> LlmCapabilities:
            return LlmCapabilities()

        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            del stream
            if any(part.function_response for content in llm_request.contents for part in (content.parts or ())):
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(text="done")]),
                    partial=False,
                )
                return
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(name="broken_tool", args={"value": "x"})
                        )
                    ],
                ),
                partial=False,
            )

    agent = LlmAgent(name="broken_agent", model=TracingLlm.wrap(_CallingModel()), tools=[broken_tool])
    runner = InMemoryRunner(
        app=App(name=_APP_NAME, root_agent=agent, plugins=[TracePlugin()]),
        app_name=_APP_NAME,
    )
    await runner.session_service.create_session(app_name=_APP_NAME, user_id="user-2", session_id="ctx-broken")

    with trace_scope(run_id="run-broken", role="client", context_id="ctx-broken", stage="client_decision_agent"):
        with pytest.raises(ValueError):
            async for _ in runner.run_async(
                user_id="user-2",
                session_id="ctx-broken",
                new_message=types.Content(role="user", parts=[types.Part(text="call it")]),
            ):
                continue

    tool_spans = [span for span in spans if span["kind"] == "tool"]
    assert len(tool_spans) == 1
    assert tool_spans[0]["status"] == "error"
    assert "ValueError" in str(tool_spans[0]["error"])
    broken_detail = tool_spans[0]["detail"]
    assert isinstance(broken_detail, dict)
    assert broken_detail["toolName"] == "broken_tool"


@pytest.mark.asyncio
async def test_no_sink_never_breaks_the_model() -> None:
    set_global_sink(None)
    traced = TracingLlm.wrap(build_fake_model())
    responses = await _collect(traced, _request())
    assert responses
    assert SHARED_LINKER.last_llm("missing") is None
    emit_span({"spanId": "ignored"})


def test_adk_usage_carries_the_cached_content_tokens() -> None:
    """The provider-reported cache hits must survive the ADK usage mapping."""
    usage = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=793,
        candidates_token_count=2,
        total_token_count=795,
        cached_content_token_count=768,
    )

    tokens = adk_tracing._content_tokens(usage)

    assert tokens is not None
    assert tokens["cached"] == 768
    assert tokens["prompt"] == 793


def test_adk_usage_marks_an_absent_cache_as_none() -> None:
    usage = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=11,
        candidates_token_count=2,
        total_token_count=13,
    )

    tokens = adk_tracing._content_tokens(usage)

    assert tokens is not None
    assert tokens["cached"] is None
