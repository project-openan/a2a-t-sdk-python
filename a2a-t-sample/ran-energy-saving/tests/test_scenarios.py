"""End-to-end acceptance tests of the seven RAN energy-saving scenarios.

Each test starts a real uvicorn server in a background thread and runs the real client runtime
(``A2ATClient`` + A2A SDK + ADK ``Runner`` with the deterministic fake model) against it over
``http://127.0.0.1:<ephemeral>`` direct discovery. Nothing touches the network and the scripted
responses make every step deterministic.

Asserted per scenario (design §8):

* the exact performative/round sequence, both directions;
* the decisions the ADK-backed client made;
* the terminal state, and that accept scenarios created a Task and a report while reject/abort
  scenarios created no Task at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

import pytest
from a2a.types import TaskState
from a2a_t.core.metadata import NEGOTIATION_T_EXTENSION_URI
from demo.agents.client_agent.agent import build_client_agent
from demo.catalog import to_run_spec
from demo.composition import install_offline_provider, offline_model_factory
from demo.scenario import (
    FINAL_STATE_NEGOTIATION_ABORTED,
    FINAL_STATE_NEGOTIATION_ENDED,
    FINAL_STATE_TASK_COMPLETED,
    LANGUAGE_ZH,
    SUPPORTED_SCENARIO_NAMES,
    Scenario,
    get_scenario,
)
from integration_helpers import (
    free_tcp_port,
    offline_direct_config,
    run_scenario_flow,
    running_server,
)
from protocol.client_flow import ClientFlowResult
from protocol.client_runtime import build_client_runtime
from protocol.messages import metadata_to_dict, parse_negotiation_context
from protocol.run_spec import RunSpec
from shared.models import NegotiationDecision

#: Client decision action matching one expected performative.
_ACTION_BY_PERFORMATIVE: dict[str, str] = {
    "ACCEPT": "accept",
    "PROPOSE": "counter",
    "REJECT": "reject",
    "ABORT": "abort",
}

def _expected_actions(scenario: Scenario) -> list[str]:
    """Return the decisions the scenario's client steps imply, in order."""
    return [
        _ACTION_BY_PERFORMATIVE[step.performative]
        for step in scenario.expected_sequence
        if step.actor == "client"
    ]


def _negotiation_contents(
    events: Sequence[Mapping[str, object]], performative: str
) -> list[tuple[int, str]]:
    """Return ``(round, Negotiation-T content)`` of every inbound message of one performative."""
    contents: list[tuple[int, str]] = []
    for event in events:
        if event.get("kind") != "message":
            continue
        metadata = event.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        metadata_dict = metadata_to_dict(metadata)
        session = parse_negotiation_context(metadata_dict)
        if session is None or session.performative.value != performative:
            continue
        content = metadata_dict.get(NEGOTIATION_T_EXTENSION_URI)
        if isinstance(content, str):
            contents.append((session.round, content))
    return contents


def _completed_status_seen(result: ClientFlowResult) -> bool:
    """Return whether the client observed the terminal ``COMPLETED`` status."""
    return any(
        event.get("kind") == "status"
        and event.get("state") == TaskState.Name(TaskState.TASK_STATE_COMPLETED)
        for event in result.events
    )


@pytest.mark.parametrize(
    ("language", "unknown_area", "expected_region"),
    (
        ("en-US", "Region Z", "A2ATestRegion"),
        ("zh-CN", "未知区域", "A2ATestRegion"),
    ),
)
def test_the_unknown_area_request_keeps_its_unserved_region(
    language: str,
    unknown_area: str,
    expected_region: str,
) -> None:
    """The vendor profile must not turn the deliberate unknown area into a served one."""
    unknown_spec = to_run_spec(
        get_scenario("unknown_area"),
        language=language,
        region=expected_region,
        sites=("NRES_XCS_6102937",),
    )
    assert unknown_area in unknown_spec.user_request
    assert expected_region not in unknown_spec.user_request

    night_spec = to_run_spec(
        get_scenario("night"),
        language=language,
        region=expected_region,
        sites=("NRES_XCS_6102937",),
    )
    assert expected_region in night_spec.user_request


@pytest.mark.parametrize("scenario_name", SUPPORTED_SCENARIO_NAMES)
async def test_scenario_matches_the_design_sequence(
    scenario_name: str,
    english_language: None,
) -> None:
    """Run one scenario end to end over SSE and assert design §8 exactly."""
    scenario = get_scenario(scenario_name)
    config = offline_direct_config(free_tcp_port())

    with running_server(config):
        result, exchange = await run_scenario_flow(config, scenario_name, "sse")

    assert exchange.steps == list(scenario.expected_sequence)
    assert [decision.action for decision in result.decisions] == _expected_actions(scenario)
    assert result.final_state == scenario.expected_final_state

    if scenario.expected_final_state == FINAL_STATE_TASK_COMPLETED:
        assert result.task_id
        assert result.report
        assert _completed_status_seen(result) is True
        return

    # reject and abort never create a Task: no task id, no report, no terminal Task status.
    assert scenario.expected_final_state in (
        FINAL_STATE_NEGOTIATION_ENDED,
        FINAL_STATE_NEGOTIATION_ABORTED,
    )
    assert result.task_id is None
    assert result.report is None
    assert _completed_status_seen(result) is False


async def test_planned_outage_request_creates_a_baseline_task(
    english_language: None,
) -> None:
    """Use case 2 is a single baseline Create; the Modify phase is the selected-Task request."""
    config = offline_direct_config(free_tcp_port())

    with running_server(config):
        result, exchange = await run_scenario_flow(config, "outage", "sse")

    assert exchange.steps == []
    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert result.report is not None
    assert result.report.get("cell_id") == "cell-002"
    assert result.report.get("window") == "01:00~05:00"
    assert result.report.get("target_rate") == 10
    assert result.report.get("status") == "applied"

    # The baseline creates the Task once; no follow-up Task-T is sent automatically.
    assert len(result.reports) == 1
    submitted = [
        event
        for event in result.events
        if event.get("kind") == "status"
        and event.get("state") == TaskState.Name(TaskState.TASK_STATE_SUBMITTED)
    ]
    assert len(submitted) == 1


class _RegionBCatalog:
    """Catalog that accepts the unknown-area proposal with the second served area."""

    @property
    def names(self) -> tuple[str, ...]:
        return ("unknown_area",)

    def spec(self, name: str, *, language: str) -> RunSpec:
        spec = to_run_spec(get_scenario(name), language=language)
        return replace(
            spec,
            scripted_decisions=(
                NegotiationDecision(
                    action="accept",
                    rationale="The engineer selected Region B.",
                    filled_params={
                        "region": "Region B",
                        "guaranteedThroughputTarget": (
                            "{00:00~07:00,2Mbps}, {07:00~23:00,20Mbps}, {23:00~24:00,2Mbps}"
                        ),
                    },
                ),
            ),
        )


async def test_unknown_area_can_use_the_second_served_area(english_language: None) -> None:
    """Selecting Region B in the unknown-area proposal runs the Region B cell."""
    config = offline_direct_config(free_tcp_port())

    with running_server(config):
        runtime = await build_client_runtime(
            config,
            "unknown_area",
            "scripted",
            "sse",
            catalog=_RegionBCatalog(),
            offline_model_factory=offline_model_factory,
            install_offline_provider=install_offline_provider,
            client_agent_factory=build_client_agent,
        )
        try:
            result = await runtime.run()
        finally:
            await runtime.aclose()

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert result.report is not None
    assert result.report.get("region") == "Region B"
    assert result.report.get("cell_id") == "cell-003"


@pytest.mark.parametrize("scenario_name", ("information", "target", "feasibility"))
async def test_accept_scenarios_complete_in_chinese(
    scenario_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The zh-CN accept refill passes the server's revalidation and reaches a report.

    The rendered zh-CN task context writes the outage without a space (``在00:00~18:00停电``);
    the refill routing has to recognise that spelling or the feasibility accept is rejected.
    """
    monkeypatch.setenv("A2AT_LANGUAGE", LANGUAGE_ZH)
    scenario = get_scenario(scenario_name)
    config = replace(offline_direct_config(free_tcp_port()), language=LANGUAGE_ZH)

    with running_server(config):
        result, exchange = await run_scenario_flow(config, scenario_name, "sse")

    assert exchange.steps == list(scenario.expected_sequence)
    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert result.task_id
    assert result.report


@pytest.mark.parametrize(
    ("scenario_name", "expected_tail"),
    [
        ("reject", ("server", "MESSAGE", None)),
        ("abort", ("server", "ABORT", 3)),
    ],
)
async def test_no_task_scenarios_end_with_the_plain_end_or_abort(
    scenario_name: str,
    expected_tail: tuple[str, str, int | None],
    english_language: None,
) -> None:
    """Reject ends with a plain end message; abort ends with ``S:Abort(3)`` and no Task."""
    config = offline_direct_config(free_tcp_port())

    with running_server(config):
        result, exchange = await run_scenario_flow(config, scenario_name, "sse")

    last = exchange.steps[-1]
    assert (last.actor, last.performative, last.round) == expected_tail
    assert result.final_state == get_scenario(scenario_name).expected_final_state
    assert result.task_id is None
    assert result.report is None
    assert exchange.events[-1].get("kind") == "message"


async def test_information_propose_names_the_missing_throughput_target(
    english_language: None,
) -> None:
    """The information propose itemizes the server-required throughput target, not the region."""
    config = offline_direct_config(free_tcp_port())

    with running_server(config):
        result, _ = await run_scenario_flow(config, "information", "sse")

    proposals = _negotiation_contents(result.events, "PROPOSE")
    assert len(proposals) == 1
    assert "guaranteedThroughputTarget" in proposals[0][1]
    assert result.final_state == FINAL_STATE_TASK_COMPLETED


async def test_target_round_two_content_is_revised(english_language: None) -> None:
    """The offline scripted extraction must render a different proposal in round 2 than round 1."""
    config = offline_direct_config(free_tcp_port())

    with running_server(config):
        result, _ = await run_scenario_flow(config, "target", "sse")

    proposals = _negotiation_contents(result.events, "PROPOSE")
    assert [round_number for round_number, _ in proposals] == [1, 2]
    assert proposals[0][1] != proposals[1][1]
    assert "Revised proposal for round 2" in proposals[1][1]


async def test_server_abort_reason_names_the_server_and_the_round_limit(
    english_language: None,
) -> None:
    """The abort scenario's server abort must not borrow the canned client-abort reason."""
    config = offline_direct_config(free_tcp_port())

    with running_server(config):
        result, _ = await run_scenario_flow(config, "abort", "sse")

    proposals = _negotiation_contents(result.events, "PROPOSE")
    assert [round_number for round_number, _ in proposals] == [1, 2, 3]
    assert all("Information Negotiation" in content for _, content in proposals)

    aborts = _negotiation_contents(result.events, "ABORT")
    assert len(aborts) == 1
    reason = aborts[0][1]
    assert "The server ended the negotiation" in reason
    assert "maximum number of rounds (3)" in reason
    assert "client aborted" not in reason.lower()


async def test_a_secured_server_completes_the_information_scenario(
    english_language: None,
) -> None:
    """The real SDK client presents the service token on every A2A call."""
    port = free_tcp_port()
    server_config = replace(
        offline_direct_config(port),
        server_auth_token="server-secret",
    )
    client_config = replace(
        offline_direct_config(port),
        server_auth_token="server-secret",
    )

    with running_server(server_config):
        result, _ = await run_scenario_flow(client_config, "information", "sse")

    assert result.final_state == FINAL_STATE_TASK_COMPLETED
