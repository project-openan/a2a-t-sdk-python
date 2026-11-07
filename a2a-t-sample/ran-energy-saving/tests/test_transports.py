"""SSE vs webhook parity of the RAN energy-saving client flow.

Both transports run against the same real in-process server with the same offline configuration.
The assertions cover the complete two-way performative sequence (which must be identical) and the
terminal state, and document the one event-list difference the transports are known to have:

* SSE streams the terminal ``TASK_STATE_COMPLETED`` status after the report artifact;
* webhook ends a task phase at the report artifact (the underlying task stays ``WORKING``), so it
  has no terminal status. The non-blocking RPC reply additionally reports ``TASK_STATE_SUBMITTED``
  and the push stream reports it again, which is why webhook sees one extra ``SUBMITTED`` event.
"""

from __future__ import annotations

import asyncio

import pytest
from a2a.types import TaskState
from demo.scenario import SCENARIO_FEASIBILITY, SCENARIO_INFORMATION, SCENARIO_TARGET, get_scenario
from integration_helpers import (
    free_tcp_port,
    offline_direct_config,
    run_scenario_flow,
    running_server,
)
from protocol.client_flow import ClientFlowResult
from shared.config import TRANSPORT_SSE, TRANSPORT_WEBHOOK

_COMPLETED = TaskState.Name(TaskState.TASK_STATE_COMPLETED)


def _status_states(result: ClientFlowResult) -> list[str]:
    """Return the Task-state names of the client's status events, in arrival order."""
    return [
        str(event["state"])
        for event in result.events
        if event.get("kind") == "status" and isinstance(event.get("state"), str)
    ]


def _report_artifact_seen(result: ClientFlowResult) -> bool:
    """Return whether the client received the named report artifact."""
    return any(
        event.get("kind") == "artifact" and "report" in str(event.get("name") or "").lower() for event in result.events
    )


@pytest.mark.parametrize("scenario_name", [SCENARIO_INFORMATION, SCENARIO_TARGET])
async def test_sse_and_webhook_share_the_performative_sequence(
    scenario_name: str,
    english_language: None,
) -> None:
    """Both transports produce the design sequence and the same terminal state."""
    scenario = get_scenario(scenario_name)
    config = offline_direct_config(free_tcp_port())

    with running_server(config):
        sse_result, sse_exchange = await run_scenario_flow(config, scenario_name, TRANSPORT_SSE)
        webhook_result, webhook_exchange = await run_scenario_flow(config, scenario_name, TRANSPORT_WEBHOOK)

    assert sse_exchange.steps == list(scenario.expected_sequence)
    assert webhook_exchange.steps == sse_exchange.steps
    assert sse_result.final_state == scenario.expected_final_state
    assert webhook_result.final_state == scenario.expected_final_state
    if scenario.expected_final_state == "input_invalid":
        assert sse_exchange.steps == []
        assert sse_result.task_id is None
        assert webhook_result.task_id is None
        return
    assert sse_result.task_id
    assert webhook_result.task_id
    assert sse_result.report
    assert webhook_result.report

    sse_states = _status_states(sse_result)
    webhook_states = _status_states(webhook_result)
    assert _COMPLETED in sse_states
    # Documented difference: the report artifact closes the webhook phase, so no terminal status
    # is pushed; the non-blocking reply plus the push report SUBMITTED twice instead.
    assert _COMPLETED not in webhook_states
    assert webhook_states.count(TaskState.Name(TaskState.TASK_STATE_SUBMITTED)) == 2
    assert _report_artifact_seen(sse_result) is True
    assert _report_artifact_seen(webhook_result) is True


async def test_two_webhook_runs_share_the_callback_port_without_cross_talk(
    english_language: None,
) -> None:
    """The whole point of per-run queues: two testers at once on one callback port."""
    del english_language
    config = offline_direct_config(free_tcp_port())
    feasibility = get_scenario(SCENARIO_FEASIBILITY)
    target = get_scenario(SCENARIO_TARGET)

    with running_server(config):
        (feasibility_result, feasibility_exchange), (target_result, target_exchange) = await asyncio.gather(
            run_scenario_flow(config, SCENARIO_FEASIBILITY, TRANSPORT_WEBHOOK),
            run_scenario_flow(config, SCENARIO_TARGET, TRANSPORT_WEBHOOK),
        )

    assert feasibility_exchange.steps == list(feasibility.expected_sequence)
    assert target_exchange.steps == list(target.expected_sequence)
    assert feasibility_result.final_state == feasibility.expected_final_state
    assert target_result.final_state == target.expected_final_state
    assert feasibility_result.task_id
    assert target_result.task_id
    assert feasibility_result.task_id != target_result.task_id
    assert feasibility_result.report
    assert target_result.report
