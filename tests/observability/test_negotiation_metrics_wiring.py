"""Facade wiring of the terminal negotiation metric (spec 5.5): A2ATClient/A2ATServer auto-report.

``report_negotiation_rounds`` lives in ``a2a_t.observability.negotiation_metrics``; this suite pins
the wiring that calls it automatically when either facade constructs a terminal negotiation
message (accept/reject/abort): the from-data legs (deterministic, zero LLM) and one from-text leg
(one scripted LLM extraction call). PROPOSE messages report nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from opentelemetry import metrics as metrics_api
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from a2a_t.client.a2at_client import A2ATClient
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import (
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    NEGOTIATION_ABORT_URI,
)
from a2a_t.llm.models import LLMResponse
from a2a_t.negotiation.content import (
    InformationEndingContent,
    InformationProposeContent,
    NegotiationConclusion,
    NegotiationEndingData,
    NegotiationItem,
    NegotiationProposeData,
)
from a2a_t.observability.negotiation_metrics import (
    _METRIC_INSTRUMENTS as _NEGOTIATION_METRIC_INSTRUMENTS,
)
from a2a_t.server.a2at_server import A2ATServer

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"
ABORT_REASON = "已达到协商轮次上限，本次协商确认终止。"


class ScriptedLlmClient:
    """LLM boundary fake replaying scripted payloads."""

    def __init__(self, *payloads: str) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls += 1
        payload = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        return LLMResponse(
            content=payload, model="test-model", usage={"prompt_tokens": 1, "completion_tokens": 1}, metadata={}
        )


def write_env(tmp_path: Path) -> Path:
    """Write one facade env file selecting the packaged zh resources."""
    env_path = tmp_path / "facade.env"
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
    return env_path


def build_client(env_path: Path, llm_client: Any = None) -> A2ATClient:
    """Build one client facade whose LLM client factory call returns the given stand-in client."""
    with patch("a2a_t.client.a2at_client.LLMClientFactory.create", return_value=llm_client or object()):
        return A2ATClient(env_path=env_path)


def build_server(env_path: Path, llm_client: Any = None) -> A2ATServer:
    """Build one server facade whose LLM client factory call returns the given stand-in client."""
    with patch("a2a_t.server.a2at_server.LLMClientFactory.create", return_value=llm_client or object()):
        return A2ATServer(env_path=env_path)


@pytest.fixture()
def metric_reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    metrics_api.set_meter_provider(MeterProvider(metric_readers=[reader]))
    _NEGOTIATION_METRIC_INSTRUMENTS.clear()
    yield reader


def _negotiation_points(reader: InMemoryMetricReader) -> list[Any]:
    data = reader.get_metrics_data()
    if data is None:
        return []
    points: list[Any] = []
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == "a2at.negotiation.total_rounds":
                    points.extend(metric.data.data_points)
    return points


def test_client_from_text_abort_reports_negotiation_rounds(tmp_path: Path, metric_reader: InMemoryMetricReader) -> None:
    """The LLM-driven from-text abort path reports one histogram sample from the result context."""
    client = build_client(write_env(tmp_path), ScriptedLlmClient(f'{{"termination_reason":"{ABORT_REASON}"}}'))

    result = client.generate_negotiation_abort_prompt_from_text(
        ABORT_REASON, NegotiationContext(SESSION_ID, 5, 5, NegotiationPerformative.ABORT), NEGOTIATION_ABORT_URI
    )

    assert result.negotiation_context is not None
    points = _negotiation_points(metric_reader)
    assert len(points) == 1
    assert points[0].sum == 5
    attrs = dict(points[0].attributes)
    assert attrs["gen_ai.agent.a2at.negotiation.id"] == SESSION_ID
    assert attrs["outcome"] == "abort"
    assert attrs["gen_ai.agent.a2at.extension.name"] == "Negotiation-T"


def test_client_from_data_accept_reports_negotiation_rounds(
    tmp_path: Path, metric_reader: InMemoryMetricReader
) -> None:
    client = build_client(write_env(tmp_path))
    data = NegotiationEndingData(
        NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.ACCEPT),
        InformationEndingContent(NegotiationConclusion.ACCEPT, [NegotiationItem("节能区域", "松山湖")]),
    )

    client.generate_negotiation_accept_prompt_from_data(data, INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI)

    points = _negotiation_points(metric_reader)
    assert len(points) == 1
    assert points[0].sum == 2
    attrs = dict(points[0].attributes)
    assert attrs["outcome"] == "accept"


def test_client_propose_from_data_reports_nothing(tmp_path: Path, metric_reader: InMemoryMetricReader) -> None:
    """PROPOSE is not a terminal performative: no histogram sample."""
    client = build_client(write_env(tmp_path))
    data = NegotiationProposeData(
        NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE),
        InformationProposeContent([NegotiationItem("节能区域", "松山湖")], None),
    )

    client.generate_negotiation_propose_prompt_from_data(data, INFORMATION_NEGOTIATION_PROPOSE_URI)

    assert _negotiation_points(metric_reader) == []


def test_server_from_data_reject_reports_negotiation_rounds(
    tmp_path: Path, metric_reader: InMemoryMetricReader
) -> None:
    server = build_server(write_env(tmp_path))
    data = NegotiationEndingData(
        NegotiationContext(SESSION_ID, 4, 5, NegotiationPerformative.REJECT),
        InformationEndingContent(NegotiationConclusion.REJECT, [NegotiationItem("节能区域", "松山湖")]),
    )

    server.generate_negotiation_reject_prompt_from_data(data, INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI)

    points = _negotiation_points(metric_reader)
    assert len(points) == 1
    assert points[0].sum == 4
    attrs = dict(points[0].attributes)
    assert attrs["gen_ai.agent.a2at.negotiation.id"] == SESSION_ID
    assert attrs["outcome"] == "reject"
