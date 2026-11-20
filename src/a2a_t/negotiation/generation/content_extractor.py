"""LLM-backed content extraction of the from-text negotiation generation leg.

Port of Java ``DefaultNegotiationContentExtractor`` plus the retry policy Java owns in
``NegotiationGenerationOrchestrator.withRetry``: the extractor loads the system and user prompts of
the addressed negotiation category, asks the LLM for the snake_case JSON described by the
extraction schema, and maps the response onto the typed content records of
:mod:`a2a_t.negotiation.content`.

The retry loop owns the whole from-text extraction step. A step failing with one of the retryable
codes ``negotiation.content_extract_failed``, ``llm.invocation_failed`` or ``llm.response_invalid``
is re-run up to the configured attempt limit; any other code is re-raised immediately, and the
exhaustion failure re-raises the original error code with its original facts. The whitelist is the
single :data:`a2a_t.core.validation_pipeline.RETRYABLE_ERROR_CODES` set the core retry helper
narrowed; the loop itself is local because the core helper retries
:class:`~a2a_t.core.errors.exceptions.ContentValidationError` while this step fails with
:class:`~a2a_t.core.errors.exceptions.NegotiationGenerationError`.

Failure mapping (Java parity): a missing LLM client maps to ``llm.not_configured``, transport
failures map to ``llm.invocation_failed``, responses that violate the response contract (blank or
not a JSON object) map to the retryable ``llm.response_invalid``, responses that cannot be mapped
onto the expected content map to the retryable ``negotiation.content_extract_failed``, missing
required fields map to the non-retryable ``negotiation.field_missing``, a conclusion contradicting
the addressed performative maps to ``negotiation.conclusion_mismatch``, an oversized input text
maps to ``input.text_too_long`` (the third D5 access point, checked before any LLM call), and
content that contradicts the addressed performative or action maps to ``negotiation.invalid_input``
— the confirm-request mutual exclusion through the shared
:func:`a2a_t.negotiation.content.confirm_request.validate_confirm_request` function (D14). A lookup
miss of the extraction prompts of the addressed category and language is translated to
``template.not_found`` (the Java orchestrator's ``extractContent`` catch point).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import Final, Protocol, TypeVar

from a2a_t.config.models import DEFAULT_LLM_MAX_ATTEMPTS
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError, NegotiationGenerationError, ResourceNotFoundError
from a2a_t.core.errors.input_limit import InputLimitConfig
from a2a_t.core.metadata import NegotiationPerformative
from a2a_t.core.validation_pipeline import RETRYABLE_ERROR_CODES
from a2a_t.llm.provider import LLMClient

from ..content.confirm_request import has_items, present_text, validate_confirm_request
from ..content.enums import NegotiationAction, NegotiationConclusion, NegotiationType
from ..content.models import (
    FeasibilityEndingContent,
    FeasibilityProposeContent,
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationContent,
    NegotiationItem,
    TargetEndingContent,
    TargetProposeContent,
)
from ..resources.reference import NegotiationReference
from .json_schema_builder import build_extraction_schema
from .message_builder import TOKEN_INPUT, TOKEN_PHASE, build_messages

__all__ = ["DefaultNegotiationContentExtractor", "NegotiationContentExtractor"]

_LOGGER = logging.getLogger(__name__)

#: Internal diagnostic step name of the retry logs (Java ``STEP_CONTENT_EXTRACT``).
_CONTENT_EXTRACT_STEP: Final[str] = "negotiation_content_extract"

#: Prompt category of the information negotiation extraction (Java ``CATEGORY_INFORMATION``).
_CATEGORY_INFORMATION: Final[str] = "information_negotiation"

#: Prompt category of the target negotiation extraction (Java ``CATEGORY_TARGET``).
_CATEGORY_TARGET: Final[str] = "target_negotiation"

#: Prompt category of the feasibility negotiation extraction (Java ``CATEGORY_FEASIBILITY``).
_CATEGORY_FEASIBILITY: Final[str] = "feasibility_negotiation"

#: Prompt category of the type-independent abort extraction (Java ``CATEGORY_ABORT``).
_CATEGORY_ABORT: Final[str] = "abort_negotiation"

#: ``step`` fact of the response-contract failures, English (Java ``EXTRACTION_STEP_EN``).
_EXTRACTION_STEP_EN: Final[str] = "negotiation content extraction"

#: ``step`` fact of the response-contract failures, zh-CN (Java ``EXTRACTION_STEP_ZH``).
_EXTRACTION_STEP_ZH: Final[str] = "协商内容提取"

#: Signature of the message assembly collaborator (the sibling ``build_messages`` function).
MessageBuilder = Callable[[str, str, Mapping[str, str | None] | None], list[dict[str, str]]]

T = TypeVar("T")


class NegotiationContentExtractor(Protocol):
    """Extracts typed negotiation content from a free-text input.

    Implementations decide how the text is understood; the returned content must already satisfy
    the generator input rules so the deterministic rendering step can consume it directly.
    """

    def extract(self, text: str | None, reference: NegotiationReference) -> NegotiationContent:
        """Extract the typed content of one negotiation message from free text.

        Args:
            text: free-text input describing the message content.
            reference: reference identifying the negotiation type, performative and language to
                extract for.

        Returns:
            typed negotiation content matching the reference.

        Raises:
            NegotiationGenerationError: with one of the codes ``llm.not_configured``,
                ``llm.invocation_failed``, ``llm.response_invalid``,
                ``negotiation.content_extract_failed``, ``negotiation.field_missing``,
                ``negotiation.conclusion_mismatch``, ``negotiation.invalid_input`` or
                ``input.text_too_long``.
        """
        ...


class DefaultNegotiationContentExtractor:
    """Default content extractor backed by one structured LLM call per attempt.

    The LLM client is injected through the :class:`~a2a_t.llm.provider.LLMClient` protocol and is
    never looked up globally; ``None`` keeps the LLM-backed steps unavailable and fails with
    ``llm.not_configured`` at call time. The message assembly defaults to the sibling
    :func:`~a2a_t.negotiation.generation.message_builder.build_messages` (packaged prompts) and the
    extraction schema to :func:`~a2a_t.negotiation.generation.json_schema_builder.build_extraction_schema`.
    """

    def __init__(
        self,
        llm_client: LLMClient | None,
        message_builder: MessageBuilder = build_messages,
        *,
        max_attempts: int = DEFAULT_LLM_MAX_ATTEMPTS,
        input_limit: InputLimitConfig | None = None,
    ) -> None:
        """Create an extractor backed by one LLM client.

        Args:
            llm_client: LLM client used for the structured content extraction call; ``None`` fails
                with ``llm.not_configured`` at call time.
            message_builder: collaborator assembling the system and user messages of the extraction
                step; defaults to the packaged-prompt builder.
            max_attempts: maximum number of attempts of the retryable extraction step, from the
                ``A2AT_LLM_MAX_ATTEMPTS`` configuration key (default per Java: 3).
            input_limit: input limit guarding the free-text entry; ``None`` uses the default
                ``InputLimitConfig`` (16384 characters).

        Raises:
            ValueError: when ``max_attempts`` is below 1.
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._llm_client = llm_client
        self._message_builder = message_builder
        self._max_attempts = max_attempts
        self._input_limit = InputLimitConfig() if input_limit is None else input_limit

    @property
    def max_attempts(self) -> int:
        """Maximum number of attempts of the retryable extraction step."""
        return self._max_attempts

    def extract(self, text: str | None, reference: NegotiationReference) -> NegotiationContent:
        """Extract the typed content of one negotiation message from free text.

        The input-length gate (the third D5 access point) checks the raw text before any LLM call,
        so an oversized input fails fast with zero LLM calls. A resource lookup miss of the
        extraction prompts is translated to ``template.not_found`` (the Java orchestrator's
        ``extractContent`` catch point).

        Args:
            text: free-text input describing the message content; blank fails with
                ``negotiation.invalid_input``.
            reference: reference identifying the negotiation type, performative and language to
                extract for.

        Returns:
            typed negotiation content matching the reference.

        Raises:
            TypeError: when the reference is ``None`` (Java ``NullPointerException`` parity).
            NegotiationGenerationError: with one of the codes ``llm.not_configured``,
                ``llm.invocation_failed``, ``llm.response_invalid``,
                ``negotiation.content_extract_failed``, ``negotiation.field_missing``,
                ``negotiation.conclusion_mismatch``, ``negotiation.invalid_input``,
                ``input.text_too_long`` or ``template.not_found``.
        """
        if reference is None:
            raise TypeError("Negotiation reference must not be null.")
        if self._input_limit.is_too_long(text):
            raise NegotiationGenerationError(
                ErrorCatalog.INPUT_TEXT_TOO_LONG,
                self._input_limit.too_long_facts(text),
                language=reference.language,
            )
        if text is None or not text.strip():
            raise _invalid_input(reference.language, "Input text for negotiation content extraction must not be blank.")
        try:
            return _with_retry(self._max_attempts, _CONTENT_EXTRACT_STEP, lambda: self._extract_once(text, reference))
        except A2ATError as error:
            if _is_prompt_resource_miss(error):
                raise _template_not_found(reference, error) from error
            raise

    def _extract_once(self, text: str, reference: NegotiationReference) -> NegotiationContent:
        """Run the single-attempt extraction: build messages, invoke the LLM, map the content."""
        tokens: dict[str, str | None] = {
            TOKEN_PHASE: _phase_token(reference.performative),
            TOKEN_INPUT: text,
        }
        messages = self._message_builder(_prompt_category(reference.type), reference.language, tokens)
        schema = build_extraction_schema(reference.type, reference.performative)
        payload = self._invoke_llm(messages, schema, reference.language)
        content = _map_content(payload, reference.type, reference.performative, reference.language)
        _LOGGER.info(
            "negotiation_content_extraction_completed type=%s performative=%s",
            reference.type,
            reference.performative,
        )
        return content

    def _invoke_llm(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, object],
        language: str,
    ) -> dict[str, object]:
        """Invoke the LLM once and parse its response into the payload mapping.

        Raises:
            NegotiationGenerationError: with ``llm.not_configured`` when no client is injected,
                ``llm.invocation_failed`` when the client raises, or ``llm.response_invalid`` when
                the response is blank or is not a JSON object.
        """
        if self._llm_client is None:
            raise NegotiationGenerationError(ErrorCatalog.LLM_NOT_CONFIGURED, language=language)
        try:
            response = self._llm_client.structured(
                messages=messages, json_schema=schema, temperature=None, max_tokens=None
            )
        except Exception as error:
            raise NegotiationGenerationError(
                ErrorCatalog.LLM_INVOCATION_FAILED,
                {"provider": type(self._llm_client).__name__, "reason": str(error)},
                language=language,
                cause=error,
            ) from error
        content = None if response is None else response.content
        if content is None or not content.strip():
            raise _response_invalid(language, "Negotiation content extraction returned an empty response.")
        try:
            parsed = json.loads(content)
        except ValueError as error:
            raise _response_invalid(
                language, "Negotiation content extraction response is not a JSON object."
            ) from error
        if not isinstance(parsed, dict):
            raise _response_invalid(language, "Negotiation content extraction response is not a JSON object.")
        return parsed


def _with_retry(max_attempts: int, step: str, action: Callable[[], T]) -> T:
    """Run one LLM step with the retry policy of the negotiation content layer.

    Mirrors the semantics of :func:`a2a_t.core.validation_pipeline.with_retry` for negotiation
    generation failures: a failure carrying a retryable code is re-run up to the attempt limit, a
    failure carrying any other code is re-raised immediately, and the exhaustion failure re-raises
    the original error with its original code and facts.

    Args:
        max_attempts: maximum number of attempts, must be at least 1.
        step: internal diagnostic step name used in the retry logs.
        action: action to execute.

    Returns:
        the result of the action.

    Raises:
        ValueError: when ``max_attempts`` is below 1.
        NegotiationGenerationError: when the action fails with a non-retryable error, or fails on
            every attempt with a retryable error.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    for attempt in range(1, max_attempts + 1):
        try:
            return action()
        except NegotiationGenerationError as error:
            code = error.code_str
            if code not in RETRYABLE_ERROR_CODES:
                raise
            if attempt == max_attempts:
                _LOGGER.warning(
                    "negotiation_llm_retry_exhausted step=%s max_attempts=%s code=%s", step, max_attempts, code
                )
                raise
            _LOGGER.warning(
                "negotiation_llm_retry step=%s attempt=%s max_attempts=%s code=%s", step, attempt, max_attempts, code
            )
    raise RuntimeError("unreachable")  # pragma: no cover


def _map_content(
    payload: dict[str, object],
    negotiation_type: NegotiationType | None,
    performative: NegotiationPerformative,
    language: str,
) -> NegotiationContent:
    """Map one parsed extraction payload onto the typed content of the addressed pair."""
    if performative is NegotiationPerformative.ABORT:
        return NegotiationAbortContent(_required_string(payload, "termination_reason", language))
    if performative is NegotiationPerformative.PROPOSE:
        return _map_propose_content(payload, negotiation_type, language)
    return _map_ending_content(payload, negotiation_type, performative, language)


def _map_propose_content(
    payload: dict[str, object],
    negotiation_type: NegotiationType | None,
    language: str,
) -> NegotiationContent:
    """Map one propose extraction payload onto the typed propose content of the type."""
    if negotiation_type is NegotiationType.INFORMATION:
        return InformationProposeContent(
            _required_propose_items(
                payload, "items", "information negotiation requested items", language
            ),
            _optional_string(payload, "relationship", language),
        )
    if negotiation_type is NegotiationType.TARGET:
        return _map_target_propose_content(payload, language)
    if negotiation_type is NegotiationType.FEASIBILITY:
        return _map_feasibility_propose_content(payload, language)
    raise TypeError(f"Unsupported negotiation type of a propose extraction: {negotiation_type!r}")


def _map_target_propose_content(payload: dict[str, object], language: str) -> NegotiationContent:
    """Map one target propose extraction payload, validating the confirm-request round (D14)."""
    description = _required_string(payload, "target_negotiation_description", language)
    intent_understanding = _optional_items(payload, "intent_understanding", language)
    alignment_and_clarification = _optional_items(payload, "alignment_and_clarification", language)
    request_for_clarification = _optional_items(payload, "request_for_clarification", language)
    confirm_request = _optional_string(payload, "target_confirm_request", language)
    validate_confirm_request(
        label="Target",
        confirm_request=confirm_request,
        conditional_sections={
            "intent understanding": intent_understanding,
            "alignment and clarification": alignment_and_clarification,
            "clarification request": request_for_clarification,
        },
        language=language,
        style="extracted",
    )
    return TargetProposeContent(
        description, intent_understanding, alignment_and_clarification, request_for_clarification, confirm_request
    )


def _map_feasibility_propose_content(payload: dict[str, object], language: str) -> NegotiationContent:
    """Map one feasibility propose extraction payload, validating the action-driven sections."""
    description = _required_string(payload, "feasibility_negotiation_description", language)
    action = _feasibility_action(payload, language)
    contents_to_evaluate = _optional_items(payload, "contents_to_evaluate", language)
    infeasibility_details = _optional_items(payload, "infeasibility_details_and_proposal", language)
    confirm_request = _optional_string(payload, "feasibility_confirm_request", language)
    if present_text(confirm_request) is None:
        if action is NegotiationAction.REQUEST_FEASIBILITY_EVALUATION:
            if not has_items(contents_to_evaluate):
                raise _invalid_input(
                    language,
                    "Feasibility evaluation request extracted no contents to evaluate; the driven "
                    "section would be empty.",
                )
        elif not has_items(infeasibility_details):
            raise _invalid_input(
                language,
                "Alternative proposal on failure extracted no infeasibility details; the driven "
                "section would be empty.",
            )
    else:
        validate_confirm_request(
            label="Feasibility",
            confirm_request=confirm_request,
            conditional_sections={
                "contents to evaluate": contents_to_evaluate,
                "infeasibility details and proposal": infeasibility_details,
            },
            action=action,
            confirm_action=NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
            language=language,
            style="extracted",
        )
    return FeasibilityProposeContent(description, action, contents_to_evaluate, infeasibility_details, confirm_request)


def _map_ending_content(
    payload: dict[str, object],
    negotiation_type: NegotiationType | None,
    performative: NegotiationPerformative,
    language: str,
) -> NegotiationContent:
    """Map one terminal extraction payload onto the typed ending content of the type."""
    conclusion = _required_conclusion(payload, language)
    _require_conclusion_matches_phase(conclusion, performative, language)
    if negotiation_type is NegotiationType.INFORMATION:
        return InformationEndingContent(
            conclusion,
            _required_non_empty_items(payload, "items", "information negotiation result content", language),
        )
    if negotiation_type is NegotiationType.TARGET:
        return _map_target_ending_content(payload, conclusion, language)
    if negotiation_type is NegotiationType.FEASIBILITY:
        return FeasibilityEndingContent(conclusion, _required_string(payload, "feasibility_summary", language))
    raise TypeError(f"Unsupported negotiation type of a terminal extraction: {negotiation_type!r}")


def _map_target_ending_content(
    payload: dict[str, object], conclusion: NegotiationConclusion, language: str
) -> NegotiationContent:
    """Map one target terminal extraction payload, requiring the conclusion-driven field."""
    confirmed_intent = _optional_string(payload, "confirmed_intent", language)
    failure_reason = _optional_string(payload, "failure_reason", language)
    if conclusion is NegotiationConclusion.ACCEPT and (confirmed_intent is None or not confirmed_intent.strip()):
        raise _slot_missing(language, "confirmed_intent")
    if conclusion is NegotiationConclusion.REJECT and (failure_reason is None or not failure_reason.strip()):
        raise _slot_missing(language, "failure_reason")
    return TargetEndingContent(conclusion, confirmed_intent, failure_reason)


def _require_conclusion_matches_phase(
    conclusion: NegotiationConclusion, performative: NegotiationPerformative, language: str
) -> None:
    """Reject a conclusion that contradicts the addressed performative."""
    if performative is NegotiationPerformative.ACCEPT:
        expected = NegotiationConclusion.ACCEPT
    else:
        expected = NegotiationConclusion.REJECT
    if conclusion is not expected:
        raise NegotiationGenerationError(
            ErrorCatalog.NEGOTIATION_CONCLUSION_MISMATCH,
            {"expected": expected.literal, "actual": conclusion.literal},
            language=language,
        )


def _feasibility_action(payload: dict[str, object], language: str) -> NegotiationAction:
    """Read the feasibility action of one propose extraction payload."""
    value = payload.get("action")
    if value is None:
        raise _invalid_input(
            language,
            "Feasibility negotiation content extraction produced no action; the action drives the "
            "conditional sections of the message.",
        )
    if isinstance(value, str):
        for candidate in NegotiationAction:
            if candidate.name == value:
                return candidate
    raise _extract_failed(language, "action", "Feasibility negotiation action must be one of the two action names")


def _required_conclusion(payload: dict[str, object], language: str) -> NegotiationConclusion:
    """Read the terminal conclusion of one terminal extraction payload."""
    value = payload.get("conclusion")
    if value is None:
        raise _slot_missing(language, "conclusion")
    if isinstance(value, str):
        for candidate in NegotiationConclusion:
            if candidate.literal == value:
                return candidate
    raise _extract_failed(language, "conclusion", "Negotiation conclusion must be Accept or Reject")


def _required_string(payload: dict[str, object], field: str, language: str) -> str:
    """Read one mandatory string field of an extraction payload."""
    value = payload.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _slot_missing(language, field)
    if isinstance(value, str):
        return value
    raise _extract_failed(language, field, f"Field {field} must be a string")


def _optional_string(payload: dict[str, object], field: str, language: str) -> str | None:
    """Read one optional string field of an extraction payload."""
    value = payload.get(field)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise _extract_failed(language, field, f"Field {field} must be a string or null")


def _required_items(payload: dict[str, object], field: str, language: str) -> list[NegotiationItem]:
    """Read one mandatory item-array field of an extraction payload."""
    value = payload.get(field)
    if value is None:
        raise _slot_missing(language, field)
    return _items_of(value, field, language)


def _required_propose_items(
    payload: dict[str, object], field: str, description: str, language: str
) -> list[NegotiationItem]:
    """Read the non-empty item array of an information *propose* extraction.

    An empty result is an extraction miss, not a client-side field omission: the model failed to
    copy the items from the inbound proposal. Classifying it as ``content_extract_failed`` lets
    the pipeline retry the extraction instead of aborting the negotiation on the non-retryable
    ``field_missing`` code.
    """
    items = _required_items(payload, field, language)
    if not items:
        raise _extract_failed(
            language,
            description,
            "the extraction returned no items for the inbound information proposal",
        )
    return items


def _required_non_empty_items(
    payload: dict[str, object], field: str, description: str, language: str
) -> list[NegotiationItem]:
    """Read one mandatory non-empty item-array field of an extraction payload."""
    items = _required_items(payload, field, language)
    if not items:
        raise _slot_missing(language, description)
    return items


def _optional_items(payload: dict[str, object], field: str, language: str) -> list[NegotiationItem] | None:
    """Read one optional item-array field of an extraction payload."""
    value = payload.get(field)
    if value is None:
        return None
    return _items_of(value, field, language)


def _items_of(value: object, field: str, language: str) -> list[NegotiationItem]:
    """Map one raw JSON array onto negotiation items."""
    if not isinstance(value, list):
        raise _extract_failed(language, field, f"Field {field} must be an array of items")
    items: list[NegotiationItem] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise _extract_failed(language, field, f"Field {field} must contain item objects")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise _extract_failed(language, field, f"Field {field} contained an item without a name")
        item_value = entry.get("value")
        if item_value is not None and not isinstance(item_value, str):
            raise _extract_failed(language, field, f"Field {field} contained an item whose value is not a string")
        items.append(NegotiationItem(name, item_value))
    return items


def _phase_token(performative: NegotiationPerformative) -> str:
    """Return the lower-case phase token the user prompt carries for the performative."""
    return performative.name.lower()


def _prompt_category(negotiation_type: NegotiationType | None) -> str:
    """Return the prompt category directory of the extraction of the negotiation type."""
    if negotiation_type is None:
        return _CATEGORY_ABORT
    if negotiation_type is NegotiationType.INFORMATION:
        return _CATEGORY_INFORMATION
    if negotiation_type is NegotiationType.TARGET:
        return _CATEGORY_TARGET
    return _CATEGORY_FEASIBILITY


def _slot_missing(language: str, field: str) -> NegotiationGenerationError:
    """Create the ``negotiation.field_missing`` failure for one missing field."""
    return NegotiationGenerationError(ErrorCatalog.NEGOTIATION_FIELD_MISSING, {"field": field}, language=language)


def _extract_failed(language: str, field: str, reason: str) -> NegotiationGenerationError:
    """Create the retryable ``negotiation.content_extract_failed`` failure for one field."""
    return NegotiationGenerationError(
        ErrorCatalog.NEGOTIATION_CONTENT_EXTRACT_FAILED, {"field": field, "reason": reason}, language=language
    )


def _invalid_input(language: str, reason: str) -> NegotiationGenerationError:
    """Create the ``negotiation.invalid_input`` failure for one reason sentence."""
    return NegotiationGenerationError(ErrorCatalog.NEGOTIATION_INVALID_INPUT, {"reason": reason}, language=language)


def _response_invalid(language: str, detail: str) -> NegotiationGenerationError:
    """Create the retryable ``llm.response_invalid`` failure for one response-contract violation."""
    _LOGGER.warning("negotiation_content_extraction_response_invalid detail=%s", detail)
    return NegotiationGenerationError(
        ErrorCatalog.LLM_RESPONSE_INVALID, {"step": _extraction_step_label(language)}, language=language
    )


def _extraction_step_label(language: str) -> str:
    """Return the ``step`` fact label in the reference language."""
    return _EXTRACTION_STEP_ZH if language is not None and language.startswith("zh") else _EXTRACTION_STEP_EN


def _is_prompt_resource_miss(error: A2ATError) -> bool:
    """Report whether one failure is a lookup miss of the extraction prompt resources.

    The common access layer surfaces a missing packaged prompt as ``infra.resource_read_failed``
    (its replacement of the Java ``ResourceNotFoundException``); an injected message builder may
    also raise the raw :class:`~a2a_t.core.errors.exceptions.ResourceNotFoundError`. Both mean the
    same at this boundary — the extraction prompts of the addressed category and language do not
    exist — which the Java orchestrator's ``extractContent`` catch point translates to
    ``template.not_found``.
    """
    return isinstance(error, ResourceNotFoundError) or error.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED


def _template_not_found(reference: NegotiationReference, cause: A2ATError) -> NegotiationGenerationError:
    """Create the ``template.not_found`` failure from one extraction resource lookup miss."""
    return NegotiationGenerationError(
        ErrorCatalog.TEMPLATE_NOT_FOUND,
        {"template_uri": reference.uri, "language": reference.language},
        language=reference.language,
        cause=cause,
    )
