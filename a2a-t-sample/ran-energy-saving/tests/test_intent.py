"""Tests of the protocol-neutral intent layer and the demo's built-in intents."""

from __future__ import annotations

import pytest

from demo.intent import (
    DemoIntentProcessor,
    build_scenario_intent,
    build_all_scenario_intents,
)
from protocol.intent import OperatorRequest, ProcessedIntent


@pytest.mark.parametrize(
    ("scenario", "missing", "conflict"),
    (
        ("information", ("guaranteedThroughputTarget",), ()),
        ("reject", ("guaranteedThroughputTarget",), ()),
        ("target", (), ("overlapping throughput windows",)),
        ("abort", (), ("overlapping throughput windows",)),
    ),
)
def test_built_in_intent_preserves_gaps_and_conflicts(
    scenario: str,
    missing: tuple[str, ...],
    conflict: tuple[str, ...],
) -> None:
    """The intent layer must not repair what the negotiation is meant to surface."""
    intent = build_scenario_intent(scenario, "en-US")

    assert intent.missing_fields == missing
    assert intent.conflicts == conflict
    assert intent.source == "scenario"


def test_built_in_intent_keeps_an_unknown_region() -> None:
    intent = build_scenario_intent("unknown_area", "en-US")

    assert intent.region == "Region Z"
    assert "Region Z" in intent.normalized_text
    assert intent.missing_fields == ()


def test_built_in_intent_renders_the_outage_baseline_not_the_modify_request() -> None:
    """The initial Task-T of the two-phase run is a Create baseline."""
    intent = build_scenario_intent("outage", "en-US")

    assert intent.operation_type == "Create"
    assert "00:00~24:00,10Mbps" in intent.normalized_text
    assert intent.normalized_text.startswith("## Operation Type\nCreate")


async def test_processor_uses_the_built_in_intent_for_a_canned_request() -> None:
    processor = DemoIntentProcessor(language="en-US")
    built_in = build_scenario_intent("night", "en-US")

    intent = await processor.process(
        OperatorRequest(
            text=built_in.raw_request,
            language="en-US",
            scenario="night",
        )
    )

    assert intent == built_in


async def test_offline_processor_falls_back_to_raw_for_free_text() -> None:
    processor = DemoIntentProcessor(language="en-US")
    text = "Please save energy in a place I have not described."

    intent = await processor.process(
        OperatorRequest(text=text, language="en-US", source="ui", custom=True)
    )

    assert intent == ProcessedIntent.raw(
        text,
        reason="No live model is configured for free-text intent extraction.",
    )
    assert intent.source == "raw_fallback"
    assert intent.warnings


def test_all_scenarios_have_a_raw_and_normalized_request() -> None:
    intents = build_all_scenario_intents("en-US")

    assert set(intents) == {
        "night",
        "information",
        "unknown_area",
        "target",
        "feasibility",
        "reject",
        "abort",
        "outage",
    }
    for intent in intents.values():
        assert intent.raw_request
        assert intent.normalized_text
        assert intent.raw_request != "" and intent.normalized_text != ""
