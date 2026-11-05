"""Tests of the protocol-neutral intent layer and the demo's built-in intents."""

from __future__ import annotations

import pytest
from demo.intent import (
    DemoIntentProcessor,
    build_all_scenario_intents,
    build_scenario_intent,
    render_normalized_text,
)
from demo.scenario import (
    NATURAL_LANGUAGE_PROMPT_INPUT_EN,
    NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_EN,
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


def _render_without_goal(language: str) -> str:
    return render_normalized_text(
        operation="Create",
        region="Region A",
        sites=(),
        windows=("00:00~06:00,30Mbps",),
        goal="",
        description="",
        expected="",
        background=None,
        language=language,
    )


def test_an_unstated_goal_renders_a_neutral_placeholder() -> None:
    """No goal must not become an invented "maximize energy saving" policy at the NMS."""
    text = _render_without_goal("en-US")

    assert "## Task Target\nNot provided." in text
    assert "Maximize energy saving" not in text


def test_an_unstated_goal_renders_a_neutral_placeholder_in_chinese() -> None:
    text = _render_without_goal("zh-CN")

    assert "## 任务目标(Task Target)\n未提供。" in text
    assert "最大化节能" not in text


def test_a_stated_goal_is_rendered_verbatim() -> None:
    text = render_normalized_text(
        operation="Create",
        region="Region A",
        sites=(),
        windows=(),
        goal="Reduce energy consumption by 10%.",
        description="",
        expected="",
        background=None,
        language="en-US",
    )

    assert "## Task Target\nReduce energy consumption by 10%." in text


async def test_the_nms_context_replaces_the_neutral_region_and_sites() -> None:
    """A real-NMS run must send only the region and sites the vendor serves."""
    processor = DemoIntentProcessor(
        language="en-US",
        forced_region="A2ATestRegion",
        forced_sites=("NRES_XCS_6102937", "LTE_26441_LNR_OCL"),
    )

    intent = await processor.process(
        OperatorRequest(
            text=NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_EN,
            language="en-US",
            scenario="feasibility",
        )
    )

    assert intent.region == "A2ATestRegion"
    assert intent.sites == ("NRES_XCS_6102937", "LTE_26441_LNR_OCL")
    assert "NRES_XCS_6102937" in intent.normalized_text
    assert "LTE_26441_LNR_OCL" in intent.normalized_text
    assert "Station 1" not in intent.normalized_text
    assert "Station 2" not in intent.normalized_text


def test_the_nms_profile_rewrites_the_raw_request_too() -> None:
    """The request shown on the stage and the Task-T on the wire name the same region."""
    intent = build_scenario_intent(
        "night",
        "en-US",
        region="A2ATestRegion",
        sites=("NRES_XCS_6102937", "LTE_26441_LNR_OCL"),
    )

    assert intent.raw_request == "Start energy saving in A2ATestRegion."
    assert intent.region == "A2ATestRegion"
    assert "Region A" not in intent.raw_request
    assert "Region A" not in intent.normalized_text


async def test_a_profiled_request_still_matches_the_built_in() -> None:
    """The OSS Agent receives the profiled request, so matching must see the same text."""
    processor = DemoIntentProcessor(
        language="en-US",
        forced_region="A2ATestRegion",
        forced_sites=("NRES_XCS_6102937", "LTE_26441_LNR_OCL"),
    )
    profiled = build_scenario_intent(
        "information",
        "en-US",
        region="A2ATestRegion",
        sites=("NRES_XCS_6102937", "LTE_26441_LNR_OCL"),
    )

    intent = await processor.process(
        OperatorRequest(text=profiled.raw_request, language="en-US", scenario="information")
    )

    assert intent.source == "scenario"
    assert intent.region == "A2ATestRegion"
    assert intent.raw_request == profiled.raw_request
    assert intent.missing_fields == ("guaranteedThroughputTarget",)


def test_the_run_catalog_serves_the_profiled_request() -> None:
    """The page's default request, the intent and the Task-T agree in a vendor-profile run."""
    from demo.catalog import DemoRunCatalog

    spec = DemoRunCatalog(
        region="A2ATestRegion",
        sites=("NRES_XCS_6102937", "LTE_26441_LNR_OCL"),
    ).spec("information", language="en-US")

    assert spec.intent is not None
    assert spec.intent.raw_request == "Start energy saving in A2ATestRegion."
    assert "Region A" not in spec.user_request


async def test_the_nms_sites_do_not_turn_a_region_task_into_a_site_task() -> None:
    processor = DemoIntentProcessor(
        language="en-US",
        forced_region="A2ATestRegion",
        forced_sites=("NRES_XCS_6102937", "LTE_26441_LNR_OCL"),
    )

    intent = await processor.process(
        OperatorRequest(
            text=NATURAL_LANGUAGE_PROMPT_INPUT_EN,
            language="en-US",
            scenario="night",
        )
    )

    assert intent.region == "A2ATestRegion"
    assert intent.sites == ()
    assert "NRES_XCS_6102937" not in intent.normalized_text
