# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Golden fixture parity of the negotiation content layer against the real built-in resources.

Port of the Java ``a2a-t-corpus`` ``golden/GoldenFixtureComparisonTest``: every golden fixture is
rendered deterministically from the fixed :mod:`~tests.negotiation.generation.golden_inputs`
inputs through an orchestrator wired with the built-in templates and vocabulary — no LLM client is
involved. The rendered text must match the committed fixture file byte for byte, so any drift of
the templates, the vocabulary or the rendering pipeline fails this test and requires a reviewed
fixture revision. This suite also pins the MetadataContent contract, the determinism of the
from-data generation and the zero-LLM guarantee of the from-data variants (the P5 acceptance
criterion "golden 输出与 Java golden 逐字节一致（LF 归一后）"), plus the same parity through the
12-method service facade with raw string template URIs (D16).
"""

from __future__ import annotations

from typing import Any

import pytest

from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
    MetadataContent,
)
from a2a_t.llm.models import LLMResponse
from a2a_t.negotiation.content import NegotiationAbortData, NegotiationEndingData, NegotiationProposeData
from a2a_t.negotiation.generation import NegotiationContentService
from a2a_t.negotiation.generation.builder import builder

from .golden_inputs import (
    GOLDEN_CASES,
    GOLDEN_ROOT,
    LANGUAGES,
    SESSION_ID,
    GoldenCase,
    orchestrator,
    read_golden_fixture,
)

CASE_IDS = [case.name for case in GOLDEN_CASES]


def _render(golden_case: GoldenCase, language: str) -> MetadataContent:
    """Render one fixture and assert the URI and extension contract up front."""
    result = golden_case.generate(orchestrator(language), language)
    assert result.template_uri == golden_case.template_uri
    assert result.extension_uri == NEGOTIATION_T_EXTENSION_URI
    return result


class CountingClient:
    """LLM client stub recording every call and failing the test on the first one."""

    def __init__(self) -> None:
        self.calls = 0

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls += 1
        raise AssertionError("The from-data generation must never call the LLM client")


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("golden_case", GOLDEN_CASES, ids=CASE_IDS)
def test_renders_byte_identical_to_the_golden_fixture(golden_case: GoldenCase, language: str) -> None:
    result = _render(golden_case, language)

    assert result.prompt_text == read_golden_fixture(golden_case, language)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("golden_case", GOLDEN_CASES, ids=CASE_IDS)
def test_builds_the_metadata_map_with_exactly_three_entries(golden_case: GoldenCase, language: str) -> None:
    result = _render(golden_case, language)

    metadata = result.build_metadata_content()
    assert list(metadata) == [NEGOTIATION_T_EXTENSION_URI, TEMPLATE_URI_METADATA_KEY, NEGOTIATION_CONTEXT_METADATA_KEY]
    assert result.prompt_text == metadata[NEGOTIATION_T_EXTENSION_URI]
    assert golden_case.template_uri == metadata[TEMPLATE_URI_METADATA_KEY]
    assert result.negotiation_context == golden_case.context()
    nested_context = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert isinstance(nested_context, dict)
    assert golden_case.context().id == nested_context["id"]
    assert golden_case.context().round == nested_context["round"]
    assert golden_case.context().max_rounds == nested_context["maxRounds"]
    assert golden_case.performative.value == nested_context["performative"]
    assert metadata == result.build_metadata_content()


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("golden_case", GOLDEN_CASES, ids=CASE_IDS)
def test_renders_the_same_input_deterministically(golden_case: GoldenCase, language: str) -> None:
    wired = orchestrator(language)

    first = golden_case.generate(wired, language)
    second = golden_case.generate(wired, language)

    assert first.prompt_text == second.prompt_text
    assert first == second


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("golden_case", GOLDEN_CASES, ids=CASE_IDS)
def test_keeps_the_structural_invariants_of_rendered_messages(golden_case: GoldenCase, language: str) -> None:
    result = _render(golden_case, language)
    prompt_text = result.prompt_text

    context_title = "协商上下文" if language == "zh-CN" else "Negotiation Context"
    assert context_title not in prompt_text, "the context section must not be rendered into the message"
    assert f"- id: {SESSION_ID}" not in prompt_text, "context lines must not be rendered"
    assert result.negotiation_context == golden_case.context(), "the context travels in the metadata"
    assert not prompt_text.endswith("\n"), "message must not end with a newline"
    assert "\n\n\n" not in prompt_text, "sections must be joined by exactly one blank line"
    assert "{{" not in prompt_text, "no unreplaced slot placeholder may remain"
    assert "<!--" not in prompt_text, "the leading template description comment must be dropped"
    if language == "zh-CN":
        assert "要求：" not in prompt_text, "template requirement lines must not enter the message"
    else:
        assert "Requirements:" not in prompt_text, "template requirement lines must not enter the message"
    for block in prompt_text.split("\n\n"):
        assert block.startswith("## "), "every rendered block must be one titled section"


@pytest.mark.parametrize("language", LANGUAGES)
def test_from_data_generation_never_calls_the_llm(language: str) -> None:
    """Prove that the from-data variants never touch the LLM: all twelve type/performative
    combinations of both languages run against a counting LLM client that would record every call."""
    llm = CountingClient()
    configuration = builder()
    configuration.language = language
    configuration.llm_client = llm
    wired = configuration.build()

    for golden_case in GOLDEN_CASES:
        result = golden_case.generate(wired, language)
        assert result.prompt_text.strip()

    assert llm.calls == 0, "from-data generation must be deterministic and LLM-free"


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("golden_case", GOLDEN_CASES, ids=CASE_IDS)
def test_service_facade_with_raw_string_template_uri_renders_the_golden_fixture(
    golden_case: GoldenCase, language: str
) -> None:
    """The same parity through the 12-method service facade with the raw string template URI."""
    configuration = builder()
    configuration.language = language
    service = NegotiationContentService(configuration.build())
    context = golden_case.context()
    content = golden_case.content(language)

    if golden_case.performative.value == "PROPOSE":
        result = service.generate_propose_from_data(NegotiationProposeData(context, content), golden_case.template_uri)
    elif golden_case.performative.value == "ACCEPT":
        result = service.generate_accept_from_data(NegotiationEndingData(context, content), golden_case.template_uri)
    elif golden_case.performative.value == "REJECT":
        result = service.generate_reject_from_data(NegotiationEndingData(context, content), golden_case.template_uri)
    else:
        result = service.generate_abort_from_data(NegotiationAbortData(context, content), golden_case.template_uri)

    assert result.prompt_text == read_golden_fixture(golden_case, language)
    assert result.template_uri == golden_case.template_uri


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_committed_golden_fixture_file_exists_and_is_lf_normalized(language: str) -> None:
    """The fixture set is complete (12 files per language) and carries no CRLF bytes or BOM."""
    files = sorted(path.name for path in (GOLDEN_ROOT / language).glob("*.md"))
    assert files == sorted(case.file_name for case in GOLDEN_CASES)
    for path in (GOLDEN_ROOT / language).glob("*.md"):
        raw = path.read_bytes()
        assert b"\r" not in raw, f"{path} must be LF-normalized"
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{path} must not carry a UTF-8 BOM"
        raw.decode("utf-8")
