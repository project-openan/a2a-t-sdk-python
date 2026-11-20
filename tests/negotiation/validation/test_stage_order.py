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

"""Stage-order proof of the negotiation validation pipeline (P6 acceptance).

The port plan pins a four-stage order — input gate (16384 characters) -> rule gate (UUID shape and
round budget) -> LLM semantic gate -> deterministic merge — with the template loading gate running
between the rule gate and the semantic gate (Java ``ParamExtractor``). Each test proves that stage
N runs only after stage N-1 passes by counting the invocations of every stage stub: a blocked stage
leaves every later stage at exactly zero invocations, and the full pass runs each stage exactly once
in the pinned order.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from a2a_t.core.errors.exceptions import NegotiationParamExtractionError, ResourceNotFoundError, SlotValidationError
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import INFORMATION_NEGOTIATION_PROPOSE
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.llm.models import LLMResponse
from a2a_t.negotiation.content.enums import NegotiationType
from a2a_t.negotiation.generation.builder import NegotiationGenerationOrchestratorBuilder
from a2a_t.negotiation.resources.reference import NegotiationReference
from a2a_t.negotiation.validation.compliance_checker import NegotiationRuleCheckResult
from a2a_t.negotiation.validation.param_extractor import ParamExtractor
from a2a_t.negotiation.validation.semantic_validator import SemanticValidationResult

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

CONTEXT = NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.PROPOSE)

REFERENCE = NegotiationReference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, "zh-CN")

VALID_ZH_PROMPT = "## 所需信息项\n1. 节能区域信息：请提供真实存在的区域\n"

TEMPLATE_CONTENT = "dummy template content"

ZH_CN = "zh-CN"


class CountingLlm:
    """LLM stub counting every structured call; returns one scripted payload."""

    def __init__(
        self, payload: str = '{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":{}}'
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
        return LLMResponse(
            content=self.payload,
            model="counting-model",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
            metadata={},
        )


class StageRecorder:
    """Base of every counting stage stub: records one entry per invocation."""

    def __init__(self, name: str, order: list[str]) -> None:
        self.name = name
        self.order = order
        self.invocations = 0

    def _enter(self) -> None:
        self.invocations += 1
        self.order.append(self.name)


class CountingComplianceChecker(StageRecorder):
    """Rule-gate stub recording invocations and returning one scripted outcome."""

    def __init__(self, order: list[str], passed: bool = True) -> None:
        super().__init__("rule", order)
        self.passed = passed

    def check(self, context: NegotiationContext) -> NegotiationRuleCheckResult:
        self._enter()
        if self.passed:
            return NegotiationRuleCheckResult(True, ())
        return NegotiationRuleCheckResult(
            False, (SlotValidationError("round", "negotiation.round_exceeded", "round exceeds maxRounds"),)
        )


class CountingTemplateContentLoader(StageRecorder):
    """Template-loading-gate stub recording invocations and returning one scripted body."""

    def __init__(self, order: list[str], failure: BaseException | None = None) -> None:
        super().__init__("load", order)
        self.failure = failure

    def load(self, reference: NegotiationReference) -> str:
        self._enter()
        if self.failure is not None:
            raise self.failure
        return TEMPLATE_CONTENT


class CountingSemanticValidator(StageRecorder):
    """Semantic-gate stub recording invocations and returning one scripted outcome."""

    def __init__(self, order: list[str], result: SemanticValidationResult | None = None) -> None:
        super().__init__("semantic", order)
        self.result = (
            SemanticValidationResult(True, "information", (), {"region": "松山湖"}) if result is None else result
        )

    def validate_negotiation(
        self,
        prompt: str,
        caller_schema: dict[str, Any],
        reference: NegotiationReference,
        template_content: str,
    ) -> SemanticValidationResult:
        self._enter()
        return self.result

    def validate(
        self,
        prompt: str,
        schema: dict[str, Any],
        reference: NegotiationReference,
        template_content: str,
    ) -> Any:
        from a2a_t.core.validation_pipeline import ValidationResult

        self._enter()
        return ValidationResult(self.result.verdict, self.result.errors, self.result.params)


class CountingParamExtractor:
    """Whole-extractor stub counting the invocations of the wired validation leg."""

    def __init__(self) -> None:
        self.invocations = 0

    def extract(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: dict[str, Any],
        reference: NegotiationReference,
    ) -> FilledParamData:
        self.invocations += 1
        return FilledParamData({"id": SESSION_ID})


# --------------------------------------------------------------------------------------
# stage 1: the input gate blocks every later stage
# --------------------------------------------------------------------------------------


def test_oversized_prompt_is_rejected_before_the_extractor_and_any_llm_call() -> None:
    llm = CountingLlm()
    counting_extractor = CountingParamExtractor()
    orchestrator = NegotiationGenerationOrchestratorBuilder(
        language=ZH_CN,
        llm_client=llm,
        max_text_chars=16,
        param_extractor=counting_extractor,
    ).build()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        orchestrator.validate_propose_prompt_and_data_filling("x" * 17, CONTEXT, {}, INFORMATION_NEGOTIATION_PROPOSE)

    assert excinfo.value.code_str == "input.text_too_long"
    assert excinfo.value.facts == {"actual_length": "17", "max_chars": "16"}
    assert counting_extractor.invocations == 0
    assert llm.calls == 0


@pytest.mark.parametrize("prompt", ["", "   ", None], ids=["empty", "blank", "none"])
def test_blank_prompt_is_rejected_by_the_input_gate_without_touching_the_rule_gate(prompt: str | None) -> None:
    order: list[str] = []
    rule = CountingComplianceChecker(order)
    loader = CountingTemplateContentLoader(order)
    semantic = CountingSemanticValidator(order)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        ParamExtractor(rule, semantic, 1, loader).extract(prompt, CONTEXT, {}, REFERENCE)

    assert excinfo.value.code_str == "negotiation.invalid_input"
    assert rule.invocations == 0
    assert loader.invocations == 0
    assert semantic.invocations == 0


def test_missing_schema_is_rejected_by_the_input_gate_without_touching_the_rule_gate() -> None:
    order: list[str] = []
    rule = CountingComplianceChecker(order)
    loader = CountingTemplateContentLoader(order)
    semantic = CountingSemanticValidator(order)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        ParamExtractor(rule, semantic, 1, loader).extract(VALID_ZH_PROMPT, CONTEXT, None, REFERENCE)

    assert excinfo.value.code_str == "negotiation.invalid_input"
    assert rule.invocations == 0
    assert loader.invocations == 0
    assert semantic.invocations == 0


# --------------------------------------------------------------------------------------
# stage 2: the rule gate blocks the template loading and the semantic gate
# --------------------------------------------------------------------------------------


def test_rule_gate_failure_blocks_the_template_loading_and_the_semantic_gate() -> None:
    order: list[str] = []
    rule = CountingComplianceChecker(order, passed=False)
    loader = CountingTemplateContentLoader(order)
    semantic = CountingSemanticValidator(order)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        ParamExtractor(rule, semantic, 1, loader).extract(VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE)

    assert excinfo.value.code_str == "negotiation.rule_violation"
    assert rule.invocations == 1
    assert loader.invocations == 0
    assert semantic.invocations == 0
    assert order == ["rule"]


def test_rule_gate_failure_through_the_default_wiring_makes_zero_llm_calls() -> None:
    llm = CountingLlm()
    order: list[str] = []
    rule = CountingComplianceChecker(order, passed=False)
    orchestrator = NegotiationGenerationOrchestratorBuilder(
        language=ZH_CN, llm_client=llm, compliance_checker=rule
    ).build()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        orchestrator.validate_propose_prompt_and_data_filling(
            VALID_ZH_PROMPT,
            NegotiationContext(SESSION_ID, 9, 5, NegotiationPerformative.PROPOSE),
            {},
            INFORMATION_NEGOTIATION_PROPOSE,
        )

    assert excinfo.value.code_str == "negotiation.rule_violation"
    assert llm.calls == 0


# --------------------------------------------------------------------------------------
# stage 3: the template loading gate blocks the semantic gate
# --------------------------------------------------------------------------------------


def test_template_loading_failure_blocks_the_semantic_gate() -> None:
    order: list[str] = []
    rule = CountingComplianceChecker(order)
    loader = CountingTemplateContentLoader(order, failure=ResourceNotFoundError("missing", "templates/x"))
    semantic = CountingSemanticValidator(order)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        ParamExtractor(rule, semantic, 1, loader).extract(VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE)

    assert excinfo.value.code_str == "template.not_found"
    assert rule.invocations == 1
    assert loader.invocations == 1
    assert semantic.invocations == 0
    assert order == ["rule", "load"]


# --------------------------------------------------------------------------------------
# stage 4: a rejected semantic gate blocks the merge
# --------------------------------------------------------------------------------------


def test_semantic_rejection_blocks_the_deterministic_merge() -> None:
    order: list[str] = []
    rule = CountingComplianceChecker(order)
    loader = CountingTemplateContentLoader(order)
    semantic = CountingSemanticValidator(
        order,
        SemanticValidationResult(False, None, (SlotValidationError("s", "negotiation.rule_violation", "m"),), {}),
    )

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        ParamExtractor(rule, semantic, 1, loader).extract(VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE)

    assert excinfo.value.code_str == "negotiation.semantic_rejected"
    assert rule.invocations == 1
    assert loader.invocations == 1
    assert semantic.invocations == 1
    assert order == ["rule", "load", "semantic"]


# --------------------------------------------------------------------------------------
# the full pass runs every stage exactly once, then merges deterministically
# --------------------------------------------------------------------------------------


def test_full_pass_runs_every_stage_exactly_once_in_the_pinned_order() -> None:
    order: list[str] = []
    rule = CountingComplianceChecker(order)
    loader = CountingTemplateContentLoader(order)
    semantic = CountingSemanticValidator(
        order, SemanticValidationResult(True, "information", (), {"id": "llm-value", "region": "松山湖"})
    )

    filled = ParamExtractor(rule, semantic, 1, loader).extract(VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE)

    assert order == ["rule", "load", "semantic"]
    assert (rule.invocations, loader.invocations, semantic.invocations) == (1, 1, 1)
    # the deterministic merge writes the rule-gate context parameters first and lets them win
    assert filled.data == {
        "id": SESSION_ID,
        "round": 2,
        "maxRounds": 5,
        "region": "松山湖",
    }


def test_full_pass_through_the_default_wiring_makes_exactly_one_llm_call() -> None:
    llm = CountingLlm()
    orchestrator = NegotiationGenerationOrchestratorBuilder(language=ZH_CN, llm_client=llm).build()

    filled = orchestrator.validate_propose_prompt_and_data_filling(
        VALID_ZH_PROMPT, CONTEXT, {}, INFORMATION_NEGOTIATION_PROPOSE
    )

    assert llm.calls == 1
    assert filled.data["id"] == SESSION_ID
    assert filled.data["round"] == 2
    assert filled.data["maxRounds"] == 5


def test_completion_events_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    llm = CountingLlm()
    orchestrator = NegotiationGenerationOrchestratorBuilder(language=ZH_CN, llm_client=llm).build()

    with caplog.at_level(logging.INFO, logger="a2a_t.negotiation.validation.semantic_validator"):
        orchestrator.validate_propose_prompt_and_data_filling(
            VALID_ZH_PROMPT, CONTEXT, {}, INFORMATION_NEGOTIATION_PROPOSE
        )

    assert "negotiation_semantic_validation_completed verdict=True error_count=0" in caplog.text
