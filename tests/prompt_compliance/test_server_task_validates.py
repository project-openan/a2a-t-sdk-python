"""Tests of the server extension content validators (Java ``validate*PromptAndDataFilling``).

Pins the three ``validate_{task,notification,auth}_prompt_and_data_filling`` APIs against the Java
``DefaultContentValidator`` + ``InputLimitedContentValidator`` + ``DefaultSemanticValidator``
contract: the extension and version gates, the input length gate (fail-fast before any LLM call),
the template loading gate, the retryable semantic step with its constant three-key output contract
and the ``content.*`` code resolution, and the deterministic parameter merge.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.config.models import A2ATConfig, LlmRuntimeConfig, PromptRuntimeConfig
from a2a_t.core.errors.exceptions import ContentValidationError
from a2a_t.core.errors.input_limit import InputLimitConfig
from a2a_t.core.standard_templates import (
    AUTHORIZATION_EXTENSION_NAME,
    NOTIFICATION_EXTENSION_NAME,
    TASK_EXTENSION_NAME,
)
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.models import LLMResponse
from a2a_t.server.prompt_compliance.content_semantic_validator import build_content_validation_schema
from a2a_t.server.prompt_compliance.content_validator import (
    DefaultContentValidator,
    InputLimitedContentValidator,
    build_content_validator,
    build_content_validators,
)

#: Extension families covered by the facade validators (Java server wiring).
EXTENSIONS = (
    (TASK_EXTENSION_NAME, "Task-T/network-layer/ran-energy-saving/v1"),
    (NOTIFICATION_EXTENSION_NAME, "Notification-T/network-layer/subscribe-incident/v1"),
    (AUTHORIZATION_EXTENSION_NAME, "Authorization-T/authorization-policy-management/v1"),
)

_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"site": {"type": "string", "description": "Site name"}},
    "required": ["site"],
}


class MockLLM:
    """Scripted LLM client recording every structured call (exact call-count assertions)."""

    def __init__(self, responses: list[str | BaseException]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, object],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "json_schema": json_schema})
        if not self._responses:
            raise AssertionError("Unexpected extra LLM call")
        content = self._responses.pop(0)
        if isinstance(content, BaseException):
            raise content
        return LLMResponse(content=content, model="mock", usage={}, metadata={})


def _config(*, max_attempts: int = 1, max_text_chars: int = 16384) -> A2ATConfig:
    return A2ATConfig(
        prompt=PromptRuntimeConfig(language="en-US", source_type="packaged"),
        input_limits=InputLimitConfig(max_text_chars=max_text_chars),
        llm=LlmRuntimeConfig(max_attempts=max_attempts),
    )


def _verdict_response(verdict: bool, errors: list[dict[str, object]], params: dict[str, object]) -> str:
    return json.dumps({"semantic_verdict": verdict, "errors": errors, "params": params})


@pytest.mark.parametrize(
    ("extension_name", "template_uri"),
    EXTENSIONS,
    ids=[extension[0] for extension in EXTENSIONS],
)
def test_validate_returns_filled_params_for_every_extension(extension_name: str, template_uri: str) -> None:
    llm = MockLLM([_verdict_response(True, [], {"site": "Site A"})])
    validator = build_content_validator(
        extension_name=extension_name,
        config=_config(),
        llm_client=llm,
    )

    result = validator.validate("Site: Site A", _PARAM_SCHEMA, template_uri)

    assert isinstance(result, FilledParamData)
    assert result.data == {"site": "Site A"}
    assert len(llm.calls) == 1


def test_build_content_validators_assembles_all_three_extensions() -> None:
    llm = MockLLM([])
    validators = build_content_validators(config=_config(), llm_client=llm)

    assert set(validators) == {TASK_EXTENSION_NAME, NOTIFICATION_EXTENSION_NAME, AUTHORIZATION_EXTENSION_NAME}
    assert all(isinstance(validator, InputLimitedContentValidator) for validator in validators.values())


def test_user_prompt_fills_every_literal_bracket_token() -> None:
    llm = MockLLM([_verdict_response(True, [], {})])
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(),
        llm_client=llm,
    )

    validator.validate("Site: Site A", _PARAM_SCHEMA, "Task-T/network-layer/ran-energy-saving/v1")

    user_message = llm.calls[0]["messages"][1]["content"]
    assert "Extension Name: Task-T" in user_message
    assert "Input Content: Site: Site A" in user_message
    assert "Template URI: Task-T/network-layer/ran-energy-saving/v1" in user_message
    assert "Template Content: ## Operation Type" in user_message
    assert json.dumps(_PARAM_SCHEMA, ensure_ascii=False, separators=(",", ":")) in user_message


def test_output_schema_is_the_constant_three_key_contract() -> None:
    llm = MockLLM([_verdict_response(True, [], {})])
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(),
        llm_client=llm,
    )

    validator.validate("Site: Site A", _PARAM_SCHEMA, "Task-T/network-layer/ran-energy-saving/v1")

    assert llm.calls[0]["json_schema"] == build_content_validation_schema()


def test_semantic_rejection_carries_the_structured_errors() -> None:
    errors = [
        {
            "slot_name": "site",
            "code": "content.param_missing",
            "facts": {"section_label": "site"},
        }
    ]
    llm = MockLLM([_verdict_response(False, errors, {"site": None})])
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(),
        llm_client=llm,
    )

    with pytest.raises(ContentValidationError) as exc_info:
        validator.validate("Site: ", _PARAM_SCHEMA, "Task-T/network-layer/ran-energy-saving/v1")

    failure = exc_info.value
    assert failure.code_str == "negotiation.semantic_rejected"
    assert failure.errors[0].slot_name == "site"
    assert failure.errors[0].code == "content.param_missing"
    # The params of the rejected extraction stay attached to the failure (Java parity).
    assert failure.params == {"site": None}


@pytest.mark.parametrize(
    ("reported_code", "expected_code", "expected_facts"),
    [
        ("content.param_missing", "content.param_missing", {"section_label": "site"}),
        ("content.format_error", "content.format_error", {"section_label": "site"}),
        ("negotiation.rule_violation", "content.rule_violation", {"section_label": "site"}),
        ("unknown-code", "content.rule_violation", {"section_label": "site"}),
    ],
    ids=["content-domain", "another-content-code", "cross-domain", "unknown"],
)
def test_reported_codes_resolve_against_the_content_domain(
    reported_code: str,
    expected_code: str,
    expected_facts: dict[str, str],
) -> None:
    errors = [{"slot_name": "site", "code": reported_code, "facts": {"section_label": "site"}}]
    llm = MockLLM([_verdict_response(False, errors, {})])
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(),
        llm_client=llm,
    )

    with pytest.raises(ContentValidationError) as exc_info:
        validator.validate("Site: ", _PARAM_SCHEMA, "Task-T/network-layer/ran-energy-saving/v1")

    error = exc_info.value.errors[0]
    assert error.code == expected_code
    assert error.facts == expected_facts
    assert error.message  # the SDK-rendered message, never the raw LLM text


@pytest.mark.parametrize(
    "response",
    [
        json.dumps({"semantic_verdict": "yes", "errors": [], "params": {}}),
        json.dumps({"errors": [], "params": {}}),
        json.dumps({"semantic_verdict": True, "errors": {}, "params": {}}),
        json.dumps({"semantic_verdict": True, "errors": [], "params": []}),
        json.dumps({"semantic_verdict": True, "errors": [{"slot_name": "site"}], "params": {}}),
        "not-json",
        "",
    ],
    ids=[
        "verdict-not-boolean",
        "verdict-missing",
        "errors-not-array",
        "params-not-object",
        "error-triple-incomplete",
        "not-json",
        "empty",
    ],
)
def test_response_contract_violations_fail_with_response_invalid(response: str) -> None:
    llm = MockLLM([response])
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(),
        llm_client=llm,
    )

    with pytest.raises(ContentValidationError) as exc_info:
        validator.validate("Site: Site A", _PARAM_SCHEMA, "Task-T/network-layer/ran-energy-saving/v1")

    assert exc_info.value.code_str == "llm.response_invalid"


def test_retryable_llm_failure_is_retried_to_the_attempt_limit() -> None:
    llm = MockLLM([LLMRuntimeError("LLM invocation failed.")] * 3)
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(max_attempts=3),
        llm_client=llm,
    )

    with pytest.raises(ContentValidationError) as exc_info:
        validator.validate("Site: Site A", _PARAM_SCHEMA, "Task-T/network-layer/ran-energy-saving/v1")

    assert exc_info.value.code_str == "llm.invocation_failed"
    # Retryability is pinned by the exact LLM call count (port plan 7.3).
    assert len(llm.calls) == 3


def test_non_retryable_rejection_is_not_retried() -> None:
    errors = [{"slot_name": "site", "code": "content.param_missing", "facts": {"section_label": "site"}}]
    llm = MockLLM([_verdict_response(False, errors, {})])
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(max_attempts=3),
        llm_client=llm,
    )

    with pytest.raises(ContentValidationError):
        validator.validate("Site: ", _PARAM_SCHEMA, "Task-T/network-layer/ran-energy-saving/v1")

    assert len(llm.calls) == 1


def test_missing_llm_client_fails_with_not_configured() -> None:
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(),
        llm_client=None,
    )

    with pytest.raises(ContentValidationError) as exc_info:
        validator.validate("Site: Site A", _PARAM_SCHEMA, "Task-T/network-layer/ran-energy-saving/v1")

    assert exc_info.value.code_str == "llm.not_configured"


@pytest.mark.parametrize(
    ("prompt", "schema"),
    [
        ("", _PARAM_SCHEMA),
        ("   ", _PARAM_SCHEMA),
        ("Site: Site A", None),
    ],
    ids=["blank-prompt", "whitespace-prompt", "none-schema"],
)
def test_input_gate_rejects_blank_prompt_and_none_schema(prompt: str, schema: Any) -> None:
    llm = MockLLM([])
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(),
        llm_client=llm,
    )

    with pytest.raises(ContentValidationError) as exc_info:
        validator.validate(prompt, schema, "Task-T/network-layer/ran-energy-saving/v1")

    assert exc_info.value.code_str == "negotiation.invalid_input"
    assert len(llm.calls) == 0


@pytest.mark.parametrize(
    ("extension_name", "template_uri"),
    EXTENSIONS,
    ids=[extension[0] for extension in EXTENSIONS],
)
def test_extension_gate_rejects_foreign_template_uris(extension_name: str, template_uri: str) -> None:
    foreign_uri = "Negotiation-T/information-negotiation/propose/v1"
    llm = MockLLM([])
    validator = build_content_validator(
        extension_name=extension_name,
        config=_config(),
        llm_client=llm,
    )

    with pytest.raises(ContentValidationError) as exc_info:
        validator.validate("Site: Site A", _PARAM_SCHEMA, foreign_uri)

    assert exc_info.value.code_str == "negotiation.invalid_input"
    assert "does not match expected extension" in str(exc_info.value)
    assert len(llm.calls) == 0


def test_version_gate_rejects_unsupported_template_versions() -> None:
    llm = MockLLM([])
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(),
        llm_client=llm,
    )

    with pytest.raises(ContentValidationError) as exc_info:
        validator.validate("Site: Site A", _PARAM_SCHEMA, "Task-T/network-layer/ran-energy-saving/v2")

    assert exc_info.value.code_str == "negotiation.invalid_input"
    assert "Unsupported template URI version" in str(exc_info.value)


def test_template_loading_gate_fails_with_template_not_found() -> None:
    llm = MockLLM([])
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(),
        llm_client=llm,
    )

    with pytest.raises(ContentValidationError) as exc_info:
        validator.validate("Site: Site A", _PARAM_SCHEMA, "Task-T/network-layer/unknown-scenario/v1")

    assert exc_info.value.code_str == "template.not_found"
    assert len(llm.calls) == 0


def test_input_limit_gate_rejects_oversized_prompt_before_any_llm_call() -> None:
    llm = MockLLM([])
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(max_text_chars=16),
        llm_client=llm,
    )

    with pytest.raises(ContentValidationError) as exc_info:
        validator.validate("Site: Site A with a very long tail", _PARAM_SCHEMA, EXTENSIONS[0][1])

    assert exc_info.value.code_str == "input.text_too_long"
    assert len(llm.calls) == 0


def test_template_uri_boundary_is_parsed_fail_fast() -> None:
    validator = build_content_validator(
        extension_name=TASK_EXTENSION_NAME,
        config=_config(),
        llm_client=MockLLM([]),
    )

    with pytest.raises(TypeError, match="templateUri"):
        validator.validate("Site: Site A", _PARAM_SCHEMA, None)
    with pytest.raises(ValueError, match="Unparseable template URI"):
        validator.validate("Site: Site A", _PARAM_SCHEMA, "not-a-uri")


def test_default_content_validator_rejects_attempt_limits_below_one() -> None:
    from a2a_t.common.prompt_resources import create

    with pytest.raises(ValueError, match="at least 1"):
        DefaultContentValidator(
            extension_name=TASK_EXTENSION_NAME,
            language="en-US",
            max_attempts=0,
            llm_client=None,
            resource_access=create(PromptRuntimeConfig(language="en-US", source_type="packaged")),
        )


def test_facade_validate_methods_route_to_the_extension_validators() -> None:
    """The A2ATServer facade methods delegate to the extension-keyed content validators."""
    from a2a_t.server.a2at_server import A2ATServer

    llm = MockLLM([_verdict_response(True, [], {"site": "Site A"})])
    validators = build_content_validators(config=_config(), llm_client=llm)
    server = A2ATServer.__new__(A2ATServer)
    server._content_validators = validators

    result = server.validate_task_prompt_and_data_filling(
        "Site: Site A", _PARAM_SCHEMA, "Task-T/network-layer/ran-energy-saving/v1"
    )

    assert result.data == {"site": "Site A"}
    assert len(llm.calls) == 1


def test_facade_validate_methods_share_one_validator_set() -> None:
    from a2a_t.server.a2at_server import A2ATServer

    llm = MockLLM([])
    validators = build_content_validators(config=_config(), llm_client=llm)
    server = A2ATServer.__new__(A2ATServer)
    server._content_validators = validators

    with pytest.raises(ContentValidationError) as exc_info:
        server.validate_notification_prompt_and_data_filling(
            "Site: Site A", _PARAM_SCHEMA, "Task-T/network-layer/ran-energy-saving/v1"
        )

    assert exc_info.value.code_str == "negotiation.invalid_input"
    with pytest.raises(ValueError, match="Unparseable template URI"):
        server.validate_auth_prompt_and_data_filling("Site: Site A", _PARAM_SCHEMA, "bad-uri")
