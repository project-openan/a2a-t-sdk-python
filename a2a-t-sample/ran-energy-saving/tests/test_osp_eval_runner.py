"""Tests of the offline OSP evaluation runner."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, TypeVar

import pytest
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import (
    ContentValidationError,
    PromptGenerationError,
    SlotValidationError,
)
from eval.osp.compare import compare_intent, compare_task_t
from eval.osp.followup import followup_input
from eval.osp.intent_schema import OSP_INTENT_SCHEMA
from eval.osp.runner import (
    STAGE_ERROR,
    STAGE_FAILED,
    STAGE_PASSED,
    STAGE_RECORDED,
    load_cases,
    run_case,
)

CASES = {case["id"]: case for case in load_cases()}

T = TypeVar("T")


@dataclass
class FakePromptClient:
    """SDK double keyed by the case's operator text."""

    intent_overrides: Mapping[int, Mapping[str, object]] = field(default_factory=dict)
    validate_outcomes: Mapping[int | tuple[int, str], str] = field(default_factory=dict)
    generation_errors: Mapping[int | tuple[int, str], PromptGenerationError] = field(
        default_factory=dict
    )
    last_case_id: int | None = None
    current_segment: str = "original"
    calls: list[tuple[int | None, str]] = field(default_factory=list)

    def _case_for_text(self, text: str) -> Mapping[str, Any]:
        for case in CASES.values():
            if case["user_input"] == text:
                self.current_segment = "original"
                return case
            if followup_input(case) == text:
                self.current_segment = "followup"
                return case
        raise AssertionError(f"unknown user input: {text!r}")

    def _segment_value(self, values: Mapping[int | tuple[int, str], T], default: T) -> T:
        value = values.get((self.last_case_id, self.current_segment))
        return value if value is not None else values.get(self.last_case_id, default)

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> object:
        case = self._case_for_text(text)
        self.last_case_id = int(case["id"])
        self.calls.append((self.last_case_id, "generate"))
        error = self.generation_errors.get((self.last_case_id, self.current_segment))
        if error is None:
            error = self.generation_errors.get(self.last_case_id)
        if error is not None:
            raise error
        return SimpleNamespace(
            prompt_text=case["expect"]["protocols"]["a2a-t"]["task_t"],
            template_uri=template_uri,
        )

    def validate_task_prompt_and_data_filling(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> object:
        del prompt, template_uri
        assert self.last_case_id is not None
        case = CASES[self.last_case_id]
        self.calls.append((self.last_case_id, "intent" if schema is OSP_INTENT_SCHEMA else "runtime"))
        if schema is OSP_INTENT_SCHEMA:
            intent = self.intent_overrides.get(
                self.last_case_id, case["expect"]["intent"]
            )
            return SimpleNamespace(data=dict(intent))
        outcome = self._segment_value(self.validate_outcomes, "pass")
        if outcome == "blocked":
            raise ContentValidationError(
                ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED,
                {"reason": "test"},
                message="blocked by test",
            )
        if outcome == "negotiable":
            raise ContentValidationError(
                ErrorCatalog.CONTENT_PARAM_MISSING,
                {"section_label": "Area"},
                message="missing area",
                errors=[
                    SlotValidationError(
                        slot_name="region",
                        code="content.param_missing",
                        message="missing area",
                        facts={"section_label": "Area"},
                    )
                ],
            )
        if outcome == "error":
            raise RuntimeError("validation crashed")
        return SimpleNamespace(data={})


def _statuses(outcome: Any) -> dict[str, str]:
    return {stage.stage: stage.status for stage in outcome.stages}


def _statuses_of(stages: Any) -> dict[str, str]:
    return {stage.stage: stage.status for stage in stages}


def test_compare_intent_normalizes_notation() -> None:
    expected = {
        "Operation type": "Create",
        "Energy saving cell RAT": "ALL",
        "Energy saving aggressiveness": "",
        "Energy saving region": "A2ATestRegion",
        "Whether Experience-Preserving Energy Saving is Required": "",
        "Special Requirements for Carrier Shutdown Frequencies": "",
        "Energy saving time": "00:00:00-00:59:59&01:00:00-01:59:59",
        "Frequencies of carriers that can be shut down": ["1600"],
        "Gain target": "10",
        "Guaranteed throughput target by zone": [
            {
                "Guaranteed throughput target": "10Mbps",
                "Effective time period": "00:00:00-00:59:59&01:00:00-01:59:59",
            }
        ],
        "Scenario-based energy saving": [],
    }
    actual = {
        "Operation type": "create",
        "Energy saving cell RAT": "LNR",
        "Energy saving aggressiveness": "",
        "Energy saving region": "a2atestregion",
        "Whether Experience-Preserving Energy Saving is Required": "",
        "Special Requirements for Carrier Shutdown Frequencies": "",
        "Energy saving time": "00:00-02:00",
        "Frequencies of carriers that can be shut down": ["1,600"],
        "Gain target": "10%",
        "Guaranteed throughput target by zone": [
            {
                "Guaranteed throughput target": "10 Mbps",
                "Effective time period": "00:00-02:00",
            }
        ],
        "Scenario-based energy saving": [],
    }
    # 1,600 normalizes to 1600 only when the number regex is applied to the whole string.
    assert compare_intent(expected, actual) == []


def test_compare_intent_reports_the_missing_region() -> None:
    expected = CASES[1]["expect"]["intent"]
    actual = dict(expected)
    actual["Energy saving region"] = ""
    diffs = compare_intent(expected, actual)
    assert [diff.path for diff in diffs] == ["intent.Energy saving region"]


def test_compare_task_t_ignores_fixed_sections() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = (
        expected.replace("## Task Description", "## Task Description\nchanged prose")
        .replace("## Terminology Explanation", "## Terminology Explanation\nchanged terms")
        .replace("## Expected Output", "## Expected Output\nchanged output")
    )
    assert compare_task_t(expected, actual) == []


def test_compare_task_t_reports_a_context_rate_change() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace("720p video", "1080p video")
    diffs = compare_task_t(expected, actual)
    assert [diff.path for diff in diffs] == ["task_t.Task Context"]


def test_compare_task_t_accepts_the_runtime_context_phrasing() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace(
        "Ensuring user experience for 720p video",
        "Rate guarantee goal: ensuring user experience for 720p video (full period).",
    )
    assert compare_task_t(expected, actual) == []


def test_compare_task_t_accepts_the_runtime_time_notation() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace(
        'Energy saving time range: start time "00:00:00", end time "22:00:00"',
        "Energy saving time range: 00:00-22:00 (recurring daily)",
    ).replace(
        "Ensuring user experience for 720p video",
        "Rate guarantee goal: user experience for 720p video.",
    )
    assert compare_task_t(expected, actual) == []


def test_compare_task_t_accepts_a_prefixed_task_object() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace("## Task Object\nA2ATestRegion", "## Task Object\nThe area is A2ATestRegion.")
    assert compare_task_t(expected, actual) == []


def test_compare_task_t_accepts_the_runtime_site_context() -> None:
    expected = CASES[32]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace(
        'Site list: ["LNR_KPI_7745","NES_27A_321071"], '
        "Time-segmented rate guarantee target: {08:00-18:00: 10Mbps}, "
        "Background event: power outage from 08:00 to 18:00.",
        "Outage time: Day 2, 08:00~18:00; Affected site list: "
        "LNR_KPI_7745, NES_27A_321071; Rate guarantee goal: {08:00~18:00, 10Mbps}.",
    )
    assert compare_task_t(expected, actual) == []


def test_compare_task_t_reports_an_extra_context_rate() -> None:
    expected = CASES[49]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace(
        "Set the AVG DL THP to 10.",
        "Rate guarantee goal: 2 Mbps; AVG DL THP: 10 Mbps.",
    )
    diffs = compare_task_t(expected, actual)
    assert [diff.path for diff in diffs] == ["task_t.Task Context"]


def test_compare_task_t_ignores_context_word_order() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace(
        "Ensuring user experience for 720p video",
        "Rate guarantee goal: 720p video user experience",
    )
    assert compare_task_t(expected, actual) == []


def test_compare_task_t_accepts_a_to_time_range() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace(
        'Energy saving time range: start time "00:00:00", end time "22:00:00"',
        "Energy saving time range: 00:00 to 22:00",
    )
    assert compare_task_t(expected, actual) == []


def test_compare_task_t_accepts_rate_target_paraphrases() -> None:
    expected = CASES[2]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace(
        "Ensure the UX target is at least 10",
        "Rate guarantee goal: at least 10",
    )
    assert compare_task_t(expected, actual) == []


def test_compare_task_t_accepts_a_carrier_frequency_prefix() -> None:
    expected = CASES[16]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace(
        "EARFCN 1600 can be shut down",
        "Carrier frequency EARFCN 1600 can be shut down",
    )
    assert compare_task_t(expected, actual) == []


def test_compare_intent_treats_null_and_empty_lists_as_equal() -> None:
    expected = {
        "Frequencies of carriers that can be shut down": [],
        "Guaranteed throughput target by zone": [],
        "Scenario-based energy saving": [],
    }
    actual = {
        "Frequencies of carriers that can be shut down": None,
        "Guaranteed throughput target by zone": None,
        "Scenario-based energy saving": None,
    }
    assert compare_intent(expected, actual) == []


def test_runner_passes_a_clean_case() -> None:
    client = FakePromptClient()
    outcome = run_case(CASES[1], client, stop_after="validate")
    assert outcome.passed is True
    assert _statuses(outcome) == {
        "intent": STAGE_PASSED,
        "task_t": STAGE_PASSED,
        "validate": STAGE_PASSED,
    }


def test_runner_records_the_conflict_fields() -> None:
    client = FakePromptClient(validate_outcomes={(40, "original"): "blocked"})
    outcome = run_case(CASES[40], client, stop_after="validate")
    assert outcome.passed is True
    # The base case disables the conflicting intent/Task-T assertions; the follow-up override
    # turns them back on, so the replacement request is judged on both.
    assert _statuses(outcome) == {
        "intent": STAGE_PASSED,
        "task_t": STAGE_PASSED,
        "validate": STAGE_PASSED,
    }
    assert _statuses_of(outcome.interception) == {
        "intent": STAGE_RECORDED,
        "task_t": STAGE_RECORDED,
        "validate": STAGE_PASSED,
    }


def test_runner_records_an_unfilled_validation_result() -> None:
    # No corpus case is unfilled any more, but the contract still has to hold for generated
    # cases whose source validation column is empty.
    unfilled = copy.deepcopy(CASES[49])
    unfilled["expect"]["protocols"]["a2a-t"]["validate"] = None
    unfilled["expect"]["protocols"]["a2a-t"]["validate_label"] = "未填写"
    unfilled["assert"]["a2a-t"]["validate"] = False
    client = FakePromptClient()
    outcome = run_case(unfilled, client, stop_after="validate")
    assert outcome.passed is True
    assert _statuses(outcome) == {
        "intent": STAGE_PASSED,
        "task_t": STAGE_PASSED,
        "validate": STAGE_RECORDED,
    }


def test_runner_handles_a_blocked_generation_as_the_expected_outcome() -> None:
    error = PromptGenerationError(
        ErrorCatalog.SLOT_NOT_PROVIDED,
        {"slot_label": "Area"},
        message="'Area' is not provided in the input.",
    )
    client = FakePromptClient(generation_errors={(40, "original"): error})
    outcome = run_case(CASES[40], client, stop_after="validate")
    assert outcome.passed is True
    assert _statuses(outcome) == {
        "intent": STAGE_PASSED,
        "task_t": STAGE_PASSED,
        "validate": STAGE_PASSED,
    }
    assert outcome.interception is not None
    validate_stage = next(
        stage for stage in outcome.interception if stage.stage == "validate"
    )
    assert validate_stage.actual == "blocked"


def test_blocked_case_runs_the_followup_segment() -> None:
    client = FakePromptClient(validate_outcomes={(25, "original"): "blocked"})
    outcome = run_case(CASES[25], client, stop_after="validate")
    assert outcome.passed is True
    assert _statuses(outcome) == {
        "intent": STAGE_PASSED,
        "task_t": STAGE_PASSED,
        "validate": STAGE_PASSED,
    }
    assert _statuses_of(outcome.interception) == {
        "intent": STAGE_RECORDED,
        "task_t": STAGE_RECORDED,
        "validate": STAGE_PASSED,
    }
    assert client.calls.count((25, "generate")) == 2, "both the blocked and the follow-up input ran"


def test_blocked_case_fails_when_the_original_request_is_not_blocked() -> None:
    client = FakePromptClient()  # the original request validates as pass
    outcome = run_case(CASES[25], client, stop_after="validate")
    assert outcome.passed is False
    assert _statuses(outcome) == {
        "intent": STAGE_PASSED,
        "task_t": STAGE_PASSED,
        "validate": STAGE_PASSED,
    }
    assert _statuses_of(outcome.interception)["validate"] == STAGE_FAILED


def test_blocked_case_reports_a_followup_generation_failure() -> None:
    error = PromptGenerationError(
        ErrorCatalog.SLOT_NOT_PROVIDED,
        {"slot_label": "Site list"},
        message="'Site list' is not provided in the input.",
    )
    client = FakePromptClient(
        generation_errors={(25, "followup"): error},
        validate_outcomes={(25, "original"): "blocked"},
    )
    outcome = run_case(CASES[25], client, stop_after="validate")
    assert outcome.passed is False
    assert _statuses_of(outcome.interception)["validate"] == STAGE_PASSED
    assert _statuses(outcome)["validate"] == STAGE_FAILED


def test_runner_fails_when_an_unexpected_block_stops_generation() -> None:
    error = PromptGenerationError(
        ErrorCatalog.SLOT_NOT_PROVIDED,
        {"slot_label": "Area"},
        message="'Area' is not provided in the input.",
    )
    client = FakePromptClient(generation_errors={1: error})
    outcome = run_case(CASES[1], client, stop_after="validate")
    assert outcome.passed is False
    assert _statuses(outcome) == {
        "intent": STAGE_ERROR,
        "task_t": STAGE_ERROR,
        "validate": STAGE_FAILED,
    }


def test_runner_stops_after_intent() -> None:
    client = FakePromptClient()
    outcome = run_case(CASES[1], client, stop_after="intent")
    assert _statuses(outcome) == {"intent": STAGE_PASSED}
    assert client.calls == [(1, "generate"), (1, "intent")]


def test_runner_records_the_intent_of_a_negotiation_case() -> None:
    client = FakePromptClient()
    outcome = run_case(CASES[32], client, stop_after="intent")
    assert outcome.passed is True
    assert _statuses(outcome) == {"intent": STAGE_RECORDED}
    assert "post-negotiation" in outcome.stages[0].message


def test_runner_fails_the_intent_stage_on_a_wrong_extraction() -> None:
    wrong = dict(CASES[1]["expect"]["intent"])
    wrong["Energy saving region"] = ""
    client = FakePromptClient(intent_overrides={1: wrong})
    outcome = run_case(CASES[1], client, stop_after="intent")
    assert outcome.passed is False
    assert _statuses(outcome) == {"intent": STAGE_FAILED}


def test_validate_stage_treats_negotiable_errors_as_not_blocked() -> None:
    client = FakePromptClient(validate_outcomes={1: "negotiable"})
    outcome = run_case(CASES[1], client, stop_after="validate")
    assert _statuses(outcome)["validate"] == STAGE_PASSED


def test_validate_stage_reports_a_crash() -> None:
    client = FakePromptClient(validate_outcomes={1: "error"})
    outcome = run_case(CASES[1], client, stop_after="validate")
    assert _statuses(outcome)["validate"] == STAGE_ERROR
    assert outcome.passed is False


def test_stop_after_rejects_an_unsupported_stage() -> None:
    with pytest.raises(ValueError, match="unsupported stop_after"):
        run_case(CASES[1], FakePromptClient(), stop_after="send")


def test_time_segment_does_not_swallow_a_rate_as_seconds() -> None:
    """``{08:00-24:00:10Mbps}`` keeps ``10`` as the rate; ``:10`` is not seconds."""
    from eval.osp.compare import _contexts_match

    assert _contexts_match(
        "Time-segmented rate guarantee target: {00:00-08:00: 2Mbps}, {08:00-24:00:10Mbps}",
        "Time-segmented rate guarantee goal: {00:00-08:00: 2Mbps}, {08:00-24:00: 10Mbps}",
    )


def test_scenario_outage_is_the_same_as_power_outage() -> None:
    from eval.osp.compare import compare_intent

    expected = CASES[25]["expect"]["intent"]
    actual = dict(expected)
    actual["Scenario-based energy saving"] = [
        {
            **expected["Scenario-based energy saving"][0],
            "Scenario": "Outage",
        }
    ]
    assert compare_intent(expected, actual) == []


def test_intent_defaults_fill_rat_and_full_day() -> None:
    from eval.osp.runner import _apply_intent_defaults

    data, applied = _apply_intent_defaults(
        {
            "Energy saving cell RAT": None,
            "Energy saving time": "",
            "Scenario-based energy saving": [],
        }
    )
    assert data["Energy saving cell RAT"] == "ALL"
    assert len(str(data["Energy saving time"]).split("&")) == 24
    assert applied == ("Energy saving cell RAT=ALL", "Energy saving time=full day")


def test_intent_defaults_keep_a_site_level_time_empty() -> None:
    from eval.osp.runner import _apply_intent_defaults

    data, applied = _apply_intent_defaults(
        {
            "Energy saving cell RAT": "ALL",
            "Energy saving time": "",
            "Scenario-based energy saving": [{"Site list": ["NE_A"]}],
        }
    )
    assert data["Energy saving time"] == ""
    assert applied == ()


def test_runner_applies_the_schema_defaults_before_comparison() -> None:
    missing = dict(CASES[16]["expect"]["intent"])
    missing["Energy saving cell RAT"] = None
    missing["Energy saving time"] = ""
    client = FakePromptClient(intent_overrides={16: missing})
    outcome = run_case(CASES[16], client, stop_after="intent")
    assert outcome.passed is True
    assert _statuses(outcome) == {"intent": STAGE_PASSED}
    assert "schema defaults" in outcome.stages[0].message


def test_time_ranges_with_seconds_normalize_to_the_same_hours() -> None:
    """A range ending at HH:59 covers that hour; the expanded SDK form must match it."""
    from eval.osp.compare import _normalize_time

    assert _normalize_time("08:00:00-09:59:59") == _normalize_time(
        "08:00:00-08:59:59&09:00:00-09:59:59"
    )
    assert _normalize_time("19:00-22:00") == ("19", "20", "21")


def test_task_context_label_separator_is_not_a_business_difference() -> None:
    """'Label: value' and 'Set the label to value' state the same context."""
    from eval.osp.compare import _contexts_match

    assert _contexts_match("Set the Power Savings Ratio to 10%", "Power Savings Ratio: 10%.")
    assert _contexts_match(
        "maximize user experience assurance",
        "with the objective to maximize user experience assurance.",
    )
