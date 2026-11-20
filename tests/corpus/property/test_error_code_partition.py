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

"""Error-code partition property layer (port of Java ``ErrorCodePartitionPropertyTest``, §8.3).

The public error-code universe partitions into exactly the content-layer
:class:`~a2a_t.core.errors.catalog.ErrorCatalog` codes, and the retryable partition is exactly the
three retryable LLM-step codes. Every trigger runs with the attempt limit fixed at
``MAX_ATTEMPTS``, so the retryable partition has an operational definition observable from the
outside: a failure code is retryable if and only if the scripted LLM client was consumed to the
attempt limit. Pre-LLM failures (0 calls) and non-retryable in-step failures (1 call) never reach
the limit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import (
    NegotiationGenerationError,
    NegotiationParamExtractionError,
)
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from tests.corpus.llm_stub import ScriptedNegotiationLlmClient
from tests.corpus.models import LlmFailMarker
from tests.corpus.property.arbitraries import contexts, languages
from tests.corpus.property.harness import (
    MAX_ATTEMPTS,
    failing,
    object_schema,
    scripted,
    service,
    service_with_failing_template_loader,
    template_uri,
)

#: The complete public error-code set of the content layer (ErrorCatalog codes).
CONTENT_LAYER_ERROR_CODES: frozenset[ErrorCatalog] = frozenset(
    {
        ErrorCatalog.TEMPLATE_NOT_FOUND,
        ErrorCatalog.NEGOTIATION_CONTENT_EXTRACT_FAILED,
        ErrorCatalog.NEGOTIATION_CONCLUSION_MISMATCH,
        ErrorCatalog.NEGOTIATION_CONTENT_INVALID,
        ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED,
        ErrorCatalog.NEGOTIATION_RULE_VIOLATION,
        ErrorCatalog.NEGOTIATION_FIELD_MISSING,
        ErrorCatalog.NEGOTIATION_INVALID_INPUT,
        ErrorCatalog.LLM_INVOCATION_FAILED,
        ErrorCatalog.LLM_RESPONSE_INVALID,
    }
)

#: The retryable partition: exactly the three retryable LLM-step codes of the content layer.
RETRYABLE_ERROR_CODES: frozenset[ErrorCatalog] = frozenset(
    {
        ErrorCatalog.NEGOTIATION_CONTENT_EXTRACT_FAILED,
        ErrorCatalog.LLM_INVOCATION_FAILED,
        ErrorCatalog.LLM_RESPONSE_INVALID,
    }
)

INFORMATION_PROPOSE_URI = "Negotiation-T/information-negotiation/propose/v1"
TARGET_ACCEPT_REJECT_URI = "Negotiation-T/target-negotiation/accept-reject/v1"


@dataclass(frozen=True, slots=True)
class FailureOutcome:
    """The normalized outcome of one triggered failure: its public error code and LLM call count."""

    code: ErrorCatalog
    llm_calls: int


class FailureTrigger(Enum):
    """One deterministic trigger of one public failure code (the Java enum counterpart)."""

    BLANK_TEXT = "blank-text"
    CONCLUSION_MISMATCH = "conclusion-mismatch"
    MISSING_REQUIRED_FIELD = "missing-required-field"
    ROUND_ABOVE_BUDGET = "round-above-budget"
    SEMANTIC_REJECTED = "semantic-rejected"
    RESPONSE_INVALID = "response-invalid"
    LLM_INFRASTRUCTURE_ERROR = "llm-infrastructure-error"
    TEMPLATE_NOT_FOUND = "template-not-found"

    @property
    def expected_code(self) -> ErrorCatalog:
        """The one public error code this trigger deterministically produces."""
        return _EXPECTED_CODES[self]

    def run(self, language: str, context: NegotiationContext) -> FailureOutcome:
        """Trigger the failure and return its normalized outcome.

        Args:
            language: message language of the run.
            context: negotiation context of the run.

        Returns:
            the failure's public error code and its exact LLM call count.
        """
        return _RUNNERS[self](language, context)


#: The public code each trigger deterministically produces.
_EXPECTED_CODES: dict[FailureTrigger, ErrorCatalog] = {
    FailureTrigger.BLANK_TEXT: ErrorCatalog.NEGOTIATION_INVALID_INPUT,
    FailureTrigger.CONCLUSION_MISMATCH: ErrorCatalog.NEGOTIATION_CONCLUSION_MISMATCH,
    FailureTrigger.MISSING_REQUIRED_FIELD: ErrorCatalog.NEGOTIATION_FIELD_MISSING,
    FailureTrigger.ROUND_ABOVE_BUDGET: ErrorCatalog.NEGOTIATION_RULE_VIOLATION,
    FailureTrigger.SEMANTIC_REJECTED: ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED,
    FailureTrigger.RESPONSE_INVALID: ErrorCatalog.LLM_RESPONSE_INVALID,
    FailureTrigger.LLM_INFRASTRUCTURE_ERROR: ErrorCatalog.LLM_INVOCATION_FAILED,
    FailureTrigger.TEMPLATE_NOT_FOUND: ErrorCatalog.TEMPLATE_NOT_FOUND,
}


def _run_blank_text(language: str, context: NegotiationContext) -> FailureOutcome:
    """A blank free-text input fails before the LLM step with ``negotiation.invalid_input``."""
    llm = ScriptedNegotiationLlmClient.assertion_only()
    wired = service(language, llm)
    with pytest.raises(NegotiationGenerationError) as excinfo:
        wired.generate_propose_from_text("   ", context, template_uri(INFORMATION_PROPOSE_URI))
    return FailureOutcome(excinfo.value.code, llm.call_count)


def _run_conclusion_mismatch(language: str, context: NegotiationContext) -> FailureOutcome:
    """An accept call receiving a Reject payload fails with ``negotiation.conclusion_mismatch``."""
    llm = scripted('{"conclusion":"Reject","confirmed_intent":null,"failure_reason":"no agreement"}')
    wired = service(language, llm)
    with pytest.raises(NegotiationGenerationError) as excinfo:
        wired.generate_accept_from_text(
            "I must refuse the current offer.", context, template_uri(TARGET_ACCEPT_REJECT_URI)
        )
    return FailureOutcome(excinfo.value.code, llm.call_count)


def _run_missing_required_field(language: str, context: NegotiationContext) -> FailureOutcome:
    """A payload missing every required slot fails with ``negotiation.field_missing``."""
    llm = scripted('{"relationship":null}')
    wired = service(language, llm)
    with pytest.raises(NegotiationGenerationError) as excinfo:
        wired.generate_propose_from_text(
            "Please provide the missing information.", context, template_uri(INFORMATION_PROPOSE_URI)
        )
    return FailureOutcome(excinfo.value.code, llm.call_count)


def _run_round_above_budget(language: str, context: NegotiationContext) -> FailureOutcome:
    """An over-budget incoming context fails the rule gate with ``negotiation.rule_violation``."""
    # The over-budget context is the incoming context of the propose message being validated.
    over_budget = NegotiationContext(
        context.id, context.max_rounds + 1, context.max_rounds, NegotiationPerformative.PROPOSE
    )
    llm = ScriptedNegotiationLlmClient.assertion_only()
    wired = service(language, llm)
    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        wired.validate_propose_prompt_and_data_filling(
            "Rendered negotiation message text.",
            over_budget,
            object_schema({}),
            template_uri(INFORMATION_PROPOSE_URI),
        )
    return FailureOutcome(excinfo.value.code, llm.call_count)


def _run_semantic_rejected(language: str, context: NegotiationContext) -> FailureOutcome:
    """A rejecting semantic verdict fails with ``negotiation.semantic_rejected``."""
    llm = scripted(
        '{"semantic_verdict":false,"negotiation_type":"information",'
        '"errors":[{"slot_name":"region","code":"negotiation.field_missing",'
        '"facts":{"field":"区域"}}],"params":{}}'
    )
    wired = service(language, llm)
    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        wired.validate_propose_prompt_and_data_filling(
            "Rendered negotiation message text.",
            context,
            object_schema({}),
            template_uri(INFORMATION_PROPOSE_URI),
        )
    return FailureOutcome(excinfo.value.code, llm.call_count)


def _run_response_invalid(language: str, context: NegotiationContext) -> FailureOutcome:
    """A non-JSON LLM answer on every attempt fails with ``llm.response_invalid`` after retries."""
    llm = failing(LlmFailMarker.NON_JSON)
    wired = service(language, llm)
    with pytest.raises(NegotiationGenerationError) as excinfo:
        wired.generate_propose_from_text(
            "Please provide the missing information.", context, template_uri(INFORMATION_PROPOSE_URI)
        )
    return FailureOutcome(excinfo.value.code, llm.call_count)


def _run_llm_infrastructure_error(language: str, context: NegotiationContext) -> FailureOutcome:
    """A transport failure on every attempt fails with ``llm.invocation_failed`` after retries."""
    llm = failing(LlmFailMarker.RUNTIME_EXCEPTION)
    wired = service(language, llm)
    with pytest.raises(NegotiationGenerationError) as excinfo:
        wired.generate_propose_from_text(
            "Please provide the missing information.", context, template_uri(INFORMATION_PROPOSE_URI)
        )
    return FailureOutcome(excinfo.value.code, llm.call_count)


def _run_template_not_found(language: str, context: NegotiationContext) -> FailureOutcome:
    """A template miss fails with ``template.not_found`` before any LLM call."""
    llm = ScriptedNegotiationLlmClient.assertion_only()
    wired = service_with_failing_template_loader(language, llm)
    with pytest.raises(NegotiationGenerationError) as excinfo:
        wired.generate_propose_from_text(
            "Please provide the missing information.", context, template_uri(INFORMATION_PROPOSE_URI)
        )
    return FailureOutcome(excinfo.value.code, llm.call_count)


#: The trigger runner registry, one deterministic runner per public failure code.
_RUNNERS: dict[FailureTrigger, Callable[[str, NegotiationContext], FailureOutcome]] = {
    FailureTrigger.BLANK_TEXT: _run_blank_text,
    FailureTrigger.CONCLUSION_MISMATCH: _run_conclusion_mismatch,
    FailureTrigger.MISSING_REQUIRED_FIELD: _run_missing_required_field,
    FailureTrigger.ROUND_ABOVE_BUDGET: _run_round_above_budget,
    FailureTrigger.SEMANTIC_REJECTED: _run_semantic_rejected,
    FailureTrigger.RESPONSE_INVALID: _run_response_invalid,
    FailureTrigger.LLM_INFRASTRUCTURE_ERROR: _run_llm_infrastructure_error,
    FailureTrigger.TEMPLATE_NOT_FOUND: _run_template_not_found,
}


@settings(max_examples=100)
@given(language=languages(), context=contexts(), trigger=st.sampled_from(tuple(FailureTrigger)))
def test_every_failure_carries_a_code_from_the_content_layer_code_set(
    language: str,
    context: NegotiationContext,
    trigger: FailureTrigger,
) -> None:
    """Every triggered failure carries its expected content-layer code and the right retry shape.

    A failure is retried to the attempt limit if and only if its code is retryable — the
    operational definition of the retryable partition.
    """
    outcome = trigger.run(language, context)
    assert outcome.code in CONTENT_LAYER_ERROR_CODES, (
        f"code '{outcome.code.value}' is outside the content-layer error codes"
    )
    assert outcome.code == trigger.expected_code
    assert (outcome.code in RETRYABLE_ERROR_CODES) == (outcome.llm_calls == MAX_ATTEMPTS), (
        f"a failure is retried to the attempt limit if and only if its code is retryable, but "
        f"code {outcome.code.value} made {outcome.llm_calls} LLM call(s)"
    )
