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

"""Error-code usage matrix of the negotiation validation codes (port of the validation rows of
Java ``NegotiationErrorCodeUsageMatrixTest``).

Each row drives one failure condition of the documented matrix through the real builder-wired
pipeline and asserts the exception type, the public error code, the number of LLM calls (encoding
retryability: a retryable failure consumes all attempts, a non-retryable failure exactly one or
zero), and that a structured detail identifying the problem field or slot is carried.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from a2a_t.core.errors.exceptions import A2ATError, NegotiationParamExtractionError
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.models import LLMResponse
from a2a_t.negotiation.generation.builder import NegotiationGenerationOrchestratorBuilder
from a2a_t.negotiation.generation.content_service import NegotiationContentService

UUID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

INFORMATION_PROPOSE_URI = "Negotiation-T/information-negotiation/propose/v1"

VALID_CONTEXT_PROMPT = "## 协商上下文\n- id: " + UUID + "\n- round: 1\n- maxRounds: 5"

SCHEMA: dict[str, Any] = {"type": "object"}

CONTEXT = NegotiationContext(UUID, 1, 5, NegotiationPerformative.PROPOSE)

#: Codes whose failure must carry non-empty structured slot details (Java ``verify``).
CODES_EXPECTING_SLOT_DETAILS = frozenset(
    {
        "negotiation.rule_violation",
        "negotiation.semantic_rejected",
        "llm.invocation_failed",
        "llm.response_invalid",
    }
)


class CountingClient:
    """LLM client counting every structured call; a ``None`` payload fails every call."""

    def __init__(self, payload: str | None) -> None:
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


def semantic_payload(negotiation_type: str) -> str:
    """Build one well-formed four-key semantic validation payload."""
    return (
        '{"semantic_verdict":true,"negotiation_type":"' + negotiation_type + '","errors":[],'
        '"params":{"region":"松山湖"}}'
    )


def throwing_semantic_validator() -> Any:
    """Build a semantic validator whose every call misses the prompt resources."""

    class ThrowingSemanticValidator:
        def validate_negotiation(self, prompt, caller_schema, reference, template_content):
            raise _resource_not_found()

        def validate(self, prompt, schema, reference, template_content):
            raise _resource_not_found()

    def _resource_not_found() -> Any:
        from a2a_t.core.errors.exceptions import ResourceNotFoundError

        return ResourceNotFoundError("Semantic validation prompt does not exist.", "prompt_resources/prompts")

    return ThrowingSemanticValidator()


def propose_service(llm: CountingClient, **overrides: Any) -> NegotiationContentService:
    """Build the service under test for the propose validation."""
    return NegotiationContentService(
        NegotiationGenerationOrchestratorBuilder(language="zh-CN", llm_client=llm, **overrides).build()
    )


VALIDATION_ROWS: list[dict[str, Any]] = [
    {
        "name": "param_non_negotiation_input",
        "payload": semantic_payload("information"),
        "expected_llm_calls": 0,
        "overrides": {},
        "action": lambda service: service.validate_propose_prompt_and_data_filling(
            "plain text without any negotiation section", None, SCHEMA, INFORMATION_PROPOSE_URI
        ),
        "expected_code": "negotiation.invalid_input",
    },
    {
        "name": "param_rule_violation",
        "payload": semantic_payload("information"),
        "expected_llm_calls": 0,
        "overrides": {},
        "action": lambda service: service.validate_propose_prompt_and_data_filling(
            "## 所需信息项\n1. 区域\n",
            NegotiationContext(UUID, 9, 5, NegotiationPerformative.PROPOSE),
            SCHEMA,
            INFORMATION_PROPOSE_URI,
        ),
        "expected_code": "negotiation.rule_violation",
    },
    {
        "name": "param_semantic_rejection",
        "payload": (
            '{"semantic_verdict":false,"negotiation_type":null,"errors":[{"slot_name":"section.info_static",'
            '"code":"negotiation.type_mismatch","facts":{"implied":"information","declared":"information"}}],'
            '"params":{}}'
        ),
        "expected_llm_calls": 1,
        "overrides": {"max_attempts": 3},
        "action": lambda service: service.validate_propose_prompt_and_data_filling(
            VALID_CONTEXT_PROMPT, CONTEXT, SCHEMA, INFORMATION_PROPOSE_URI
        ),
        "expected_code": "negotiation.semantic_rejected",
    },
    {
        "name": "param_llm_infrastructure_failure_is_retryable",
        "payload": None,
        "expected_llm_calls": 2,
        "overrides": {"max_attempts": 2},
        "action": lambda service: service.validate_propose_prompt_and_data_filling(
            VALID_CONTEXT_PROMPT, CONTEXT, SCHEMA, INFORMATION_PROPOSE_URI
        ),
        "expected_code": "llm.invocation_failed",
    },
    {
        "name": "internal_semantic_shape_violation_is_retryable_infrastructure",
        "payload": '{"semantic_verdict":true,"errors":[],"params":{}}',
        "expected_llm_calls": 2,
        "overrides": {"max_attempts": 2},
        "action": lambda service: service.validate_propose_prompt_and_data_filling(
            VALID_CONTEXT_PROMPT, CONTEXT, SCHEMA, INFORMATION_PROPOSE_URI
        ),
        "expected_code": "llm.response_invalid",
    },
    {
        "name": "param_prompt_resource_missing_is_template_not_found",
        "payload": semantic_payload("information"),
        "expected_llm_calls": 0,
        "overrides": {"semantic_validator": throwing_semantic_validator()},
        "action": lambda service: service.validate_propose_prompt_and_data_filling(
            VALID_CONTEXT_PROMPT, CONTEXT, SCHEMA, INFORMATION_PROPOSE_URI
        ),
        "expected_code": "template.not_found",
    },
]


@pytest.mark.parametrize("row", VALIDATION_ROWS, ids=[row["name"] for row in VALIDATION_ROWS])
def test_every_error_code_row_of_the_matrix_behaves_as_pinned(row: dict[str, Any]) -> None:
    llm = CountingClient(row["payload"])
    service = propose_service(llm, **row["overrides"])
    action: Callable[[NegotiationContentService], FilledParamData] = row["action"]

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        action(service)

    failure = excinfo.value
    assert isinstance(failure, A2ATError)
    assert failure.code_str == row["expected_code"]
    assert llm.calls == row["expected_llm_calls"]
    assert str(failure).strip() != ""

    if row["expected_code"] in CODES_EXPECTING_SLOT_DETAILS:
        assert failure.errors, "row must carry non-empty structured error details"
        assert all(error.slot_name for error in failure.errors), "error details must carry slot names"
