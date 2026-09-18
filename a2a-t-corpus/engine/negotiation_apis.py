"""Negotiation-T API registration (port of the Java ``ClientApis``/``ServerApis`` negotiation entries).

JSON step arguments are bound to the production :class:`~a2a_t.negotiation.generation.orchestrator.
NegotiationGenerationOrchestrator` assembled by the corpus runtime. Three argument shapes are
consumed:

- from-text generation: ``{"text", "context", "templateUri"}``;
- from-data generation: ``{"data": {"context", "content"}, "templateUri"}`` where the typed
  negotiation content is rebuilt from the ``content`` object guided by the negotiation type and
  performative of the template URI;
- validation: ``{"promptText", "context"?, "schema", "templateUri"}``.

The wire ``api`` names stay the Java-side camelCase facade names (shared corpus contract); this
module maps them to the snake_case orchestrator methods.
"""

from __future__ import annotations

from typing import Any

from engine.registry import ApiRegistry

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.core.template_uri import TemplateUri
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.negotiation.content.enums import (
    NegotiationAction,
    NegotiationConclusion,
    NegotiationType,
)
from a2a_t.negotiation.content.models import (
    FeasibilityEndingContent,
    FeasibilityProposeContent,
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationAbortData,
    NegotiationEndingContent,
    NegotiationEndingData,
    NegotiationItem,
    NegotiationProposeContent,
    NegotiationProposeData,
    TargetEndingContent,
    TargetProposeContent,
)
from a2a_t.negotiation.generation.orchestrator import NegotiationGenerationOrchestrator

__all__ = ["register_negotiation_apis"]

#: Template-URI segment -> negotiation type.
_TYPE_SEGMENT_TO_TYPE: dict[str, NegotiationType] = {
    NegotiationType.INFORMATION.type_segment: NegotiationType.INFORMATION,
    NegotiationType.TARGET.type_segment: NegotiationType.TARGET,
    NegotiationType.FEASIBILITY.type_segment: NegotiationType.FEASIBILITY,
}


def register_negotiation_apis(registry: ApiRegistry, orchestrator: NegotiationGenerationOrchestrator) -> None:
    """Register the full Negotiation-T phase-1 API set of one assembled orchestrator."""

    # ---- from-text generation ----

    registry.register(
        "generateNegotiationProposePromptFromText",
        lambda args: metadata_to_map(
            orchestrator.generate_propose_from_text(
                Args.text(args, "text"),
                Args.context(args, "context"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )
    registry.register(
        "generateNegotiationAcceptPromptFromText",
        lambda args: metadata_to_map(
            orchestrator.generate_accept_from_text(
                Args.text(args, "text"),
                Args.context(args, "context"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )
    registry.register(
        "generateNegotiationRejectPromptFromText",
        lambda args: metadata_to_map(
            orchestrator.generate_reject_from_text(
                Args.text(args, "text"),
                Args.context(args, "context"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )
    registry.register(
        "generateNegotiationAbortPromptFromText",
        lambda args: metadata_to_map(
            orchestrator.generate_abort_from_text(
                Args.text(args, "text"),
                Args.context(args, "context"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )

    # ---- from-data generation ----

    registry.register(
        "generateNegotiationProposePromptFromData",
        lambda args: metadata_to_map(
            orchestrator.generate_propose_from_data(
                Args.data(args, "data", NegotiationPerformative.PROPOSE),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )
    registry.register(
        "generateNegotiationAcceptPromptFromData",
        lambda args: metadata_to_map(
            orchestrator.generate_accept_from_data(
                Args.data(args, "data", NegotiationPerformative.ACCEPT),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )
    registry.register(
        "generateNegotiationRejectPromptFromData",
        lambda args: metadata_to_map(
            orchestrator.generate_reject_from_data(
                Args.data(args, "data", NegotiationPerformative.REJECT),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )
    registry.register(
        "generateNegotiationAbortPromptFromData",
        lambda args: metadata_to_map(
            orchestrator.generate_abort_from_data(
                Args.data(args, "data", NegotiationPerformative.ABORT),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )

    # ---- validation ----

    registry.register(
        "validateProposePromptAndDataFilling",
        lambda args: _filled_to_map(
            orchestrator.validate_propose_prompt_and_data_filling(
                Args.text(args, "promptText"),
                Args.optional_context(args, "context"),
                Args.map(args, "schema"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )
    registry.register(
        "validateAcceptPromptAndDataFilling",
        lambda args: _filled_to_map(
            orchestrator.validate_accept_prompt_and_data_filling(
                Args.text(args, "promptText"),
                Args.optional_context(args, "context"),
                Args.map(args, "schema"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )
    registry.register(
        "validateRejectPromptAndDataFilling",
        lambda args: _filled_to_map(
            orchestrator.validate_reject_prompt_and_data_filling(
                Args.text(args, "promptText"),
                Args.optional_context(args, "context"),
                Args.map(args, "schema"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )
    registry.register(
        "validateAbortPromptAndDataFilling",
        lambda args: _filled_to_map(
            orchestrator.validate_abort_prompt_and_data_filling(
                Args.text(args, "promptText"),
                Args.optional_context(args, "context"),
                Args.map(args, "schema"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )


def metadata_to_map(content: Any) -> dict[str, object]:
    """Serialize one negotiation generation result into the transcript payload shape."""
    return {
        "templateUri": content.template_uri,
        "promptText": content.prompt_text,
        "extensionUri": content.extension_uri,
    }


def _filled_to_map(filled: FilledParamData) -> dict[str, object]:
    """Serialize one filled-parameter result into the transcript payload shape."""
    payload: dict[str, object] = {"data": filled.data}
    if filled.context:
        payload["context"] = dict(filled.context)
    return payload


class Args:
    """Step-argument binding helpers for the negotiation handlers.

    Binding failures surface as coded business errors (never bare ``ValueError``) so the corpus
    engine records an assertable catalog code instead of an engine-level crash:
    missing/ill-shaped ``promptText``/``schema``/``context``/``data`` arguments map to
    ``negotiation.invalid_input`` and a missing/unparseable ``templateUri`` maps to
    ``template.not_found``.
    """

    @staticmethod
    def text(args: dict[str, Any], name: str) -> str:
        """Return one required string argument (blank passes through to the SDK's coded error)."""
        value = args.get(name)
        if not isinstance(value, str):
            raise _invalid_argument(f"missing or non-string step argument: {name}")
        return value

    @staticmethod
    def map(args: dict[str, Any], name: str) -> dict[str, Any]:
        """Return one required non-empty object argument."""
        value = args.get(name)
        if not isinstance(value, dict):
            raise _invalid_argument(f"missing or non-object step argument: {name}")
        if not value:
            raise _invalid_argument(f"empty object step argument: {name}")
        return dict(value)

    @staticmethod
    def template_uri(args: dict[str, Any], name: str) -> TemplateUri:
        """Parse one required template URI argument, fail-fast when malformed."""
        raw = args.get(name)
        if not isinstance(raw, str):
            raise _template_uri_error("<missing>")
        parsed = TemplateUri.parse(raw)
        if parsed is None:
            raise _template_uri_error(raw)
        return parsed

    @staticmethod
    def context(args: dict[str, Any], name: str) -> NegotiationContext:
        """Build the required negotiation context of a from-text step."""
        return _context_from_args(Args.map(args, name))

    @staticmethod
    def optional_context(args: dict[str, Any], name: str) -> NegotiationContext | None:
        """Build the optional negotiation context of a validation step (absent => ``None``)."""
        raw = args.get(name)
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise _invalid_argument(f"non-object step argument: {name}")
        return _context_from_args(dict(raw))

    @staticmethod
    def data(
        args: dict[str, Any],
        name: str,
        performative: NegotiationPerformative,
    ) -> NegotiationProposeData | NegotiationEndingData | NegotiationAbortData:
        """Build the typed negotiation data bundle of a from-data step.

        The negotiation type is read from the step's ``templateUri`` (its second URI segment),
        which selects the concrete typed content class the ``content`` object is projected onto.
        """
        raw = Args.map(args, name)
        template_uri = Args.template_uri(args, "templateUri")
        try:
            negotiation_type = _negotiation_type_of(template_uri)
            typed_content = _build_content(
                negotiation_type,
                performative,
                Args.map(raw, "content"),
            )
            context = _context_from_args(Args.map(raw, "context"))
        except ValueError as error:
            raise _invalid_argument(f"invalid negotiation data: {error}") from error
        if performative is NegotiationPerformative.ACCEPT or performative is NegotiationPerformative.REJECT:
            return NegotiationEndingData(context=context, content=typed_content)
        if performative is NegotiationPerformative.PROPOSE:
            return NegotiationProposeData(context=context, content=typed_content)
        return NegotiationAbortData(context=context, content=typed_content)


def _invalid_argument(reason: str) -> A2ATBusinessError:
    """Build the coded ``negotiation.invalid_input`` failure for one step-argument binding miss."""
    return A2ATBusinessError(ErrorCatalog.NEGOTIATION_INVALID_INPUT, {"reason": reason})


def _template_uri_error(raw: str) -> A2ATBusinessError:
    """Build the coded ``template.not_found`` failure for one missing/unparseable template URI."""
    return A2ATBusinessError(ErrorCatalog.TEMPLATE_NOT_FOUND, {"template_uri": raw})


def _context_from_args(raw: dict[str, Any]) -> NegotiationContext:
    """Rebuild one negotiation context from a step-argument map, coding structural misses."""
    try:
        return _context_from_map(raw)
    except (KeyError, ValueError, TypeError) as error:
        raise _invalid_argument(f"invalid negotiation context: {error}") from error


def _context_from_map(raw: dict[str, Any]) -> NegotiationContext:
    """Rebuild one negotiation context from its JSON object."""
    return NegotiationContext(
        id=raw["id"],
        round=raw["round"],
        max_rounds=raw.get("maxRounds", NegotiationContext.DEFAULT_MAX_ROUNDS),
        performative=NegotiationPerformative.try_parse(raw["performative"]),
    )


def _negotiation_type_of(template_uri: TemplateUri) -> NegotiationType:
    """Read the negotiation type from the template URI's first path segment."""
    if not template_uri.path_segments:
        raise ValueError(f"negotiation template URI too short: {template_uri.uri}")
    segment = template_uri.path_segments[0]
    try:
        return _TYPE_SEGMENT_TO_TYPE[segment]
    except KeyError:
        raise ValueError(f"unrecognized negotiation type segment: {segment}") from None


def _build_content(
    negotiation_type: NegotiationType,
    performative: NegotiationPerformative,
    raw: dict[str, Any],
) -> Any:
    """Project one JSON content object onto the typed content class addressed by type+performative."""
    if performative is NegotiationPerformative.ABORT:
        return NegotiationAbortContent(
            termination_reason=_required(raw, "termination_reason", "abort content")
        )
    if performative is NegotiationPerformative.ACCEPT or performative is NegotiationPerformative.REJECT:
        raw_conclusion = raw.get("conclusion")
        if raw_conclusion is None:
            raise ValueError("ending content must declare its conclusion")
        conclusion = NegotiationConclusion(str(raw_conclusion))
        return _build_ending_content(negotiation_type, conclusion, raw)
    if performative is NegotiationPerformative.PROPOSE:
        return _build_propose_content(negotiation_type, raw)
    raise ValueError(f"unsupported performative: {performative}")


def _build_ending_content(
    negotiation_type: NegotiationType,
    conclusion: NegotiationConclusion,
    raw: dict[str, Any],
) -> NegotiationEndingContent:
    """Build the ending content of the given type with its conclusion field set."""
    if negotiation_type is NegotiationType.INFORMATION:
        return InformationEndingContent(conclusion=conclusion, items=_items(raw.get("items")))
    if negotiation_type is NegotiationType.TARGET:
        return TargetEndingContent(
            conclusion=conclusion,
            confirmed_intent=_optional_str(raw, "confirmed_intent"),
            failure_reason=_optional_str(raw, "failure_reason"),
        )
    if negotiation_type is NegotiationType.FEASIBILITY:
        return FeasibilityEndingContent(
            conclusion=conclusion,
            feasibility_summary=_required(raw, "feasibility_summary", "ending content"),
        )
    raise ValueError(f"unsupported negotiation type: {negotiation_type}")


def _build_propose_content(negotiation_type: NegotiationType, raw: dict[str, Any]) -> NegotiationProposeContent:
    """Build the propose content of the given type."""
    if negotiation_type is NegotiationType.INFORMATION:
        return InformationProposeContent(
            items=_items(raw.get("items")),
            relationship=_optional_str(raw, "relationship"),
        )
    if negotiation_type is NegotiationType.TARGET:
        return TargetProposeContent(
            target_negotiation_description=_required(raw, "target_negotiation_description", "propose content"),
            intent_understanding=_items(raw.get("intent_understanding")),
            alignment_and_clarification=_items(raw.get("alignment_and_clarification")),
            request_for_clarification=_items(raw.get("request_for_clarification")),
            target_confirm_request=_optional_str(raw, "target_confirm_request"),
        )
    if negotiation_type is NegotiationType.FEASIBILITY:
        action = raw.get("action")
        if action is None:
            raise ValueError("feasibility propose content must declare its action")
        return FeasibilityProposeContent(
            feasibility_negotiation_description=_required(
                raw, "feasibility_negotiation_description", "propose content"
            ),
            action=NegotiationAction(str(action)),
            contents_to_evaluate=_items(raw.get("contents_to_evaluate")),
            infeasibility_details_and_proposal=_items(raw.get("infeasibility_details_and_proposal")),
            feasibility_confirm_request=_optional_str(raw, "feasibility_confirm_request"),
        )
    raise ValueError(f"unsupported negotiation type: {negotiation_type}")


def _items(raw: Any) -> list[NegotiationItem] | None:
    """Project a JSON item array onto typed negotiation items (``None``/absent => ``None``)."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(f"negotiation items must be an array: {raw}")
    return [
        NegotiationItem(name=_required(item, "name", "negotiation item"), value=_optional_str(item, "value"))
        for item in raw
    ]


def _required(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} requires a non-blank '{key}'")
    return value


def _optional_str(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    return str(value)
