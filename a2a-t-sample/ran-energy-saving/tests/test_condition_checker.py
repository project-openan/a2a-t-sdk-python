"""Contract tests of the client-side condition checker (D5/D19)."""

from __future__ import annotations

from collections.abc import Mapping

from shared.condition_checker import (
    ALLOWED_NO,
    ALLOWED_UNKNOWN,
    ALLOWED_YES,
    CONDITION_SCHEMA,
    ConditionAnswer,
    ConditionChecker,
)
from shared.judge_llm import JudgeUnavailable
from shared.models import DecisionBasis


class _Judge:
    configured = True

    def __init__(self, response: object) -> None:
        self.response = response
        self.prompts: list[str] = []

    async def complete_json(
        self,
        prompt: str,
        *,
        system: str = "",
        json_schema: object | None = None,
    ) -> dict[str, object]:
        del system, json_schema
        self.prompts.append(prompt)
        if isinstance(self.response, BaseException):
            raise self.response
        assert isinstance(self.response, Mapping)
        return dict(self.response)


def _basis() -> DecisionBasis:
    return DecisionBasis(
        principles="Feasibility: accept the server recommendation.",
        principles_version="2026-11-23.1",
        client_conditions="During the outage 2Mbps is acceptable; below 2Mbps is not.",
    )


async def test_a_candidate_value_is_answered_from_the_basis() -> None:
    judge = _Judge({"allowed": "no", "reason": "1Mbps is below the stated floor"})

    answer = await ConditionChecker(judge).check(
        "Is this rate acceptable?", "1Mbps", basis=_basis()
    )

    assert answer == ConditionAnswer(ALLOWED_NO, "1Mbps is below the stated floor")
    assert "1Mbps" in judge.prompts[0]
    assert "below 2Mbps is not" in judge.prompts[0]


async def test_a_disabled_checker_is_unknown() -> None:
    judge = _Judge({"allowed": "yes", "reason": "fine"})

    answer = await ConditionChecker(judge, enabled=False).check(
        "Is this rate acceptable?", "2Mbps", basis=_basis()
    )

    assert answer.allowed == ALLOWED_UNKNOWN
    assert judge.prompts == []


async def test_an_empty_basis_is_unknown() -> None:
    judge = _Judge({"allowed": "yes", "reason": "fine"})

    answer = await ConditionChecker(judge).check(
        "Is this rate acceptable?", "2Mbps", basis=DecisionBasis()
    )

    assert answer.allowed == ALLOWED_UNKNOWN


async def test_a_judge_failure_is_unknown_never_yes() -> None:
    judge = _Judge(JudgeUnavailable("endpoint down"))

    answer = await ConditionChecker(judge).check(
        "Is this rate acceptable?", "2Mbps", basis=_basis()
    )

    assert answer.allowed == ALLOWED_UNKNOWN


async def test_an_invalid_answer_is_unknown() -> None:
    judge = _Judge({"allowed": "maybe", "reason": "unclear"})

    answer = await ConditionChecker(judge).check(
        "Is this rate acceptable?", "2Mbps", basis=_basis()
    )

    assert answer.allowed == ALLOWED_UNKNOWN


async def test_a_yes_answer_is_preserved() -> None:
    judge = _Judge({"allowed": "yes", "reason": "2Mbps is stated as acceptable"})

    answer = await ConditionChecker(judge).check(
        "Is this rate acceptable?", "2Mbps", basis=_basis()
    )

    assert answer.allowed == ALLOWED_YES


def test_the_schema_requires_a_typed_answer() -> None:
    assert CONDITION_SCHEMA["required"] == ["allowed", "reason"]
    assert set(CONDITION_SCHEMA["properties"]["allowed"]["enum"]) == {
        ALLOWED_YES,
        ALLOWED_NO,
        ALLOWED_UNKNOWN,
    }
