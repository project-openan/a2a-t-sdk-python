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

"""Full-chain validation tests over real generated messages (port of Java
``ValidateAndFillingDataPipelineTest``).

Unlike :mod:`test_service_validation` (handcrafted prompts), this suite validates the messages the
generation leg actually renders — each message is produced by ``generate_*_from_data`` and then fed
through the matching ``validate_*_prompt_and_data_filling`` method — so the generation and validation
legs are pinned against each other. The LLM is a scripted client recording every structured call and
the merged output schema it received; every other collaborator is the production wiring.
"""

from __future__ import annotations

from typing import Any

import pytest

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import NegotiationParamExtractionError, SlotValidationError
from a2a_t.core.errors.messages import render
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import (
    INFORMATION_NEGOTIATION_ACCEPT_REJECT,
    INFORMATION_NEGOTIATION_PROPOSE,
    TARGET_NEGOTIATION_PROPOSE,
)
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.models import LLMResponse
from a2a_t.negotiation.content import (
    InformationEndingContent,
    InformationProposeContent,
    NegotiationConclusion,
    NegotiationEndingData,
    NegotiationItem,
    NegotiationProposeData,
    TargetProposeContent,
)
from a2a_t.negotiation.generation.builder import NegotiationGenerationOrchestratorBuilder
from a2a_t.negotiation.generation.orchestrator import NegotiationGenerationOrchestrator

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

CONTEXT = NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.PROPOSE)

TARGET_CONTEXT = NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE)

ACCEPT_CONTEXT = NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.ACCEPT)

REJECT_CONTEXT = NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.REJECT)

ACCESS_PORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"accessPort": {"type": "string"}, "bizScenario": {"type": "string"}},
    "required": ["accessPort", "bizScenario"],
}

ZH_CN = "zh-CN"


class ScriptedLlmClient:
    """LLM boundary fake replaying scripted payloads and recording every structured call."""

    def __init__(self, *payloads: str) -> None:
        self.payloads = list(payloads)
        self.calls = 0
        self.failure: BaseException | None = None
        self.last_messages: list[dict[str, str]] = []
        self.last_schema: dict[str, Any] = {}

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.last_messages = messages
        self.last_schema = json_schema
        if self.failure is not None:
            raise self.failure
        payload = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        return LLMResponse(
            content=payload, model="test-model", usage={"prompt_tokens": 1, "completion_tokens": 1}, metadata={}
        )


def orchestrator(llm: ScriptedLlmClient, **overrides: Any) -> NegotiationGenerationOrchestrator:
    """Build the orchestrator under test over one scripted LLM client."""
    return NegotiationGenerationOrchestratorBuilder(language=ZH_CN, llm_client=llm, **overrides).build()


def information_propose_message(orchestrator_instance: NegotiationGenerationOrchestrator) -> str:
    """Render the information propose message of the round trip under test."""
    content = orchestrator_instance.generate_propose_from_data(
        NegotiationProposeData(
            NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.PROPOSE),
            InformationProposeContent([NegotiationItem("接入端口名称", "P533-珠江旧城-PTN3900-23-TPA1EG24-1")], None),
        ),
        INFORMATION_NEGOTIATION_PROPOSE,
    )
    return content.prompt_text or ""


def information_ending_message(
    orchestrator_instance: NegotiationGenerationOrchestrator, conclusion: NegotiationConclusion
) -> str:
    """Render the information ending message of the round trip under test."""
    data = NegotiationEndingData(
        NegotiationContext(
            SESSION_ID,
            2,
            5,
            NegotiationPerformative.ACCEPT
            if conclusion is NegotiationConclusion.ACCEPT
            else NegotiationPerformative.REJECT,
        ),
        InformationEndingContent(conclusion, [NegotiationItem("接入端口名称", "P533-珠江旧城-PTN3900-23-TPA1EG24-1")]),
    )
    if conclusion is NegotiationConclusion.ACCEPT:
        content = orchestrator_instance.generate_accept_from_data(data, INFORMATION_NEGOTIATION_ACCEPT_REJECT)
    else:
        content = orchestrator_instance.generate_reject_from_data(data, INFORMATION_NEGOTIATION_ACCEPT_REJECT)
    return content.prompt_text or ""


def target_propose_message(orchestrator_instance: NegotiationGenerationOrchestrator) -> str:
    """Render the target propose message of the round trip under test."""
    content = orchestrator_instance.generate_propose_from_data(
        NegotiationProposeData(
            NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE),
            TargetProposeContent(
                "确认专线质差投诉的时延修复目标调整方案",
                [NegotiationItem("修复意图", "在2026年5月15日前将深圳至广州专线的平均时延恢复至20ms以内")],
                None,
                None,
                None,
            ),
        ),
        TARGET_NEGOTIATION_PROPOSE,
    )
    return content.prompt_text or ""


# --------------------------------------------------------------------------------------
# legal messages run the full chain with a single LLM call
# --------------------------------------------------------------------------------------


def test_legal_propose_message_runs_the_full_chain_with_a_single_llm_call() -> None:
    llm = ScriptedLlmClient(
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":'
        '{"accessPort":"P533-珠江旧城-PTN3900-23-TPA1EG24-1","bizScenario":"专线质差"}}'
    )
    message = information_propose_message(orchestrator(llm))

    filled = orchestrator(llm).validate_propose_prompt_and_data_filling(
        message, CONTEXT, ACCESS_PORT_SCHEMA, INFORMATION_NEGOTIATION_PROPOSE
    )

    assert isinstance(filled, FilledParamData)
    assert llm.calls == 1
    assert filled.data["id"] == SESSION_ID
    assert filled.data["round"] == 2
    assert filled.data["maxRounds"] == 5
    assert filled.data["accessPort"] == "P533-珠江旧城-PTN3900-23-TPA1EG24-1"
    assert filled.data["bizScenario"] == "专线质差"
    assert len(filled.data) == 5
    assert isinstance(filled.data["id"], str)
    assert isinstance(filled.data["round"], int)
    assert isinstance(filled.data["maxRounds"], int)


def test_legal_accept_message_runs_the_full_chain_with_a_single_llm_call() -> None:
    llm = ScriptedLlmClient(
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":'
        '{"accessPort":"P533-珠江旧城-PTN3900-23-TPA1EG24-1"}}'
    )
    message = information_ending_message(orchestrator(llm), NegotiationConclusion.ACCEPT)

    filled = orchestrator(llm).validate_accept_prompt_and_data_filling(
        message, ACCEPT_CONTEXT, ACCESS_PORT_SCHEMA, INFORMATION_NEGOTIATION_ACCEPT_REJECT
    )

    assert llm.calls == 1
    assert filled.data["id"] == SESSION_ID
    assert filled.data["round"] == 2
    assert filled.data["maxRounds"] == 5
    assert filled.data["accessPort"] == "P533-珠江旧城-PTN3900-23-TPA1EG24-1"
    assert len(filled.data) == 4


def test_legal_reject_message_runs_the_full_chain_with_a_single_llm_call() -> None:
    llm = ScriptedLlmClient(
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[],'
        '"params":{"unavailable_item":"接入端口名称"}}'
    )
    message = information_ending_message(orchestrator(llm), NegotiationConclusion.REJECT)

    filled = orchestrator(llm).validate_reject_prompt_and_data_filling(
        message, REJECT_CONTEXT, ACCESS_PORT_SCHEMA, INFORMATION_NEGOTIATION_ACCEPT_REJECT
    )

    assert llm.calls == 1
    assert filled.data["id"] == SESSION_ID
    assert filled.data["unavailable_item"] == "接入端口名称"
    assert len(filled.data) == 4


# --------------------------------------------------------------------------------------
# caller schemas: nested arrays and schemaless inputs
# --------------------------------------------------------------------------------------


def test_nested_array_params_are_extracted_through_the_merged_schema() -> None:
    repair_target_item_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "latencyTarget": {"type": "string"},
            "completionDeadline": {"type": "string"},
        },
    }
    caller_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"repairTargets": {"type": "array", "items": repair_target_item_schema}},
        "required": ["repairTargets"],
        "additionalProperties": False,
    }
    llm = ScriptedLlmClient(
        '{"semantic_verdict":true,"negotiation_type":"target","errors":[],"params":{"repairTargets":'
        '[{"latencyTarget":"within 20ms","completionDeadline":"48 hours"}]}}'
    )
    message = target_propose_message(orchestrator(llm))

    filled = orchestrator(llm).validate_propose_prompt_and_data_filling(
        message, TARGET_CONTEXT, caller_schema, TARGET_NEGOTIATION_PROPOSE
    )

    assert llm.calls == 1
    assert filled.data["id"] == SESSION_ID
    assert filled.data["repairTargets"] == [{"latencyTarget": "within 20ms", "completionDeadline": "48 hours"}]

    merged_schema = llm.last_schema
    assert merged_schema["required"] == ["semantic_verdict", "negotiation_type", "errors", "params"]
    assert merged_schema["additionalProperties"] is False
    params_schema = merged_schema["properties"]["params"]
    assert params_schema["type"] == "object"
    repair_targets = params_schema["properties"]["repairTargets"]
    assert repair_targets["type"] == "array"


def test_caller_schema_without_a_type_keyword_is_wrapped_and_the_chain_succeeds() -> None:
    schema_without_type: dict[str, Any] = {
        "properties": {"accessPort": {"type": "string"}},
        "required": ["accessPort"],
    }
    llm = ScriptedLlmClient(
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":'
        '{"accessPort":"P533-珠江旧城-PTN3900-23-TPA1EG24-1"}}'
    )
    message = information_propose_message(orchestrator(llm))

    filled = orchestrator(llm).validate_propose_prompt_and_data_filling(
        message, CONTEXT, schema_without_type, INFORMATION_NEGOTIATION_PROPOSE
    )

    assert llm.calls == 1
    assert filled.data["accessPort"] == "P533-珠江旧城-PTN3900-23-TPA1EG24-1"
    params_schema = llm.last_schema["properties"]["params"]
    assert params_schema["type"] == "object"
    assert params_schema["properties"] == {"accessPort": {"type": "string"}}
    assert params_schema["required"] == ["accessPort"]


# --------------------------------------------------------------------------------------
# semantic decisions are never retried
# --------------------------------------------------------------------------------------


def test_semantic_rejection_is_not_retried_and_passes_the_errors_through() -> None:
    conclusion_facts = {"conclusion": "Accept", "section_label": "section.info_conclusion"}
    result_facts = {"section_label": "section.info_items"}
    semantic_errors = (
        SlotValidationError(
            "section.info_conclusion",
            ErrorCatalog.NEGOTIATION_CONCLUSION_CONTENT_MISMATCH.value,
            render(ErrorCatalog.NEGOTIATION_CONCLUSION_CONTENT_MISMATCH, conclusion_facts, ZH_CN),
            conclusion_facts,
        ),
        SlotValidationError(
            "section.info_items",
            ErrorCatalog.NEGOTIATION_MISSING_RESULT_CONTENT.value,
            render(ErrorCatalog.NEGOTIATION_MISSING_RESULT_CONTENT, result_facts, ZH_CN),
            result_facts,
        ),
    )
    llm = ScriptedLlmClient(
        '{"semantic_verdict":false,"negotiation_type":null,"errors":'
        '[{"slot_name":"section.info_conclusion","code":"negotiation.conclusion_content_mismatch",'
        '"facts":{"conclusion":"Accept","section_label":"section.info_conclusion"}},'
        '{"slot_name":"section.info_items","code":"negotiation.missing_result_content",'
        '"facts":{"section_label":"section.info_items"}}],"params":{}}'
    )
    instance = orchestrator(llm)
    message = information_propose_message(instance)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        instance.validate_propose_prompt_and_data_filling(
            message, CONTEXT, ACCESS_PORT_SCHEMA, INFORMATION_NEGOTIATION_PROPOSE
        )

    assert excinfo.value.code_str == "negotiation.semantic_rejected"
    assert tuple(excinfo.value.errors) == semantic_errors
    assert llm.calls == 1, "a negative verdict is a decision, not a failure, and must not be retried"


def test_declared_type_mismatch_is_a_semantic_rejection() -> None:
    llm = ScriptedLlmClient('{"semantic_verdict":true,"negotiation_type":"target","errors":[],"params":{}}')
    instance = orchestrator(llm)
    message = information_propose_message(instance)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        instance.validate_propose_prompt_and_data_filling(
            message, CONTEXT, ACCESS_PORT_SCHEMA, INFORMATION_NEGOTIATION_PROPOSE
        )

    assert excinfo.value.code_str == "negotiation.semantic_rejected"
    assert llm.calls == 1, "a type mismatch is a semantic decision and must not be retried"
    assert len(excinfo.value.errors) == 1
    assert excinfo.value.errors[0].slot_name == "section.target"
    assert excinfo.value.errors[0].code == "negotiation.type_mismatch"
    assert excinfo.value.errors[0].facts == {"implied": "target", "declared": "information"}


def test_true_verdict_with_null_type_is_a_semantic_rejection() -> None:
    llm = ScriptedLlmClient('{"semantic_verdict":true,"negotiation_type":null,"errors":[],"params":{}}')
    instance = orchestrator(llm)
    message = information_propose_message(instance)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        instance.validate_propose_prompt_and_data_filling(
            message, CONTEXT, ACCESS_PORT_SCHEMA, INFORMATION_NEGOTIATION_PROPOSE
        )

    assert excinfo.value.code_str == "negotiation.semantic_rejected"
    assert llm.calls == 1, "a null type with a true verdict is a semantic rejection, not a retryable failure"
    assert len(excinfo.value.errors) == 1
    assert excinfo.value.errors[0].slot_name == "section.info_static"
    assert excinfo.value.errors[0].code == "negotiation.type_mismatch"


def test_false_verdict_with_null_type_is_a_shape_legal_outcome() -> None:
    llm = ScriptedLlmClient(
        '{"semantic_verdict":false,"negotiation_type":null,"errors":'
        '[{"slot_name":"section.context","code":"inconsistent_context",'
        '"facts":{"reason":"Context contradicts the message body."}}],"params":{}}'
    )
    instance = orchestrator(llm)
    message = information_propose_message(instance)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        instance.validate_propose_prompt_and_data_filling(
            message, CONTEXT, ACCESS_PORT_SCHEMA, INFORMATION_NEGOTIATION_PROPOSE
        )

    assert excinfo.value.code_str == "negotiation.semantic_rejected"
    assert len(excinfo.value.errors) == 1
    assert excinfo.value.errors[0].slot_name == "section.context"
    assert excinfo.value.errors[0].code == "negotiation.rule_violation"
    assert llm.calls == 1, "verdict false with a null type is shape-legal and must not be retried"


@pytest.mark.parametrize(
    ("case_name", "payload", "expected_error"),
    [
        (
            "conclusion outside accept and reject",
            '{"semantic_verdict":false,"negotiation_type":"information","errors":'
            '[{"slot_name":"section.target_conclusion","code":"negotiation.conclusion_mismatch",'
            '"facts":{"expected":"Accept","actual":"Abort"}}],"params":{}}',
            SlotValidationError(
                "section.target_conclusion",
                ErrorCatalog.NEGOTIATION_CONCLUSION_MISMATCH.value,
                render(
                    ErrorCatalog.NEGOTIATION_CONCLUSION_MISMATCH,
                    {"expected": "Accept", "actual": "Abort"},
                    ZH_CN,
                ),
                {"expected": "Accept", "actual": "Abort"},
            ),
        ),
        (
            "ending result content section missing",
            '{"semantic_verdict":false,"negotiation_type":"information","errors":'
            '[{"slot_name":"section.target_result_content","code":"negotiation.missing_result_content",'
            '"facts":{"section_label":"section.target_result_content"}}],"params":{}}',
            SlotValidationError(
                "section.target_result_content",
                ErrorCatalog.NEGOTIATION_MISSING_RESULT_CONTENT.value,
                render(
                    ErrorCatalog.NEGOTIATION_MISSING_RESULT_CONTENT,
                    {"section_label": "section.target_result_content"},
                    ZH_CN,
                ),
                {"section_label": "section.target_result_content"},
            ),
        ),
        (
            "information propose carries both conditional sections",
            '{"semantic_verdict":false,"negotiation_type":"information","errors":'
            '[{"slot_name":"section.info_static","code":"negotiation.field_inconsistency",'
            '"facts":{"section_label":"section.info_static","reason":'
            '"the static section coexists with the items"}}],"params":{}}',
            SlotValidationError(
                "section.info_static",
                ErrorCatalog.NEGOTIATION_FIELD_INCONSISTENCY.value,
                render(
                    ErrorCatalog.NEGOTIATION_FIELD_INCONSISTENCY,
                    {"section_label": "section.info_static", "reason": "the static section coexists with the items"},
                    ZH_CN,
                ),
                {"section_label": "section.info_static", "reason": "the static section coexists with the items"},
            ),
        ),
    ],
    ids=["conclusion-mismatch", "missing-result-content", "field-inconsistency"],
)
def test_structural_semantic_checks_surface_through_the_semantic_errors(
    case_name: str, payload: str, expected_error: SlotValidationError
) -> None:
    llm = ScriptedLlmClient(payload)
    instance = orchestrator(llm)
    message = information_propose_message(instance)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        instance.validate_propose_prompt_and_data_filling(
            message, CONTEXT, ACCESS_PORT_SCHEMA, INFORMATION_NEGOTIATION_PROPOSE
        )

    assert excinfo.value.code_str == "negotiation.semantic_rejected", case_name
    assert list(excinfo.value.errors) == [expected_error], case_name
    assert llm.calls == 1, case_name


def test_propose_uri_validating_a_result_message_is_a_semantic_rejection() -> None:
    phase_facts = {"implied": "accept-reject", "declared": "propose"}
    phase_error = SlotValidationError(
        "section.info_result_content",
        ErrorCatalog.NEGOTIATION_PHASE_MISMATCH.value,
        render(ErrorCatalog.NEGOTIATION_PHASE_MISMATCH, phase_facts, ZH_CN),
        phase_facts,
    )
    llm = ScriptedLlmClient(
        '{"semantic_verdict":false,"negotiation_type":"information","errors":'
        '[{"slot_name":"section.info_result_content","code":"negotiation.phase_mismatch",'
        '"facts":{"implied":"accept-reject","declared":"propose"}}],"params":{}}'
    )
    instance = orchestrator(llm)
    reject_message = information_ending_message(instance, NegotiationConclusion.REJECT)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        instance.validate_propose_prompt_and_data_filling(
            reject_message, CONTEXT, ACCESS_PORT_SCHEMA, INFORMATION_NEGOTIATION_PROPOSE
        )

    assert excinfo.value.code_str == "negotiation.semantic_rejected"
    assert list(excinfo.value.errors) == [phase_error]
    assert llm.calls == 1


# --------------------------------------------------------------------------------------
# retryable infrastructure failures and resource misses
# --------------------------------------------------------------------------------------


def test_missing_negotiation_type_key_is_retried_then_fails_as_an_infrastructure_error() -> None:
    llm = ScriptedLlmClient('{"semantic_verdict":true,"errors":[],"params":{}}')
    instance = orchestrator(llm, max_attempts=3)
    message = information_propose_message(instance)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        instance.validate_propose_prompt_and_data_filling(
            message, CONTEXT, ACCESS_PORT_SCHEMA, INFORMATION_NEGOTIATION_PROPOSE
        )

    assert excinfo.value.code_str == "llm.response_invalid"
    assert llm.calls == 3, "a shape-invalid response is a retryable infrastructure failure"
    assert len(excinfo.value.errors) == 1
    assert excinfo.value.errors[0].slot_name == "_llm"
    assert excinfo.value.facts == {"step": "semantic_validation"}
    assert "negotiation_type" in str(excinfo.value.__cause__.__cause__)


def test_semantic_llm_failure_is_retried_and_exhausts_with_the_llm_pseudo_slot() -> None:
    llm = ScriptedLlmClient("unused")
    llm.failure = LLMRuntimeError("LLM endpoint unavailable")
    instance = orchestrator(llm, max_attempts=2)
    message = information_propose_message(instance)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        instance.validate_propose_prompt_and_data_filling(
            message, CONTEXT, ACCESS_PORT_SCHEMA, INFORMATION_NEGOTIATION_PROPOSE
        )

    assert excinfo.value.code_str == "llm.invocation_failed"
    assert llm.calls == 2
    assert len(excinfo.value.errors) == 1
    assert excinfo.value.errors[0].slot_name == "_llm"
    assert "endpoint unavailable" in str(excinfo.value)


def test_missing_prompt_resources_close_as_template_not_found_without_bubbling_the_resource_exception() -> None:
    from a2a_t.core.errors.exceptions import A2ATError, ResourceNotFoundError

    class ThrowingSemanticValidator:
        def validate_negotiation(self, prompt, caller_schema, reference, template_content):
            raise ResourceNotFoundError(
                "Negotiation semantic validation prompt resource does not exist.",
                "prompt_resources/prompts/negotiation_semantic_validation/zh-CN/system.md",
            )

        def validate(self, prompt, schema, reference, template_content):
            raise ResourceNotFoundError(
                "Negotiation semantic validation prompt resource does not exist.",
                "prompt_resources/prompts/negotiation_semantic_validation/zh-CN/system.md",
            )

    llm = ScriptedLlmClient("unused")
    instance = NegotiationGenerationOrchestratorBuilder(
        language=ZH_CN, llm_client=llm, semantic_validator=ThrowingSemanticValidator()
    ).build()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        instance.validate_propose_prompt_and_data_filling(
            "## 所需信息项\n1. 接入端口名称\n",
            NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE),
            ACCESS_PORT_SCHEMA,
            INFORMATION_NEGOTIATION_PROPOSE,
        )

    assert excinfo.value.code_str == "template.not_found"
    assert type(excinfo.value) is NegotiationParamExtractionError, (
        "the raw resource exception must not bubble out of the pipeline"
    )
    assert isinstance(excinfo.value, A2ATError), "the mapped failure stays catchable through the A2ATError root"
    assert llm.calls == 0


# --------------------------------------------------------------------------------------
# non-negotiation messages
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case_name", "task_t_message"),
    [
        (
            "chinese task prompt",
            "## 任务类型(Task Type)\n传输专线业务投诉诊断\n\n## 任务对象(Task Object)\n接入端口名称：P533-珠江旧城"
            "-PTN3900-23-TPA1EG24-1\n\n## 任务目标(Task Target)\n对网络侧故障进行诊断，返回故障根因和修复建议等诊断结果"
            "信息。\n",
        ),
        (
            "english task prompt",
            "## Task Type\nTransport private line service complaint diagnosis\n\n## Task Object\nAccess Port Name:"
            " P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1\n\n## Task Target\nDiagnose network-side faults and"
            " return diagnostic result information.\n",
        ),
    ],
    ids=["chinese-task-prompt", "english-task-prompt"],
)
def test_task_t_messages_are_rejected_as_non_negotiation_input_with_a_rendered_message(
    case_name: str, task_t_message: str
) -> None:
    llm = ScriptedLlmClient('{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":{}}')
    instance = orchestrator(llm)

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        instance.validate_propose_prompt_and_data_filling(
            task_t_message, None, ACCESS_PORT_SCHEMA, INFORMATION_NEGOTIATION_PROPOSE
        )

    assert excinfo.value.code_str == "negotiation.invalid_input", case_name
    assert str(excinfo.value) == "输入的协商内容无效:缺少协商上下文(该报文不是协商报文)", case_name
    assert excinfo.value.errors == [], case_name
    assert llm.calls == 0, case_name
