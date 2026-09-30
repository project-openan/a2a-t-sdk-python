"""A2ATLLMClientDecorator tests (spec 2.2/4.2/5.5): usage stash, L2 token metric, facade wiring.

The decorator wraps the SDK LLM client created by ``A2ATClient``/``A2ATServer``: after every
``structured`` call it stashes the token usage (contextvar bridge to the client entry span) and
records the ``gen_ai.client.token.usage`` histogram immediately. All reporting is guarded and
never breaks the LLM call result.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from opentelemetry import metrics as metrics_api
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from a2a_t.client.a2at_client import A2ATClient
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import NEGOTIATION_ABORT_URI
from a2a_t.llm.models import LLMResponse
from a2a_t.observability.attributes import (
    ATTR_GEN_AI_REQUEST_MODEL,
    ATTR_GEN_AI_TOKEN_TYPE,
    ATTR_GEN_AI_USAGE_INPUT_TOKENS,
    ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
)
from a2a_t.observability.client.transport_decorator import A2ATClientTransportDecorator
from a2a_t.observability.llm_decorator import _METRIC_INSTRUMENTS as _LLM_METRIC_INSTRUMENTS
from a2a_t.observability.llm_decorator import A2ATLLMClientDecorator
from a2a_t.observability.llm_stash import clear_llm_usage, get_llm_usage
from a2a_t.server.a2at_server import A2ATServer
from tests.observability.stubs import FakeMessage, FakeStreamResponse, make_send_request

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"
ABORT_REASON = "已达到协商轮次上限，本次协商确认终止。"


class FakeLlmClient:
    """LLM boundary fake returning one scripted LLMResponse."""

    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.calls = 0
        self.last_kwargs: dict[str, Any] = {}

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.last_kwargs = {"messages": messages, "json_schema": json_schema}
        return self.response

    def other_method(self) -> str:
        return "delegated"


def _response(usage: dict[str, int] | None = None) -> LLMResponse:
    return LLMResponse(
        content="{}",
        model="qwen-max",
        usage=usage if usage is not None else {"prompt_tokens": 11, "completion_tokens": 7},
        metadata={},
    )


@pytest.fixture()
def exporter() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    trace.set_tracer_provider(TracerProvider())
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter


@pytest.fixture()
def metric_reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    metrics_api.set_meter_provider(MeterProvider(metric_readers=[reader]))
    _LLM_METRIC_INSTRUMENTS.clear()
    yield reader


def _token_points(reader: InMemoryMetricReader) -> list[tuple[dict[str, Any], Any]]:
    data = reader.get_metrics_data()
    points: list[tuple[dict[str, Any], Any]] = []
    if data is None:
        return points
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == "gen_ai.client.token.usage":
                    for point in metric.data.data_points:
                        points.append((dict(point.attributes or {}), point))
    return points


def test_structured_sets_llm_usage_stash() -> None:
    decorator = A2ATLLMClientDecorator(FakeLlmClient(_response()))

    try:
        decorator.structured(messages=[{"role": "user", "content": "hi"}], json_schema={})

        assert get_llm_usage() is not None
        stash = get_llm_usage()
        assert stash is not None
        assert stash.input_tokens == 11
        assert stash.output_tokens == 7
        assert stash.request_model == "qwen-max"
    finally:
        clear_llm_usage()


def test_structured_records_token_metric(metric_reader: InMemoryMetricReader) -> None:
    decorator = A2ATLLMClientDecorator(FakeLlmClient(_response()))

    try:
        decorator.structured(messages=[], json_schema={})
    finally:
        clear_llm_usage()

    points = _token_points(metric_reader)
    assert len(points) == 2
    attrs_by_type = {attrs["gen_ai.token.type"]: point.sum for attrs, point in points}
    assert attrs_by_type == {"input": 11, "output": 7}
    assert all(attrs[ATTR_GEN_AI_TOKEN_TYPE] in ("input", "output") for attrs, _ in points)


def test_usage_reporting_failure_never_breaks_flow() -> None:
    class ExplodingUsage:
        pass

    inner = FakeLlmClient(LLMResponse(content="{}", model="m", usage=ExplodingUsage(), metadata={}))  # type: ignore[arg-type]
    decorator = A2ATLLMClientDecorator(inner)

    try:
        response = decorator.structured(messages=[], json_schema={})
    finally:
        clear_llm_usage()

    assert response is inner.response
    assert get_llm_usage() is None


def test_getattr_delegates_to_inner() -> None:
    decorator = A2ATLLMClientDecorator(FakeLlmClient(_response()))

    assert decorator.other_method() == "delegated"


async def test_client_facade_llm_call_fills_stash_and_entry_span(
    tmp_path: Path, exporter: InMemorySpanExporter
) -> None:
    """Full chain: facade LLM call → stash → next client entry span carries gen_ai.usage.* attrs."""
    env_path = tmp_path / "client.env"
    env_path.write_text(
        "\n".join(
            (
                "A2AT_LANGUAGE=zh-CN",
                "A2AT_PROMPT_SOURCE_TYPE=packaged",
                "A2AT_LLM_PROVIDER=openai",
                "A2AT_LLM_MODEL=test-model",
                "A2AT_LLM_BASE_URL=https://llm.example.test/v1",
                "A2AT_LLM_API_KEY=test-key",
                "",
            )
        ),
        encoding="utf-8",
    )
    scripted = FakeLlmClient(LLMResponse(content=f'{{"termination_reason":"{ABORT_REASON}"}}', model="test-model",
                                         usage={"prompt_tokens": 3, "completion_tokens": 5}, metadata={}))
    with patch("a2a_t.client.a2at_client.LLMClientFactory.create", return_value=scripted):
        client = A2ATClient(env_path=env_path)

    client.generate_negotiation_abort_prompt_from_text(
        ABORT_REASON, NegotiationContext(SESSION_ID, 5, 5, NegotiationPerformative.ABORT), NEGOTIATION_ABORT_URI
    )
    stash = get_llm_usage()
    assert stash is not None
    assert stash.input_tokens == 3
    assert stash.output_tokens == 5
    assert stash.request_model == "test-model"

    class FakeTransport:
        def __init__(self) -> None:
            self.result: Any = FakeStreamResponse("message", FakeMessage(metadata={}, task_id="T1", context_id="C1"))

        async def send_message(self, request: Any, *, context: Any = None) -> Any:
            return self.result

    try:
        transport = A2ATClientTransportDecorator(FakeTransport())
        await transport.send_message(make_send_request({}, task_id="T1", context_id="C1"))
    finally:
        leftover = get_llm_usage()
        clear_llm_usage()

    attrs = exporter.get_finished_spans()[-1].attributes or {}
    assert attrs[ATTR_GEN_AI_USAGE_INPUT_TOKENS] == 3
    assert attrs[ATTR_GEN_AI_USAGE_OUTPUT_TOKENS] == 5
    assert attrs[ATTR_GEN_AI_REQUEST_MODEL] == "test-model"
    assert leftover is None


async def test_server_facade_wraps_llm_client(tmp_path: Path) -> None:
    env_path = tmp_path / "server.env"
    env_path.write_text(
        "\n".join(
            (
                "A2AT_LANGUAGE=zh-CN",
                "A2AT_PROMPT_SOURCE_TYPE=packaged",
                "A2AT_LLM_PROVIDER=openai",
                "A2AT_LLM_MODEL=test-model",
                "A2AT_LLM_BASE_URL=https://llm.example.test/v1",
                "A2AT_LLM_API_KEY=test-key",
                "",
            )
        ),
        encoding="utf-8",
    )
    scripted = FakeLlmClient(_response())
    with patch("a2a_t.server.a2at_server.LLMClientFactory.create", return_value=scripted):
        server = A2ATServer(env_path=env_path)

    try:
        server._llm_client.structured(messages=[], json_schema={})  # type: ignore[attr-defined]
        stash = get_llm_usage()
    finally:
        clear_llm_usage()

    assert scripted.calls == 1
    assert stash is not None
    assert stash.input_tokens == 11
    assert stash.request_model == "qwen-max"
