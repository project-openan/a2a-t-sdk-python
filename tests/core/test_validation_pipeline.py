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

"""Tests for the content validation pipeline (port of Java ``ValidationPipeline``).

Every test runs with stubbed checkers and validators — zero LLM calls. The
retry semantics are asserted through exact call counts, per the port-plan
testing paradigm (section 7.3): a failure is retried to the attempt limit if
and only if its code is retryable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pytest

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import (
    A2ATBusinessError,
    A2ATError,
    ContentValidationError,
    ResourceNotFoundError,
    SlotValidationError,
)
from a2a_t.core.validation_pipeline import (
    LLM_RESPONSE_INVALID_CODE,
    NEGOTIATION_INVALID_INPUT_CODE,
    NEGOTIATION_SEMANTIC_REJECTED_CODE,
    RETRYABLE_ERROR_CODES,
    SEMANTIC_VALIDATION_STEP,
    TEMPLATE_NOT_FOUND_CODE,
    FilledParamData,
    RuleChecker,
    SemanticValidator,
    TemplateContentLoader,
    ValidationPipeline,
    ValidationResult,
    with_retry,
)

RETRYABLE_CODES = sorted(RETRYABLE_ERROR_CODES)

NON_RETRYABLE_CODES = [
    "negotiation.rule_violation",
    "negotiation.semantic_rejected",
    "negotiation.invalid_input",
    "template.not_found",
    # a llm.* code outside the whitelist: the retry decision must be an exact
    # code match, never an llm. prefix match
    "llm.not_configured",
]


def _error(code: str, message: str = "failure", **kwargs: object) -> ContentValidationError:
    """Build a content validation failure carrying one catalog code string."""
    return ContentValidationError(ErrorCatalog(code), message=message, **kwargs)  # type: ignore[arg-type]


@dataclass
class SemanticValidatorCall:
    """One recorded invocation of the semantic validator stub."""

    prompt: str
    schema: Mapping[str, object]
    reference: str
    template_content: str


class RecordingRuleChecker:
    """Deterministic rule checker stub recording every call (zero LLM)."""

    def __init__(self, context_params: Mapping[str, object] | None = None) -> None:
        self._context_params = dict(context_params or {})
        self.prompts: list[str] = []

    def check(self, prompt: str) -> dict[str, object]:
        self.prompts.append(prompt)
        return dict(self._context_params)


class FailingRuleChecker:
    """Rule checker stub failing every check with a rule violation."""

    def __init__(self, failure: ContentValidationError) -> None:
        self.failure = failure
        self.prompts: list[str] = []

    def check(self, prompt: str) -> dict[str, object]:
        self.prompts.append(prompt)
        raise self.failure


class ScriptedSemanticValidator:
    """Semantic validator stub replaying a scripted sequence of outcomes.

    Calling beyond the script fails the test, so the recorded call list pins
    the exact number of LLM invocations.
    """

    def __init__(self, outcomes: Sequence[ValidationResult | BaseException]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[SemanticValidatorCall] = []

    def validate(
        self,
        prompt: str,
        schema: Mapping[str, object],
        reference: str,
        template_content: str,
    ) -> ValidationResult:
        self.calls.append(
            SemanticValidatorCall(
                prompt=prompt,
                schema=dict(schema),
                reference=reference,
                template_content=template_content,
            )
        )
        if not self._outcomes:
            raise AssertionError(f"semantic validator was called {len(self.calls)} times but the script is exhausted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class RecordingTemplateContentLoader:
    """Template content loader stub recording every resolved reference."""

    def __init__(self, content: str = "LOADED TEMPLATE") -> None:
        self._content = content
        self.references: list[str] = []

    def load(self, reference: str) -> str:
        self.references.append(reference)
        return self._content


class FailingTemplateContentLoader:
    """Template content loader stub raising on every load."""

    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.references: list[str] = []

    def load(self, reference: str) -> str:
        self.references.append(reference)
        raise self.failure


@dataclass
class StageEvent:
    """One recorded pipeline stage invocation, for stage-ordering assertions."""

    stage: str
    detail: str = ""


@dataclass
class StageRecorder:
    """Shared event log for the stage-ordering stubs."""

    events: list[StageEvent] = field(default_factory=list)


class StageRecordingRuleChecker:
    """Rule checker stub logging stage events."""

    def __init__(self, recorder: StageRecorder) -> None:
        self._recorder = recorder

    def check(self, prompt: str) -> dict[str, object]:
        self._recorder.events.append(StageEvent("rule_gate", prompt))
        return {"round": "3"}


class StageRecordingLoader:
    """Template loader stub logging stage events."""

    def __init__(self, recorder: StageRecorder) -> None:
        self._recorder = recorder

    def load(self, reference: str) -> str:
        self._recorder.events.append(StageEvent("template_load", reference))
        return "TEMPLATE"


class StageRecordingValidator:
    """Semantic validator stub logging stage events."""

    def __init__(self, recorder: StageRecorder) -> None:
        self._recorder = recorder

    def validate(
        self,
        prompt: str,
        schema: Mapping[str, object],
        reference: str,
        template_content: str,
    ) -> ValidationResult:
        self._recorder.events.append(StageEvent("semantic_gate", template_content))
        return ValidationResult(verdict=True, params={"site": "Site A"})


def _accepted_verdict(params: Mapping[str, object] | None = None) -> ValidationResult:
    return ValidationResult(verdict=True, params=dict(params or {}))


def _build_pipeline(
    rule_checker: RuleChecker,
    semantic_validator: SemanticValidator[str],
    max_attempts: int = 3,
    language: str | None = None,
    template_content_loader: TemplateContentLoader[str] | None = None,
) -> ValidationPipeline[str]:
    return ValidationPipeline(
        rule_checker=rule_checker,
        semantic_validator=semantic_validator,
        max_attempts=max_attempts,
        language=language,
        template_content_loader=template_content_loader,
    )


# ---------------------------------------------------------------------------
# with_retry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("max_attempts", [0, -1])
def test_with_retry_rejects_attempt_limits_below_one(max_attempts: int) -> None:
    with pytest.raises(ValueError, match="max_attempts must be at least 1"):
        with_retry(max_attempts, "step", lambda: "unused")


def test_with_retry_returns_the_first_success_result() -> None:
    calls: list[int] = []

    def action() -> str:
        calls.append(1)
        return "ok"

    assert with_retry(3, "step", action) == "ok"
    assert len(calls) == 1


def test_with_retry_succeeds_after_one_retryable_failure() -> None:
    calls: list[int] = []

    def action() -> str:
        calls.append(1)
        if len(calls) < 2:
            raise _error("llm.invocation_failed", "flaky provider")
        return "recovered"

    assert with_retry(3, "step", action) == "recovered"
    assert len(calls) == 2


@pytest.mark.parametrize("code", RETRYABLE_CODES)
def test_with_retry_exhaustion_reraises_the_original_failure(code: str) -> None:
    calls: list[int] = []
    failure = _error(code, "persistent failure")

    def action() -> str:
        calls.append(1)
        raise failure

    with pytest.raises(ContentValidationError) as excinfo:
        with_retry(3, "step", action)

    assert excinfo.value is failure
    assert excinfo.value.code == code
    assert len(calls) == 3


@pytest.mark.parametrize("code", NON_RETRYABLE_CODES)
def test_with_retry_reraises_non_retryable_failures_immediately(code: str) -> None:
    calls: list[int] = []
    failure = _error(code, "final verdict, not an outage")

    def action() -> str:
        calls.append(1)
        raise failure

    with pytest.raises(ContentValidationError) as excinfo:
        with_retry(3, "step", action)

    assert excinfo.value is failure
    assert len(calls) == 1


def test_with_retry_propagates_non_content_errors_immediately() -> None:
    calls: list[int] = []

    def action() -> str:
        calls.append(1)
        raise ValueError("not a content failure")

    with pytest.raises(ValueError):
        with_retry(3, "step", action)

    assert len(calls) == 1


def test_retryable_error_codes_are_exactly_the_three_code_whitelist() -> None:
    assert RETRYABLE_ERROR_CODES == frozenset(
        {"negotiation.content_extract_failed", "llm.invocation_failed", "llm.response_invalid"}
    )


def test_pipeline_codes_match_the_java_catalog_spelling() -> None:
    """Pin the pipeline code constants (derived from ErrorCatalog) to the Java spelling (D4)."""
    assert NEGOTIATION_INVALID_INPUT_CODE == "negotiation.invalid_input"
    assert TEMPLATE_NOT_FOUND_CODE == "template.not_found"
    assert LLM_RESPONSE_INVALID_CODE == "llm.response_invalid"
    assert NEGOTIATION_SEMANTIC_REJECTED_CODE == "negotiation.semantic_rejected"
    assert NEGOTIATION_INVALID_INPUT_CODE == ErrorCatalog.NEGOTIATION_INVALID_INPUT.value
    assert TEMPLATE_NOT_FOUND_CODE == ErrorCatalog.TEMPLATE_NOT_FOUND.value
    assert LLM_RESPONSE_INVALID_CODE == ErrorCatalog.LLM_RESPONSE_INVALID.value
    assert NEGOTIATION_SEMANTIC_REJECTED_CODE == ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED.value


# ---------------------------------------------------------------------------
# ContentValidationError
# ---------------------------------------------------------------------------


def test_content_validation_error_defaults() -> None:
    error = _error("negotiation.invalid_input", "bad input")

    assert error.code == "negotiation.invalid_input"
    assert error.code_str == "negotiation.invalid_input"
    assert str(error) == "bad input"
    assert error.errors == []
    assert error.params == {}
    assert error.facts == {}


def test_content_validation_error_copies_errors_and_params() -> None:
    errors = [SlotValidationError("site", "content.param_missing", "missing")]
    params = {"site": None, "round": "3"}
    error = ContentValidationError(
        ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED,
        message="rejected",
        errors=errors,
        params=params,
    )

    errors.append(SlotValidationError("other", "content.param_missing", "also missing"))

    assert error.errors == [SlotValidationError("site", "content.param_missing", "missing")]
    # null-ish extraction values are preserved (Java parity: the semantic
    # validator emits None for slots it could not extract)
    assert error.params == {"site": None, "round": "3"}
    assert list(error.params) == ["site", "round"]


def test_content_validation_error_exposes_the_cause() -> None:
    cause = ValueError("raw LLM garbage")
    error = _error("llm.response_invalid", "invalid response", cause=cause)

    assert error.__cause__ is cause


def test_content_validation_error_roots_in_the_core_errors_tree() -> None:
    assert issubclass(ContentValidationError, A2ATBusinessError)
    assert issubclass(A2ATBusinessError, A2ATError)
    assert issubclass(ResourceNotFoundError, A2ATError)


# ---------------------------------------------------------------------------
# ValidationPipeline: stage ordering and the happy path
# ---------------------------------------------------------------------------


def test_stage_ordering_is_rule_gate_then_template_load_then_semantic_gate() -> None:
    recorder = StageRecorder()
    pipeline = _build_pipeline(
        StageRecordingRuleChecker(recorder),
        StageRecordingValidator(recorder),
        max_attempts=2,
        template_content_loader=StageRecordingLoader(recorder),
    )

    result = pipeline.validate("prompt text", {"type": "object"}, "Task-T/network-layer/ran-energy-saving/v1")

    assert result.data == {"round": "3", "site": "Site A"}
    assert [(event.stage, event.detail) for event in recorder.events] == [
        ("rule_gate", "prompt text"),
        ("template_load", "Task-T/network-layer/ran-energy-saving/v1"),
        ("semantic_gate", "TEMPLATE"),
    ]


def test_input_gate_failure_short_circuits_every_later_stage() -> None:
    recorder = StageRecorder()
    pipeline = _build_pipeline(
        StageRecordingRuleChecker(recorder),
        StageRecordingValidator(recorder),
        max_attempts=2,
        template_content_loader=StageRecordingLoader(recorder),
    )

    with pytest.raises(ContentValidationError):
        pipeline.validate("   ", {"type": "object"}, "Task-T/network-layer/ran-energy-saving/v1")

    assert recorder.events == []


def test_validate_merges_rule_params_first_with_context_precedence() -> None:
    rule_checker = RecordingRuleChecker({"round": "3", "site": "Rule Site"})
    semantic_validator = ScriptedSemanticValidator(
        [_accepted_verdict({"site": "LLM Site", "incident_level": "critical"})]
    )
    pipeline = _build_pipeline(rule_checker, semantic_validator)

    result = pipeline.validate("prompt", {"type": "object"}, "reference", "TEMPLATE")

    assert isinstance(result, FilledParamData)
    assert result.data == {"round": "3", "site": "Rule Site", "incident_level": "critical"}
    assert list(result.data) == ["round", "site", "incident_level"]


def test_validate_passes_prompt_schema_reference_and_template_to_the_semantic_gate() -> None:
    semantic_validator = ScriptedSemanticValidator([_accepted_verdict({"site": "Site A"})])
    pipeline = _build_pipeline(RecordingRuleChecker(), semantic_validator)
    schema = {"type": "object", "properties": {"site": {"type": "string"}}}

    pipeline.validate("the prompt", schema, "the reference", "the template")

    assert len(semantic_validator.calls) == 1
    call = semantic_validator.calls[0]
    assert call.prompt == "the prompt"
    assert call.schema == schema
    assert call.reference == "the reference"
    assert call.template_content == "the template"


def test_validate_runs_the_rule_gate_exactly_once_per_validation() -> None:
    rule_checker = RecordingRuleChecker({"round": "3"})
    semantic_validator = ScriptedSemanticValidator([_accepted_verdict()])
    pipeline = _build_pipeline(rule_checker, semantic_validator)

    pipeline.validate("prompt", {}, "reference", "TEMPLATE")

    assert rule_checker.prompts == ["prompt"]


def test_validate_resolves_the_template_through_the_loader_gate() -> None:
    loader = RecordingTemplateContentLoader("LOADED TEMPLATE")
    semantic_validator = ScriptedSemanticValidator([_accepted_verdict({"site": "Site A"})])
    pipeline = _build_pipeline(RecordingRuleChecker(), semantic_validator, template_content_loader=loader)

    pipeline.validate("prompt", {}, "Task-T/network-layer/ran-energy-saving/v1")

    assert loader.references == ["Task-T/network-layer/ran-energy-saving/v1"]
    assert semantic_validator.calls[0].template_content == "LOADED TEMPLATE"


def test_validate_prefers_explicit_template_content_over_the_loader() -> None:
    loader = RecordingTemplateContentLoader("LOADED TEMPLATE")
    semantic_validator = ScriptedSemanticValidator([_accepted_verdict()])
    pipeline = _build_pipeline(RecordingRuleChecker(), semantic_validator, template_content_loader=loader)

    pipeline.validate("prompt", {}, "reference", "EXPLICIT TEMPLATE")

    assert loader.references == []
    assert semantic_validator.calls[0].template_content == "EXPLICIT TEMPLATE"


def test_validate_without_loader_or_explicit_content_fails_fast() -> None:
    rule_checker = RecordingRuleChecker()
    semantic_validator = ScriptedSemanticValidator([])
    pipeline = _build_pipeline(rule_checker, semantic_validator)

    with pytest.raises(RuntimeError, match="Template content loader is not configured"):
        pipeline.validate("prompt", {}, "reference")

    # the loader guard runs before the input gate, mirroring Java's
    # IllegalStateException in the three-argument validate variant
    assert rule_checker.prompts == []
    assert semantic_validator.calls == []


@pytest.mark.parametrize(
    ("constructor_kwargs", "missing_argument"),
    [
        (
            {"rule_checker": None, "semantic_validator": ScriptedSemanticValidator([]), "max_attempts": 1},
            "rule_checker",
        ),
        (
            {"rule_checker": RecordingRuleChecker(), "semantic_validator": None, "max_attempts": 1},
            "semantic_validator",
        ),
    ],
)
def test_pipeline_rejects_missing_gate_collaborators(
    constructor_kwargs: dict[str, object], missing_argument: str
) -> None:
    with pytest.raises(TypeError, match=f"{missing_argument} must not be None"):
        ValidationPipeline(**constructor_kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ValidationPipeline: input gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "schema", "reference", "expected_reason"),
    [
        ("", None, "reference", "Parameter schema must not be null."),
        ("   ", None, "reference", "Parameter schema must not be null."),
        (None, {"type": "object"}, "reference", "Prompt must not be null or blank."),
        ("   ", {"type": "object"}, "reference", "Prompt must not be null or blank."),
        ("", {"type": "object"}, "reference", "Prompt must not be null or blank."),
        ("prompt", {"type": "object"}, None, "Template reference must not be null."),
    ],
)
def test_invalid_inputs_fail_with_negotiation_invalid_input(
    prompt: str | None,
    schema: Mapping[str, object] | None,
    reference: str | None,
    expected_reason: str,
) -> None:
    rule_checker = RecordingRuleChecker()
    semantic_validator = ScriptedSemanticValidator([])
    pipeline = _build_pipeline(rule_checker, semantic_validator)

    with pytest.raises(ContentValidationError) as excinfo:
        pipeline.validate(prompt, schema, reference, "TEMPLATE")

    assert excinfo.value.code == NEGOTIATION_INVALID_INPUT_CODE
    assert excinfo.value.errors == [
        SlotValidationError("_input", NEGOTIATION_INVALID_INPUT_CODE, str(excinfo.value), {"reason": expected_reason})
    ]
    assert rule_checker.prompts == []
    assert semantic_validator.calls == []


def test_rule_gate_failure_propagates_untouched() -> None:
    failure = _error("negotiation.rule_violation", "structure violation")
    rule_checker = FailingRuleChecker(failure)
    semantic_validator = ScriptedSemanticValidator([])
    pipeline = _build_pipeline(rule_checker, semantic_validator)

    with pytest.raises(ContentValidationError) as excinfo:
        pipeline.validate("prompt", {}, "reference", "TEMPLATE")

    assert excinfo.value is failure
    assert semantic_validator.calls == []


# ---------------------------------------------------------------------------
# ValidationPipeline: semantic gate
# ---------------------------------------------------------------------------


def test_semantic_rejection_carries_errors_and_partial_params() -> None:
    errors = [SlotValidationError("site", "content.param_missing", "site is missing")]
    semantic_validator = ScriptedSemanticValidator(
        [ValidationResult(verdict=False, errors=errors, params={"site": None, "round": "2"})]
    )
    pipeline = _build_pipeline(RecordingRuleChecker({"round": "2"}), semantic_validator)

    with pytest.raises(ContentValidationError) as excinfo:
        pipeline.validate("prompt", {}, "reference", "TEMPLATE")

    assert excinfo.value.code == "negotiation.semantic_rejected"
    assert excinfo.value.errors == errors
    assert excinfo.value.params == {"site": None, "round": "2"}


@pytest.mark.parametrize("code", RETRYABLE_CODES)
def test_semantic_gate_retries_retryable_codes_to_the_attempt_limit(code: str) -> None:
    failure = _error(code, "persistent failure")
    semantic_validator = ScriptedSemanticValidator([failure, failure, failure])
    pipeline = _build_pipeline(RecordingRuleChecker(), semantic_validator, max_attempts=3)

    with pytest.raises(ContentValidationError) as excinfo:
        pipeline.validate("prompt", {}, "reference", "TEMPLATE")

    assert excinfo.value is failure
    assert excinfo.value.code == code
    assert len(semantic_validator.calls) == 3


@pytest.mark.parametrize("code", NON_RETRYABLE_CODES)
def test_semantic_gate_does_not_retry_non_retryable_codes(code: str) -> None:
    failure = _error(code, "final verdict")
    semantic_validator = ScriptedSemanticValidator([failure])
    pipeline = _build_pipeline(RecordingRuleChecker(), semantic_validator, max_attempts=3)

    with pytest.raises(ContentValidationError) as excinfo:
        pipeline.validate("prompt", {}, "reference", "TEMPLATE")

    assert excinfo.value is failure
    assert len(semantic_validator.calls) == 1


def test_semantic_gate_recovers_after_a_retryable_failure() -> None:
    semantic_validator = ScriptedSemanticValidator(
        [
            _error("llm.invocation_failed", "flaky provider"),
            _accepted_verdict({"site": "Site A"}),
        ]
    )
    pipeline = _build_pipeline(RecordingRuleChecker(), semantic_validator, max_attempts=3)

    result = pipeline.validate("prompt", {}, "reference", "TEMPLATE")

    assert result.data == {"site": "Site A"}
    assert len(semantic_validator.calls) == 2


def test_unexpected_semantic_failure_is_wrapped_as_llm_response_invalid() -> None:
    cause = ValueError("raw LLM garbage")
    semantic_validator = ScriptedSemanticValidator([cause])
    pipeline = _build_pipeline(RecordingRuleChecker(), semantic_validator, max_attempts=3)

    with pytest.raises(ContentValidationError) as excinfo:
        pipeline.validate("prompt", {}, "reference", "TEMPLATE")

    assert excinfo.value.code == "llm.response_invalid"
    assert excinfo.value.__cause__ is cause
    assert excinfo.value.errors[0].slot_name == "_llm"
    assert excinfo.value.errors[0].code == "llm.response_invalid"
    # unexpected failures are never retried
    assert len(semantic_validator.calls) == 1


def test_semantic_step_name_is_stable() -> None:
    # the step name is a diagnostic contract logged on every retry
    assert SEMANTIC_VALIDATION_STEP == "semantic_validation"


# ---------------------------------------------------------------------------
# ValidationPipeline: template loading gate
# ---------------------------------------------------------------------------


def test_loader_resource_not_found_translates_to_template_not_found() -> None:
    missing = ResourceNotFoundError(
        "missing template resource",
        "prompt_resources/templates/Unknown-T/scenario/v1/en-US/template.md",
    )
    loader = FailingTemplateContentLoader(missing)
    semantic_validator = ScriptedSemanticValidator([])
    pipeline = _build_pipeline(RecordingRuleChecker(), semantic_validator, template_content_loader=loader)

    with pytest.raises(ContentValidationError) as excinfo:
        pipeline.validate("prompt", {}, "Unknown-T/scenario/v1")

    assert excinfo.value.code == "template.not_found"
    assert excinfo.value.__cause__ is missing
    assert excinfo.value.facts == {
        "template_uri": "prompt_resources/templates/Unknown-T/scenario/v1/en-US/template.md",
        "language": "en-US",
    }
    assert semantic_validator.calls == []


def test_loader_resource_not_found_uses_the_configured_language() -> None:
    missing = ResourceNotFoundError("missing template resource", "templates/X/v1/zh-CN/template.md")
    loader = FailingTemplateContentLoader(missing)
    semantic_validator = ScriptedSemanticValidator([])
    pipeline = _build_pipeline(
        RecordingRuleChecker(), semantic_validator, language="zh-CN", template_content_loader=loader
    )

    with pytest.raises(ContentValidationError) as excinfo:
        pipeline.validate("prompt", {}, "Unknown-T/scenario/v1")

    assert excinfo.value.facts == {"template_uri": "templates/X/v1/zh-CN/template.md", "language": "zh-CN"}


def test_loader_unexpected_error_propagates_unchanged() -> None:
    failure = OSError("disk on fire")
    loader = FailingTemplateContentLoader(failure)
    semantic_validator = ScriptedSemanticValidator([])
    pipeline = _build_pipeline(RecordingRuleChecker(), semantic_validator, template_content_loader=loader)

    with pytest.raises(OSError) as excinfo:
        pipeline.validate("prompt", {}, "reference")

    assert excinfo.value is failure
    assert semantic_validator.calls == []
