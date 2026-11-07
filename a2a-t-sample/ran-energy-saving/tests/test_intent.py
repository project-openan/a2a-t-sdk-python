"""Tests of the intent layer: an ADK/LLM reading that never rewrites the operator's text."""

from __future__ import annotations

import pytest
from demo.agents.intent_agent import BusinessIntent
from demo.intent import (
    DemoIntentProcessor,
    IntentExtractionError,
    _processed_intent,
    apply_region_profile_text,
)
from protocol.intent import INTENT_SOURCE_MODEL, INTENT_SOURCE_RAW, OperatorRequest, ProcessedIntent

_TEXT = "Start energy saving in Region A with 00:00–17:30 at 10 Mbps."


class _FakeExtractor:
    """Intent extractor double returning one fixed business reading."""

    def __init__(self, intent: BusinessIntent | None = None, error: Exception | None = None) -> None:
        self.intent = intent
        self.error = error
        self.texts: list[str] = []

    async def extract(self, request: OperatorRequest) -> ProcessedIntent:
        self.texts.append(request.text)
        if self.error is not None:
            raise self.error
        assert self.intent is not None
        return _processed_intent(request.text, self.intent)


async def test_without_a_model_the_request_stays_raw() -> None:
    processor = DemoIntentProcessor(language="en-US")

    intent = await processor.process(OperatorRequest(text=_TEXT, language="en-US"))

    assert intent.source == INTENT_SOURCE_RAW
    assert intent.raw_request == _TEXT
    assert intent.normalized_text == _TEXT
    assert intent.plain_text == _TEXT
    assert intent.warnings


async def test_the_parsed_intent_keeps_the_operator_text_verbatim() -> None:
    extractor = _FakeExtractor(
        BusinessIntent(
            operation_type="Create",
            region="Region A",
            windows=["{00:00~17:30,10Mbps}"],
            energy_saving_goal="Maximize energy saving.",
        )
    )
    processor = DemoIntentProcessor(language="en-US", extractor=extractor)

    intent = await processor.process(OperatorRequest(text=_TEXT, language="en-US"))

    assert extractor.texts == [_TEXT]
    assert intent.source == INTENT_SOURCE_MODEL
    assert intent.region == "Region A"
    assert intent.windows == ("{00:00~17:30,10Mbps}",)
    # The intent is a reading, not a rewrite: every text surface is the operator's own text.
    assert intent.raw_request == _TEXT
    assert intent.normalized_text == _TEXT
    assert intent.plain_text == _TEXT


async def test_an_extraction_failure_falls_back_to_the_raw_text() -> None:
    extractor = _FakeExtractor(error=IntentExtractionError("no parseable intent"))
    processor = DemoIntentProcessor(language="en-US", extractor=extractor)

    intent = await processor.process(OperatorRequest(text=_TEXT, language="en-US"))

    assert intent.source == INTENT_SOURCE_RAW
    assert intent.normalized_text == _TEXT
    assert any("no parseable intent" in warning for warning in intent.warnings)


def test_processed_intent_maps_every_business_field() -> None:
    intent = _processed_intent(
        _TEXT,
        BusinessIntent(
            operation_type="Modify",
            region="Region A",
            sites=["Site 1", "Site 2"],
            windows=["{00:00~03:00,10Mbps}", "{03:00~05:00,2Mbps}"],
            energy_saving_goal="Maximize energy saving.",
            task_description="Update the running task.",
            expected_output="Intent report.",
            background_event="planned outage 03:00~05:00",
            missing_fields=["siteList"],
            conflicts=["overlapping windows"],
        ),
    )

    assert intent.operation_type == "Modify"
    assert intent.sites == ("Site 1", "Site 2")
    assert intent.windows == ("{00:00~03:00,10Mbps}", "{03:00~05:00,2Mbps}")
    assert intent.background_event == "planned outage 03:00~05:00"
    assert intent.missing_fields == ("siteList",)
    assert intent.conflicts == ("overlapping windows",)


@pytest.mark.parametrize(
    ("language", "expected_region", "expected_sites"),
    [
        ("en-US", "A2ATestRegion", ("NRES_XCS_6102937", "LTE_26441_LNR_OCL")),
        ("zh-CN", "A2ATestRegion", ("NRES_XCS_6102937", "LTE_26441_LNR_OCL")),
    ],
)
def test_the_vendor_profile_rewrites_the_example_request_once(
    language: str,
    expected_region: str,
    expected_sites: tuple[str, ...],
) -> None:
    from demo.scenario import EXAMPLE_REQUESTS, SCENARIO_FEASIBILITY

    neutral = EXAMPLE_REQUESTS[SCENARIO_FEASIBILITY][language]
    old_region = "Region A" if language == "en-US" else "区域 A"
    old_sites = ("Site 1", "Site 2") if language == "en-US" else ("站点 1", "站点 2")

    profiled = apply_region_profile_text(
        neutral,
        old_region=old_region,
        region=expected_region,
        old_sites=old_sites,
        sites=expected_sites,
        language=language,
    )

    assert expected_region in profiled
    for old in (old_region, *old_sites):
        assert old not in profiled
