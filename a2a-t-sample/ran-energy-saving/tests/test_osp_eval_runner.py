"""Tests of the offline OSP evaluation runner."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, TypeVar

import pytest
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import PromptGenerationError, SlotValidationError
from eval.osp.compare import (
    INTENT_ROW_ADDED,
    INTENT_ROW_CHANGED,
    INTENT_ROW_MISSING,
    INTENT_ROW_RECORDED,
    INTENT_ROW_SAME,
    TASK_T_COMPARE_SECTIONS,
    compare_intent,
    compare_task_t,
    flatten_intent,
    flatten_task_t,
)
from eval.osp.followup import followup_input
from eval.osp.runner import (
    STAGE_ERROR,
    STAGE_FAILED,
    STAGE_PASSED,
    STAGE_RECORDED,
    CaseOutcome,
    StageOutcome,
    build_report,
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
    validation_filled: Mapping[int, Mapping[str, object]] = field(default_factory=dict)
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


def test_compare_task_t_accepts_the_template_area_label_and_quotes() -> None:
    """The packaged template's example renders ``Area: "Region A"``; that is the same area."""
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace(
        "## Task Object\nA2ATestRegion", '## Task Object\nArea: "A2ATestRegion"'
    )
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
    expected = CASES[42]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace(
        "Set the AVG DL THP to 10Mbps.",
        "Rate guarantee goal: 5Mbps. Set the AVG DL THP to 10Mbps.",
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


def test_flatten_task_t_lists_one_row_per_section() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    rows = flatten_task_t(expected, expected)
    assert [row.path for row in rows] == [
        f"task_t.{section}" for section in TASK_T_COMPARE_SECTIONS
    ]
    assert {row.status for row in rows} == {INTENT_ROW_SAME}
    assert {row.verdict for row in rows} <= {INTENT_ROW_SAME, INTENT_ROW_RECORDED}


def test_flatten_task_t_ignores_whitespace_and_line_breaks() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    # Extra blank lines and trailing spaces per line: the section text is unchanged as written.
    actual = expected.replace("\n", "  \n\n")
    assert {row.status for row in flatten_task_t(expected, actual)} == {INTENT_ROW_SAME}


def test_flatten_task_t_marks_the_changed_section() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace("720p video", "1080p video")
    rows = {row.path: row for row in flatten_task_t(expected, actual)}
    assert rows["task_t.Task Context"].status == INTENT_ROW_CHANGED
    assert rows["task_t.Task Context"].verdict == INTENT_ROW_CHANGED
    changed = [path for path, row in rows.items() if row.status == INTENT_ROW_CHANGED]
    assert changed == ["task_t.Task Context"]


def test_flatten_task_t_keeps_a_rephrasing_verbatim_but_semantically_equal() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace(
        "Ensuring user experience for 720p video",
        "Rate guarantee goal: ensuring user experience for 720p video (full period).",
    )
    rows = {row.path: row for row in flatten_task_t(expected, actual)}
    context = rows["task_t.Task Context"]
    assert context.status == INTENT_ROW_CHANGED
    assert context.verdict == INTENT_ROW_SAME
    assert context.reason == "semantically equal after normalization"
    assert compare_task_t(expected, actual) == []


def test_flatten_task_t_marks_a_missing_and_an_added_section() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace("## Task Object", "## Task Object (renamed)")
    rows = {row.path: row for row in flatten_task_t(expected, actual)}
    assert rows["task_t.Task Object"].status == INTENT_ROW_MISSING
    assert rows["task_t.Task Object"].verdict == INTENT_ROW_MISSING
    assert rows["task_t.Task Object"].actual is None
    assert rows["task_t.Task Object (renamed)"].status == INTENT_ROW_ADDED
    # An extra section is inspection evidence only; it must not turn the case red.
    assert [diff.path for diff in compare_task_t(expected, actual)] == ["task_t.Task Object"]


def test_flatten_task_t_records_the_fixed_template_sections() -> None:
    expected = CASES[1]["expect"]["protocols"]["a2a-t"]["task_t"]
    actual = expected.replace("## Task Description", "## Task Description\nchanged prose")
    rows = {row.path: row for row in flatten_task_t(expected, actual)}
    assert rows["task_t.Task Description"].status == INTENT_ROW_CHANGED
    assert rows["task_t.Task Description"].verdict == INTENT_ROW_RECORDED
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


def test_flatten_intent_compares_nested_zone_fields_leaf_by_leaf() -> None:
    expected = {
        "Operation type": "Create",
        "Guaranteed throughput target by zone": [
            {"Guaranteed throughput target": "10Mbps", "Effective time period": "19:00-22:00"}
        ],
    }
    actual = {
        "Operation type": "Create",
        "Guaranteed throughput target by zone": [
            {"Guaranteed throughput target": "5Mbps", "Effective time period": "19:00-22:00"}
        ],
    }

    rows = {row.path: row for row in flatten_intent(expected, actual)}

    assert rows["intent.Operation type"].status == INTENT_ROW_SAME
    rate = rows[
        "intent.Guaranteed throughput target by zone[0].Guaranteed throughput target"
    ]
    assert rate.status == INTENT_ROW_CHANGED
    assert rate.expected == "10Mbps"
    assert rate.actual == "5Mbps"
    period = rows["intent.Guaranteed throughput target by zone[0].Effective time period"]
    assert period.status == INTENT_ROW_SAME


def test_flatten_intent_marks_normalized_equality_with_a_reason() -> None:
    expected = {"Energy saving time": "19:00-22:00", "Gain target": "10"}
    actual = {
        "Energy saving time": (
            "19:00:00-19:59:59&20:00:00-20:59:59&21:00:00-21:59:59"
        ),
        "Gain target": "10%",
    }

    rows = {row.path: row for row in flatten_intent(expected, actual)}

    # The verbatim text differs; the normalized verdict is what the pass/fail decision uses.
    assert rows["intent.Energy saving time"].status == INTENT_ROW_CHANGED
    assert rows["intent.Energy saving time"].verdict == INTENT_ROW_SAME
    assert rows["intent.Energy saving time"].reason == "归一化后一致"
    assert rows["intent.Gain target"].status == INTENT_ROW_CHANGED
    assert rows["intent.Gain target"].verdict == INTENT_ROW_SAME
    assert compare_intent(expected, actual) == []


def test_flatten_intent_pairs_scenario_entries_by_site_list() -> None:
    expected = {
        "Scenario-based energy saving": [
            {"Site list": ["NE 1"], "Scenario period": "08:00-10:00"},
            {"Site list": ["NE 2"], "Scenario period": "08:00-10:00"},
        ]
    }
    actual = {
        "Scenario-based energy saving": [
            {"Site list": ["NE 2"], "Scenario period": "08:00-10:00"},
            {"Site list": ["NE 1"], "Scenario period": "08:00-10:00"},
        ]
    }

    rows = flatten_intent(expected, actual)

    assert [row.status for row in rows if ".Site list" in row.path] == [
        INTENT_ROW_SAME,
        INTENT_ROW_SAME,
    ]


def test_flatten_intent_reports_a_missing_nested_entry_as_missing_leaves() -> None:
    expected = {
        "Guaranteed throughput target by zone": [
            {"Guaranteed throughput target": "10Mbps", "Effective time period": "01:00-02:00"},
            {"Guaranteed throughput target": "5Mbps", "Effective time period": "03:00-04:00"},
        ]
    }
    actual = {
        "Guaranteed throughput target by zone": [
            {"Guaranteed throughput target": "10Mbps", "Effective time period": "01:00-02:00"}
        ]
    }

    rows = {row.path: row for row in flatten_intent(expected, actual)}

    missing = rows[
        "intent.Guaranteed throughput target by zone[1].Guaranteed throughput target"
    ]
    assert missing.status == INTENT_ROW_MISSING
    assert missing.expected == "5Mbps"


def test_adjacent_equal_rate_segments_are_the_same_target_as_one_merged_segment() -> None:
    """The vendor may merge two equal-rate windows; that is the same schedule, not a diff."""
    expected = {
        "Scenario-based energy saving": [
            {
                "Site list": ["NE 1"],
                "Guaranteed throughput target by site": [
                    {
                        "Guaranteed throughput target": "2Mbps",
                        "Effective time period": "08:00:00-11:59:59",
                    },
                    {
                        "Guaranteed throughput target": "2Mbps",
                        "Effective time period": "12:00:00-17:59:59",
                    },
                ],
            }
        ]
    }
    actual = {
        "Scenario-based energy saving": [
            {
                "Site list": ["NE 1"],
                "Guaranteed throughput target by site": [
                    {
                        "Guaranteed throughput target": "2Mbps",
                        "Effective time period": "08:00:00-17:59:59",
                    },
                ],
            }
        ]
    }

    rows = {row.path: row for row in flatten_intent(expected, actual)}
    path = "intent.Scenario-based energy saving[0].Guaranteed throughput target by site"

    assert rows[path].verdict == INTENT_ROW_SAME
    assert rows[path].status == INTENT_ROW_CHANGED
    assert rows[path].reason == "时段分段表达不同，归一化后一致"
    assert compare_intent(expected, actual) == []


def test_adjacent_segments_with_different_rates_are_still_a_difference() -> None:
    """Merging only applies to the same rate: a changed rate per window must stay visible."""
    expected = {
        "Guaranteed throughput target by zone": [
            {"Guaranteed throughput target": "2Mbps", "Effective time period": "08:00-11:59"},
            {"Guaranteed throughput target": "5Mbps", "Effective time period": "12:00-17:59"},
        ]
    }
    actual = {
        "Guaranteed throughput target by zone": [
            {"Guaranteed throughput target": "2Mbps", "Effective time period": "08:00-17:59"}
        ]
    }

    assert compare_intent(expected, actual) != []


def test_compare_intent_reports_nested_leaf_paths() -> None:
    expected = {
        "Guaranteed throughput target by zone": [
            {"Guaranteed throughput target": "10Mbps", "Effective time period": "19:00-22:00"}
        ]
    }
    actual = {
        "Guaranteed throughput target by zone": [
            {"Guaranteed throughput target": "5Mbps", "Effective time period": "19:00-22:00"}
        ]
    }

    diffs = compare_intent(expected, actual)

    assert [diff.path for diff in diffs] == [
        "intent.Guaranteed throughput target by zone[0].Guaranteed throughput target"
    ]


def test_flatten_intent_reports_an_added_leaf_inside_a_nested_entry() -> None:
    expected = {"Scenario-based energy saving": [{"Site list": ["NE 1"]}]}
    actual = {
        "Scenario-based energy saving": [
            {"Site list": ["NE 1"], "Scenario": "Power outage"}
        ]
    }

    rows = {row.path: row for row in flatten_intent(expected, actual)}

    added = rows["intent.Scenario-based energy saving[0].Scenario"]
    assert added.status == INTENT_ROW_ADDED
    assert added.actual == "Power outage"


def test_runner_passes_a_clean_case() -> None:
    client = FakePromptClient()
    outcome = run_case(CASES[1], client, stop_after="validate")
    assert outcome.passed is True
    assert _statuses(outcome) == {
        "intent": STAGE_RECORDED,
        "task_t": STAGE_PASSED,
        "validate": STAGE_PASSED,
    }


def test_runner_records_the_conflict_fields() -> None:
    client = FakePromptClient(
        generation_errors={(40, "original"): _generation_block("slot.constraint_violated")}
    )
    outcome = run_case(CASES[40], client, stop_after="validate")
    assert outcome.passed is True
    # The base case disables the conflicting intent/Task-T assertions; the follow-up override
    # turns them back on, so the replacement request is judged on both.
    assert _statuses(outcome) == {
        "intent": STAGE_RECORDED,
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
        "intent": STAGE_RECORDED,
        "task_t": STAGE_PASSED,
        "validate": STAGE_RECORDED,
    }


def _generation_block(code: str) -> PromptGenerationError:
    slot = SlotValidationError(
        slot_name="Gain target",
        code=code,
        message="the gain target is not allowed",
        facts={"slot_label": "Gain target", "actual": "20% - 30%"},
    )
    return PromptGenerationError(
        ErrorCatalog.SLOT_CONSTRAINT_VIOLATED,
        {"slot_label": "Gain target", "actual": "20% - 30%"},
        message="blocked by test",
        failed_parameters=[slot],
    )


def test_the_validate_stage_judges_the_expected_slot_errors_of_a_generation_block() -> None:
    case = copy.deepcopy(CASES[16])
    case["expect"]["protocols"]["a2a-t"]["slot_errors"] = {
        "mode": "any",
        "expect": [{"code": "slot.constraint_violated", "contains": ["gain", "增益"]}],
    }
    client = FakePromptClient(generation_errors={(16, "original"): _generation_block(
        "slot.constraint_violated"
    )})

    outcome = run_case(case, client, stop_after="validate")

    assert outcome.interception is not None
    validate_stage = next(
        stage for stage in outcome.interception if stage.stage == "validate"
    )
    assert validate_stage.status == STAGE_PASSED
    assert validate_stage.detail is not None
    assert validate_stage.detail["slotErrorMatch"]["matched"] is True
    assert validate_stage.diffs == ()


def test_the_validate_stage_fails_when_the_block_reports_another_slot_error() -> None:
    case = copy.deepcopy(CASES[16])
    case["expect"]["protocols"]["a2a-t"]["slot_errors"] = {
        "mode": "any",
        "expect": [{"code": "slot.not_provided"}],
    }
    client = FakePromptClient(generation_errors={(16, "original"): _generation_block(
        "slot.constraint_violated"
    )})

    outcome = run_case(case, client, stop_after="validate")

    assert outcome.interception is not None
    validate_stage = next(
        stage for stage in outcome.interception if stage.stage == "validate"
    )
    assert validate_stage.status == STAGE_FAILED
    assert [diff.path for diff in validate_stage.diffs] == ["validate.slotErrors[0]"]
    assert outcome.passed is False
    # The request was blocked; only its slot errors differ from the case. The gate held, so the
    # replacement still runs and the case is red on the slot expectation alone.
    assert outcome.followup_skipped is False
    assert _statuses(outcome) == {
        "intent": STAGE_RECORDED,
        "task_t": STAGE_PASSED,
        "validate": STAGE_PASSED,
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
        "intent": STAGE_RECORDED,
        "task_t": STAGE_PASSED,
        "validate": STAGE_PASSED,
    }
    assert outcome.interception is not None
    validate_stage = next(
        stage for stage in outcome.interception if stage.stage == "validate"
    )
    assert validate_stage.actual == "blocked"


def test_blocked_case_runs_the_followup_segment() -> None:
    client = FakePromptClient(
        generation_errors={(25, "original"): _generation_block("slot.constraint_violated")}
    )
    outcome = run_case(CASES[25], client, stop_after="validate")
    assert outcome.passed is True
    assert _statuses(outcome) == {
        "intent": STAGE_RECORDED,
        "task_t": STAGE_PASSED,
        "validate": STAGE_PASSED,
    }
    assert _statuses_of(outcome.interception) == {
        "intent": STAGE_RECORDED,
        "task_t": STAGE_RECORDED,
        "validate": STAGE_PASSED,
    }
    assert client.calls.count((25, "generate")) == 2, "both the blocked and the follow-up input ran"


def test_blocked_case_runs_the_followup_when_the_batch_stops_before_the_validation() -> None:
    # A batch that stops at ``intent`` / ``task_t`` never observes a verdict, so there is nothing
    # that could gate the follow-up: the historical two-segment behaviour is kept.
    client = FakePromptClient()
    outcome = run_case(CASES[25], client, stop_after="task_t")
    assert outcome.followup_skipped is False
    assert _statuses(outcome) == {"intent": STAGE_RECORDED, "task_t": STAGE_PASSED}
    assert _statuses_of(outcome.interception) == {
        "intent": STAGE_RECORDED,
        "task_t": STAGE_RECORDED,
    }
    assert client.calls.count((25, "generate")) == 2


def test_blocked_case_records_a_miss_and_passes() -> None:
    client = FakePromptClient()  # the original request validates as pass
    outcome = run_case(CASES[25], client, stop_after="validate")
    # The gate never fired, so there is nothing to judge: the verdict is recorded as evidence and
    # the case passes. The replacement request (whose expectations the corpus carries) is not run.
    assert outcome.passed is True
    assert outcome.followup_skipped is True
    assert outcome.stages == ()
    assert _statuses_of(outcome.interception) == {
        "intent": STAGE_RECORDED,
        "task_t": STAGE_RECORDED,
        "validate": STAGE_RECORDED,
    }
    validate_stage = next(stage for stage in outcome.interception if stage.stage == "validate")
    assert validate_stage.actual == "pass"
    assert client.calls.count((25, "generate")) == 1, "the follow-up request must not run"


def test_blocked_case_reports_a_followup_generation_failure() -> None:
    error = PromptGenerationError(
        ErrorCatalog.SLOT_NOT_PROVIDED,
        {"slot_label": "Site list"},
        message="'Site list' is not provided in the input.",
    )
    client = FakePromptClient(
        generation_errors={
            (25, "original"): _generation_block("slot.constraint_violated"),
            (25, "followup"): error,
        }
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
        "intent": STAGE_RECORDED,
        "task_t": STAGE_RECORDED,
        "validate": STAGE_FAILED,
    }


def test_runner_stops_after_intent() -> None:
    client = FakePromptClient()
    outcome = run_case(CASES[1], client, stop_after="intent")
    assert outcome.passed is True
    assert _statuses(outcome) == {"intent": STAGE_RECORDED}
    assert client.calls == [(1, "generate")]


def test_the_intent_stage_is_record_only() -> None:
    client = FakePromptClient()
    outcome = run_case(CASES[32], client, stop_after="intent")
    assert outcome.passed is True
    assert _statuses(outcome) == {"intent": STAGE_RECORDED}
    assert "移除" in outcome.stages[0].message


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

    expected = CASES[13]["expect"]["intent"]
    actual = dict(expected)
    actual["Scenario-based energy saving"] = [
        {
            **expected["Scenario-based energy saving"][0],
            "Scenario": "Outage",
        }
    ]
    assert compare_intent(expected, actual) == []


def test_time_ranges_with_seconds_normalize_to_the_same_hours() -> None:
    """A range ending at HH:59 covers that hour; the expanded SDK form must match it."""
    from eval.osp.compare import _normalize_time

    assert _normalize_time("08:00:00-09:59:59") == _normalize_time(
        "08:00:00-08:59:59&09:00:00-09:59:59"
    )
    assert _normalize_time("19:00-22:00") == ("19", "20", "21")


def test_an_over_expanded_range_does_not_match_the_shorthand() -> None:
    """The model used to add the 22:00 hour for '19:00-22:00'; that is a real difference."""
    from eval.osp.compare import _normalize_time

    assert _normalize_time("19:00-22:00") != _normalize_time(
        "19:00:00-19:59:59&20:00:00-20:59:59&21:00:00-21:59:59&22:00:00-22:59:59"
    )


def test_task_context_label_separator_is_not_a_business_difference() -> None:
    """'Label: value' and 'Set the label to value' state the same context."""
    from eval.osp.compare import _contexts_match

    assert _contexts_match("Set the Power Savings Ratio to 10%", "Power Savings Ratio: 10%.")
    assert _contexts_match(
        "maximize user experience assurance",
        "with the objective to maximize user experience assurance.",
    )


def test_report_summarizes_each_stage() -> None:
    """The report's per-stage counts drive the yellow-zone Task-T and interception summary."""
    outcomes = [
        CaseOutcome(
            case_id=1,
            name="one",
            stages=(
                StageOutcome(stage="intent", status=STAGE_PASSED),
                StageOutcome(stage="task_t", status=STAGE_FAILED),
                StageOutcome(stage="validate", status=STAGE_RECORDED),
            ),
            passed=False,
        ),
        CaseOutcome(
            case_id=2,
            name="two",
            stages=(StageOutcome(stage="intent", status=STAGE_ERROR),),
            passed=False,
            error="boom",
            interception=(StageOutcome(stage="validate", status=STAGE_PASSED),),
        ),
    ]

    summary = build_report(
        outcomes, stop_after="validate", started_at="2026-11-15T00:00:00Z"
    )["summary"]

    assert isinstance(summary, Mapping)
    assert summary["total"] == 2
    assert summary["stages"] == {
        "intent": {"passed": 1, "failed": 0, "recorded": 0, "error": 1},
        "task_t": {"passed": 0, "failed": 1, "recorded": 0, "error": 0},
        "validate": {"passed": 1, "failed": 0, "recorded": 1, "error": 0},
    }
