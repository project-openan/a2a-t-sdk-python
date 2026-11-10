"""Deterministic grounding checks of an automatic negotiation decision."""

from __future__ import annotations

from protocol.grounding import find_ungrounded, grounding_reask_text
from shared.models import NegotiationAreaOption, NegotiationOptions, NegotiationProposeView

_TARGET = "{00:00~07:00,2Mbps}, {07:00~23:00,20Mbps}, {23:00~24:00,2Mbps}"
_BASELINE = {
    "operationType": "Create",
    "region": "Songshanhu Administration Committee",
    "guaranteedThroughputTarget": _TARGET,
}


def _propose(content: str, *, options: NegotiationOptions | None = None) -> NegotiationProposeView:
    return NegotiationProposeView(
        negotiation_id="negotiation-1",
        kind="information",
        round=1,
        max_rounds=3,
        content=content,
        context_id="context-1",
        options=options,
    )


def _fields(**overrides: object) -> list[str]:
    filled = dict(_BASELINE)
    filled.update(overrides)
    return [
        item.field
        for item in find_ungrounded(
            filled,
            baseline=_BASELINE,
            intent=_BASELINE,
            propose=_propose("Region 'Songshanhu Administration Committee' is not recognized."),
        )
    ]


def test_an_unknown_operator_region_is_not_replaced_by_a_model_value() -> None:
    """The reported run: the server rejected the region and offered no replacement."""
    ungrounded = _fields(region="A2ATestRegion")

    assert ungrounded == ["region"]


def test_an_unchanged_operator_value_is_grounded() -> None:
    assert _fields() == []


def test_a_server_option_grounds_a_region() -> None:
    propose = _propose(
        "Region 'Songshanhu Administration Committee' is not recognized.",
        options=NegotiationOptions(
            field="region",
            requested="Songshanhu Administration Committee",
            options=[NegotiationAreaOption(value="A2ATestRegion")],
        ),
    )
    ungrounded = find_ungrounded(
        {**_BASELINE, "region": "A2ATestRegion"},
        baseline=_BASELINE,
        intent=_BASELINE,
        propose=propose,
    )

    assert ungrounded == []


def test_affirmative_server_content_grounds_a_region() -> None:
    ungrounded = find_ungrounded(
        {**_BASELINE, "region": "A2ATestRegion"},
        baseline=_BASELINE,
        intent=_BASELINE,
        propose=_propose("Available test region: A2ATestRegion."),
    )

    assert ungrounded == []


def test_a_rejected_value_in_negative_context_is_not_grounding() -> None:
    ungrounded = find_ungrounded(
        {**_BASELINE, "region": "A2ATestRegion"},
        baseline=_BASELINE,
        intent=_BASELINE,
        propose=_propose("Region 'A2ATestRegion' is not recognized."),
    )

    assert [item.field for item in ungrounded] == ["region"]


def test_a_changed_operation_type_is_never_grounded_by_prose() -> None:
    ungrounded = find_ungrounded(
        {**_BASELINE, "operationType": "Modify"},
        baseline=_BASELINE,
        intent=_BASELINE,
        propose=_propose("Create a task for A2ATestRegion."),
    )

    assert [item.field for item in ungrounded] == ["operationType"]


def test_target_segments_are_checked_one_by_one() -> None:
    propose = _propose(
        "Corrected target: {00:00~10:00,5Mbps}, {10:00~24:00,5Mbps}.",
    )
    ungrounded = find_ungrounded(
        {**_BASELINE, "guaranteedThroughputTarget": "{00:00~10:00,5Mbps}, {10:00~24:00,9Mbps}"},
        baseline=_BASELINE,
        intent=_BASELINE,
        propose=propose,
    )

    assert [item.field for item in ungrounded] == ["guaranteedThroughputTarget"]


def test_a_site_level_filled_without_an_entity_change_is_grounded() -> None:
    """The reported failure: siteList/backgroundEvent plus the agreed rate, no region/operationType.

    Those fields were already accepted by the server in the initial Task-T; a negotiation that
    only changes the rate must not re-report them as missing.
    """
    filled = {
        "siteList": '["LNR_KPI_7745","NES_27A_321071"]',
        "backgroundEvent": "power outage from 08:00 to 18:00",
        "guaranteedThroughputTarget": "{08:00~18:00,2Mbps}",
    }
    ungrounded = find_ungrounded(
        filled,
        baseline=filled,
        intent={},
        propose=_propose("Reduce rate target to 2Mbps or less for the outage period.", options=None),
    )

    assert ungrounded == []


def test_an_area_level_request_may_omit_the_rate_target() -> None:
    """Area-level requests may omit the rate; the server runs the no-disruption default."""
    filled = {"operationType": "Create", "region": "Region A"}
    ungrounded = find_ungrounded(
        filled,
        baseline=filled,
        intent=filled,
        propose=_propose("Region 'Region A' is not recognized."),
    )

    assert ungrounded == []


def test_reask_text_names_the_fields_and_server_options() -> None:
    propose = _propose(
        "Region 'Songshanhu Administration Committee' is not recognized.",
        options=NegotiationOptions(
            field="region",
            options=[NegotiationAreaOption(value="A2ATestRegion")],
        ),
    )
    ungrounded = find_ungrounded(
        {**_BASELINE, "region": "OtherRegion"},
        baseline=_BASELINE,
        intent=_BASELINE,
        propose=propose,
    )

    text = grounding_reask_text(propose, ungrounded)

    assert "region=OtherRegion" in text
    assert "Server options for region: A2ATestRegion." in text
    assert "Do not invent a value" in text


def test_a_prose_feasibility_target_grounds_the_accepted_segment() -> None:
    """A feasibility proposal states its revised rate in prose, not in segment notation."""
    propose = _propose("Reduce the throughput target from 10Mbps to 2Mbps for the outage period.")
    filled = {**_BASELINE, "guaranteedThroughputTarget": "{08:00~18:00,2Mbps}"}

    ungrounded = find_ungrounded(
        filled,
        baseline=_BASELINE,
        intent=_BASELINE,
        propose=propose,
    )

    assert ungrounded == []
