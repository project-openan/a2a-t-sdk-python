"""Tests of the LLM-backed semantic validator (port of Java ``DefaultNegotiationSemanticValidatorTest``).

The LLM is replaced by a scripted client returning one payload or raising one failure; every other
collaborator is the production wiring (the real packaged semantic validation prompts of both
languages), so the suite stays offline and deterministic.

The heart of the suite is the constant four-key output contract ``{semantic_verdict,
negotiation_type, errors, params}`` and the error triple ``{slot_name, code, facts}``: every shape
violation raises the internal validation error, every code outside the closed ``negotiation.*``
domain — unknown or cross-domain — falls back to ``negotiation.rule_violation`` with a WARN log, and
no raw LLM text ever reaches the surfaced errors.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from a2a_t.core.errors.exceptions import (
    ContentValidationError,
    ResourceNotFoundError,
)
from a2a_t.core.metadata import NegotiationPerformative
from a2a_t.core.validation_pipeline import ValidationResult
from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError
from a2a_t.llm.models import LLMResponse
from a2a_t.negotiation.content.enums import NegotiationType
from a2a_t.negotiation.resources.reference import NegotiationReference
from a2a_t.negotiation.validation.semantic_validator import (
    DefaultNegotiationSemanticValidator,
    NegotiationValidationError,
    SemanticValidationResult,
    build_semantic_validation_schema,
)

VALID_PROMPT = (
    "## Negotiation Context\n"
    "- id: 3dbc13b5-bd57-4c2b-b503-24e381b6c8d3\n"
    "- round: 1\n"
    "- maxRounds: 5\n\n"
    "## Required Information Items\n"
    "1. energy saving region: provide a real region\n"
)

TEMPLATE_CONTENT = "dummy template content"

EN_US = "en-US"
ZH_CN = "zh-CN"

CALLER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"confirmed_rate_mbps": {"type": "integer"}},
}

INFORMATION_PROPOSE_URI = "Negotiation-T/information-negotiation/propose/v1"


class ScriptedClient:
    """Scripted LLM client returning one payload or raising one failure; records every request."""

    def __init__(self, payload: str = "{}", failure: BaseException | None = None) -> None:
        self.payload = payload
        self.failure = failure
        self.calls = 0
        self.last_messages: list[dict[str, str]] = []
        self.last_schema: dict[str, Any] = {}
        self.last_temperature: float | None = None
        self.last_max_tokens: int | None = None

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
        self.last_temperature = temperature
        self.last_max_tokens = max_tokens
        if self.failure is not None:
            raise self.failure
        return LLMResponse(
            content=self.payload, model="test-model", usage={"prompt_tokens": 1, "completion_tokens": 1}, metadata={}
        )


class RecordingSchemaBuilder:
    """Schema-merging collaborator recording the caller schema and returning a fixed merged schema."""

    def __init__(self) -> None:
        self.last_caller_schema: dict[str, Any] | None = None

    def __call__(self, caller_schema: dict[str, Any]) -> dict[str, Any]:
        self.last_caller_schema = dict(caller_schema)
        return {"merged": True}


def reference(
    negotiation_type: NegotiationType | None = NegotiationType.INFORMATION,
    performative: NegotiationPerformative = NegotiationPerformative.PROPOSE,
    language: str = EN_US,
) -> NegotiationReference:
    """Build the reference of one validation under test."""
    return NegotiationReference(negotiation_type, performative, language)


def validator(llm: ScriptedClient, schema_builder: Any = None) -> DefaultNegotiationSemanticValidator:
    """Build the validator under test over the scripted client."""
    if schema_builder is None:
        return DefaultNegotiationSemanticValidator(llm)
    return DefaultNegotiationSemanticValidator(llm, schema_builder)


def semantic_payload(negotiation_type: str | None = "information", errors: str = "[]", params: str = "{}") -> str:
    """Build one well-formed four-key response payload."""
    type_value = "null" if negotiation_type is None else f'"{negotiation_type}"'
    return f'{{"semantic_verdict":true,"negotiation_type":{type_value},"errors":{errors},"params":{params}}}'


def error_payload(
    slot_name: str,
    code: str,
    facts: dict[str, Any],
    verdict: bool = False,
    negotiation_type: str | None = None,
) -> str:
    """Build one well-formed four-key response payload carrying a single LLM-reported error."""
    import json

    return json.dumps(
        {
            "semantic_verdict": verdict,
            "negotiation_type": negotiation_type,
            "errors": [{"slot_name": slot_name, "code": code, "facts": facts}],
            "params": {},
        }
    )


# --------------------------------------------------------------------------------------
# happy paths: verdict, type, errors and params
# --------------------------------------------------------------------------------------


def test_valid_response_yields_verdict_type_errors_and_params() -> None:
    llm = ScriptedClient(
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":{"confirmed_rate_mbps":2}}'
    )
    schema_builder = RecordingSchemaBuilder()

    result = validator(llm, schema_builder).validate_negotiation(
        VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT
    )

    assert result.verdict is True
    assert result.negotiation_type == "information"
    assert result.errors == ()
    assert result.params == {"confirmed_rate_mbps": 2}
    assert llm.calls == 1
    assert schema_builder.last_caller_schema == CALLER_SCHEMA


def test_reject_message_params_carry_the_reasons_of_non_provision() -> None:
    llm = ScriptedClient(
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[],'
        '"params":{"energy saving region":"site inventory unavailable, cannot provide"}}'
    )
    reject_schema = {"type": "object", "properties": {"energy saving region": {"type": "string"}}}

    result = validator(llm).validate_negotiation(
        VALID_PROMPT, reject_schema, reference(performative=NegotiationPerformative.REJECT), TEMPLATE_CONTENT
    )

    assert result.verdict is True
    assert result.params == {"energy saving region": "site inventory unavailable, cannot provide"}


def test_propose_message_params_carry_the_full_expectation_text() -> None:
    llm = ScriptedClient(
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[],'
        '"params":{"energy saving region":"energy saving area information, e.g. Songshanhu"}}'
    )
    propose_schema = {"type": "object", "properties": {"energy saving region": {"type": "string"}}}

    result = validator(llm).validate_negotiation(VALID_PROMPT, propose_schema, reference(), TEMPLATE_CONTENT)

    assert result.verdict is True
    assert result.params == {"energy saving region": "energy saving area information, e.g. Songshanhu"}


# --------------------------------------------------------------------------------------
# prompt wiring: the packaged system and user prompts of both languages
# --------------------------------------------------------------------------------------


def test_english_system_prompt_carries_the_propose_expectation_extraction_rule() -> None:
    llm = ScriptedClient(semantic_payload())

    validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    system_prompt = llm.last_messages[0]["content"]
    assert "the full expectation text stated for that field" in system_prompt
    assert "never treat a sample as the field's supplied value" in system_prompt


def test_chinese_system_prompt_carries_the_propose_expectation_extraction_rule() -> None:
    llm = ScriptedClient(semantic_payload())

    validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(language=ZH_CN), TEMPLATE_CONTENT)

    system_prompt = llm.last_messages[0]["content"]
    assert "完整期望内容原文" in system_prompt
    assert "不得将样例当作该字段已提供的真实值" in system_prompt


def test_english_system_prompt_carries_the_reject_reason_extraction_rule() -> None:
    llm = ScriptedClient(semantic_payload())

    validator(llm).validate_negotiation(
        VALID_PROMPT, CALLER_SCHEMA, reference(performative=NegotiationPerformative.REJECT), TEMPLATE_CONTENT
    )

    assert "reason of non-provision stated for that field" in llm.last_messages[0]["content"]


def test_chinese_system_prompt_carries_the_reject_reason_extraction_rule() -> None:
    llm = ScriptedClient(semantic_payload())

    validator(llm).validate_negotiation(
        VALID_PROMPT,
        CALLER_SCHEMA,
        reference(performative=NegotiationPerformative.REJECT, language=ZH_CN),
        TEMPLATE_CONTENT,
    )

    assert "无法提供的原因文本" in llm.last_messages[0]["content"]


def test_single_structured_call_receives_merged_schema_and_filled_user_prompt() -> None:
    llm = ScriptedClient(semantic_payload())
    schema_builder = RecordingSchemaBuilder()

    validator(llm, schema_builder).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    messages = llm.last_messages
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "semantic validation" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    user_prompt = messages[1]["content"]
    assert "information" in user_prompt
    assert "propose" in user_prompt
    assert TEMPLATE_CONTENT in user_prompt
    assert INFORMATION_PROPOSE_URI in user_prompt
    assert "confirmed_rate_mbps" in user_prompt
    assert "energy saving region" in user_prompt
    assert llm.last_schema == {"merged": True}
    assert llm.last_temperature is None
    assert llm.last_max_tokens is None
    assert schema_builder.last_caller_schema == CALLER_SCHEMA


@pytest.mark.parametrize("language", [EN_US, ZH_CN], ids=["en-US", "zh-CN"])
def test_user_prompt_carries_the_phase_token_of_the_performative(language: str) -> None:
    llm = ScriptedClient(semantic_payload())

    validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(language=language), TEMPLATE_CONTENT)

    user_prompt = llm.last_messages[1]["content"]
    expected_label = "协商阶段：propose" if language == ZH_CN else "Negotiation phase: propose"
    assert expected_label in user_prompt


def test_user_prompt_carries_the_compact_caller_schema_serialization() -> None:
    llm = ScriptedClient(semantic_payload())

    validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    user_prompt = llm.last_messages[1]["content"]
    assert '{"type":"object","properties":{"confirmed_rate_mbps":{"type":"integer"}}}' in user_prompt


# --------------------------------------------------------------------------------------
# output contract: shape violations raise the internal validation error
# --------------------------------------------------------------------------------------


def test_missing_negotiation_type_key_is_a_shape_violation() -> None:
    llm = ScriptedClient('{"semantic_verdict":true,"errors":[],"params":{}}')

    with pytest.raises(NegotiationValidationError, match="negotiation_type"):
        validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)


@pytest.mark.parametrize(
    "payload",
    [
        '{"negotiation_type":"information","errors":[],"params":{}}',
        '{"semantic_verdict":true,"negotiation_type":"information","params":{}}',
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[]}',
    ],
    ids=["missing-verdict", "missing-errors", "missing-params"],
)
def test_missing_other_required_keys_are_shape_violations(payload: str) -> None:
    llm = ScriptedClient(payload)

    with pytest.raises(NegotiationValidationError):
        validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)


@pytest.mark.parametrize(
    "payload",
    [
        '{"semantic_verdict":"yes","negotiation_type":"information","errors":[],"params":{}}',
        '{"semantic_verdict":true,"negotiation_type":"information","errors":"none","params":{}}',
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":[1,2]}',
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[{"slot_name":"section.context"}],"params":{}}',
        '{"semantic_verdict":true,"negotiation_type":42,"errors":[],"params":{}}',
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[{"slot_name":"s","code":"c","facts":"f"}],"params":{}}',
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[["s"]],"params":{}}',
    ],
    ids=[
        "verdict-not-boolean",
        "errors-not-array",
        "params-array-items-not-objects",
        "error-item-missing-code-and-facts",
        "type-not-string",
        "facts-not-object",
        "error-item-not-object",
    ],
)
def test_wrong_shapes_are_shape_violations(payload: str) -> None:
    llm = ScriptedClient(payload)

    with pytest.raises(NegotiationValidationError):
        validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)


@pytest.mark.parametrize(
    "payload",
    ["", "   ", "not json at all", "[1, 2, 3]", "5"],
    ids=["empty", "blank", "not-json", "json-array", "json-scalar"],
)
def test_non_json_or_empty_responses_are_shape_violations(payload: str) -> None:
    llm = ScriptedClient(payload)

    with pytest.raises(NegotiationValidationError):
        validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)


# --------------------------------------------------------------------------------------
# type consistency of a true verdict
# --------------------------------------------------------------------------------------


def test_null_type_with_true_verdict_is_turned_into_semantic_rejection() -> None:
    llm = ScriptedClient(semantic_payload(negotiation_type=None))

    result = validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    assert result.verdict is False
    assert result.negotiation_type is None
    assert len(result.errors) == 1
    assert result.errors[0].slot_name == "section.info_static"
    assert result.errors[0].code == "negotiation.type_mismatch"
    assert result.errors[0].facts == {"implied": "unknown", "declared": "information"}


def test_type_mismatch_with_true_verdict_is_turned_into_semantic_rejection() -> None:
    llm = ScriptedClient(semantic_payload(negotiation_type="target"))

    result = validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    assert result.verdict is False
    assert result.negotiation_type == "target"
    assert len(result.errors) == 1
    assert result.errors[0].slot_name == "section.target"
    assert result.errors[0].code == "negotiation.type_mismatch"
    assert "information" in result.errors[0].message


@pytest.mark.parametrize(
    "negotiation_type", ["target", "feasibility", "bogus"], ids=["target", "feasibility", "unknown"]
)
def test_unknown_type_name_with_true_verdict_rejects_on_the_declared_section(negotiation_type: str) -> None:
    llm = ScriptedClient(semantic_payload(negotiation_type=negotiation_type))

    result = validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    assert result.verdict is False
    assert result.negotiation_type == negotiation_type
    assert (
        result.errors[0].slot_name
        == {
            "target": "section.target",
            "feasibility": "section.feasibility",
            "bogus": "section.info_static",
        }[negotiation_type]
    )


def test_true_verdict_of_the_abort_reference_skips_the_type_consistency_check() -> None:
    llm = ScriptedClient(semantic_payload(negotiation_type=None))

    result = validator(llm).validate_negotiation(
        VALID_PROMPT, CALLER_SCHEMA, reference(None, NegotiationPerformative.ABORT), TEMPLATE_CONTENT
    )

    assert result.verdict is True
    assert result.negotiation_type is None
    assert result.errors == ()


# --------------------------------------------------------------------------------------
# negative verdicts pass through with the LLM-reported errors
# --------------------------------------------------------------------------------------


def test_negative_verdict_passes_through_with_type_null_and_errors() -> None:
    llm = ScriptedClient(
        '{"semantic_verdict":false,"negotiation_type":null,'
        '"errors":[{"slot_name":"section.target_result_content","code":"negotiation.conclusion_content_mismatch",'
        '"facts":{"conclusion":"Accept","section_label":"section.target_result_content"}}],"params":{}}'
    )

    result = validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    assert result.verdict is False
    assert result.negotiation_type is None
    assert len(result.errors) == 1
    assert result.errors[0].slot_name == "section.target_result_content"
    assert result.errors[0].code == "negotiation.conclusion_content_mismatch"
    assert result.errors[0].facts == {"conclusion": "Accept", "section_label": "section.target_result_content"}
    assert "Accept" in result.errors[0].message


@pytest.mark.parametrize(
    ("facts", "expected_facts"),
    [
        (
            {"conclusion": "Accept", "section_label": "section.target"},
            {"conclusion": "Accept", "section_label": "section.target"},
        ),
        (
            {"conclusion": "Accept", "section_label": "section.target", "raw": "invented"},
            {"conclusion": "Accept", "section_label": "section.target"},
        ),
        ({"conclusion": 2, "section_label": True}, {"conclusion": "2", "section_label": "True"}),
        ({"conclusion": None, "section_label": "s", "nested": {"a": 1}}, {"section_label": "s"}),
    ],
    ids=["declared-keys", "extra-key-dropped", "numeric-and-boolean-values", "null-and-nested-values-dropped"],
)
def test_known_code_facts_are_strictly_matched_against_the_catalog_fact_parameters(
    facts: dict[str, Any], expected_facts: dict[str, str]
) -> None:
    import json

    llm = ScriptedClient(
        json.dumps(
            {
                "semantic_verdict": False,
                "negotiation_type": None,
                "errors": [
                    {
                        "slot_name": "section.target_result_content",
                        "code": "negotiation.conclusion_content_mismatch",
                        "facts": facts,
                    }
                ],
                "params": {},
            }
        )
    )

    result = validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    assert result.errors[0].facts == expected_facts


# --------------------------------------------------------------------------------------
# code resolution: unknown and cross-domain codes fall back with a WARN
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reported_code",
    [
        "data_problem",
        "totally-unknown",
        "negotiation.unknown_sub_code",
        "prompt.param_missing",
        "template.not_found",
        "llm.response_invalid",
        "slot.not_provided",
        "content.param_missing",
    ],
    ids=[
        "unstructured-code",
        "unknown-code",
        "unknown-negotiation-code",
        "prompt-domain-code",
        "template-domain-code",
        "llm-domain-code",
        "slot-domain-code",
        "content-domain-code",
    ],
)
def test_codes_outside_the_negotiation_domain_fall_back_to_rule_violation(reported_code: str) -> None:
    llm = ScriptedClient(error_payload("section.info_static", reported_code, {"reason": "irrelevant"}))

    result = validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    assert result.verdict is False
    assert len(result.errors) == 1
    assert result.errors[0].code == "negotiation.rule_violation"
    assert result.errors[0].facts == {"section_label": "section.info_static"}
    assert "section.info_static" in result.errors[0].message


def test_unknown_code_logs_the_fallback_warning(caplog: pytest.LogCaptureFixture) -> None:
    llm = ScriptedClient(error_payload("section.info_static", "data_problem", {"reason": "irrelevant"}))

    with caplog.at_level(logging.WARNING, logger="a2a_t.negotiation.validation.semantic_validator"):
        validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    assert (
        "negotiation_semantic_validation_unknown_code original_code=data_problem "
        "fallback_code=negotiation.rule_violation" in caplog.text
    )


def test_cross_domain_code_logs_the_fallback_warning(caplog: pytest.LogCaptureFixture) -> None:
    llm = ScriptedClient(error_payload("section.info_static", "prompt.param_missing", {"reason": "irrelevant"}))

    with caplog.at_level(logging.WARNING, logger="a2a_t.negotiation.validation.semantic_validator"):
        validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    assert (
        "negotiation_semantic_validation_unknown_code original_code=prompt.param_missing "
        "fallback_code=negotiation.rule_violation" in caplog.text
    )


def test_known_negotiation_code_is_not_downgraded_and_logs_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    llm = ScriptedClient(
        error_payload(
            "section.info_static", "negotiation.type_mismatch", {"implied": "target", "declared": "information"}
        )
    )

    with caplog.at_level(logging.WARNING, logger="a2a_t.negotiation.validation.semantic_validator"):
        result = validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    assert result.errors[0].code == "negotiation.type_mismatch"
    assert "negotiation_semantic_validation_unknown_code" not in caplog.text


def test_raw_llm_text_never_surfaces_in_the_reported_errors() -> None:
    marker = "SECRET_RAW_LLM_TEXT_MARKER"
    llm = ScriptedClient(error_payload("section.info_static", "data_problem", {"reason": marker, "another": marker}))

    result = validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    for error in result.errors:
        assert marker not in error.message
        assert marker not in str(error.facts)
        assert marker not in error.code
    assert result.errors[0].facts == {"section_label": "section.info_static"}


# --------------------------------------------------------------------------------------
# transport failures and missing prompt resources
# --------------------------------------------------------------------------------------


def test_llm_transport_failure_propagates_for_the_pipeline_to_map() -> None:
    llm = ScriptedClient(failure=LLMRuntimeError("invocation failed"))

    with pytest.raises(LLMRuntimeError, match="invocation failed"):
        validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)


def test_missing_prompt_resource_for_language_surfaces_resource_not_found() -> None:
    llm = ScriptedClient(semantic_payload())
    unsupported = reference(language="fr-FR")

    with pytest.raises(ResourceNotFoundError):
        validator(llm).validate_negotiation(VALID_PROMPT, CALLER_SCHEMA, unsupported, TEMPLATE_CONTENT)
    assert llm.calls == 0


def test_missing_llm_client_fails_as_not_configured_at_call_time() -> None:
    llm = ScriptedClient(semantic_payload())

    with pytest.raises(LLMConfigError):
        DefaultNegotiationSemanticValidator(None).validate_negotiation(
            VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT
        )
    assert llm.calls == 0


# --------------------------------------------------------------------------------------
# the core-contract adapter (Java interface default method)
# --------------------------------------------------------------------------------------


def test_adapter_translates_a_missing_llm_configuration() -> None:
    sut = DefaultNegotiationSemanticValidator(None)

    with pytest.raises(ContentValidationError) as excinfo:
        sut.validate(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    assert excinfo.value.code_str == "llm.not_configured"
    assert excinfo.value.errors == []


def test_adapter_translates_a_transport_failure() -> None:
    sut = DefaultNegotiationSemanticValidator(ScriptedClient(failure=LLMRuntimeError("boom")))

    with pytest.raises(ContentValidationError) as excinfo:
        sut.validate(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    assert excinfo.value.code_str == "llm.invocation_failed"
    assert excinfo.value.facts == {"provider": "LLMRuntimeError", "reason": "boom"}
    assert [(error.slot_name, error.code) for error in excinfo.value.errors] == [("_llm", "llm.invocation_failed")]


def test_adapter_translates_a_response_contract_violation() -> None:
    sut = DefaultNegotiationSemanticValidator(ScriptedClient("not json at all"))

    with pytest.raises(ContentValidationError) as excinfo:
        sut.validate(VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT)

    assert excinfo.value.code_str == "llm.response_invalid"
    assert excinfo.value.facts == {"step": "semantic_validation"}
    assert [(error.slot_name, error.facts) for error in excinfo.value.errors] == [
        ("_llm", {"step": "semantic_validation"})
    ]
    assert "not json" not in str(excinfo.value)


def test_adapter_narrows_a_successful_outcome_to_the_core_result() -> None:
    llm = ScriptedClient(
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":{"confirmed_rate_mbps":2}}'
    )

    result = DefaultNegotiationSemanticValidator(llm).validate(
        VALID_PROMPT, CALLER_SCHEMA, reference(), TEMPLATE_CONTENT
    )

    assert isinstance(result, ValidationResult)
    assert result.verdict is True
    assert result.errors == ()
    assert result.params == {"confirmed_rate_mbps": 2}


def test_result_record_normalizes_errors_and_params() -> None:
    from a2a_t.core.errors.exceptions import SlotValidationError

    result = SemanticValidationResult(
        True, "information", [SlotValidationError("s", "negotiation.rule_violation", "m")], {"k": 1}
    )

    assert isinstance(result.errors, tuple)
    assert result.params == {"k": 1}


# --------------------------------------------------------------------------------------
# the merged output schema (Java ``buildSemanticValidationSchema``)
# --------------------------------------------------------------------------------------


def test_semantic_validation_schema_merges_the_caller_schema_into_a_four_key_contract() -> None:
    caller_schema = {"type": "object", "properties": {"energy_rate": {"type": "number"}}}

    schema = build_semantic_validation_schema(caller_schema)

    assert schema["type"] == "object"
    properties = schema["properties"]
    assert list(properties) == ["semantic_verdict", "negotiation_type", "errors", "params"]
    assert schema["required"] == ["semantic_verdict", "negotiation_type", "errors", "params"]
    assert schema["additionalProperties"] is False

    assert properties["semantic_verdict"] == {"type": "boolean"}

    negotiation_type = properties["negotiation_type"]
    assert negotiation_type["type"] == ["string", "null"]
    assert negotiation_type["enum"] == ["information", "target", "feasibility", None]

    errors = properties["errors"]
    assert errors["type"] == "array"
    error_item = errors["items"]
    assert list(error_item["properties"]) == ["slot_name", "code", "facts"]
    assert error_item["required"] == ["slot_name", "code", "facts"]
    assert error_item["additionalProperties"] is False
    assert error_item["properties"]["facts"] == {"type": "object", "additionalProperties": {"type": "string"}}

    assert properties["params"] == caller_schema


def test_semantic_validation_schema_wraps_caller_schema_without_type_keyword() -> None:
    caller_schema = {"properties": {"id": {"type": "string"}}}

    schema = build_semantic_validation_schema(caller_schema)

    params = schema["properties"]["params"]
    assert params["type"] == "object"
    assert params["properties"] == {"id": {"type": "string"}}
    assert "additionalProperties" not in params


def test_rejects_null_caller_schema() -> None:
    with pytest.raises(TypeError, match="Caller parameter schema must not be null."):
        build_semantic_validation_schema(None)  # type: ignore[arg-type]
