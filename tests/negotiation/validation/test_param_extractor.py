"""Tests of the parameter extractor (port of Java ``ParamExtractorTest``).

Every collaborator is stubbed — zero LLM calls. The suite pins the four-stage pipeline order (input
gate -> rule gate -> template loading -> semantic gate -> merge), the merge precedence (context
parameters first, context wins on conflict) and the mapping of every pipeline failure to the
negotiation parameter-extraction error type with its final catalog code.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

import pytest

from a2a_t.core.errors.exceptions import (
    ContentValidationError,
    NegotiationParamExtractionError,
    ResourceNotFoundError,
    SlotValidationError,
)
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.core.validation_pipeline import FilledParamData, ValidationResult
from a2a_t.negotiation.content.enums import NegotiationType
from a2a_t.negotiation.generation.orchestrator import NegotiationParamExtractor as NegotiationParamExtractorProtocol
from a2a_t.negotiation.resources.reference import NegotiationReference
from a2a_t.negotiation.validation.compliance_checker import NegotiationRuleCheckResult
from a2a_t.negotiation.validation.param_extractor import ParamExtractor
from a2a_t.negotiation.validation.semantic_validator import (
    NegotiationSemanticValidator,
    NegotiationValidationError,
    SemanticValidationResult,
)

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

CONTEXT = NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.PROPOSE)

VALID_ZH_PROMPT = "## 所需信息项\n1. 节能区域信息：请提供真实存在的区域\n"

REFERENCE = NegotiationReference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, "zh-CN")

MAX_ATTEMPTS = 1

TEMPLATE_CONTENT = "dummy template content"


class StubComplianceChecker:
    """Compliance checker stub returning one scripted rule-check outcome."""

    def __init__(self, result: NegotiationRuleCheckResult | None = None) -> None:
        self.result = NegotiationRuleCheckResult(False, ()) if result is None else result
        self.invocations = 0

    def check(self, context: NegotiationContext) -> NegotiationRuleCheckResult:
        self.invocations += 1
        return self.result


class StubSemanticValidator(NegotiationSemanticValidator):
    """Semantic validator stub returning one scripted outcome or raising one scripted failure.

    Subclassing the protocol inherits the core-contract ``validate`` adapter, mirroring the Java stub
    that implements ``validateNegotiation`` and inherits the interface default method.
    """

    def __init__(self, result: SemanticValidationResult | None = None) -> None:
        self.result = SemanticValidationResult(False, None, (), {}) if result is None else result
        self.failure: BaseException | None = None
        self.invocations = 0
        self.last_template_content: str | None = None

    def validate_negotiation(
        self,
        prompt: str,
        caller_schema: dict[str, Any],
        reference: NegotiationReference,
        template_content: str,
    ) -> SemanticValidationResult:
        self.invocations += 1
        self.last_template_content = template_content
        if self.failure is not None:
            raise self.failure
        return self.result


class StubTemplateContentLoader:
    """Template content loader stub returning one scripted body or raising one scripted failure."""

    def __init__(self, content: str = TEMPLATE_CONTENT) -> None:
        self.content = content
        self.failure: BaseException | None = None
        self.invocations = 0
        self.last_reference: NegotiationReference | None = None

    def load(self, reference: NegotiationReference) -> str:
        self.invocations += 1
        self.last_reference = reference
        if self.failure is not None:
            raise self.failure
        return self.content


def extractor(
    compliance_checker: StubComplianceChecker | None = None,
    semantic_validator: StubSemanticValidator | None = None,
    template_content_loader: StubTemplateContentLoader | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> ParamExtractor:
    """Build the extractor under test over the given stubs."""
    return ParamExtractor(
        StubComplianceChecker() if compliance_checker is None else compliance_checker,
        StubSemanticValidator() if semantic_validator is None else semantic_validator,
        max_attempts,
        StubTemplateContentLoader() if template_content_loader is None else template_content_loader,
    )


def passing_checker() -> StubComplianceChecker:
    """Build a compliance checker stub whose rule check always passes."""
    return StubComplianceChecker(NegotiationRuleCheckResult(True, ()))


# --------------------------------------------------------------------------------------
# happy paths: merge precedence and outcome
# --------------------------------------------------------------------------------------


def test_happy_path_merges_context_params_first_and_lets_context_win_on_conflict() -> None:
    compliance_checker = passing_checker()
    semantic_validator = StubSemanticValidator(
        SemanticValidationResult(
            True, "information", (), {"id": "llm-value", "confirmed_rate_mbps": 2, "nested": {"a": 1}}
        )
    )
    template_content_loader = StubTemplateContentLoader()

    filled = extractor(compliance_checker, semantic_validator, template_content_loader).extract(
        VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE
    )

    assert isinstance(filled, FilledParamData)
    assert filled.data["id"] == SESSION_ID
    assert filled.data["round"] == 2
    assert filled.data["maxRounds"] == 5
    assert filled.data["confirmed_rate_mbps"] == 2
    assert filled.data["nested"] == {"a": 1}
    assert len(filled.data) == 5
    assert semantic_validator.invocations == 1
    assert template_content_loader.invocations == 1
    assert semantic_validator.last_template_content == TEMPLATE_CONTENT


def test_extract_requires_integer_round_and_max_rounds_in_merged_data() -> None:
    semantic_validator = StubSemanticValidator(SemanticValidationResult(True, "information", (), {}))

    filled = extractor(compliance_checker=passing_checker(), semantic_validator=semantic_validator).extract(
        VALID_ZH_PROMPT, NegotiationContext(SESSION_ID, 3, 7, NegotiationPerformative.PROPOSE), {}, REFERENCE
    )

    assert isinstance(filled.data["round"], int)
    assert isinstance(filled.data["maxRounds"], int)
    assert filled.data["round"] == 3
    assert filled.data["maxRounds"] == 7


def test_extract_returns_the_validator_outcome() -> None:
    semantic_validator = StubSemanticValidator(
        SemanticValidationResult(True, "information", (), {"confirmed_rate_mbps": 2})
    )

    filled = extractor(compliance_checker=passing_checker(), semantic_validator=semantic_validator).extract(
        VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE
    )

    assert semantic_validator.invocations == 1
    assert filled.data["confirmed_rate_mbps"] == 2


def test_param_merge_conflict_logs_content_param_merge_conflict_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    semantic_validator = StubSemanticValidator(SemanticValidationResult(True, "information", (), {"id": "semantic-id"}))

    with caplog.at_level(logging.WARNING, logger="a2a_t.core.validation_pipeline"):
        extractor(compliance_checker=passing_checker(), semantic_validator=semantic_validator).extract(
            VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE
        )

    merge_conflicts = [record for record in caplog.records if record.message.startswith("content_param_merge_conflict")]
    assert len(merge_conflicts) == 1
    assert merge_conflicts[0].levelname == "WARNING"
    assert "key=id" in merge_conflicts[0].message


# --------------------------------------------------------------------------------------
# gate failures: each stage runs only after the previous one passes
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule_errors", "expected_slot"),
    [
        ((SlotValidationError("round", "negotiation.round_exceeded", "round exceeds maxRounds"),), "round"),
        ((SlotValidationError("id", "negotiation.invalid_context_id", "not a uuid"),), "id"),
    ],
    ids=["round-rule", "id-rule"],
)
def test_rule_failure_skips_template_loading_and_the_semantic_validation_call(
    rule_errors: tuple[SlotValidationError, ...], expected_slot: str
) -> None:
    compliance_checker = StubComplianceChecker(NegotiationRuleCheckResult(False, rule_errors))
    semantic_validator = StubSemanticValidator()
    template_content_loader = StubTemplateContentLoader()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        extractor(compliance_checker, semantic_validator, template_content_loader).extract(
            VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE
        )

    assert excinfo.value.code_str == "negotiation.rule_violation"
    assert len(excinfo.value.errors) == 1
    assert excinfo.value.errors[0].slot_name == expected_slot
    assert semantic_validator.invocations == 0
    assert template_content_loader.invocations == 0


def test_null_context_fails_as_non_negotiation_input_with_a_rendered_message() -> None:
    compliance_checker = StubComplianceChecker(NegotiationRuleCheckResult(False, ()))
    semantic_validator = StubSemanticValidator()
    template_content_loader = StubTemplateContentLoader()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        extractor(compliance_checker, semantic_validator, template_content_loader).extract(
            "## 任务目标\n诊断\n", None, {}, REFERENCE
        )

    assert excinfo.value.code_str == "negotiation.invalid_input"
    assert str(excinfo.value) == "输入的协商内容无效:缺少协商上下文(该报文不是协商报文)"
    assert excinfo.value.errors == []
    assert semantic_validator.invocations == 0
    assert compliance_checker.invocations == 0
    assert template_content_loader.invocations == 0


def test_blank_prompt_fails_as_invalid_input_without_touching_the_rule_gate() -> None:
    compliance_checker = StubComplianceChecker(NegotiationRuleCheckResult(True, ()))
    semantic_validator = StubSemanticValidator()
    template_content_loader = StubTemplateContentLoader()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        extractor(compliance_checker, semantic_validator, template_content_loader).extract(
            "   ", CONTEXT, {}, REFERENCE
        )

    assert excinfo.value.code_str == "negotiation.invalid_input"
    assert compliance_checker.invocations == 0
    assert template_content_loader.invocations == 0
    assert semantic_validator.invocations == 0


def test_null_schema_fails_as_invalid_input_without_touching_the_rule_gate() -> None:
    compliance_checker = StubComplianceChecker(NegotiationRuleCheckResult(True, ()))
    semantic_validator = StubSemanticValidator()

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        extractor(compliance_checker=passing_checker(), semantic_validator=semantic_validator).extract(
            VALID_ZH_PROMPT,
            CONTEXT,
            None,
            REFERENCE,  # type: ignore[arg-type]
        )

    assert excinfo.value.code_str == "negotiation.invalid_input"
    assert excinfo.value.errors[0].slot_name == "_input"
    assert compliance_checker.invocations == 0
    assert semantic_validator.invocations == 0


def test_template_load_failure_is_mapped_to_template_not_found_without_touching_the_validator() -> None:
    semantic_validator = StubSemanticValidator(SemanticValidationResult(True, "information", (), {}))
    template_content_loader = StubTemplateContentLoader()
    template_content_loader.failure = ResourceNotFoundError(
        "template missing", "templates/Negotiation-T/information-negotiation"
    )

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        extractor(
            compliance_checker=passing_checker(),
            semantic_validator=semantic_validator,
            template_content_loader=template_content_loader,
        ).extract(VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE)

    assert excinfo.value.code_str == "template.not_found"
    assert excinfo.value.errors == []
    assert template_content_loader.invocations == 1
    assert template_content_loader.last_reference == REFERENCE
    assert semantic_validator.invocations == 0


# --------------------------------------------------------------------------------------
# semantic gate failures
# --------------------------------------------------------------------------------------


def test_semantic_rejection_passes_errors_through() -> None:
    semantic_errors = (
        SlotValidationError("section.target_result_content", "negotiation.conclusion_content_mismatch", "Mismatch"),
        SlotValidationError("section.context", "negotiation.conclusion_mismatch", "Abort is reserved"),
    )
    semantic_validator = StubSemanticValidator(SemanticValidationResult(False, "target", semantic_errors, {}))

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        extractor(compliance_checker=passing_checker(), semantic_validator=semantic_validator).extract(
            VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE
        )

    assert excinfo.value.code_str == "negotiation.semantic_rejected"
    assert tuple(excinfo.value.errors) == semantic_errors


def test_internal_validation_failure_is_mapped_to_retryable_infrastructure_error() -> None:
    semantic_validator = StubSemanticValidator()
    semantic_validator.failure = NegotiationValidationError("response is missing negotiation_type")

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        extractor(compliance_checker=passing_checker(), semantic_validator=semantic_validator).extract(
            VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE
        )

    assert excinfo.value.code_str == "llm.response_invalid"
    assert len(excinfo.value.errors) == 1
    assert excinfo.value.errors[0].slot_name == "_llm"
    assert excinfo.value.errors[0].facts == {"step": "semantic_validation"}
    internal_cause = excinfo.value.__cause__
    assert isinstance(internal_cause, ContentValidationError)
    assert "negotiation_type" in str(internal_cause.__cause__)


def test_prompt_resource_miss_is_mapped_to_template_not_found() -> None:
    semantic_validator = StubSemanticValidator()
    semantic_validator.failure = ResourceNotFoundError("prompt resource missing", "prompt_resources/prompts/x")

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        extractor(compliance_checker=passing_checker(), semantic_validator=semantic_validator).extract(
            VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE
        )

    assert excinfo.value.code_str == "template.not_found"
    assert excinfo.value.errors == []


def test_transport_failure_is_mapped_to_llm_invocation_failed() -> None:
    from a2a_t.llm.errors import LLMRuntimeError

    semantic_validator = StubSemanticValidator()
    semantic_validator.failure = LLMRuntimeError("LLM endpoint unavailable.")

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        extractor(compliance_checker=passing_checker(), semantic_validator=semantic_validator).extract(
            VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE
        )

    assert excinfo.value.code_str == "llm.invocation_failed"
    assert excinfo.value.errors[0].slot_name == "_llm"
    assert excinfo.value.facts == {"provider": "LLMRuntimeError", "reason": "LLM endpoint unavailable."}


@pytest.mark.parametrize("max_attempts", [1, 2, 3], ids=["one-attempt", "two-attempts", "three-attempts"])
def test_response_contract_failures_are_retried_to_the_attempt_limit(max_attempts: int) -> None:
    semantic_validator = StubSemanticValidator()
    semantic_validator.failure = NegotiationValidationError("response is missing negotiation_type")

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        extractor(
            compliance_checker=passing_checker(),
            semantic_validator=semantic_validator,
            max_attempts=max_attempts,
        ).extract(VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE)

    assert excinfo.value.code_str == "llm.response_invalid"
    assert semantic_validator.invocations == max_attempts


def test_missing_llm_configuration_is_not_retried() -> None:
    from a2a_t.llm.errors import LLMConfigError

    semantic_validator = StubSemanticValidator()
    semantic_validator.failure = LLMConfigError("Semantic validation requires an LLM client but none is configured.")

    with pytest.raises(NegotiationParamExtractionError) as excinfo:
        extractor(
            compliance_checker=passing_checker(),
            semantic_validator=semantic_validator,
            max_attempts=3,
        ).extract(VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE)

    assert excinfo.value.code_str == "llm.not_configured"
    assert semantic_validator.invocations == 1


# --------------------------------------------------------------------------------------
# the pinned stage order
# --------------------------------------------------------------------------------------


def test_template_is_loaded_between_the_rule_gate_and_semantic_validation() -> None:
    order: list[str] = []

    class OrderingChecker(StubComplianceChecker):
        def check(self, context: NegotiationContext) -> NegotiationRuleCheckResult:
            order.append("rule")
            return NegotiationRuleCheckResult(True, ())

    class OrderingValidator(StubSemanticValidator):
        def validate_negotiation(
            self,
            prompt: str,
            caller_schema: dict[str, Any],
            reference: NegotiationReference,
            template_content: str,
        ) -> SemanticValidationResult:
            order.append("semantic")
            return SemanticValidationResult(True, "information", (), {})

    class OrderingLoader(StubTemplateContentLoader):
        def load(self, reference: NegotiationReference) -> str:
            order.append("load")
            return TEMPLATE_CONTENT

    extractor(OrderingChecker(), OrderingValidator(), OrderingLoader()).extract(VALID_ZH_PROMPT, CONTEXT, {}, REFERENCE)

    assert order == ["rule", "load", "semantic"]


def test_the_core_result_shape_drives_the_pipeline() -> None:
    """The stub validator's inherited adapter returns the core result the pipeline consumes."""
    semantic_validator = StubSemanticValidator(SemanticValidationResult(True, "information", (), {"k": 1}))

    result = semantic_validator.validate(VALID_ZH_PROMPT, {}, REFERENCE, TEMPLATE_CONTENT)

    assert isinstance(result, ValidationResult)
    assert result.verdict is True
    assert result.params == {"k": 1}


# --------------------------------------------------------------------------------------
# array-shaped caller schemas: ordered item arrays plus the deterministic dup gate
# --------------------------------------------------------------------------------------


def test_array_shaped_params_travel_with_the_context_field() -> None:
    semantic_validator = StubSemanticValidator(
        SemanticValidationResult(
            True, "information", (), [{"name": "节能区域信息", "desc": "任务覆盖的物理区域"}]
        )
    )

    filled = extractor(compliance_checker=passing_checker(), semantic_validator=semantic_validator).extract(
        VALID_ZH_PROMPT, CONTEXT, {"type": "array"}, REFERENCE
    )

    assert filled.data == [{"name": "节能区域信息", "desc": "任务覆盖的物理区域"}]
    assert filled.context == {"id": SESSION_ID, "round": 2, "maxRounds": 5}


def test_duplicate_param_names_in_array_extraction_fail_with_rule_violation() -> None:
    semantic_validator = StubSemanticValidator(
        SemanticValidationResult(
            True,
            "information",
            (),
            [{"name": "节能时段", "desc": "a"}, {"name": "节能时段", "desc": "b"}],
        )
    )

    with pytest.raises(NegotiationParamExtractionError) as info:
        extractor(compliance_checker=passing_checker(), semantic_validator=semantic_validator).extract(
            VALID_ZH_PROMPT, CONTEXT, {"type": "array"}, REFERENCE
        )

    assert info.value.code_str == "negotiation.rule_violation"
    assert info.value.facts is not None
    assert info.value.facts.get("section_label") == "params.name"
    assert "节能时段" in str(info.value.facts.get("duplicates"))


def test_unique_array_param_names_pass_the_dup_gate() -> None:
    semantic_validator = StubSemanticValidator(
        SemanticValidationResult(
            True,
            "information",
            (),
            [{"name": "节能区域信息", "desc": "a"}, {"name": "节能时段", "desc": "b"}],
        )
    )

    filled = extractor(compliance_checker=passing_checker(), semantic_validator=semantic_validator).extract(
        VALID_ZH_PROMPT, CONTEXT, {"type": "array"}, REFERENCE
    )

    assert [item["name"] for item in filled.data] == ["节能区域信息", "节能时段"]


# --------------------------------------------------------------------------------------
# the generation-seam implementation (the P5 protocol gets its P6 implementation)
# --------------------------------------------------------------------------------------


def _generation_seam(extractor: NegotiationParamExtractorProtocol) -> NegotiationParamExtractorProtocol:
    """Typed helper pinning that the concrete extractor satisfies the generation seam protocol."""
    return extractor


def test_param_extractor_implements_the_generation_negotiation_param_extractor_seam() -> None:
    sut = extractor()

    # a static type checker verifies the assignment below satisfies the protocol shape; at runtime
    # the signature pins are asserted explicitly
    wired = _generation_seam(sut)

    assert wired is sut
    signature = inspect.signature(ParamExtractor.extract, eval_str=True)
    protocol_signature = inspect.signature(NegotiationParamExtractorProtocol.extract, eval_str=True)
    assert list(signature.parameters) == list(protocol_signature.parameters)
    assert signature.return_annotation is FilledParamData
