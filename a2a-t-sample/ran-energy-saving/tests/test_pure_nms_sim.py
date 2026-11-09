"""End-to-end rehearsal of the pure-A2A field report against the NMS simulator.

The OSP evaluation set stores the vendor's plain-text answers in the ``a2a`` block of each case.
Driving the real pure client flow with those answers keeps the two field bugs covered locally:

* W-PURE-2 -- an accept that supplies several values must send all of them, not only the first.
* W-PURE-3 -- the vendor's ``COMPLETED`` plus refusal sentence must not be reported as success.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from demo.nms_sim.app import build_sim_app
from demo.nms_sim.config import (
    CARRIER_MESSAGE,
    DEFAULT_TOKEN_USER,
    DEFAULT_TOKEN_VALUE,
    FIDELITY_FAST,
    WINDOW_POLICY_ANY,
    SimConfig,
)
from demo.pure_policy import render_pure_answer
from protocol.client_flow import ClientFlowResult
from protocol.nms_client import NmsClient, NmsConfig, NmsSseTransport
from protocol.pure_client_flow import run_pure_client_flow
from shared.models import NegotiationDecision
from shared.run_constants import (
    FINAL_STATE_TASK_COMPLETED,
    FINAL_STATE_TASK_FAILED,
    PROTOCOL_PURE,
)

#: The shipped OSP cases (the workbook's ``A2A_原始数据`` sheet).
CASES_DIR = Path(__file__).resolve().parents[1] / "eval" / "osp" / "cases"

#: Budgets of one in-process pure run.
_REQUEST_TIMEOUT_SECONDS = 5.0
_CALL_TIMEOUT_SECONDS = 5.0


def _sim_config() -> SimConfig:
    """Return the vendor-faithful simulator profile used by the rehearsal."""
    return SimConfig(
        fidelity=FIDELITY_FAST,
        negotiation_carrier=CARRIER_MESSAGE,
        # The OSP cases use the production activation windows, not the demo's exact 00:00~06:00.
        window_policy=WINDOW_POLICY_ANY,
    )


def _case(case_id: int) -> dict[str, Any]:
    """Load one OSP case by id."""
    for path in sorted(CASES_DIR.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and document.get("id") == case_id:
            return document
    raise AssertionError(f"OSP case {case_id} is missing from {CASES_DIR}")


def _expected_answer(case: dict[str, Any]) -> str:
    """Return the first plain-A2A answer the workbook records for one case."""
    rounds = case["expect"]["protocols"]["a2a"]["negotiation"]["rounds"]
    assert rounds, "the case has no recorded A2A answer"
    return str(rounds[0])


async def _run_pure(
    user_request: str,
    answer: Callable[[dict[str, object]], str],
    *,
    task_id: str | None = None,
    context_id: str | None = None,
    sim_app: Any | None = None,
) -> tuple[ClientFlowResult, list[tuple[dict[str, object], str]]]:
    """Run one pure client flow against an in-process simulator.

    ``sim_app`` lets two calls share the simulator's Task store, which is how a Modify rehearsal
    continues the Task its create phase just left running.
    """
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sim_app or build_sim_app(_sim_config())),
        base_url="http://sim",
    )
    nms_client = NmsClient(
        NmsConfig(
            a2a_base="http://sim/a2a/json",
            token_url="http://sim/rest/plat/smapp/v1/sessions",
            token_user=DEFAULT_TOKEN_USER,
            token_value=DEFAULT_TOKEN_VALUE,
            idle_timeout=1.0,
            request_timeout=_REQUEST_TIMEOUT_SECONDS,
            poll_interval=0.0,
        ),
        http_client=http_client,
    )
    answered: list[tuple[dict[str, object], str]] = []

    async def on_answer(event: dict[str, object], text: str) -> None:
        answered.append((event, text))

    try:
        transport = NmsSseTransport(nms_client, protocol=PROTOCOL_PURE)
        result = await run_pure_client_flow(
            transport=transport,
            user_request=user_request,
            answer=answer,
            on_answer=on_answer,
            max_rounds=3,
            call_timeout_seconds=_CALL_TIMEOUT_SECONDS,
            plain_questions=True,
            task_id=task_id,
            context_id=context_id,
        )
    finally:
        await nms_client.aclose()
        await http_client.aclose()
    return result, answered


async def test_osp_case_40_region_answer_completes_the_task() -> None:
    """The vendor asks for the missing region; the workbook answer resolves it."""
    case = _case(40)
    expected = _expected_answer(case)

    result, answered = await _run_pure(str(case["user_input"]), lambda event: expected)

    assert len(answered) == 1
    assert answered[0][1] == expected
    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert result.reports


async def test_a_pure_follow_up_continues_the_same_vendor_task() -> None:
    """A standard A2A Modify is a follow-up message on the Task the first request created."""
    case = _case(40)
    expected = _expected_answer(case)
    sim = build_sim_app(_sim_config())

    first, _ = await _run_pure(str(case["user_input"]), lambda event: expected, sim_app=sim)
    assert first.final_state == FINAL_STATE_TASK_COMPLETED
    assert first.task_id
    assert first.context_id

    second, _ = await _run_pure(
        (
            "Modify the guaranteed throughput target to "
            "{00:00~08:00,2Mbps}, {08:00~24:00,10Mbps} for A2ATestRegion."
        ),
        lambda event: "",
        sim_app=sim,
        task_id=first.task_id,
        context_id=first.context_id,
    )

    assert second.task_id == first.task_id
    assert second.context_id == first.context_id
    assert second.final_state == FINAL_STATE_TASK_COMPLETED


async def test_an_accept_with_two_values_sends_both_and_completes() -> None:
    """W-PURE-2: the rendered answer carries the region and the throughput target."""
    case = _case(40)
    decision = NegotiationDecision(
        action="accept",
        rationale="Confirm the served region and keep the requested windows.",
        filled_params={
            "region": "A2ATestRegion",
            "guaranteedThroughputTarget": "{00:00~08:00,2Mbps}, {08:00~24:00,10Mbps}",
        },
    )
    rendered = render_pure_answer(decision)
    assert "The region is A2ATestRegion." in rendered
    assert "Set the guaranteed throughput targets:" in rendered

    result, answered = await _run_pure(str(case["user_input"]), lambda event: rendered)

    assert len(answered) == 1
    assert answered[0][1] == rendered
    assert result.final_state == FINAL_STATE_TASK_COMPLETED
    assert result.reports


async def test_an_answer_that_still_misses_the_region_is_a_failure() -> None:
    """W-PURE-3: COMPLETED with a refusal sentence is not success, even with no artifact."""
    case = _case(40)

    result, answered = await _run_pure(
        str(case["user_input"]),
        lambda event: "{00:00~08:00,2Mbps}, {08:00~24:00,10Mbps}",
    )

    assert len(answered) == 1
    assert result.final_state == FINAL_STATE_TASK_FAILED
    assert result.reports == []
    status_texts = [
        str(event.get("text"))
        for event in result.events
        if event.get("kind") == "status" and event.get("text")
    ]
    assert any("not recognized" in text for text in status_texts)


@pytest.mark.parametrize("case_id", [40, 28])
def test_the_region_cases_still_expect_a_plain_a2a_answer(case_id: int) -> None:
    """The rehearsal is driven by the shipped cases; a dataset drift must fail loudly."""
    case = _case(case_id)

    assert _expected_answer(case).startswith("The region is ")


async def test_a_vendor_question_without_round_metadata_is_recorded_as_round_one() -> None:
    """The vendor does not number its questions; the client still reports a timed round."""
    from protocol.client_runtime import _announce_pure_proposals

    case = _case(40)
    expected = _expected_answer(case)
    proposals: list[tuple[object, object]] = []
    decisions: list[tuple[object, object, object]] = []
    answer = _announce_pure_proposals(
        lambda event: expected,
        lambda propose, session: proposals.append((propose, session)),
        3,
        lambda decision, propose, session: decisions.append((decision, propose, session)),
    )

    result, answered = await _run_pure(str(case["user_input"]), answer)

    assert len(answered) == 1
    propose, session = proposals[0]
    assert session.round == 1
    assert propose.kind == "information"
    decision, propose_again, session_again = decisions[0]
    assert decision.action == "accept"
    assert decision.rationale == expected
    assert session_again.round == 1
    assert result.final_state == FINAL_STATE_TASK_COMPLETED
