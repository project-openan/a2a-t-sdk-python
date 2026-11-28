"""Unit tests of the final-intent judge: structured first, one model call as fallback.

The tester's final-intent dimension compares the corpus intent with the downstream's own reading.
Most differences are notation (``10 Mbps`` vs ``10Mbps``, ``4G`` vs ``LTE``); only a genuine
residual is worth a model call, and that call must decide every residual field at once. A missing
reading or an unavailable judge is ``unjudged``/``no_basis``, never a fabricated failure.

These tests pin the contract of :mod:`eval.osp.intent_judge`:

* :func:`evaluate_intent` classifies exact / normalized / residual and returns exactly the residual
  rows;
* :func:`judge_intent` makes zero judge calls for exact and normalized intents, one call for a
  residual, and never invents a verdict when the evidence or the judge is missing;
* the prompt carries only business context (paths, values, request, proposal), no protocol or
  implementation identity, and the same comparison is cached until the cache is cleared.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from eval.osp.compare import INTENT_ROW_CHANGED, INTENT_ROW_SAME
from eval.osp.intent_judge import (
    INTENT_JUDGE_SCHEMA,
    KIND_EXACT,
    KIND_NORMALIZED,
    KIND_RESIDUAL,
    METHOD_MODEL,
    METHOD_STRUCTURED,
    METHOD_UNJUDGED,
    STATUS_MISMATCH,
    STATUS_NO_BASIS,
    STATUS_PASS,
    STATUS_UNJUDGED,
    clear_intent_judge_cache,
    evaluate_intent,
    expected_intent_digest,
    judge_intent,
)
from shared.judge_llm import JudgeUnavailable

#: Residual paths of :func:`_residual_observed` against :func:`_intent`.
_REGION_PATH = "intent.Energy saving region"
_RATE_PATH = "intent.Guaranteed throughput target by zone[0].Guaranteed throughput target"

#: Business context that must reach the judge prompt, with no protocol wording.
_USER_REQUEST = "Plan energy saving for RegionA during the night."
_PROPOSAL = "The server proposed 5Mbps from 08:00 to 12:00."


@pytest.fixture(autouse=True)
def _empty_intent_judge_cache() -> None:
    """Every test starts from a cold cache so call counts are meaningful."""
    clear_intent_judge_cache()


class _Judge:
    """Fake independent judge returning one fixed structured answer or raising one error."""

    configured = True

    def __init__(self, response: object) -> None:
        self.response = response
        self.prompts: list[str] = []
        self.schemas: list[object] = []

    async def complete_json(
        self,
        prompt: str,
        *,
        system: str = "",
        json_schema: object | None = None,
    ) -> dict[str, object]:
        del system
        self.prompts.append(prompt)
        self.schemas.append(json_schema)
        if isinstance(self.response, BaseException):
            raise self.response
        assert isinstance(self.response, Mapping)
        return dict(self.response)


def _intent(**overrides: object) -> dict[str, object]:
    """Return a small intent document that carries scalar leaves and one nested zone target."""
    intent: dict[str, object] = {
        "Operation type": "Create",
        "Energy saving cell RAT": "LTE",
        "Energy saving region": "RegionA",
        "Energy saving time": "08:00-18:00",
        "Guaranteed throughput target by zone": [
            {"Guaranteed throughput target": "10Mbps", "Effective time period": "08:00-18:00"}
        ],
        "Scenario-based energy saving": [],
    }
    intent.update(overrides)
    return intent


def _normalized_observed() -> dict[str, object]:
    """Same business intent as :func:`_intent`, written in another notation."""
    return _intent(
        **{
            "Energy saving cell RAT": "4G",
            "Energy saving region": " regiona ",
            "Guaranteed throughput target by zone": [
                {"Guaranteed throughput target": "10 Mbps", "Effective time period": "08:00-18:00"}
            ],
        }
    )


def _residual_observed() -> dict[str, object]:
    """Two genuine business differences: another region and another rate."""
    return _intent(
        **{
            "Energy saving region": "RegionB",
            "Guaranteed throughput target by zone": [
                {"Guaranteed throughput target": "5Mbps", "Effective time period": "08:00-18:00"}
            ],
        }
    )


def _answer(evaluation: Any, *, equal: bool) -> dict[str, object]:
    """Return one judge answer that covers every residual field."""
    return {
        "all_equal": equal,
        "fields": [
            {"path": row.path, "equal": equal, "reason": "same business value" if equal else "differs"}
            for row in evaluation.residual
        ],
    }


def test_evaluate_intent_is_exact_when_every_leaf_matches_verbatim() -> None:
    expected = _intent()

    evaluation = evaluate_intent(expected, dict(expected))

    assert evaluation.kind == KIND_EXACT
    assert not evaluation.residual
    assert all(row.status == INTENT_ROW_SAME for row in evaluation.rows)


def test_evaluate_intent_is_normalized_when_only_notation_differs() -> None:
    evaluation = evaluate_intent(_intent(), _normalized_observed())

    assert evaluation.kind == KIND_NORMALIZED
    assert not evaluation.residual
    # The verbatim reading changed, the normalized reading agrees: that is what "normalized" means.
    assert any(row.status == INTENT_ROW_CHANGED and row.verdict == INTENT_ROW_SAME for row in evaluation.rows)


def test_evaluate_intent_returns_exactly_the_residual_rows() -> None:
    evaluation = evaluate_intent(_intent(), _residual_observed())

    assert evaluation.kind == KIND_RESIDUAL
    assert {row.path for row in evaluation.residual} == {_REGION_PATH, _RATE_PATH}
    assert all(row.verdict != INTENT_ROW_SAME for row in evaluation.residual)


async def test_exact_and_normalized_intents_make_no_judge_call() -> None:
    judge = _Judge({"all_equal": False})

    exact = await judge_intent(
        _intent(),
        _intent(),
        judge=judge,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )
    normalized = await judge_intent(
        _intent(),
        _normalized_observed(),
        judge=judge,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )

    assert exact.status == STATUS_PASS
    assert exact.method == METHOD_STRUCTURED
    assert normalized.status == STATUS_PASS
    assert normalized.method == METHOD_STRUCTURED
    assert judge.prompts == []


async def test_exact_intent_passes_without_a_judge_at_all() -> None:
    verdict = await judge_intent(
        _intent(),
        _intent(),
        judge=None,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )

    assert verdict.status == STATUS_PASS
    assert verdict.method == METHOD_STRUCTURED


async def test_a_residual_passes_when_the_judge_says_all_fields_are_equal() -> None:
    observed = _residual_observed()
    evaluation = evaluate_intent(_intent(), observed)
    judge = _Judge(_answer(evaluation, equal=True))

    verdict = await judge_intent(
        _intent(),
        observed,
        judge=judge,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )

    assert verdict.status == STATUS_PASS
    assert verdict.method == METHOD_MODEL
    assert {field["path"] for field in verdict.fields} == {_REGION_PATH, _RATE_PATH}
    assert len(judge.prompts) == 1


async def test_a_residual_fails_when_the_judge_says_a_field_differs() -> None:
    observed = _residual_observed()
    evaluation = evaluate_intent(_intent(), observed)
    judge = _Judge(
        {
            "all_equal": False,
            "fields": [
                {
                    "path": row.path,
                    "equal": row.path == _RATE_PATH,
                    "reason": "same rate" if row.path == _RATE_PATH else "another region",
                }
                for row in evaluation.residual
            ],
        }
    )

    verdict = await judge_intent(
        _intent(),
        observed,
        judge=judge,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )

    assert verdict.status == STATUS_MISMATCH
    assert verdict.method == METHOD_MODEL


async def test_a_run_without_an_observed_payload_has_no_basis() -> None:
    judge = _Judge({"all_equal": True, "fields": []})

    verdict = await judge_intent(
        _intent(),
        None,
        judge=judge,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )

    assert verdict.status == STATUS_NO_BASIS
    assert verdict.method == METHOD_UNJUDGED
    assert judge.prompts == []


async def test_an_absent_judge_leaves_a_residual_unjudged() -> None:
    verdict = await judge_intent(
        _intent(),
        _residual_observed(),
        judge=None,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )

    assert verdict.status == STATUS_UNJUDGED
    assert verdict.method == METHOD_UNJUDGED


async def test_an_unavailable_judge_leaves_a_residual_unjudged() -> None:
    judge = _Judge(JudgeUnavailable("judge endpoint is down"))

    verdict = await judge_intent(
        _intent(),
        _residual_observed(),
        judge=judge,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )

    assert verdict.status == STATUS_UNJUDGED
    assert verdict.method == METHOD_UNJUDGED


async def test_an_invalid_judge_answer_leaves_a_residual_unjudged() -> None:
    judge = _Judge({"all_equal": True})  # no per-field verdicts, which the schema requires

    verdict = await judge_intent(
        _intent(),
        _residual_observed(),
        judge=judge,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )

    assert verdict.status == STATUS_UNJUDGED
    assert verdict.method == METHOD_UNJUDGED


async def test_one_call_covers_every_residual_field_and_carries_only_business_context() -> None:
    observed = _residual_observed()
    evaluation = evaluate_intent(_intent(), observed)
    judge = _Judge(_answer(evaluation, equal=True))

    await judge_intent(
        _intent(),
        observed,
        judge=judge,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )

    assert len(judge.prompts) == 1
    prompt = judge.prompts[0]
    for row in evaluation.residual:
        assert row.path in prompt
        assert str(row.expected) in prompt
        assert str(row.actual) in prompt
    assert _USER_REQUEST in prompt
    assert _PROPOSAL in prompt
    lowered = prompt.lower()
    for forbidden in ("a2a", "task-t", "task_t", "sdk", "protocol", "nms"):
        assert forbidden not in lowered
    assert judge.schemas == [INTENT_JUDGE_SCHEMA]


async def test_the_same_comparison_is_cached_until_the_cache_is_cleared() -> None:
    observed = _residual_observed()
    evaluation = evaluate_intent(_intent(), observed)
    judge = _Judge(_answer(evaluation, equal=True))

    first = await judge_intent(
        _intent(),
        observed,
        judge=judge,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )
    second = await judge_intent(
        _intent(),
        observed,
        judge=judge,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )

    assert len(judge.prompts) == 1
    assert second.status == first.status

    clear_intent_judge_cache()
    await judge_intent(
        _intent(),
        observed,
        judge=judge,
        user_request=_USER_REQUEST,
        proposal_summary=_PROPOSAL,
    )

    assert len(judge.prompts) == 2


async def test_a_different_business_context_is_a_different_cache_entry() -> None:
    observed = _residual_observed()
    evaluation = evaluate_intent(_intent(), observed)
    judge = _Judge(_answer(evaluation, equal=True))

    await judge_intent(
        _intent(),
        observed,
        judge=judge,
        user_request="Plan energy saving for RegionA.",
        proposal_summary=_PROPOSAL,
    )
    await judge_intent(
        _intent(),
        observed,
        judge=judge,
        user_request="Save energy in RegionA tonight.",
        proposal_summary=_PROPOSAL,
    )

    assert len(judge.prompts) == 2


async def test_a_contradictory_judge_answer_is_unjudged_not_a_pass() -> None:
    """An aggregate that disagrees with a per-field mismatch is not usable evidence."""
    observed = _residual_observed()
    evaluation = evaluate_intent(_intent(), observed)
    judge = _Judge(
        {
            "all_equal": True,
            "fields": [
                {"path": row.path, "equal": False, "reason": "differs"}
                for row in evaluation.residual
            ],
        }
    )

    verdict = await judge_intent(_intent(), observed, judge=judge)

    assert verdict.status == STATUS_UNJUDGED


async def test_an_unjudged_transport_failure_is_not_cached() -> None:
    observed = _residual_observed()
    judge = _Judge(JudgeUnavailable("endpoint down"))

    first = await judge_intent(_intent(), observed, judge=judge)
    second = await judge_intent(_intent(), observed, judge=judge)

    assert first.status == STATUS_UNJUDGED
    assert second.status == STATUS_UNJUDGED
    assert len(judge.prompts) == 2


async def test_two_unlabelled_judges_do_not_share_a_cached_verdict() -> None:
    observed = _residual_observed()
    evaluation = evaluate_intent(_intent(), observed)
    first_judge = _Judge(_answer(evaluation, equal=True))
    second_judge = _Judge(_answer(evaluation, equal=True))

    await judge_intent(_intent(), observed, judge=first_judge)
    await judge_intent(_intent(), observed, judge=second_judge)

    assert len(first_judge.prompts) == 1
    assert len(second_judge.prompts) == 1


async def test_the_verdict_records_the_expected_digest() -> None:
    observed = _residual_observed()
    evaluation = evaluate_intent(_intent(), observed)
    judge = _Judge(_answer(evaluation, equal=True))

    verdict = await judge_intent(_intent(), observed, judge=judge)

    assert verdict.expected_sha256 == expected_intent_digest(_intent())
    assert verdict.to_dict()["expectedSha256"] == verdict.expected_sha256


def test_the_judge_schema_requires_all_equal_and_a_typed_verdict_per_field() -> None:
    schema = INTENT_JUDGE_SCHEMA

    assert schema["type"] == "object"
    assert "all_equal" in schema["required"]
    assert schema["properties"]["all_equal"]["type"] == "boolean"

    fields = schema["properties"]["fields"]
    assert fields["type"] == "array"
    item = fields["items"]
    assert item["type"] == "object"
    assert {"path", "equal", "reason"} <= set(item["required"])
    assert item["properties"]["path"]["type"] == "string"
    assert item["properties"]["equal"]["type"] == "boolean"
    assert item["properties"]["reason"]["type"] == "string"
