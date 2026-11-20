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

"""Service-level integration tests of the validation leg (P6 wiring).

The 12-method service facade is driven over a builder-wired orchestrator whose only replaced
collaborator is the LLM (a scripted client counting every call); every other collaborator is the
production wiring — the packaged semantic validation prompts of both languages, the packaged
templates, the default compliance checker, the default semantic validator and the default parameter
extractor. The suite stays offline and deterministic, and the retry semantics are pinned by exact
LLM call counts (port plan 7.3).
"""

from __future__ import annotations

from typing import Any

import pytest

from a2a_t.core.errors.exceptions import NegotiationParamExtractionError, ResourceNotFoundError
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.models import LLMResponse
from a2a_t.negotiation.generation.builder import NegotiationGenerationOrchestratorBuilder
from a2a_t.negotiation.generation.content_service import NegotiationContentService

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

VALID_CONTEXT_PROMPT = "## 协商上下文\n- id: " + SESSION_ID + "\n- round: 1\n- maxRounds: 5"

SCHEMA: dict[str, Any] = {"type": "object", "properties": {"region": {"type": "string"}}}

CONTEXT = NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE)

INFORMATION_PROPOSE_URI = "Negotiation-T/information-negotiation/propose/v1"

ZH_CN = "zh-CN"


class ScriptedLlm:
    """Scripted LLM client counting every structured call and returning one payload."""

    def __init__(
        self,
        payload: str
        | None = '{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":{"region":"松山湖"}}',
    ) -> None:
        self.payload = payload
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
        if self.payload is None:
            raise LLMRuntimeError("LLM endpoint unavailable.")
        return LLMResponse(
            content=self.payload, model="test-model", usage={"prompt_tokens": 1, "completion_tokens": 1}, metadata={}
        )


def service(llm: ScriptedLlm | None = None, **overrides: Any) -> NegotiationContentService:
    """Build the service under test over a builder-wired orchestrator with a scripted LLM."""
    builder = NegotiationGenerationOrchestratorBuilder(language=ZH_CN, **overrides)
    if "llm_client" not in overrides:
        builder.llm_client = ScriptedLlm() if llm is None else llm
    return NegotiationContentService(builder.build())


# --------------------------------------------------------------------------------------
# happy paths: all four validation methods
# --------------------------------------------------------------------------------------


def test_validate_propose_happy_path_fills_the_context_and_extracted_parameters() -> None:
    llm = ScriptedLlm()

    filled = service(llm).validate_propose_prompt_and_data_filling(
        VALID_CONTEXT_PROMPT, CONTEXT, SCHEMA, INFORMATION_PROPOSE_URI
    )

    assert isinstance(filled, FilledParamData)
    assert filled.data == {"id": SESSION_ID, "round": 1, "maxRounds": 5, "region": "松山湖"}
    assert llm.calls == 1


def test_validate_accept_happy_path_fills_the_context_and_extracted_parameters() -> None:
    llm = ScriptedLlm()

    filled = service(llm).validate_accept_prompt_and_data_filling(
        VALID_CONTEXT_PROMPT,
        NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.ACCEPT),
        SCHEMA,
        "Negotiation-T/information-negotiation/accept-reject/v1",
    )

    assert filled.data == {"id": SESSION_ID, "round": 2, "maxRounds": 5, "region": "松山湖"}
    assert llm.calls == 1


def test_validate_reject_happy_path_fills_the_context_and_extracted_parameters() -> None:
    llm = ScriptedLlm(
        '{"semantic_verdict":true,"negotiation_type":"target","errors":[],'
        '"params":{"energy saving region":"site inventory unavailable, cannot provide"}}'
    )

    filled = service(llm).validate_reject_prompt_and_data_filling(
        VALID_CONTEXT_PROMPT,
        NegotiationContext(SESSION_ID, 3, 5, NegotiationPerformative.REJECT),
        {"type": "object", "properties": {"energy saving region": {"type": "string"}}},
        "Negotiation-T/target-negotiation/accept-reject/v1",
    )

    assert filled.data == {
        "id": SESSION_ID,
        "round": 3,
        "maxRounds": 5,
        "energy saving region": "site inventory unavailable, cannot provide",
    }
    assert llm.calls == 1


def test_validate_abort_happy_path_skips_the_type_consistency_check() -> None:
    llm = ScriptedLlm('{"semantic_verdict":true,"negotiation_type":null,"errors":[],"params":{"reason":"不可行"}}')

    filled = service(llm).validate_abort_prompt_and_data_filling(
        VALID_CONTEXT_PROMPT,
        NegotiationContext(SESSION_ID, 4, 5, NegotiationPerformative.ABORT),
        {"type": "object", "properties": {"reason": {"type": "string"}}},
        "Negotiation-T/common/abort/v1",
    )

    assert filled.data == {"id": SESSION_ID, "round": 4, "maxRounds": 5, "reason": "不可行"}
    assert llm.calls == 1


def test_context_parameters_win_over_extracted_parameters_on_conflict() -> None:
    llm = ScriptedLlm('{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":{"round":99}}')

    filled = service(llm).validate_propose_prompt_and_data_filling(
        VALID_CONTEXT_PROMPT, CONTEXT, SCHEMA, INFORMATION_PROPOSE_URI
    )

    assert filled.data["round"] == 1


@pytest.mark.parametrize("language", [ZH_CN, "en-US"], ids=["zh-CN", "en-US"])
def test_validation_renders_failures_in_the_configured_language(language: str) -> None:
    llm = ScriptedLlm()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        NegotiationContentService(
            NegotiationGenerationOrchestratorBuilder(language=language, llm_client=llm).build()
        ).validate_propose_prompt_and_data_filling(
            "plain text without any negotiation section", None, SCHEMA, INFORMATION_PROPOSE_URI
        )

    expected = (
        "输入的协商内容无效:缺少协商上下文(该报文不是协商报文)"
        if language == ZH_CN
        else "The negotiation input is invalid: the negotiation context is missing (the message is not a negotiation message)"
    )
    assert str(excinfo.value) == expected
    assert llm.calls == 0


# --------------------------------------------------------------------------------------
# failure paths
# --------------------------------------------------------------------------------------


def test_non_negotiation_input_fails_without_any_llm_call() -> None:
    llm = ScriptedLlm()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        service(llm).validate_propose_prompt_and_data_filling(
            "plain text without any negotiation section", None, SCHEMA, INFORMATION_PROPOSE_URI
        )

    assert excinfo.value.code_str == "negotiation.invalid_input"
    assert excinfo.value.errors == []
    assert llm.calls == 0


@pytest.mark.parametrize(
    ("ctx", "expected_slots", "expected_codes", "expected_facts"),
    [
        (
            NegotiationContext(SESSION_ID, 9, 5, NegotiationPerformative.PROPOSE),
            ["round"],
            ["negotiation.round_exceeded"],
            {"round": "9", "max_rounds": "5"},
        ),
        (
            NegotiationContext("not-a-uuid", 1, 5, NegotiationPerformative.PROPOSE),
            ["id"],
            ["negotiation.invalid_context_id"],
            {"actual": "not-a-uuid"},
        ),
        (
            NegotiationContext("short", 6, 5, NegotiationPerformative.PROPOSE),
            ["id", "round"],
            ["negotiation.invalid_context_id", "negotiation.round_exceeded"],
            {"actual": "short"},
        ),
    ],
    ids=["round-above-max-rounds", "invalid-uuid", "both-rules-report-both-errors"],
)
def test_rule_violation_fails_without_any_llm_call(
    ctx: NegotiationContext,
    expected_slots: list[str],
    expected_codes: list[str],
    expected_facts: dict[str, str],
) -> None:
    llm = ScriptedLlm()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        service(llm).validate_propose_prompt_and_data_filling(
            "## 所需信息项\n1. 区域\n", ctx, SCHEMA, INFORMATION_PROPOSE_URI
        )

    assert excinfo.value.code_str == "negotiation.rule_violation"
    assert [error.slot_name for error in excinfo.value.errors] == expected_slots
    assert [error.code for error in excinfo.value.errors] == expected_codes
    assert excinfo.value.errors[0].facts == expected_facts
    assert llm.calls == 0


def test_semantic_rejection_passes_the_reported_errors_through() -> None:
    llm = ScriptedLlm(
        '{"semantic_verdict":false,"negotiation_type":null,"errors":[{"slot_name":"section.info_static",'
        '"code":"negotiation.type_mismatch","facts":{"implied":"information","declared":"information"}}],'
        '"params":{}}'
    )

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        service(llm).validate_propose_prompt_and_data_filling(
            VALID_CONTEXT_PROMPT, CONTEXT, SCHEMA, INFORMATION_PROPOSE_URI
        )

    assert excinfo.value.code_str == "negotiation.semantic_rejected"
    assert [(error.slot_name, error.code) for error in excinfo.value.errors] == [
        ("section.info_static", "negotiation.type_mismatch")
    ]
    assert llm.calls == 1


@pytest.mark.parametrize("max_attempts", [1, 2, 3], ids=["one-attempt", "two-attempts", "three-attempts"])
def test_llm_infrastructure_failure_is_retryable_to_the_attempt_limit(max_attempts: int) -> None:
    llm = ScriptedLlm(payload=None)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        service(llm, max_attempts=max_attempts).validate_propose_prompt_and_data_filling(
            VALID_CONTEXT_PROMPT, CONTEXT, SCHEMA, INFORMATION_PROPOSE_URI
        )

    assert excinfo.value.code_str == "llm.invocation_failed"
    assert llm.calls == max_attempts


@pytest.mark.parametrize("max_attempts", [1, 2], ids=["one-attempt", "two-attempts"])
def test_response_contract_violation_is_retryable_to_the_attempt_limit(max_attempts: int) -> None:
    llm = ScriptedLlm(payload='{"semantic_verdict":true,"errors":[],"params":{}}')

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        service(llm, max_attempts=max_attempts).validate_propose_prompt_and_data_filling(
            VALID_CONTEXT_PROMPT, CONTEXT, SCHEMA, INFORMATION_PROPOSE_URI
        )

    assert excinfo.value.code_str == "llm.response_invalid"
    assert llm.calls == max_attempts


def test_oversized_prompt_fails_fast_without_any_llm_call() -> None:
    llm = ScriptedLlm()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        service(llm, max_text_chars=16).validate_propose_prompt_and_data_filling(
            "x" * 17, CONTEXT, SCHEMA, INFORMATION_PROPOSE_URI
        )

    assert excinfo.value.code_str == "input.text_too_long"
    assert excinfo.value.facts == {"actual_length": "17", "max_chars": "16"}
    assert llm.calls == 0


def test_missing_llm_client_fails_as_not_configured_without_retry() -> None:
    llm = ScriptedLlm()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        service(llm, max_attempts=3, llm_client=None).validate_propose_prompt_and_data_filling(
            VALID_CONTEXT_PROMPT, CONTEXT, SCHEMA, INFORMATION_PROPOSE_URI
        )

    assert excinfo.value.code_str == "llm.not_configured"
    assert llm.calls == 0


def test_prompt_resource_miss_is_mapped_to_template_not_found() -> None:
    class ThrowingSemanticValidator:
        def validate_negotiation(self, prompt, caller_schema, reference, template_content):
            raise ResourceNotFoundError("Semantic validation prompt does not exist.", "prompt_resources/prompts")

        def validate(self, prompt, schema, reference, template_content):
            raise ResourceNotFoundError("Semantic validation prompt does not exist.", "prompt_resources/prompts")

    llm = ScriptedLlm()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        service(
            llm,
            semantic_validator=ThrowingSemanticValidator(),  # type: ignore[arg-type]
        ).validate_propose_prompt_and_data_filling(VALID_CONTEXT_PROMPT, CONTEXT, SCHEMA, INFORMATION_PROPOSE_URI)

    assert excinfo.value.code_str == "template.not_found"
    assert excinfo.value.errors == []
    assert llm.calls == 0


# --------------------------------------------------------------------------------------
# programming errors stay outside the coded business tree
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "uri"),
    [
        ("validate_propose_prompt_and_data_filling", INFORMATION_PROPOSE_URI),
        ("validate_accept_prompt_and_data_filling", "Negotiation-T/information-negotiation/accept-reject/v1"),
        ("validate_reject_prompt_and_data_filling", "Negotiation-T/information-negotiation/accept-reject/v1"),
        ("validate_abort_prompt_and_data_filling", "Negotiation-T/common/abort/v1"),
    ],
    ids=["propose", "accept", "reject", "abort"],
)
def test_null_schema_is_a_programming_error(method: str, uri: str) -> None:
    with pytest.raises(TypeError):
        getattr(service(), method)(VALID_CONTEXT_PROMPT, CONTEXT, None, uri)


@pytest.mark.parametrize(
    ("method", "uri"),
    [
        ("validate_propose_prompt_and_data_filling", "Negotiation-T/information-negotiation/accept-reject/v1"),
        ("validate_accept_prompt_and_data_filling", INFORMATION_PROPOSE_URI),
        ("validate_abort_prompt_and_data_filling", INFORMATION_PROPOSE_URI),
    ],
    ids=["propose-uri-with-accept-segment", "accept-uri-with-propose-segment", "abort-uri-not-common"],
)
def test_wrong_performative_uri_is_a_programming_error(method: str, uri: str) -> None:
    with pytest.raises(ValueError, match="does not address a negotiation template"):
        getattr(service(), method)(VALID_CONTEXT_PROMPT, CONTEXT, SCHEMA, uri)
