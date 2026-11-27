"""End-to-end tests of the pure A2A mode against a real in-process server."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest
from a2a_t.core.metadata import TASK_T_EXTENSION_URI
from demo.composition import build_demo_runtime
from demo.pure_policy import build_demo_pure_policy
from shared.config import DISCOVERY_DIRECT, TRANSPORT_SSE
from shared.models import NegotiationDecision
from shared.run_constants import (
    FINAL_STATE_INCOMPLETE,
    FINAL_STATE_TASK_COMPLETED,
    LANGUAGE_EN,
    PROTOCOL_MODE_BOTH,
    PROTOCOL_PURE,
)

from tests.integration_helpers import (
    free_tcp_port,
    offline_direct_config,
    run_scenario_flow,
    running_server,
)
from tests.support.adk_fake_model import build_client_script, build_fake_model
from tests.support.scenario_scripts import scripted_decisions_for


def _pure_decisions(scenario: str) -> tuple[NegotiationDecision, ...]:
    """Return the client decisions that answer the local server's pure questions."""
    if scenario == "information":
        # The request omits the guaranteed target; the server asks for it. The scripted client
        # supplies one concrete value so the transport path can be asserted without a live model.
        rate = "{00:00-06:00,10Mbps}"
        return (
            NegotiationDecision(
                action="accept",
                rationale=f"Use {rate}.",
                filled_params={"guaranteedThroughputTarget": rate},
            ),
        )
    return scripted_decisions_for(scenario)


def _pure_model(decisions: tuple[NegotiationDecision, ...]):
    """Build the fake ADK model factory that serves one pure decision script."""

    def model(config: object = None, *, model: object = None) -> object:
        del config
        return model if model is not None else build_fake_model([build_client_script(decisions)])

    return model


async def _build_pure_runtime(config, scenario: str):
    """Build the pure runtime with a scripted decision model for one scenario."""
    with patch("shared.model_factory.build_traced_adk_model", _pure_model(_pure_decisions(scenario))):
        return await build_demo_runtime(
            config,
            scenario,
            "auto",
            TRANSPORT_SSE,
            DISCOVERY_DIRECT,
            protocol=PROTOCOL_PURE,
        )


@pytest.mark.parametrize("scenario", ["night", "target"])
async def test_a_pure_scenario_completes_end_to_end(scenario: str) -> None:
    port = free_tcp_port()
    config = offline_direct_config(port)

    with running_server(
        config,
        protocol_mode=PROTOCOL_PURE,
        pure_policy=build_demo_pure_policy(LANGUAGE_EN),
    ):
        runtime = await _build_pure_runtime(config, scenario)
        try:
            result = await runtime.run()
        finally:
            await runtime.aclose()

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert any(
        event.get("state") == "TASK_STATE_COMPLETED" for event in result.events
    )

    input_required = [
        event
        for event in result.events
        if event.get("state") == "TASK_STATE_INPUT_REQUIRED"
        and isinstance(event.get("metadata"), dict)
        and isinstance(event["metadata"].get("pure"), dict)
    ]
    if scenario == "night":
        assert input_required == []
    else:
        assert input_required

    for event in result.events:
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            continue
        assert TASK_T_EXTENSION_URI not in metadata
        assert "templateUri" not in metadata
        assert "negotiationContext" not in metadata


async def test_a_pure_information_question_without_a_grounded_value_ends_incomplete() -> None:
    """A question the request cannot ground is relayed, not answered from a guessed value."""
    port = free_tcp_port()
    config = offline_direct_config(port)

    with running_server(
        config,
        protocol_mode=PROTOCOL_PURE,
        pure_policy=build_demo_pure_policy(LANGUAGE_EN),
    ):
        runtime = await _build_pure_runtime(config, "information")
        try:
            result = await runtime.run()
        finally:
            await runtime.aclose()

    assert result.final_state == FINAL_STATE_INCOMPLETE
    assert result.error
    assert any(
        event.get("state") == "TASK_STATE_INPUT_REQUIRED"
        and isinstance(event.get("metadata"), dict)
        and isinstance(event["metadata"].get("pure"), dict)
        for event in result.events
    )


async def test_a_pure_send_request_answers_the_input_required_round() -> None:
    """The non-streaming ``message/send`` path keeps the same INPUT_REQUIRED continuation."""
    port = free_tcp_port()
    config = replace(offline_direct_config(port), pure_message_mode="send")

    with running_server(
        config,
        protocol_mode=PROTOCOL_PURE,
        pure_policy=build_demo_pure_policy(LANGUAGE_EN),
    ):
        runtime = await _build_pure_runtime(config, "target")
        try:
            result = await runtime.run()
        finally:
            await runtime.aclose()

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert any(
        event.get("state") == "TASK_STATE_INPUT_REQUIRED"
        and isinstance(event.get("metadata"), dict)
        and isinstance(event["metadata"].get("pure"), dict)
        for event in result.events
    )


async def test_one_server_serves_the_extension_and_standard_a2a() -> None:
    """A2A-T is an extension of A2A: one agent handles both request forms sequentially."""
    port = free_tcp_port()
    config = offline_direct_config(port)

    with running_server(
        config,
        protocol_mode=PROTOCOL_MODE_BOTH,
        pure_policy=build_demo_pure_policy(LANGUAGE_EN),
    ):
        extension_result, _ = await run_scenario_flow(config, "night", TRANSPORT_SSE)
        runtime = await _build_pure_runtime(config, "night")
        try:
            standard_result = await runtime.run()
        finally:
            await runtime.aclose()

    assert extension_result.final_state == FINAL_STATE_TASK_COMPLETED
    assert standard_result.final_state == FINAL_STATE_TASK_COMPLETED
    for event in standard_result.events:
        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            assert TASK_T_EXTENSION_URI not in metadata
            assert "templateUri" not in metadata
            assert "negotiationContext" not in metadata
