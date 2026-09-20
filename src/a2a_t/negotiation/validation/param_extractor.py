"""Validation of a negotiation message and extraction of its parameters (port of Java
``ParamExtractor``).

The extractor delegates to the shared :class:`~a2a_t.core.validation_pipeline.ValidationPipeline`
from the core module, which runs the four stages in one pass:

==========  =====================================================================
stage       behavior
==========  =====================================================================
input gate  the prompt must be non-blank and the schema non-``None``, else
            ``negotiation.invalid_input`` (the 16384-character length gate runs one
            level higher, in the orchestrator, before the pipeline is entered)
rule gate   a per-call :class:`~a2a_t.negotiation.validation.compliance_checker.NegotiationRuleCheckerAdapter`
            bridging the negotiation context carried alongside the message to the core rule
            checker contract (UUID shape and round budget)
template    the injected :class:`NegotiationTemplateContentLoader` resolves the template body
loading     after the rule gate and before semantic validation, so the template is never
            preloaded by the caller
semantic    the retryable LLM semantic gate with the constant four-key output contract
validation  (see :mod:`a2a_t.negotiation.validation.semantic_validator`)
merge       the deterministic parameter merge — context parameters first, context wins on
            conflict
==========  =====================================================================

The pipeline emits the final catalog codes with messages rendered in the language of the negotiation
reference; the extractor adds no code mapping of its own beyond narrowing the core
:class:`~a2a_t.core.errors.exceptions.ContentValidationError` to the negotiation-parameter-extraction
failure type, reconstructing the structured facts from the first slot detail that carries them so
callers keep the rendered message and gain the facts map.
"""

from __future__ import annotations

from collections.abc import Mapping

from a2a_t.common.prompt_resources.resource_access import PromptResourceAccess
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import (
    A2ATBusinessError,
    ContentValidationError,
    NegotiationParamExtractionError,
    ResourceNotFoundError,
    SlotValidationError,
)
from a2a_t.core.metadata import NegotiationContext
from a2a_t.core.validation_pipeline import (
    FilledParamData,
    SemanticValidator,
    TemplateContentLoader,
    ValidationPipeline,
)

from ..resources.reference import NegotiationReference
from .compliance_checker import NegotiationComplianceChecker, NegotiationRuleCheckerAdapter

__all__ = [
    "NegotiationTemplateContentLoader",
    "ParamExtractor",
]


class NegotiationTemplateContentLoader:
    """Template loading gate resolving the body of one negotiation reference through the access layer.

    The loader is the D31 replacement of the Java builder's inline lambda over the negotiation
    template loader: the template body is the markdown text the common resource access layer serves
    for the reference's template URI and language. A template miss — the raw
    :class:`~a2a_t.core.errors.exceptions.ResourceNotFoundError` of an injected access implementation
    or the common layer's coded ``template.not_found`` / ``template.load_failed`` business failures —
    is translated to a :class:`~a2a_t.core.errors.exceptions.ResourceNotFoundError` carrying the
    template URI, which the shared pipeline maps to ``template.not_found`` (the Java
    ``ValidationPipeline`` catch point).
    """

    def __init__(self, access: PromptResourceAccess) -> None:
        """Create one loader over the given resource access object.

        Args:
            access: resource access object resolving the negotiation templates.

        Raises:
            TypeError: when the access object is ``None`` (Java ``NullPointerException`` parity).
        """
        if access is None:
            raise TypeError("access")
        self._access = access

    def load(self, reference: NegotiationReference) -> str:
        """Load the template text addressed by one reference.

        Args:
            reference: reference addressing the template to load.

        Returns:
            the template markdown text.

        Raises:
            a2a_t.core.errors.exceptions.ResourceNotFoundError: when the addressed template does
                not exist (translated for the pipeline's ``template.not_found`` mapping).
        """
        try:
            return self._access.template_text(reference.template_uri, reference.language)
        except ResourceNotFoundError as error:
            raise ResourceNotFoundError(str(error), reference.uri) from error
        except A2ATBusinessError as error:
            if error.code not in (ErrorCatalog.TEMPLATE_NOT_FOUND, ErrorCatalog.TEMPLATE_LOAD_FAILED):
                raise
            raise ResourceNotFoundError(str(error), reference.uri) from error


class ParamExtractor:
    """Orchestrates the validation of a negotiation message and the extraction of its parameters.

    Implements the :class:`~a2a_t.negotiation.generation.orchestrator.NegotiationParamExtractor`
    collaboration seam of the generation orchestrator (the P5 seam gets its real implementation
    here): every failure surfaces as a
    :class:`~a2a_t.core.errors.exceptions.NegotiationParamExtractionError` carrying its final catalog
    code — ``negotiation.invalid_input``, ``negotiation.rule_violation``,
    ``negotiation.semantic_rejected``, ``llm.not_configured``, ``llm.invocation_failed``,
    ``llm.response_invalid`` or ``template.not_found``.
    """

    def __init__(
        self,
        compliance_checker: NegotiationComplianceChecker,
        semantic_validator: SemanticValidator[NegotiationReference],
        max_attempts: int,
        template_content_loader: TemplateContentLoader[NegotiationReference],
    ) -> None:
        """Create a parameter extractor.

        Args:
            compliance_checker: rule-level checker used as the entry gate.
            semantic_validator: LLM-backed semantic validator producing the semantic verdict and
                extracted parameters.
            max_attempts: maximum number of retry attempts for the semantic validation step.
            template_content_loader: template loading gate resolving the template body after the
                rule gate.

        Raises:
            TypeError: when any collaborator is ``None`` (Java ``NullPointerException`` parity).
        """
        if compliance_checker is None:
            raise TypeError("compliance_checker")
        if semantic_validator is None:
            raise TypeError("semantic_validator")
        if template_content_loader is None:
            raise TypeError("template_content_loader")
        self._compliance_checker = compliance_checker
        self._semantic_validator = semantic_validator
        self._max_attempts = max_attempts
        self._template_content_loader = template_content_loader

    @property
    def max_attempts(self) -> int:
        """Maximum number of retry attempts of the semantic validation step."""
        return self._max_attempts

    def extract(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        reference: NegotiationReference,
    ) -> FilledParamData:
        """Validate one negotiation message and extract its parameters through the full pipeline.

        Args:
            prompt: rendered negotiation message text.
            context: negotiation context carried alongside the message in the A2A-T metadata;
                ``None`` is reported as not being a negotiation message.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            reference: reference the message is validated against.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            NegotiationParamExtractionError: with one of the codes ``negotiation.invalid_input``,
                ``negotiation.rule_violation``, ``negotiation.semantic_rejected``,
                ``llm.not_configured``, ``llm.invocation_failed``, ``llm.response_invalid`` or
                ``template.not_found`` when the validation pipeline fails.
        """
        pipeline = ValidationPipeline(
            rule_checker=NegotiationRuleCheckerAdapter(self._compliance_checker, context, reference.language),
            semantic_validator=self._semantic_validator,
            max_attempts=self._max_attempts,
            language=reference.language,
            template_content_loader=self._template_content_loader,
        )
        try:
            filled = pipeline.validate(prompt, schema, reference)
        except ContentValidationError as failure:
            raise _negotiation_failure(failure) from failure
        _require_unique_param_names(filled.data)
        return filled


def _require_unique_param_names(data: object) -> None:
    """Reject duplicate parameter names in one array-shaped extraction (deterministic, non-LLM).

    An array-shaped caller schema extracts one object per requested item; the same name appearing
    twice is a rule violation of the shared corpus contract, reported deterministically after the
    pipeline instead of being left to the LLM verdict.

    Raises:
        NegotiationParamExtractionError: with ``negotiation.rule_violation`` when two or more
            extracted array items carry the same ``name``.
    """
    if not isinstance(data, list):
        return
    names = [entry["name"] for entry in data if isinstance(entry, Mapping) and isinstance(entry.get("name"), str)]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if not duplicates:
        return
    facts = {"section_label": "params.name", "duplicates": ", ".join(duplicates)}
    message = "Duplicate parameter names in the extracted params: " + ", ".join(duplicates)
    raise NegotiationParamExtractionError(
        ErrorCatalog.NEGOTIATION_RULE_VIOLATION,
        facts,
        message=message,
        errors=[SlotValidationError("params.name", ErrorCatalog.NEGOTIATION_RULE_VIOLATION.value, message, facts)],
    )


def _negotiation_failure(failure: ContentValidationError) -> NegotiationParamExtractionError:
    """Narrow one core content validation failure to the negotiation extraction failure type.

    The pipeline renders the final message itself; only the structured facts are reconstructed from
    the first slot detail that carries them, so callers keep the rendered message and gain the facts
    map.
    """
    entry = failure.code
    if not isinstance(entry, ErrorCatalog):
        raise failure
    facts = next((error.facts for error in failure.errors if error.facts is not None), None)
    return NegotiationParamExtractionError(
        entry,
        facts,
        message=str(failure),
        errors=failure.errors,
        cause=failure,
    )
