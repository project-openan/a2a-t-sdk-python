"""LLM-backed semantic validation of negotiation messages (port of Java
``DefaultNegotiationSemanticValidator``, ``NegotiationSemanticValidator``, ``SemanticValidationResult``
and ``NegotiationValidationException``).

One structured LLM call performs the semantic validation and the parameter extraction together. The
call loads the semantic validation prompt resources of the reference language through the common
resource access layer (D31: the Java classpath loader is replaced by ``load_prompt``, packaged-fixed),
fills the literal bracket tokens of the user prompt with the declared negotiation type, the template
URI, the template content, the serialized caller schema and the message text, and passes the caller
schema merged into the semantic validation output contract as the output schema.

After the call the validator enforces the output contract in code: the response must contain the four
required keys ``semantic_verdict``, ``negotiation_type``, ``errors`` and ``params`` with the expected
shapes, otherwise the internal :class:`NegotiationValidationError` is raised for the pipeline to map
to the retryable ``llm.response_invalid`` code; a transport failure of the LLM call propagates as
:class:`~a2a_t.llm.errors.LLMError` for the pipeline to map to ``llm.invocation_failed``.

The LLM reports each error as the constant triple ``{slot_name, code, facts}`` (snake_case keys, the
only shape the schema allows). The human-readable message is rendered from the code's message
template in the reference language — never taken from the LLM response — and the reported code is
resolved against the closed catalog:

===========================================  =====================================================
reported code                                surfaced error
===========================================  =====================================================
a ``negotiation.*`` code of the catalog      that code, facts strictly matched against its fact
                                             parameters (extra LLM fact keys are dropped)
anything else (unknown or cross-domain)      ``negotiation.rule_violation`` with the single fact
                                             ``section_label``, plus a WARN log
===========================================  =====================================================

The raw LLM text therefore never surfaces to callers: an unknown code discards the LLM-provided facts
outright, and a known code keeps only its declared fact keys. When the verdict is ``True`` and the
reference declares a type, the reported negotiation type must be present and must match the declared
type; a missing or mismatching type turns the outcome into a semantic rejection carrying a
``negotiation.type_mismatch`` error on the ``section.*`` slot of the implied (or declared) type.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol

from a2a_t.common.prompt_resources.resource_access import (
    PackagedPromptResourceAccess,
    PromptResourceAccess,
)
from a2a_t.core.errors.catalog import ErrorCatalog, by_code
from a2a_t.core.errors.exceptions import (
    A2ATError,
    ContentValidationError,
    ResourceNotFoundError,
    SlotValidationError,
)
from a2a_t.core.errors.messages import render
from a2a_t.core.validation_pipeline import SEMANTIC_VALIDATION_STEP, ValidationResult
from a2a_t.llm.errors import LLMConfigError, LLMError
from a2a_t.llm.provider import LLMClient

from ..content.enums import NegotiationType
from ..resources.reference import NegotiationReference

__all__ = [
    "DefaultNegotiationSemanticValidator",
    "NegotiationSemanticValidator",
    "NegotiationValidationError",
    "SemanticValidationResult",
    "build_semantic_validation_schema",
    "validate_semantic",
]

_LOGGER = logging.getLogger(__name__)

#: Prompt category of the negotiation semantic validation (Java ``PROMPT_RESOURCE_ROOT``).
_PROMPT_CATEGORY: Final[str] = "negotiation_semantic_validation"

#: System prompt file name of the semantic validation category.
_SYSTEM_PROMPT_FILE: Final[str] = "system.md"

#: User prompt file name of the semantic validation category.
_USER_PROMPT_FILE: Final[str] = "user.md"

#: Response key carrying the overall semantic verdict.
KEY_SEMANTIC_VERDICT: Final[str] = "semantic_verdict"

#: Response key carrying the negotiation type implied by the message sections.
KEY_NEGOTIATION_TYPE: Final[str] = "negotiation_type"

#: Response key carrying the structured semantic errors.
KEY_ERRORS: Final[str] = "errors"

#: Response key carrying the extracted parameters.
KEY_PARAMS: Final[str] = "params"

#: Error-triple key carrying the slot name.
KEY_SLOT_NAME: Final[str] = "slot_name"

#: Error-triple key carrying the error code.
KEY_CODE: Final[str] = "code"

#: Error-triple key carrying the structured fact values.
KEY_FACTS: Final[str] = "facts"

#: Fact key of the fallback code (``ErrorCatalog.NEGOTIATION_RULE_VIOLATION``).
_SECTION_LABEL_FACT: Final[str] = "section_label"

#: Catalog domain every negotiation code belongs to (Java ``NEGOTIATION_CODE_DOMAIN``).
_NEGOTIATION_CODE_DOMAIN: Final[str] = "negotiation."

#: Enum of the ``negotiation_type`` output key: the three types plus ``None``.
_NEGOTIATION_TYPE_ENUM: Final[list[str | None]] = ["information", "target", "feasibility", None]

#: Literal bracket tokens the user prompt fills (Java ``USER_PROMPT_PLACEHOLDER_PATTERN``).
_USER_PROMPT_PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\[(phase|input|template_uri|negotiation_type|template_content|schema)\]"
)

#: Signature of the schema-merging collaborator (Java ``UnaryOperator<Map<String, Object>>``).
SchemaBuilder = Callable[[Mapping[str, object]], dict[str, object]]

_DEFAULT_ACCESS: PackagedPromptResourceAccess | None = None


class NegotiationValidationError(RuntimeError):
    """Internal failure of the negotiation semantic validation step.

    This exception is internal to the validation pipeline: it signals an LLM infrastructure failure or
    a response that violates the output contract (missing required keys or wrong shapes). It must
    never bubble out of the public APIs; the validation pipeline converts it into a
    parameter-extraction failure carrying the retryable ``llm.response_invalid`` error code.
    """


@dataclass(frozen=True)
class SemanticValidationResult:
    """Outcome of the LLM-backed semantic validation of a negotiation message.

    Attributes:
        verdict: overall semantic verdict; ``True`` only when every semantic constraint holds.
        negotiation_type: negotiation type implied by the message sections, one of ``information``,
            ``target`` or ``feasibility``; may be ``None`` when the verdict is ``False``.
        errors: structured semantic errors using language-neutral ``section.*`` slot names; empty
            when the verdict is ``True``.
        params: parameters extracted from the message per the caller-provided schema.
    """

    verdict: bool
    negotiation_type: str | None
    errors: tuple[SlotValidationError, ...] = ()
    params: dict[str, object] | list[object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize the errors sequence and the extracted parameters.

        Raises:
            TypeError: when the errors sequence or the params map is ``None`` (Java
                ``NullPointerException`` parity).
        """
        if self.errors is None:
            raise TypeError("Semantic validation errors must not be null.")
        if self.params is None:
            raise TypeError("Semantic validation params must not be null.")
        object.__setattr__(self, "errors", tuple(self.errors))
        if isinstance(self.params, dict):
            object.__setattr__(self, "params", dict(self.params))
        else:
            object.__setattr__(self, "params", list(self.params))


def build_semantic_validation_schema(caller_schema: Mapping[str, object]) -> dict[str, object]:
    """Merge the caller-provided parameter schema into the semantic validation output contract.

    The merged schema requires exactly the four keys ``semantic_verdict``, ``negotiation_type``,
    ``errors`` and ``params`` and allows no additional properties. The caller schema is embedded as
    the ``params`` property; a caller schema without a ``type`` keyword is wrapped as an object
    schema first.

    Args:
        caller_schema: parameter schema provided by the caller of the validation API.

    Returns:
        merged JSON schema of the semantic validation LLM call.

    Raises:
        TypeError: when the caller schema is ``None`` (Java ``NullPointerException`` parity).
    """
    if caller_schema is None:
        raise TypeError("Caller parameter schema must not be null.")
    schema: dict[str, object] = {"type": "object"}
    properties: dict[str, object] = {
        KEY_SEMANTIC_VERDICT: {"type": "boolean"},
        KEY_NEGOTIATION_TYPE: {"type": ["string", "null"], "enum": list(_NEGOTIATION_TYPE_ENUM)},
        KEY_ERRORS: _errors_schema(),
        KEY_PARAMS: _wrap_caller_schema(caller_schema),
    }
    schema["properties"] = properties
    schema["required"] = [KEY_SEMANTIC_VERDICT, KEY_NEGOTIATION_TYPE, KEY_ERRORS, KEY_PARAMS]
    schema["additionalProperties"] = False
    return schema


def _errors_schema() -> dict[str, object]:
    """Build the JSON schema of the ``errors`` output key: the constant error triple."""
    facts_properties: dict[str, object] = {"type": "object", "additionalProperties": {"type": "string"}}
    error_properties: dict[str, object] = {
        KEY_SLOT_NAME: {"type": "string"},
        KEY_CODE: {"type": "string"},
        KEY_FACTS: facts_properties,
    }
    error_item: dict[str, object] = {
        "type": "object",
        "properties": error_properties,
        "required": [KEY_SLOT_NAME, KEY_CODE, KEY_FACTS],
        "additionalProperties": False,
    }
    return {"type": "array", "items": error_item}


def _wrap_caller_schema(caller_schema: Mapping[str, object]) -> dict[str, object]:
    """Embed one caller schema as the ``params`` property, wrapping schemaless inputs as objects."""
    if "type" in caller_schema:
        return dict(caller_schema)
    return {"type": "object", **caller_schema}


class NegotiationSemanticValidator(Protocol):
    """LLM-backed semantic validator for negotiation messages.

    The validator performs a single structured LLM call that combines semantic validation with
    parameter extraction and then enforces the declared type consistency in code: when the verdict is
    ``True`` and the reference declares a type, the negotiation type reported for the message must be
    present and must match the declared type. A response that misses one of the four required keys or
    has the wrong shape is a validation infrastructure failure signalled through the internal
    :class:`NegotiationValidationError`.
    """

    def validate_negotiation(
        self,
        prompt: str,
        caller_schema: Mapping[str, object],
        reference: NegotiationReference,
        template_content: str,
    ) -> SemanticValidationResult:
        """Validate one rendered negotiation message semantically and extract its parameters.

        Args:
            prompt: rendered negotiation message text.
            caller_schema: caller-provided parameter JSON schema embedded into the structured-call
                output contract.
            reference: reference the message is validated against, carrying the declared type,
                performative and language.
            template_content: loaded template text used as a reference for structure/completeness
                checks.

        Returns:
            semantic validation outcome carrying the verdict, the implied negotiation type, the
            semantic errors and the extracted parameters.

        Raises:
            NegotiationValidationError: if the response misses a required key or has the wrong
                shape.
            a2a_t.llm.errors.LLMError: if the LLM invocation fails at the transport level.
            a2a_t.core.errors.exceptions.ResourceNotFoundError: if the semantic validation prompt
                resources of the reference language are missing.
        """
        ...

    def validate(
        self,
        prompt: str,
        schema: Mapping[str, object],
        reference: NegotiationReference,
        template_content: str,
    ) -> ValidationResult:
        """Adapt :meth:`validate_negotiation` to the core semantic validator contract.

        Java interface-default-method parity: the outcome is narrowed to the core
        :class:`~a2a_t.core.validation_pipeline.ValidationResult` shape and the internal failures are
        translated to final catalog codes — a missing LLM configuration to ``llm.not_configured``,
        a transport failure to ``llm.invocation_failed`` and a response-contract violation to the
        retryable ``llm.response_invalid`` — while a prompt resource miss propagates for the pipeline
        to translate to ``template.not_found``.
        """
        return validate_semantic(self, prompt, schema, reference, template_content)


def validate_semantic(
    validator: NegotiationSemanticValidator,
    prompt: str,
    schema: Mapping[str, object],
    reference: NegotiationReference,
    template_content: str,
) -> ValidationResult:
    """Run one semantic validator and translate its internal failures to final catalog codes.

    Args:
        validator: semantic validator performing the single structured LLM call.
        prompt: rendered negotiation message text.
        schema: caller-provided parameter JSON schema.
        reference: reference the message is validated against.
        template_content: loaded template text used as a reference for structure/completeness
            checks.

    Returns:
        the semantic validation outcome narrowed to the core result shape.

    Raises:
        ContentValidationError: with ``llm.not_configured`` when no LLM client is configured,
            ``llm.invocation_failed`` when the LLM invocation fails at the transport level, or the
            retryable ``llm.response_invalid`` when the response violates the output contract.
    """
    try:
        result = validator.validate_negotiation(prompt, schema, reference, template_content)
        return ValidationResult(result.verdict, result.errors, result.params)
    except LLMConfigError as exception:
        # A missing LLM configuration never recovers within this call.
        raise ContentValidationError(
            ErrorCatalog.LLM_NOT_CONFIGURED,
            language=reference.language,
            cause=exception,
        ) from exception
    except LLMError as exception:
        facts = {"provider": type(exception).__name__, "reason": str(exception)}
        message = render(ErrorCatalog.LLM_INVOCATION_FAILED, facts, reference.language)
        raise ContentValidationError(
            ErrorCatalog.LLM_INVOCATION_FAILED,
            facts,
            language=reference.language,
            message=message,
            errors=[SlotValidationError("_llm", ErrorCatalog.LLM_INVOCATION_FAILED.value, message, facts)],
            cause=exception,
        ) from exception
    except NegotiationValidationError as exception:
        facts = {"step": SEMANTIC_VALIDATION_STEP}
        message = render(ErrorCatalog.LLM_RESPONSE_INVALID, facts, reference.language)
        raise ContentValidationError(
            ErrorCatalog.LLM_RESPONSE_INVALID,
            facts,
            language=reference.language,
            message=message,
            errors=[SlotValidationError("_llm", ErrorCatalog.LLM_RESPONSE_INVALID.value, message, facts)],
            cause=exception,
        ) from exception


class DefaultNegotiationSemanticValidator:
    """Default LLM-backed semantic validator backed by one structured call per attempt.

    The LLM client is injected through the :class:`~a2a_t.llm.provider.LLMClient` protocol and is
    never looked up globally; ``None`` fails with ``llm.not_configured`` at call time. The schema
    merge defaults to :func:`build_semantic_validation_schema` and the prompt loading to the packaged
    access of the common resource access layer.
    """

    def __init__(
        self,
        llm_client: LLMClient | None,
        schema_builder: SchemaBuilder = build_semantic_validation_schema,
        *,
        access: PromptResourceAccess | None = None,
    ) -> None:
        """Create the default semantic validator.

        Args:
            llm_client: LLM client used for the single structured call; ``None`` fails with
                ``llm.not_configured`` at call time.
            schema_builder: collaborator merging the caller schema into the semantic validation
                output contract.
            access: resource access object resolving the prompt tree; ``None`` uses the packaged
                access (the Java classpath loader's default).

        Raises:
            TypeError: when the schema builder is ``None`` (Java ``NullPointerException`` parity).
        """
        if schema_builder is None:
            raise TypeError("schema_builder")
        self._llm_client = llm_client
        self._schema_builder = schema_builder
        self._access = _default_access() if access is None else access

    def validate_negotiation(
        self,
        prompt: str,
        caller_schema: Mapping[str, object],
        reference: NegotiationReference,
        template_content: str,
    ) -> SemanticValidationResult:
        """Validate one rendered negotiation message semantically and extract its parameters.

        Args:
            prompt: rendered negotiation message text.
            caller_schema: caller-provided parameter JSON schema embedded into the structured-call
                output contract.
            reference: reference the message is validated against, carrying the declared type,
                performative and language.
            template_content: loaded template text used as a reference for structure/completeness
                checks.

        Returns:
            semantic validation outcome carrying the verdict, the implied negotiation type, the
            semantic errors and the extracted parameters.

        Raises:
            TypeError: when the prompt or the reference is ``None`` (Java ``NullPointerException``
                parity).
            NegotiationValidationError: if the response misses a required key or has the wrong
                shape.
            a2a_t.llm.errors.LLMError: if the LLM invocation fails at the transport level.
            a2a_t.core.errors.exceptions.ResourceNotFoundError: if the semantic validation prompt
                resources of the reference language are missing.
        """
        if prompt is None:
            raise TypeError("prompt")
        if reference is None:
            raise TypeError("reference")
        if self._llm_client is None:
            raise LLMConfigError("Semantic validation requires an LLM client but none is configured.")
        messages = self._build_messages(prompt, caller_schema, reference, template_content)
        merged_schema = self._schema_builder(caller_schema)
        response = self._llm_client.structured(
            messages=messages, json_schema=merged_schema, temperature=None, max_tokens=None
        )
        result = _interpret(_parse_response(None if response is None else response.content), reference)
        _LOGGER.info(
            "negotiation_semantic_validation_completed verdict=%s error_count=%s",
            result.verdict,
            len(result.errors),
        )
        return result

    def validate(
        self,
        prompt: str,
        schema: Mapping[str, object],
        reference: NegotiationReference,
        template_content: str,
    ) -> ValidationResult:
        """Run :meth:`validate_negotiation` through the core contract adapter.

        Raises:
            ContentValidationError: with ``llm.not_configured``, ``llm.invocation_failed`` or the
                retryable ``llm.response_invalid`` (see :func:`validate_semantic`).
        """
        return validate_semantic(self, prompt, schema, reference, template_content)

    def _build_messages(
        self,
        prompt: str,
        caller_schema: Mapping[str, object],
        reference: NegotiationReference,
        template_content: str,
    ) -> list[dict[str, str]]:
        """Build the system and user messages of the single structured call."""
        language = reference.language
        system_prompt = self._load_prompt_resource(_SYSTEM_PROMPT_FILE, language)
        user_prompt = self._load_prompt_resource(_USER_PROMPT_FILE, language)
        filled_user_prompt = _USER_PROMPT_PLACEHOLDER_PATTERN.sub(
            lambda match: _placeholder_replacement(match, prompt, caller_schema, reference, template_content),
            user_prompt,
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": filled_user_prompt},
        ]

    def _load_prompt_resource(self, file_name: str, language: str) -> str:
        """Load one prompt resource of the semantic validation category.

        Raises:
            a2a_t.core.errors.exceptions.ResourceNotFoundError: when the prompt resource of the
                language is missing from the package (the common layer's
                ``infra.resource_read_failed`` miss translated to the Java
                ``ResourceNotFoundException`` the pipeline maps to ``template.not_found``).
        """
        relative_path = f"prompt_resources/prompts/{_PROMPT_CATEGORY}/{language}/{file_name}"
        try:
            return self._access.load_prompt(_PROMPT_CATEGORY, language, file_name)
        except A2ATError as error:
            if error.code is not ErrorCatalog.INFRA_RESOURCE_READ_FAILED:
                raise
            raise ResourceNotFoundError(
                "Negotiation semantic validation prompt resource does not exist for language "
                f"{language}; set A2AT_LANGUAGE to a language with bundled prompt resources "
                "(zh-CN or en-US).",
                relative_path,
            ) from error


def _placeholder_replacement(
    match: re.Match[str],
    prompt: str,
    caller_schema: Mapping[str, object],
    reference: NegotiationReference,
    template_content: str,
) -> str:
    """Return the value one literal bracket token of the user prompt is replaced with."""
    token = match.group(1)
    if token == "phase":
        return reference.performative.name.lower()
    if token == "input":
        return prompt
    if token == "template_uri":
        return reference.uri
    if token == "negotiation_type":
        return _declared_type_name(reference)
    if token == "template_content":
        return template_content
    if token == "schema":
        return _to_json(caller_schema)
    return match.group(0)


def _to_json(caller_schema: Mapping[str, object] | None) -> str:
    """Serialize one caller schema to compact JSON (Java ``ObjectMapper.writeValueAsString``).

    Raises:
        NegotiationValidationError: when the schema is not JSON-serializable.
    """
    try:
        return json.dumps(
            {} if caller_schema is None else dict(caller_schema),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise NegotiationValidationError("Failed to serialize the caller parameter schema.") from error


def _parse_response(content: str | None) -> dict[str, object]:
    """Parse the LLM response content into the response mapping.

    Raises:
        NegotiationValidationError: when the content is blank or is not a JSON object.
    """
    if content is None or content.strip() == "":
        raise NegotiationValidationError("Semantic validation response is empty.")
    try:
        parsed: object = json.loads(content)
    except ValueError as error:
        raise NegotiationValidationError(f"Semantic validation response is not a JSON object: {error}") from error
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise NegotiationValidationError("Semantic validation response is not a JSON object.")
    return parsed


def _interpret(response: Mapping[str, object], reference: NegotiationReference) -> SemanticValidationResult:
    """Interpret one parsed response against the output contract and the declared type."""
    verdict = response.get(KEY_SEMANTIC_VERDICT)
    if not isinstance(verdict, bool):
        raise NegotiationValidationError("Semantic validation response key semantic_verdict must be a boolean.")
    if KEY_NEGOTIATION_TYPE not in response:
        raise NegotiationValidationError("Semantic validation response is missing the required key negotiation_type.")
    type_value: object = response.get(KEY_NEGOTIATION_TYPE)
    if type_value is not None and not isinstance(type_value, str):
        raise NegotiationValidationError("Semantic validation response key negotiation_type must be a string or null.")
    raw_errors: object = response.get(KEY_ERRORS)
    if not isinstance(raw_errors, list):
        raise NegotiationValidationError("Semantic validation response key errors must be an array.")
    raw_params: object = response.get(KEY_PARAMS)
    if not isinstance(raw_params, (Mapping, list)):
        raise NegotiationValidationError("Semantic validation response key params must be an object or an array.")

    errors = _parse_errors(raw_errors, reference.language)
    params = _parse_params(raw_params)
    negotiation_type = type_value if isinstance(type_value, str) else None

    if verdict and reference.type is not None:
        if negotiation_type is None:
            rejection_errors = list(errors)
            rejection_errors.append(
                _type_consistency_error(reference.type, "unknown", _declared_type_name(reference), reference.language)
            )
            return SemanticValidationResult(False, None, tuple(rejection_errors), params)
        implied_type = _parse_implied_type(negotiation_type)
        if implied_type is not reference.type:
            section_type = reference.type if implied_type is None else implied_type
            rejection_errors = list(errors)
            rejection_errors.append(
                _type_consistency_error(
                    section_type, negotiation_type, _declared_type_name(reference), reference.language
                )
            )
            return SemanticValidationResult(False, negotiation_type, tuple(rejection_errors), params)
    return SemanticValidationResult(verdict, negotiation_type, errors, params)


def _parse_errors(raw_errors: list[object], language: str) -> tuple[SlotValidationError, ...]:
    """Parse the LLM-reported errors, resolving every code against the closed catalog.

    Raises:
        NegotiationValidationError: when one reported error violates the error-triple shape.
    """
    errors: list[SlotValidationError] = []
    for raw_error in raw_errors:
        if not isinstance(raw_error, Mapping):
            raise NegotiationValidationError(
                "Semantic validation response errors must be objects with slot_name, code and facts."
            )
        slot_name: object = raw_error.get(KEY_SLOT_NAME)
        code: object = raw_error.get(KEY_CODE)
        if not isinstance(slot_name, str) or not isinstance(code, str):
            raise NegotiationValidationError(
                "Semantic validation response errors must carry string slot_name and code values."
            )
        raw_facts: object = raw_error.get(KEY_FACTS)
        if not isinstance(raw_facts, Mapping):
            raise NegotiationValidationError("Semantic validation response errors must carry a facts object.")
        facts = _string_facts(raw_facts)
        entry = _resolve_code(code)
        if entry.value != code:
            _LOGGER.warning(
                "negotiation_semantic_validation_unknown_code original_code=%s fallback_code=%s",
                code,
                entry.value,
            )
            # The LLM-provided facts are discarded outright: the fallback carries exactly its one
            # declared fact, so no raw LLM text can ride along (port plan 7.3).
            facts = {_SECTION_LABEL_FACT: slot_name}
        else:
            facts = _strict_facts(entry, facts)
        errors.append(SlotValidationError(slot_name, entry.value, render(entry, facts, language), facts))
    return tuple(errors)


def _resolve_code(code: str) -> ErrorCatalog:
    """Resolve one LLM-reported code to its catalog entry, falling back to ``negotiation.rule_violation``.

    A code outside the closed ``negotiation.*`` domain of the catalog — an unknown spelling or a
    cross-domain code such as a prompt-domain code arriving in negotiation validation — resolves to
    the fallback entry.
    """
    try:
        entry = by_code(code)
    except KeyError:
        return ErrorCatalog.NEGOTIATION_RULE_VIOLATION
    if entry.value.startswith(_NEGOTIATION_CODE_DOMAIN):
        return entry
    return ErrorCatalog.NEGOTIATION_RULE_VIOLATION


def _strict_facts(entry: ErrorCatalog, facts: Mapping[str, str]) -> dict[str, str]:
    """Keep only the fact keys the resolved code's fact parameters declare.

    The fact key set is strictly matched against the catalog entry's fact parameters, so a fact key
    the LLM invented cannot travel to callers even for a known code.
    """
    return {key: value for key, value in facts.items() if entry.has_fact_parameter(key)}


def _string_facts(raw_facts: Mapping[object, object]) -> dict[str, str]:
    """Normalize one raw facts object to string values, dropping unstringifiable entries."""
    facts: dict[str, str] = {}
    for key, value in raw_facts.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, str):
            facts[key] = value
        elif isinstance(value, (bool, int, float)):
            facts[key] = str(value)
    return facts


def _parse_params(raw_params: Mapping[object, object] | list[object]) -> dict[str, object] | list[object]:
    """Parse the extracted parameters, keeping their values verbatim.

    Object-shaped responses are keyed by string parameter names; array-shaped responses keep the
    declared item order of the caller's array schema, never merging, de-duplicating or dropping
    any same-named items.

    Raises:
        NegotiationValidationError: when one parameter key is not a string or one array item is
            not an object.
    """
    if isinstance(raw_params, list):
        params: list[object] = []
        for item in raw_params:
            if not isinstance(item, Mapping):
                raise NegotiationValidationError("Semantic validation response params array items must be objects.")
            params.append(dict(item))
        return params
    params_map: dict[str, object] = {}
    for key, value in raw_params.items():
        if not isinstance(key, str):
            raise NegotiationValidationError("Semantic validation response params keys must be strings.")
        params_map[key] = value
    return params_map


def _type_consistency_error(
    section_type: NegotiationType, implied: str, declared: str, language: str
) -> SlotValidationError:
    """Build the ``negotiation.type_mismatch`` error of one type-consistency rejection."""
    facts = {"implied": implied, "declared": declared}
    return SlotValidationError(
        _declared_type_section_key(section_type),
        ErrorCatalog.NEGOTIATION_TYPE_MISMATCH.value,
        render(ErrorCatalog.NEGOTIATION_TYPE_MISMATCH, facts, language),
        facts,
    )


def _parse_implied_type(negotiation_type: str) -> NegotiationType | None:
    """Parse the negotiation type reported for the message; ``None`` when it names no known type."""
    for candidate in NegotiationType:
        if candidate.name.lower() == negotiation_type:
            return candidate
    return None


def _declared_type_name(reference: NegotiationReference) -> str:
    """Return the declared type name of one reference, ``common`` for the type-independent abort."""
    return "common" if reference.type is None else reference.type.name.lower()


def _declared_type_section_key(negotiation_type: NegotiationType) -> str:
    """Return the ``section.*`` slot key carrying the type-consistency error of one type."""
    if negotiation_type is NegotiationType.INFORMATION:
        return "section.info_static"
    if negotiation_type is NegotiationType.TARGET:
        return "section.target"
    if negotiation_type is NegotiationType.FEASIBILITY:
        return "section.feasibility"
    raise NegotiationValidationError(f"Unknown negotiation type {negotiation_type}.")


def _default_access() -> PackagedPromptResourceAccess:
    """Return the module-level packaged access used when no access object is injected."""
    global _DEFAULT_ACCESS
    if _DEFAULT_ACCESS is None:
        _DEFAULT_ACCESS = PackagedPromptResourceAccess()
    return _DEFAULT_ACCESS
