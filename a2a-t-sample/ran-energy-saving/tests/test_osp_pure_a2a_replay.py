"""Replay the whole OSP pure-A2A corpus against the NMS simulator.

The OSP workbook records, for every case, the free-text answers a tester typed into the vendor's
plain-A2A dialogue (the P column). The real vendor asks the same terse sentence on every round
(``Not feasible, please confirm``) and merges the reply into its own intent before validating
again. The simulator now reproduces that dialogue, so the real client flow can rehearse all 51
cases offline:

* a Create case sends its P-column answers and completes;
* a Modify case first creates the paired baseline Task (held WORKING), then continues it with the
  Modify text and its own answers.

The test is the acceptance gate for "all OSP cases run in pure A2A mode": the round count and the
final state must match the workbook for every case.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from demo.nms_sim.app import build_sim_app
from demo.nms_sim.config import (
    CARRIER_MESSAGE,
    DEFAULT_TOKEN_USER,
    DEFAULT_TOKEN_VALUE,
    FIDELITY_FAST,
    PURE_QUESTION_CONFIRM,
    RATE_POLICY_OPTIONAL,
    WINDOW_POLICY_ANY,
    SimConfig,
)
from demo.web.tester.decisions import pure_answers
from eval.osp.runner import load_cases
from protocol.client_flow import ClientFlowResult
from protocol.nms_client import NmsClient, NmsConfig, NmsSseTransport
from protocol.pure_client_flow import run_pure_client_flow
from shared.run_constants import FINAL_STATE_TASK_COMPLETED, PROTOCOL_PURE

CASES: dict[int, dict[str, Any]] = {case["id"]: case for case in load_cases()}
CREATE_IDS: tuple[int, ...] = tuple(
    case_id for case_id, case in sorted(CASES.items()) if case["operation"] == "Create"
)
MODIFY_IDS: tuple[int, ...] = tuple(
    case_id for case_id, case in sorted(CASES.items()) if case["operation"] == "Modify"
)

#: The answer the tester gives the baseline Create's single confirmation round. The paired
#: Create is a complete request, so a plain acknowledgement is enough; the simulator merges no
#: new value and validates the baseline unchanged.
BASELINE_ANSWER = "Confirmed. Proceed with the request as stated."

#: Budgets of one in-process pure run.
_REQUEST_TIMEOUT_SECONDS = 5.0
_CALL_TIMEOUT_SECONDS = 5.0
_MAX_ROUNDS = 5


def _sim_config(*, hold_working: bool) -> SimConfig:
    """Return the OSP replay profile of the simulator."""
    return SimConfig(
        fidelity=FIDELITY_FAST,
        negotiation_carrier=CARRIER_MESSAGE,
        window_policy=WINDOW_POLICY_ANY,
        rate_policy=RATE_POLICY_OPTIONAL,
        long_outage_feasibility=True,
        pure_question=PURE_QUESTION_CONFIRM,
        max_rounds=_MAX_ROUNDS,
        hold_working=hold_working,
    )


async def _run_pure(
    user_request: str,
    answer: Callable[[dict[str, object]], str],
    *,
    sim_app: Any,
    task_id: str | None = None,
    context_id: str | None = None,
) -> tuple[ClientFlowResult, list[str]]:
    """Run one pure client flow against the shared in-process simulator."""
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sim_app),
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
    answered: list[str] = []

    async def on_answer(event: dict[str, object], text: str) -> None:
        del event
        answered.append(text)

    try:
        transport = NmsSseTransport(nms_client, protocol=PROTOCOL_PURE)
        result = await run_pure_client_flow(
            transport=transport,
            user_request=user_request,
            answer=answer,
            on_answer=on_answer,
            max_rounds=_MAX_ROUNDS,
            call_timeout_seconds=_CALL_TIMEOUT_SECONDS,
            plain_questions=True,
            task_id=task_id,
            context_id=context_id,
        )
    finally:
        await nms_client.aclose()
        await http_client.aclose()
    return result, answered


def _scripted(answers: list[str]) -> Callable[[dict[str, object]], str]:
    """Return an answer callback replaying ``answers`` in order."""
    remaining = list(answers)

    def answer(event: dict[str, object]) -> str:
        del event
        if not remaining:
            raise AssertionError("the pure-A2A answer script is exhausted")
        return remaining.pop(0)

    return answer


def _expected_rounds(case: dict[str, Any]) -> int:
    """Return the A2A round count the workbook records for one case."""
    negotiation = case["expect"]["protocols"]["a2a"]["negotiation"]
    return int(negotiation["expected_rounds"])


def _answers(case: dict[str, Any]) -> list[str]:
    """Return the P-column answers of one case, checked against the expected round count."""
    answers = pure_answers(case)
    assert len(answers) == _expected_rounds(case), case["id"]
    return answers


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", CREATE_IDS)
async def test_every_osp_create_case_completes_in_pure_a2a(case_id: int) -> None:
    case = CASES[case_id]
    answers = _answers(case)
    sim_app = build_sim_app(_sim_config(hold_working=False))

    result, answered = await _run_pure(
        str(case["user_input"]), _scripted(answers), sim_app=sim_app
    )

    assert answered == answers
    assert result.final_state == FINAL_STATE_TASK_COMPLETED, (
        f"case {case_id} ended as {result.final_state}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", MODIFY_IDS)
async def test_every_osp_modify_case_continues_its_baseline_in_pure_a2a(case_id: int) -> None:
    case = CASES[case_id]
    answers = _answers(case)
    baseline_request = str(case["baseline"]["request"])
    sim_app = build_sim_app(_sim_config(hold_working=True))

    baseline, baseline_answered = await _run_pure(
        baseline_request,
        _scripted([BASELINE_ANSWER]),
        sim_app=sim_app,
    )
    assert baseline_answered == [BASELINE_ANSWER], case_id
    assert baseline.final_state == FINAL_STATE_TASK_COMPLETED, (
        f"case {case_id} baseline ended as {baseline.final_state}"
    )
    assert baseline.task_id and baseline.context_id, case_id

    result, answered = await _run_pure(
        str(case["user_input"]),
        _scripted(answers),
        sim_app=sim_app,
        task_id=baseline.task_id,
        context_id=baseline.context_id,
    )

    assert answered == answers
    assert result.final_state == FINAL_STATE_TASK_COMPLETED, (
        f"case {case_id} ended as {result.final_state}"
    )
    assert result.task_id == baseline.task_id, case_id
    assert result.context_id == baseline.context_id, case_id


def test_the_corpus_round_counts_are_the_ones_the_replay_expects() -> None:
    """A corpus drift must fail loudly instead of silently shrinking the gate."""
    assert len(CASES) == 51
    assert sum(1 for case in CASES.values() if case["operation"] == "Modify") == 20
    assert sum(_expected_rounds(case) for case in CASES.values()) == 61
