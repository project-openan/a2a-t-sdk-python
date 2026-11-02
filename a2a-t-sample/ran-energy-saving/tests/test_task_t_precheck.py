"""Tests of the demo Task-T precheck seam."""

from __future__ import annotations

from a2a_t.core.validation_pipeline import FilledParamData
from demo.task_t_precheck import RanDataTaskTPrecheck
from protocol.negotiation_policy import NegotiationKind
from shared.task_schema import TASK_PARAM_SCHEMA


def _filled(**data: object) -> FilledParamData:
    """Build the semantic-validation result of one extracted Task-T."""
    return FilledParamData(data=dict(data))


def test_missing_required_fields_open_an_information_negotiation() -> None:
    finding = RanDataTaskTPrecheck()(
        _filled(operationType="Create", region="Songshanhu"),
        TASK_PARAM_SCHEMA,
    )

    assert finding is not None
    assert finding.kind is NegotiationKind.INFORMATION
    assert finding.missing_items == ("guaranteedThroughputTarget",)


def test_overlapping_target_segments_open_a_target_negotiation() -> None:
    finding = RanDataTaskTPrecheck()(
        _filled(
            operationType="Create",
            region="Songshanhu",
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
            region="Songshanhu",
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
    assert "Songshanhu" in finding.proposal_text


def test_a_valid_task_needs_no_negotiation() -> None:
    finding = RanDataTaskTPrecheck()(
        _filled(
            operationType="Create",
            region="Songshanhu",
            guaranteedThroughputTarget="{00:00~07:00,2Mbps}, {07:00~17:30,10Mbps}",
        ),
        TASK_PARAM_SCHEMA,
    )

    assert finding is None
