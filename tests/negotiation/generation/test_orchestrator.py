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

"""Tests of the negotiation generation orchestrator (port of the Java
``NegotiationGenerationOrchestratorTest``, ``InputTextLengthLimitTest`` and the portable rows of
``NegotiationGenerationOrchestratorBuilderWiringTest``).

The from-data leg is pinned against the built-in resources (zh-CN and en-US), the from-text leg
against a scripted LLM client with exact call-count assertions (retry until success, exhaustion
re-raising the original code), the error-translation catch points against their catalog codes and
facts, and the validation leg at its P6 seam (the parameter extractor protocol) together with its
own guards, which run before any collaborator is consulted.
"""

from __future__ import annotations

from typing import Any

import pytest

from a2a_t.common.prompt_resources.resource_access import PackagedPromptResourceAccess
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import (
    NegotiationGenerationError,
    NegotiationParamExtractionError,
    ResourceNotFoundError,
)
from a2a_t.core.errors.input_limit import DEFAULT_MAX_TEXT_CHARS
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
    NegotiationContext,
    NegotiationPerformative,
)
from a2a_t.core.standard_templates import (
    ENERGY_SAVING,
    INFORMATION_NEGOTIATION_ACCEPT_REJECT,
    INFORMATION_NEGOTIATION_PROPOSE,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
)
from a2a_t.core.template_uri import TemplateUri
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.llm.models import LLMResponse
from a2a_t.negotiation.content import (
    InformationEndingContent,
    InformationProposeContent,
    NegotiationConclusion,
    NegotiationItem,
)
from a2a_t.negotiation.content.models import NegotiationEndingData, NegotiationProposeData
from a2a_t.negotiation.content.vocabulary import Vocabulary
from a2a_t.negotiation.generation import (
    DefaultNegotiationContentExtractor,
    NegotiationGenerationOrchestrator,
)
from a2a_t.negotiation.generation.builder import builder
from a2a_t.negotiation.resources.reference import NegotiationReference

from .golden_inputs import default_context

UUID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"
INFORMATION_PROPOSE = INFORMATION_NEGOTIATION_PROPOSE


class ScriptedClient:
    """Scripted LLM client: returns the queued payloads in order; the last entry repeats.

    An entry may also be an exception, which is raised on that call (and repeats once exhausted).
    """

    def __init__(self, *entries: str | BaseException) -> None:
        self.script: list[str | BaseException] = list(entries)
        self.calls = 0

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        entry = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(entry, BaseException):
            raise entry
        return LLMResponse(
            content=entry,
            model="scripted-model",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
            metadata={},
        )


class CountingEmptyClient:
    """LLM client stub recording every call and returning an empty payload, so tests can assert
    both that a call happened and that the pipeline fails downstream of the gate instead of
    inside it."""

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
        return LLMResponse(
            content="", model="counting-model", usage={"prompt_tokens": 1, "completion_tokens": 1}, metadata={}
        )


class MissingTemplateAccess(PackagedPromptResourceAccess):
    """Packaged access whose template lookups always miss (Java ``missingTemplateLoader``)."""

    def template_text(self, template_uri: str | TemplateUri, language: str) -> str:
        identifier = template_uri.uri if isinstance(template_uri, TemplateUri) else template_uri
        raise ResourceNotFoundError("Negotiation template does not exist.", identifier)


class RecordingParamExtractor:
    """Recording stand-in of the P6 parameter extractor seam."""

    def __init__(self, result: FilledParamData | BaseException) -> None:
        self.result = result
        self.calls: list[tuple[str | None, NegotiationContext | None, Any, NegotiationReference]] = []

    def extract(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Any,
        reference: NegotiationReference,
    ) -> FilledParamData:
        self.calls.append((prompt, context, schema, reference))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def wired(
    *,
    language: str = "zh-CN",
    llm_client: Any = None,
    max_attempts: int | None = None,
    **kwargs: Any,
) -> NegotiationGenerationOrchestrator:
    """Build the orchestrator under test from the builder configuration."""
    configuration = builder()
    configuration.language = language
    if llm_client is not None:
        configuration.llm_client = llm_client
    if max_attempts is not None:
        configuration.max_attempts = max_attempts
    for key, value in kwargs.items():
        setattr(configuration, key, value)
    return configuration.build()


def propose_data(content: InformationProposeContent | None = None, round: int = 1) -> NegotiationProposeData:
    """Build one information propose input bundle."""
    return NegotiationProposeData(
        NegotiationContext(UUID, round, 5, NegotiationPerformative.PROPOSE),
        content if content is not None else InformationProposeContent([NegotiationItem("节能区域", "松山湖")], None),
    )


def text_of_length(length: int) -> str:
    """Build one text of exactly the given length."""
    return "x" * length


# ----------------------------------------------------------------------
# From-data leg
# ----------------------------------------------------------------------


def test_generates_information_propose_from_data_in_chinese() -> None:
    result = wired().generate_propose_from_data(propose_data(), INFORMATION_PROPOSE)

    assert result.template_uri == INFORMATION_NEGOTIATION_PROPOSE_URI
    assert result.extension_uri == NEGOTIATION_T_EXTENSION_URI
    assert result.prompt_text.strip()
    assert "协商上下文" not in result.prompt_text, "the context section must not be rendered"
    assert "所需信息项" in result.prompt_text

    metadata = result.build_metadata_content()
    assert list(metadata) == [NEGOTIATION_T_EXTENSION_URI, TEMPLATE_URI_METADATA_KEY, NEGOTIATION_CONTEXT_METADATA_KEY]
    assert result.prompt_text == metadata[NEGOTIATION_T_EXTENSION_URI]
    assert result.template_uri == metadata[TEMPLATE_URI_METADATA_KEY]
    nested = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert isinstance(nested, dict)
    assert nested == {"id": UUID, "round": 1, "maxRounds": 5, "performative": "PROPOSE"}


def test_generates_information_propose_from_data_in_english() -> None:
    result = wired(language="en-US").generate_propose_from_data(
        NegotiationProposeData(
            NegotiationContext(UUID, 2, 5, NegotiationPerformative.PROPOSE),
            InformationProposeContent([NegotiationItem("Region", "Songshan Lake")], None),
        ),
        INFORMATION_PROPOSE,
    )

    assert result.template_uri == INFORMATION_NEGOTIATION_PROPOSE_URI
    assert result.prompt_text.strip()
    assert "Negotiation Context" not in result.prompt_text, "the context section must not be rendered"
    assert result.negotiation_context == NegotiationContext(UUID, 2, 5, NegotiationPerformative.PROPOSE)
    assert "Required Information Items" in result.prompt_text


@pytest.mark.parametrize("performative", [NegotiationPerformative.ACCEPT, NegotiationPerformative.REJECT])
def test_stamps_the_performative_of_the_addressed_template_on_the_emitted_context(
    performative: NegotiationPerformative,
) -> None:
    """The emitted context is the input context stamped with the performative of the operation,
    overriding whatever the caller passed in."""
    result = wired().generate_propose_from_data(
        NegotiationProposeData(
            NegotiationContext(UUID, 2, 5, performative),
            InformationProposeContent([NegotiationItem("节能区域", "松山湖")], None),
        ),
        INFORMATION_PROPOSE,
    )

    assert result.negotiation_context.performative is NegotiationPerformative.PROPOSE


def test_rejects_a_mismatched_conclusion_of_a_from_data_ending() -> None:
    with pytest.raises(NegotiationGenerationError) as info:
        wired().generate_accept_from_data(
            NegotiationEndingData(
                NegotiationContext(UUID, 2, 5, NegotiationPerformative.ACCEPT),
                InformationEndingContent(
                    NegotiationConclusion.REJECT, [NegotiationItem("接入端口名称", "P533-珠江旧城")]
                ),
            ),
            INFORMATION_NEGOTIATION_ACCEPT_REJECT,
        )

    assert info.value.code is ErrorCatalog.NEGOTIATION_CONCLUSION_MISMATCH
    assert info.value.facts == {"expected": "Accept", "actual": "Reject"}


@pytest.mark.parametrize(
    ("method", "message"),
    [
        ("generate_propose_from_data", "Negotiation propose data must not be null."),
        ("generate_accept_from_data", "Negotiation ending data must not be null."),
        ("generate_reject_from_data", "Negotiation ending data must not be null."),
        ("generate_abort_from_data", "Negotiation abort data must not be null."),
    ],
)
def test_rejects_null_from_data_bundles(method: str, message: str) -> None:
    with pytest.raises(TypeError, match=message):
        getattr(wired(), method)(None, INFORMATION_PROPOSE)


def test_rejects_a_null_context_of_a_from_data_bundle() -> None:
    with pytest.raises(TypeError, match="Negotiation context must not be null."):
        wired().generate_propose_from_data(
            NegotiationProposeData(None, InformationProposeContent([NegotiationItem("区域", "松山湖")], None)),
            INFORMATION_PROPOSE,
        )


# ----------------------------------------------------------------------
# From-text leg
# ----------------------------------------------------------------------


def test_generates_propose_from_text_with_scripted_extraction() -> None:
    llm = ScriptedClient('{"items":[{"name":"故障发生时间","value":"精确到分钟的时间点"}],"relationship":null}')

    result = wired(llm_client=llm).generate_propose_from_text(
        "请提供故障发生时间。",
        NegotiationContext(UUID, 2, 5, NegotiationPerformative.PROPOSE),
        INFORMATION_PROPOSE,
    )

    assert llm.calls == 1
    assert result.template_uri == INFORMATION_NEGOTIATION_PROPOSE_URI
    assert result.prompt_text.strip()
    assert result.negotiation_context.round == 2
    assert "故障发生时间" in result.prompt_text


def test_retries_content_extraction_until_it_succeeds() -> None:
    llm = ScriptedClient("", "", '{"items":[{"name":"区域","value":"松山湖"}]}')

    result = wired(llm_client=llm, max_attempts=3).generate_propose_from_text(
        "请提供区域。", NegotiationContext(UUID, 1, 5, NegotiationPerformative.PROPOSE), INFORMATION_PROPOSE
    )

    assert llm.calls == 3
    assert result.prompt_text.strip()


def test_rethrows_the_original_code_when_retries_are_exhausted() -> None:
    llm = ScriptedClient(RuntimeError("LLM endpoint unavailable."))

    with pytest.raises(NegotiationGenerationError) as info:
        wired(llm_client=llm, max_attempts=2).generate_propose_from_text(
            "请提供区域。", NegotiationContext(UUID, 1, 5, NegotiationPerformative.PROPOSE), INFORMATION_PROPOSE
        )

    assert llm.calls == 2
    assert info.value.code is ErrorCatalog.LLM_INVOCATION_FAILED


def test_reports_a_missing_template_as_template_not_found_on_the_generation_leg() -> None:
    orchestrator = wired(resource_access=MissingTemplateAccess())

    with pytest.raises(NegotiationGenerationError) as info:
        orchestrator.generate_propose_from_data(propose_data(), INFORMATION_PROPOSE)

    assert info.value.code is ErrorCatalog.TEMPLATE_NOT_FOUND
    assert info.value.facts == {
        "template_uri": INFORMATION_NEGOTIATION_PROPOSE_URI,
        "language": "zh-CN",
    }
    assert isinstance(info.value.__cause__, ResourceNotFoundError)


def test_rejects_template_uris_that_do_not_address_the_expected_performative() -> None:
    with pytest.raises(ValueError) as performative_mismatch:
        wired().generate_propose_from_data(propose_data(), INFORMATION_NEGOTIATION_ACCEPT_REJECT)
    assert "Template URI does not address a negotiation template of the expected performative PROPOSE (propose)" in str(
        performative_mismatch.value
    )

    with pytest.raises(ValueError) as wrong_extension:
        wired().generate_propose_from_data(propose_data(), ENERGY_SAVING)
    assert "Template URI does not address a negotiation template of the expected performative PROPOSE (propose)" in str(
        wrong_extension.value
    )


# ----------------------------------------------------------------------
# Input length limits (port of the Java InputTextLengthLimitTest)
# ----------------------------------------------------------------------


def test_rejects_from_text_over_the_default_limit_before_any_llm_call() -> None:
    llm = CountingEmptyClient()

    with pytest.raises(NegotiationGenerationError) as info:
        wired(llm_client=llm).generate_propose_from_text(
            text_of_length(DEFAULT_MAX_TEXT_CHARS + 1),
            default_context(NegotiationPerformative.PROPOSE),
            INFORMATION_PROPOSE,
        )

    assert info.value.code is ErrorCatalog.INPUT_TEXT_TOO_LONG
    assert str(DEFAULT_MAX_TEXT_CHARS + 1) in str(info.value), "the message must state the actual length"
    assert str(DEFAULT_MAX_TEXT_CHARS) in str(info.value), "the message must state the limit"
    assert info.value.facts == {
        "actual_length": str(DEFAULT_MAX_TEXT_CHARS + 1),
        "max_chars": str(DEFAULT_MAX_TEXT_CHARS),
    }
    assert llm.calls == 0, "an oversized text must never reach the LLM"


def test_accepts_from_text_at_exactly_the_default_limit() -> None:
    llm = CountingEmptyClient()

    with pytest.raises(NegotiationGenerationError) as info:
        wired(llm_client=llm).generate_propose_from_text(
            text_of_length(DEFAULT_MAX_TEXT_CHARS),
            default_context(NegotiationPerformative.PROPOSE),
            INFORMATION_PROPOSE,
        )

    assert info.value.code is not ErrorCatalog.INPUT_TEXT_TOO_LONG
    assert llm.calls >= 1, "a text at exactly the limit must reach the LLM"


def test_rejects_a_validation_prompt_over_the_default_limit_before_any_llm_call() -> None:
    llm = CountingEmptyClient()
    recording = RecordingParamExtractor(FilledParamData({}))
    orchestrator = wired(llm_client=llm, param_extractor=recording)

    with pytest.raises(NegotiationParamExtractionError) as info:
        orchestrator.validate_propose_prompt_and_data_filling(
            text_of_length(DEFAULT_MAX_TEXT_CHARS + 1),
            default_context(NegotiationPerformative.PROPOSE),
            {"type": "object"},
            INFORMATION_PROPOSE,
        )

    assert info.value.code is ErrorCatalog.INPUT_TEXT_TOO_LONG
    assert info.value.facts == {
        "actual_length": str(DEFAULT_MAX_TEXT_CHARS + 1),
        "max_chars": str(DEFAULT_MAX_TEXT_CHARS),
    }
    assert llm.calls == 0, "an oversized prompt must never reach the LLM"
    assert recording.calls == [], "the gate runs before any collaborator is consulted"


def test_accepts_a_validation_prompt_at_exactly_the_default_limit() -> None:
    llm = CountingEmptyClient()
    recording = RecordingParamExtractor(
        NegotiationParamExtractionError(ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED, language="zh-CN")
    )
    orchestrator = wired(llm_client=llm, param_extractor=recording)

    with pytest.raises(NegotiationParamExtractionError) as info:
        orchestrator.validate_propose_prompt_and_data_filling(
            text_of_length(DEFAULT_MAX_TEXT_CHARS),
            default_context(NegotiationPerformative.PROPOSE),
            {"type": "object"},
            INFORMATION_PROPOSE,
        )

    assert info.value.code is not ErrorCatalog.INPUT_TEXT_TOO_LONG, "the gate must pass at exactly the limit"
    assert len(recording.calls) == 1, "a prompt at exactly the limit must reach the pipeline"


def test_a_custom_limit_drives_the_rejection() -> None:
    llm = CountingEmptyClient()

    with pytest.raises(NegotiationGenerationError) as info:
        wired(llm_client=llm, max_text_chars=10).generate_propose_from_text(
            "01234567890", default_context(NegotiationPerformative.PROPOSE), INFORMATION_PROPOSE
        )

    assert info.value.code is ErrorCatalog.INPUT_TEXT_TOO_LONG
    assert llm.calls == 0


@pytest.mark.parametrize("max_text_chars", [0, -1])
def test_the_builder_rejects_a_non_positive_limit(max_text_chars: int) -> None:
    with pytest.raises(ValueError, match="max text chars"):
        wired(max_text_chars=max_text_chars)


# ----------------------------------------------------------------------
# Validation leg (P6 seam)
# ----------------------------------------------------------------------


def test_the_injected_param_extractor_receives_the_prompt_context_schema_and_reference() -> None:
    recording = RecordingParamExtractor(FilledParamData({"id": UUID, "round": 1}))
    orchestrator = wired(param_extractor=recording)
    prompt = "## 所需信息项\n1. 区域\n"
    context = default_context(NegotiationPerformative.PROPOSE)
    schema = {"type": "object", "properties": {"region": {"type": "string"}}}

    filled = orchestrator.validate_propose_prompt_and_data_filling(prompt, context, schema, INFORMATION_PROPOSE)

    assert filled.data == {"id": UUID, "round": 1}
    assert len(recording.calls) == 1
    recorded_prompt, recorded_context, recorded_schema, recorded_reference = recording.calls[0]
    assert recorded_prompt == prompt
    assert recorded_context == context
    assert recorded_schema == schema
    assert recorded_reference.type is not None
    assert recorded_reference.performative is NegotiationPerformative.PROPOSE
    assert recorded_reference.language == "zh-CN"


def test_the_param_extractor_failure_is_rethrown_unchanged() -> None:
    failure = NegotiationParamExtractionError(
        ErrorCatalog.TEMPLATE_NOT_FOUND,
        {"template_uri": INFORMATION_NEGOTIATION_PROPOSE_URI, "language": "zh-CN"},
        language="zh-CN",
    )
    orchestrator = wired(param_extractor=RecordingParamExtractor(failure))

    with pytest.raises(NegotiationParamExtractionError) as info:
        orchestrator.validate_propose_prompt_and_data_filling(
            "## 所需信息项\n1. 区域\n",
            default_context(NegotiationPerformative.PROPOSE),
            {"type": "object"},
            INFORMATION_PROPOSE,
        )

    assert info.value is failure


@pytest.mark.parametrize(
    "method",
    [
        "validate_propose_prompt_and_data_filling",
        "validate_accept_prompt_and_data_filling",
        "validate_reject_prompt_and_data_filling",
        "validate_abort_prompt_and_data_filling",
    ],
)
def test_the_validation_leg_rejects_a_null_schema_before_any_collaborator(method: str) -> None:
    recording = RecordingParamExtractor(FilledParamData({}))
    orchestrator = wired(param_extractor=recording)

    with pytest.raises(TypeError, match="Parameter schema must not be null."):
        getattr(orchestrator, method)("## 所需信息项\n1. 区域\n", None, None, INFORMATION_PROPOSE)

    assert recording.calls == []


def test_the_unwired_validation_leg_fails_with_a_clear_wiring_error() -> None:
    """The orchestrator guard fires only when constructed without a parameter extractor.

    The builder always wires the default P6 validation pipeline now (Java parity), so the unwired
    state is reachable only by constructing the orchestrator directly.
    """
    access = PackagedPromptResourceAccess()
    orchestrator = NegotiationGenerationOrchestrator(
        language="zh-CN",
        max_text_chars=DEFAULT_MAX_TEXT_CHARS,
        resource_access=access,
        content_extractor=DefaultNegotiationContentExtractor(None),
        param_extractor=None,
        vocabulary=Vocabulary.for_language("zh-CN", access=access),
    )

    with pytest.raises(RuntimeError, match="param extractor is not configured"):
        orchestrator.validate_propose_prompt_and_data_filling(
            "## 所需信息项\n1. 区域\n",
            default_context(NegotiationPerformative.PROPOSE),
            {"type": "object"},
            INFORMATION_PROPOSE,
        )


@pytest.mark.parametrize(
    ("method", "template_uri"),
    [
        ("validate_accept_prompt_and_data_filling", INFORMATION_PROPOSE),
        ("validate_reject_prompt_and_data_filling", INFORMATION_PROPOSE),
        ("validate_abort_prompt_and_data_filling", INFORMATION_PROPOSE),
    ],
)
def test_the_validation_leg_rejects_mismatched_template_uris(method: str, template_uri: Any) -> None:
    recording = RecordingParamExtractor(FilledParamData({}))
    orchestrator = wired(param_extractor=recording)

    with pytest.raises(ValueError, match="expected performative"):
        getattr(orchestrator, method)(
            "## 所需信息项\n1. 区域\n",
            default_context(NegotiationPerformative.PROPOSE),
            {"type": "object"},
            template_uri,
        )

    assert recording.calls == []


# ----------------------------------------------------------------------
# Builder wiring (portable rows of the Java wiring test)
# ----------------------------------------------------------------------


def test_the_generation_completed_event_is_logged_on_the_module_logger(caplog: Any) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="a2a_t.negotiation.generation.orchestrator"):
        wired().generate_propose_from_data(propose_data(), INFORMATION_PROPOSE)

    completed = [record for record in caplog.records if record.message.startswith("negotiation_generation_completed")]
    assert completed, "the pipeline must emit the generation-completed event"
    assert completed[0].name == "a2a_t.negotiation.generation.orchestrator"


def test_the_generation_failed_event_is_logged_with_the_code_and_uri(caplog: Any) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="a2a_t.negotiation.generation.orchestrator"):
        with pytest.raises(NegotiationGenerationError):
            wired(resource_access=MissingTemplateAccess()).generate_propose_from_data(
                propose_data(), INFORMATION_PROPOSE
            )

    failed = [record for record in caplog.records if record.message.startswith("negotiation_generation_failed")]
    assert failed
    assert "template.not_found" in failed[0].getMessage()
    assert INFORMATION_NEGOTIATION_PROPOSE_URI in failed[0].getMessage()


def test_the_builder_requires_a_language() -> None:
    with pytest.raises(ValueError, match="Negotiation language must be configured."):
        builder().build()


@pytest.mark.parametrize("max_attempts", [0, -1])
def test_the_builder_rejects_a_non_positive_attempt_limit(max_attempts: int) -> None:
    with pytest.raises(ValueError, match="max attempts must be at least 1"):
        wired(max_attempts=max_attempts)


def test_the_builder_rejects_a_language_without_bundled_vocabulary() -> None:
    with pytest.raises(Exception, match="fr-FR"):
        wired(language="fr-FR")


def test_the_attempt_limit_reaches_the_extraction_retry_chain() -> None:
    llm = ScriptedClient(RuntimeError("LLM endpoint unavailable."))

    with pytest.raises(NegotiationGenerationError):
        wired(llm_client=llm, max_attempts=2).generate_propose_from_text(
            "请提供节能区域。", default_context(NegotiationPerformative.PROPOSE), INFORMATION_PROPOSE
        )

    assert llm.calls == 2, "the generation chain must retry up to the limit"


def test_the_builder_exposes_no_type_recognizer_injection_point() -> None:
    """Port of the Java reflection guard: the builder surface carries no ``*recogn*`` field."""
    import dataclasses

    field_names = [field.name.lower() for field in dataclasses.fields(builder())]
    assert not any("recogn" in name for name in field_names), field_names
