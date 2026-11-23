"""Unit tests of the client decision-quality judge.

The quality table is an appendix: it reads the case's expected negotiation reply (the Q column)
and the run evidence, and it must never turn a transport/验收 result into something else. These
tests pin the deterministic checks (action, structure, pre-send interception) and the model-backed
checks (value equivalence, decision-basis coverage).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from eval.osp.client_quality import ClientQualityObservation, judge_client_quality
from shared.run_constants import FINAL_STATE_INPUT_INVALID

#: A Q-column reply in the heading form the corpus uses for feasibility negotiation.
_ACCEPT_REPLY = (
    "## Feasibility Negotiation Result\n\n"
    "Accept\n\n"
    "## Feasibility Negotiation Result in Detail\n\n"
    "Reduce throughput target from 10Mbps to 2Mbps for the power outage period."
)


class _Judge:
    """Fake judge client returning one fixed structured answer."""

    configured = True

    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = dict(response)
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def complete_json(
        self,
        prompt: str,
        *,
        system: str = "",
        json_schema: object | None = None,
    ) -> dict[str, object]:
        del json_schema
        self.prompts.append(prompt)
        self.systems.append(system)
        return dict(self.response)


def _case(
    rounds: list[str],
    *,
    expected: bool = True,
    conditions: str = "",
) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": 30,
        "expect": {
            "protocols": {
                "a2a-t": {
                    "negotiation": {
                        "expected": expected,
                        "rounds": list(rounds),
                    }
                }
            }
        },
    }
    if conditions:
        case["client_conditions"] = conditions
    return case


def _observation(
    *,
    round_number: int = 1,
    kind: str = "feasibility",
    proposal: str = "Reduce the throughput target to 2Mbps during the power outage.",
    decision: str = "accept",
    accepted_text: str = "",
    structure_ok: bool | None = True,
) -> ClientQualityObservation:
    return ClientQualityObservation(
        round=round_number,
        kind=kind,
        proposal=proposal,
        decision=decision,
        accepted_text=accepted_text,
        structure_ok=structure_ok,
    )


async def test_an_accept_that_matches_the_expected_reply_passes() -> None:
    result = await judge_client_quality(
        case=_case([_ACCEPT_REPLY]),
        observations=[_observation()],
        judge=_Judge(
            {"verdict": "equal", "covered": True, "reason": "the agreed value matches"}
        ),
    )

    assert result.quality == "pass"
    row = result.rounds[0]
    assert row.action == "pass"
    assert row.value == "pass"
    assert row.structure == "pass"
    assert row.quality == "pass"
    assert row.method == "model"


async def test_a_reject_where_the_expectation_is_accept_fails_the_action_check() -> None:
    result = await judge_client_quality(
        case=_case([_ACCEPT_REPLY]),
        observations=[_observation(decision="reject")],
        judge=_Judge({"verdict": "equal", "covered": True}),
    )

    assert result.quality == "fail"
    assert result.rounds[0].action == "fail"
    assert result.rounds[0].quality == "fail"


async def test_a_presend_interception_fails_every_round_with_the_gate_reason() -> None:
    case = _case([_ACCEPT_REPLY, _ACCEPT_REPLY])
    gate_reason = "Task-T 校验未通过：缺少停机窗口。"

    result = await judge_client_quality(
        case=case,
        observations=[],
        judge=_Judge({"verdict": "equal", "covered": True}),
        final_state=FINAL_STATE_INPUT_INVALID,
        gate_reason=gate_reason,
    )

    assert result.quality == "fail"
    assert len(result.rounds) == 2
    for row in result.rounds:
        assert row.review == "fail"
        assert row.quality == "fail"
        assert gate_reason in row.reason


async def test_a_proposal_without_an_expected_text_is_unjudged() -> None:
    result = await judge_client_quality(
        case=_case([]),
        observations=[_observation()],
        judge=_Judge({"verdict": "not_equal", "covered": True}),
    )

    assert result.quality == "unjudged"
    row = result.rounds[0]
    assert row.quality == "unjudged"
    assert row.method == "unjudged"


async def test_a_missing_judge_with_a_semantic_expectation_is_unjudged() -> None:
    result = await judge_client_quality(
        case=_case([_ACCEPT_REPLY]),
        observations=[_observation()],
        judge=None,
    )

    assert result.quality == "unjudged"
    row = result.rounds[0]
    assert row.value == "unjudged"
    assert row.quality == "unjudged"


async def test_a_not_equal_judgement_fails_and_is_flagged_for_review() -> None:
    result = await judge_client_quality(
        case=_case([_ACCEPT_REPLY]),
        observations=[_observation(accepted_text="Reduce the target to 5Mbps.")],
        judge=_Judge(
            {"verdict": "not_equal", "covered": True, "reason": "the rate is 5Mbps, not 2Mbps"}
        ),
    )

    assert result.quality == "fail"
    row = result.rounds[0]
    assert row.value == "fail"
    assert row.method == "model"
    assert result.needs_human_review is True


async def test_an_unclear_judgement_is_unjudged_instead_of_failed() -> None:
    result = await judge_client_quality(
        case=_case([_ACCEPT_REPLY]),
        observations=[_observation()],
        judge=_Judge(
            {"verdict": "unclear", "covered": True, "reason": "the reply does not state the rate"}
        ),
    )

    assert result.quality == "unjudged"
    row = result.rounds[0]
    assert row.value == "unjudged"
    assert row.quality == "unjudged"
    assert result.needs_human_review is False


async def test_a_proposal_outside_the_decision_basis_fails_the_coverage_check() -> None:
    conditions = "停电期间可以接受把速率降到 2Mbps；站点不能改。"
    judge = _Judge(
        {
            "verdict": "equal",
            "covered": False,
            "reason": "the proposal changes the site, which the client conditions forbid",
        }
    )

    result = await judge_client_quality(
        case=_case([_ACCEPT_REPLY], conditions=conditions),
        observations=[
            _observation(proposal="Reduce the target to 2Mbps and move the task to another site.")
        ],
        judge=judge,
        client_conditions=conditions,
    )

    assert result.quality == "fail"
    assert result.rounds[0].coverage == "fail"
    assert result.rounds[0].quality == "fail"
    assert judge.prompts
    assert conditions in judge.prompts[0]
    assert "move the task to another site" in judge.prompts[0]


async def test_an_accept_without_a_valid_structure_fails_the_structure_check() -> None:
    result = await judge_client_quality(
        case=_case([_ACCEPT_REPLY]),
        observations=[_observation(structure_ok=False)],
        judge=_Judge({"verdict": "equal", "covered": True}),
    )

    assert result.quality == "fail"
    assert result.rounds[0].structure == "fail"


async def test_the_result_serializes_to_the_columns_the_report_needs() -> None:
    result = await judge_client_quality(
        case=_case([_ACCEPT_REPLY]),
        observations=[_observation()],
        judge=_Judge({"verdict": "equal", "covered": True, "reason": "same"}),
    )

    data = result.to_dict()

    assert data["quality"] == "pass"
    assert data["method"] == "model"
    assert data["needsHumanReview"] is False
    row = data["rounds"][0]
    for key in (
        "round",
        "proposal",
        "expected",
        "decision",
        "action",
        "value",
        "structure",
        "review",
        "coverage",
        "quality",
        "method",
        "reason",
    ):
        assert key in row
