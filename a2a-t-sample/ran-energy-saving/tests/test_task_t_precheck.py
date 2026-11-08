"""Tests of the demo Task-T precheck seam."""

from __future__ import annotations

from a2a_t.core.validation_pipeline import FilledParamData
from demo.task_t_precheck import RanDataTaskTPrecheck, _deterministic_negotiation_kind
from protocol.negotiation_policy import NegotiationKind
from shared.task_schema import TASK_PARAM_SCHEMA

#: A rendered Task-T prompt repeats the rate target in Task Description and Task Context.
_REPEATED_TARGET_PROMPT = (
    "## Task Description\n"
    "Based on Region A and the time-segmented rate guarantee goal "
    "{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,10Mbps}, "
    "output a wireless network energy saving plan.\n\n"
    "## Task Context\n"
    "Time-segmented rate guarantee goal: "
    "{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,10Mbps}."
)


def _filled(**data: object) -> FilledParamData:
    """Build the semantic-validation result of one extracted Task-T."""
    return FilledParamData(data=dict(data))


def test_missing_required_fields_open_an_information_negotiation() -> None:
    finding = RanDataTaskTPrecheck()(
        _filled(operationType="Create", region="Region A"),
        TASK_PARAM_SCHEMA,
    )

    assert finding is not None
    assert finding.kind is NegotiationKind.INFORMATION
    assert finding.missing_items == ("guaranteedThroughputTarget",)


def test_overlapping_target_segments_open_a_target_negotiation() -> None:
    finding = RanDataTaskTPrecheck()(
        _filled(
            operationType="Create",
            region="Region A",
            guaranteedThroughputTarget="{00:00~07:00,2Mbps}, {06:00~17:30,10Mbps}",
        ),
        TASK_PARAM_SCHEMA,
    )

    assert finding is not None
    assert finding.kind is NegotiationKind.TARGET


def test_an_impossible_outage_guarantee_opens_a_feasibility_negotiation() -> None:
    finding = RanDataTaskTPrecheck()(
        _filled(
            operationType="Create",
            region="Region A",
            guaranteedThroughputTarget="{00:00~07:00,10Mbps}",
            backgroundEvent="power outage 00:00~18:00",
        ),
        TASK_PARAM_SCHEMA,
    )

    assert finding is not None
    assert finding.kind is NegotiationKind.FEASIBILITY


def test_an_unknown_area_opens_an_information_negotiation() -> None:
    finding = RanDataTaskTPrecheck()(
        _filled(
            operationType="Create",
            region="Region Z",
            guaranteedThroughputTarget="{00:00~07:00,2Mbps}, {07:00~23:00,20Mbps}",
        ),
        TASK_PARAM_SCHEMA,
    )

    assert finding is not None
    assert finding.kind is NegotiationKind.INFORMATION
    assert finding.missing_items == ("region",)
    assert "Region Z" in finding.proposal_text
    assert "Region A" in finding.proposal_text
    assert "Region B" in finding.proposal_text
    assert finding.options is not None
    options = finding.options["options"]
    assert [option["value"] for option in options] == [
        "Region A",
        "Region B",
    ]


def test_an_alias_is_not_an_unknown_area() -> None:
    finding = RanDataTaskTPrecheck()(
        _filled(
            operationType="Create",
            region="Region A",
            guaranteedThroughputTarget="{00:00~07:00,2Mbps}",
        ),
        TASK_PARAM_SCHEMA,
    )

    assert finding is None


def test_a_near_miss_area_recommends_one_candidate() -> None:
    finding = RanDataTaskTPrecheck()(
        _filled(
            operationType="Create",
            region="Region A Admin Commitee",
            guaranteedThroughputTarget="{00:00~07:00,2Mbps}",
        ),
        TASK_PARAM_SCHEMA,
    )

    assert finding is not None
    assert "Closest match: Region A" in finding.proposal_text
    assert finding.options is not None
    options = finding.options["options"]
    assert [option["recommended"] for option in options] == [True, False]


def test_a_target_repeated_by_the_rendered_prompt_is_not_an_overlap() -> None:
    """Task Description and Task Context both carry the target; the duplicate is not a conflict."""
    finding = RanDataTaskTPrecheck()(
        _filled(
            operationType="Create",
            region="Region A",
            guaranteedThroughputTarget=(
                "{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,10Mbps}"
            ),
        ),
        TASK_PARAM_SCHEMA,
        prompt=_REPEATED_TARGET_PROMPT,
    )

    assert finding is None


def test_a_repeated_segment_is_deduplicated_before_the_overlap_check() -> None:
    """The same segment twice is one segment, not two overlapping ones."""
    kind = _deterministic_negotiation_kind(
        {"guaranteedThroughputTarget": _REPEATED_TARGET_PROMPT}
    )

    assert kind is None


def test_a_valid_task_needs_no_negotiation() -> None:
    finding = RanDataTaskTPrecheck()(
        _filled(
            operationType="Create",
            region="Region A",
            guaranteedThroughputTarget="{00:00~07:00,2Mbps}, {07:00~17:30,10Mbps}",
        ),
        TASK_PARAM_SCHEMA,
    )

    assert finding is None
