"""Content validation pipeline (port of Java ``ValidationPipeline`` and friends).

Orchestrates the content validation pipeline: input validation, rule-level
gate, retryable semantic validation and deterministic parameter merging.

Stage order: input gate → rule-level gate → (optional) template loading gate →
retryable semantic (LLM) gate → deterministic parameter merging. Parameter
merging writes the context parameters first and the semantically extracted
parameters second; on a key conflict the context parameter wins and a warning is
logged, so the LLM output can never override the rule-level parsed values.

The error catalog, message rendering and the exception tree are consumed from
``a2a_t.core.errors``:
:class:`~a2a_t.core.errors.exceptions.ContentValidationError` carries the
machine-readable catalog code, the rendered message, the structured per-slot
details and the partial extraction params;
:class:`~a2a_t.core.errors.exceptions.ResourceNotFoundError` raised by a
template loader is translated to ``template.not_found`` at this boundary,
mirroring the Java orchestrator catch points.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import (
    ContentValidationError,
    ResourceNotFoundError,
    SlotValidationError,
)
from a2a_t.core.errors.messages import DEFAULT_LANGUAGE, render
from a2a_t.core.template_uri import TemplateUri

__all__ = [
    "ContentValidationError",
    "ContentValidator",
    "FilledParamData",
    "LLM_RESPONSE_INVALID_CODE",
    "NEGOTIATION_INVALID_INPUT_CODE",
    "NEGOTIATION_SEMANTIC_REJECTED_CODE",
    "RETRYABLE_ERROR_CODES",
    "ResourceNotFoundError",
    "RuleChecker",
    "SEMANTIC_VALIDATION_STEP",
    "SemanticValidator",
    "SlotValidationError",
    "TEMPLATE_NOT_FOUND_CODE",
    "TemplateContentLoader",
    "ValidationPipeline",
    "ValidationResult",
    "with_retry",
]

_LOGGER = logging.getLogger(__name__)

#: Internal diagnostic step name of the semantic validation stage.
SEMANTIC_VALIDATION_STEP = "semantic_validation"

#: Input-gate failure code (``ErrorCatalog.NEGOTIATION_INVALID_INPUT``).
NEGOTIATION_INVALID_INPUT_CODE: str = ErrorCatalog.NEGOTIATION_INVALID_INPUT.value

#: Template resolution failure code (``ErrorCatalog.TEMPLATE_NOT_FOUND``).
TEMPLATE_NOT_FOUND_CODE: str = ErrorCatalog.TEMPLATE_NOT_FOUND.value

#: Unexpected semantic-step failure code (``ErrorCatalog.LLM_RESPONSE_INVALID``).
LLM_RESPONSE_INVALID_CODE: str = ErrorCatalog.LLM_RESPONSE_INVALID.value

#: Semantic rejection code (``ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED``).
NEGOTIATION_SEMANTIC_REJECTED_CODE: str = ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED.value

#: Failure codes that the retry helper re-runs, narrowing the Java core
#: ``llm.*`` domain to the exact three-code whitelist of the content layer
#: (port-plan hard constraint): a failure is retried to the attempt limit if
#: and only if its code is in this set, and exhaustion re-raises the original
#: code. Note this is deliberately stricter than an ``llm.`` prefix match —
#: ``llm.not_configured`` is not retryable.
RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        ErrorCatalog.NEGOTIATION_CONTENT_EXTRACT_FAILED.value,
        ErrorCatalog.LLM_INVOCATION_FAILED.value,
        ErrorCatalog.LLM_RESPONSE_INVALID.value,
    }
)

_MISSING_LOADER_MESSAGE = "Template content loader is not configured; provide the template content explicitly."

T = TypeVar("T")
# the addressing type appears only in argument position of the two generic
# protocols, so it must be contravariant (mypy misc: invariant type variable
# used in protocol where contravariant one is expected)
TReference = TypeVar("TReference", contravariant=True)


@dataclass(frozen=True)
class FilledParamData:
    """Filled parameter data produced by the content validation pipeline.

    Attributes:
        data: merged parameter values keyed by parameter name, or the ordered parameter object
            array when the caller schema is array-shaped.
        context: context parameters (``id``/``round``/``maxRounds``) carried alongside
            array-shaped data; ``None`` when the parameters already merge them (object shape).
    """

    data: dict[str, object] | list[object]
    context: dict[str, object] | None = None

    def __post_init__(self) -> None:
        """Defensively copy the parameter map or array, preserving insertion order."""
        if isinstance(self.data, dict):
            object.__setattr__(self, "data", dict(self.data))
        else:
            object.__setattr__(self, "data", list(self.data))
        if self.context is not None:
            object.__setattr__(self, "context", dict(self.context))


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of a semantic validation step in the content validation pipeline.

    Attributes:
        verdict: overall semantic verdict; ``True`` only when every semantic
            constraint holds.
        errors: structured semantic errors; empty when the verdict is ``True``.
        params: parameters extracted from the content per the caller-provided
            schema.
    """

    verdict: bool
    errors: tuple[SlotValidationError, ...] = ()
    params: dict[str, object] | list[object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Defensively copy the errors sequence and the parameter map or array."""
        object.__setattr__(self, "errors", tuple(self.errors))
        if isinstance(self.params, dict):
            object.__setattr__(self, "params", dict(self.params))
        else:
            object.__setattr__(self, "params", list(self.params))


class RuleChecker(Protocol):
    """Deterministic rule-level checker for content.

    The checker is deterministic and never calls an LLM. It validates structural
    constraints of the content and returns context parameters parsed from it.
    """

    def check(self, prompt: str) -> Mapping[str, object]:
        """Run the rule-level check of one content prompt.

        Args:
            prompt: content prompt text.

        Returns:
            context parameters parsed from the content.

        Raises:
            ContentValidationError: if the content violates a structural rule.
        """
        ...


class SemanticValidator(Protocol[TReference]):
    """LLM-backed semantic validator for content.

    The validator performs a single structured LLM call that combines semantic
    validation with parameter extraction. The reference type is generic so each
    caller passes its own template addressing type — for example a
    :class:`~a2a_t.core.template_uri.TemplateUri` for extension content or a
    richer negotiation reference carrying type and phase.
    """

    def validate(
        self,
        prompt: str,
        schema: Mapping[str, object],
        reference: TReference,
        template_content: str,
    ) -> ValidationResult:
        """Validate one content prompt semantically and extract its parameters.

        Args:
            prompt: content prompt text.
            schema: caller-provided parameter JSON schema embedded into the
                structured-call output contract.
            reference: template addressing value the content is validated
                against.
            template_content: loaded template text used as a reference for
                structure/completeness checks.

        Returns:
            semantic validation outcome carrying the verdict, the semantic
            errors and the extracted parameters.
        """
        ...


class TemplateContentLoader(Protocol[TReference]):
    """Loads the template text for a template reference during validation."""

    def load(self, reference: TReference) -> str:
        """Load the template text for the given template reference.

        Args:
            reference: template addressing value to resolve.

        Returns:
            loaded template text.
        """
        ...


class ContentValidator(Protocol):
    """Entry point for validating content and extracting filled parameters."""

    def validate(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: TemplateUri,
    ) -> FilledParamData:
        """Validate one content prompt and extract its filled parameters.

        Args:
            prompt: content prompt text.
            schema: caller-provided parameter JSON schema.
            template_uri: URI of the template the content is validated against,
                such as ``Task-T/network-layer/ran-energy-saving/v1``.

        Returns:
            filled parameter data carrying the merged parameters.
        """
        ...


def _error_code_str(error: ContentValidationError) -> str:
    """Return the plain string error code of one content validation failure."""
    code = getattr(error, "code_str", None)
    if isinstance(code, str):
        return code
    raw = getattr(error, "code", None)
    value = getattr(raw, "value", None)
    return value if isinstance(value, str) else str(raw)


def with_retry(max_attempts: int, step: str, action: Callable[[], T]) -> T:
    """Execute one action with retry on the retryable failure codes.

    Only :class:`ContentValidationError` failures carrying one of
    :data:`RETRYABLE_ERROR_CODES` are retried; every other failure code and
    every unknown exception is re-raised immediately. When the attempts are
    exhausted, the original failure is re-raised with its original error code.

    Args:
        max_attempts: maximum number of attempts, must be at least 1.
        step: internal diagnostic step name used in the retry logs.
        action: action to execute.

    Returns:
        the result of the action.

    Raises:
        ValueError: when ``max_attempts`` is below 1.
        ContentValidationError: when the action fails with a non-retryable
            error, or fails on every attempt with a retryable error.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    for attempt in range(1, max_attempts + 1):
        try:
            return action()
        except ContentValidationError as exc:
            code = _error_code_str(exc)
            if code not in RETRYABLE_ERROR_CODES:
                raise
            if attempt == max_attempts:
                _LOGGER.warning("%s_retry_exhausted attempts=%s last_error=%s", step, attempt, code)
                raise
            _LOGGER.warning("%s_retry_attempt attempt=%s/%s error=%s", step, attempt, max_attempts, code)
    raise RuntimeError("unreachable")  # pragma: no cover


def _merge_params(
    context_params: Mapping[str, object],
    semantic_params: Mapping[str, object],
) -> dict[str, object]:
    """Merge rule-gate context params with semantic params; context wins on conflict."""
    merged: dict[str, object] = dict(context_params)
    for key, value in semantic_params.items():
        if key in merged:
            _LOGGER.warning("content_param_merge_conflict key=%s resolution=context_param_wins", key)
            continue
        merged[key] = value
    return merged


class ValidationPipeline(Generic[T]):
    """Orchestrates the content validation pipeline for one addressing type.

    The pipeline chains the input gate, the deterministic rule-level gate, the
    optional template loading gate, the retryable LLM-backed semantic gate and
    the deterministic parameter merge. Rule-gate context parameters always win
    over semantically extracted parameters on a key conflict.
    """

    def __init__(
        self,
        *,
        rule_checker: RuleChecker,
        semantic_validator: SemanticValidator[T],
        max_attempts: int,
        language: str | None = None,
        template_content_loader: TemplateContentLoader[T] | None = None,
    ) -> None:
        """Create a validation pipeline with an optional template loading gate.

        Args:
            rule_checker: rule-level checker used as the entry gate.
            semantic_validator: LLM-backed semantic validator producing the
                semantic verdict and extracted parameters.
            max_attempts: maximum number of retry attempts for the semantic
                validation step.
            language: language used to render failure messages, for example
                ``zh-CN``; ``None`` falls back to ``en-US``.
            template_content_loader: optional loader resolving the template
                body; when absent only the preloaded
                :meth:`validate` variant with explicit template content is
                available.

        Raises:
            TypeError: when the rule checker or the semantic validator is
                ``None``.
        """
        if rule_checker is None:
            raise TypeError("rule_checker must not be None")
        if semantic_validator is None:
            raise TypeError("semantic_validator must not be None")
        self._rule_checker = rule_checker
        self._semantic_validator = semantic_validator
        self._max_attempts = max_attempts
        self._language = language
        self._template_content_loader = template_content_loader

    def validate(
        self,
        prompt: str | None,
        schema: Mapping[str, object],
        reference: T,
        template_content: str | None = None,
    ) -> FilledParamData:
        """Validate one content prompt and extract its filled parameters.

        When ``template_content`` is ``None`` the template body is resolved
        through the injected template loading gate (which must then be
        configured); otherwise the provided body is used directly.

        Args:
            prompt: content prompt text.
            schema: caller-provided parameter JSON schema.
            reference: template addressing value the content is validated
                against.
            template_content: loaded template text used as a reference for
                structure/completeness checks; ``None`` resolves it through the
                loader.

        Returns:
            filled parameter data carrying the merged parameters.

        Raises:
            RuntimeError: when ``template_content`` is ``None`` and no template
                content loader was injected into this pipeline.
            ContentValidationError: with ``negotiation.invalid_input`` if the
                prompt is ``None`` or blank, the schema is ``None``, or the
                reference is ``None``; or if the validation fails at any stage.
        """
        if template_content is None and self._template_content_loader is None:
            raise RuntimeError(_MISSING_LOADER_MESSAGE)
        validated_prompt, context_params = self._validate_inputs_and_run_rule_gate(prompt, schema, reference)
        if template_content is None:
            template_content = self._load_template_content(reference)
        return self._run_semantic_validation_and_merge(
            validated_prompt, schema, reference, context_params, template_content
        )

    def _validate_inputs_and_run_rule_gate(
        self,
        prompt: str | None,
        schema: Mapping[str, object],
        reference: T,
    ) -> tuple[str, Mapping[str, object]]:
        """Run the input gate, then the deterministic rule-level gate.

        Returns:
            the validated prompt (never ``None``) and the rule-gate context
            parameters.
        """
        if schema is None:
            raise self._invalid_input("Parameter schema must not be null.")
        if prompt is None or prompt.strip() == "":
            raise self._invalid_input("Prompt must not be null or blank.")
        if reference is None:
            raise self._invalid_input("Template reference must not be null.")
        return prompt, self._rule_checker.check(prompt)

    def _load_template_content(self, reference: T) -> str:
        """Resolve the template body through the injected loader gate."""
        loader = self._template_content_loader
        if loader is None:
            raise RuntimeError(_MISSING_LOADER_MESSAGE)
        try:
            return loader.load(reference)
        except ResourceNotFoundError as exc:
            raise self._template_not_found(exc) from exc

    def _run_semantic_validation_and_merge(
        self,
        prompt: str,
        schema: Mapping[str, object],
        reference: T,
        context_params: Mapping[str, object],
        template_content: str,
    ) -> FilledParamData:
        """Run the retryable semantic gate, then merge the extracted parameters."""
        try:
            semantic_result = with_retry(
                self._max_attempts,
                SEMANTIC_VALIDATION_STEP,
                lambda: self._semantic_validator.validate(prompt, schema, reference, template_content),
            )
        except ResourceNotFoundError as exc:
            raise self._template_not_found(exc) from exc
        except ContentValidationError:
            # already carries a final catalog code emitted by the semantic validator
            raise
        except Exception as exc:
            raise self._llm_response_invalid(exc) from exc

        if not semantic_result.verdict:
            raise ContentValidationError(
                ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED,
                language=self._language,
                errors=semantic_result.errors,
                params=semantic_result.params,
            )

        if isinstance(semantic_result.params, list):
            # Array-shaped caller schema: the context parameters cannot merge into the ordered
            # object array, so they travel on the dedicated context field instead.
            filled_param_data = FilledParamData(
                list(semantic_result.params),
                context=dict(context_params) if context_params else None,
            )
            _LOGGER.info("content_validation_completed param_count=%s", len(filled_param_data.data))
            return filled_param_data

        merged = _merge_params(context_params, semantic_result.params)
        filled_param_data = FilledParamData(merged)
        _LOGGER.info("content_validation_completed param_keys=%s", list(filled_param_data.data))
        return filled_param_data

    def _invalid_input(self, reason: str) -> ContentValidationError:
        """Build the ``negotiation.invalid_input`` failure for one gate reason."""
        facts = {"reason": reason}
        message = render(ErrorCatalog.NEGOTIATION_INVALID_INPUT, facts, self._language)
        return ContentValidationError(
            ErrorCatalog.NEGOTIATION_INVALID_INPUT,
            facts,
            language=self._language,
            message=message,
            errors=[SlotValidationError("_input", NEGOTIATION_INVALID_INPUT_CODE, message, facts)],
        )

    def _template_not_found(self, exception: ResourceNotFoundError) -> ContentValidationError:
        """Build the ``template.not_found`` failure from a resource lookup error."""
        effective_language = self._language or DEFAULT_LANGUAGE
        facts = {"template_uri": exception.resource_path, "language": effective_language}
        return ContentValidationError(
            ErrorCatalog.TEMPLATE_NOT_FOUND,
            facts,
            language=self._language,
            cause=exception,
        )

    def _llm_response_invalid(self, cause: BaseException) -> ContentValidationError:
        """Build the ``llm.response_invalid`` failure wrapping an unexpected error."""
        facts = {"step": SEMANTIC_VALIDATION_STEP}
        message = render(ErrorCatalog.LLM_RESPONSE_INVALID, facts, self._language)
        return ContentValidationError(
            ErrorCatalog.LLM_RESPONSE_INVALID,
            facts,
            language=self._language,
            message=message,
            errors=[SlotValidationError("_llm", LLM_RESPONSE_INVALID_CODE, message, facts)],
            cause=cause,
        )
