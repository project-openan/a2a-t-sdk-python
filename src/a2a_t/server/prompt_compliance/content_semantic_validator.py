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

"""LLM-backed semantic validation of extension content (port of Java ``DefaultSemanticValidator``).

One structured LLM call performs the semantic validation and the parameter extraction together. The
call loads the ``content_validation`` prompt resources of the reference language through the common
resource access layer (D31: like the negotiation semantic validation prompts, they are an SDK
contract loaded from the packaged tree regardless of the configured prompt source type), fills the
literal bracket tokens of the user prompt with the extension name, the message text, the template
URI, the template content and the serialized caller schema, and passes the constant three-key
output contract as the output schema.

After the call the validator enforces the output contract in code: the response must contain the
three required keys ``semantic_verdict``, ``errors`` and ``params`` with the expected shapes,
otherwise the failure carries the retryable ``llm.response_invalid`` code; a transport failure of
the LLM call carries ``llm.invocation_failed``; a missing LLM client carries
``llm.not_configured``; a missing prompt resource raises
:class:`~a2a_t.core.errors.exceptions.ResourceNotFoundError` for the validation pipeline to map to
``template.not_found`` (the Java ``ValidationPipeline`` catch point).

The LLM reports each error as the constant triple ``{slot_name, code, facts}`` (snake_case keys).
The human-readable message is rendered from the code's message template in the reference language —
never taken from the LLM response — and the reported code is resolved against the closed catalog: a
``content.*`` code of the catalog is kept as-is, anything else (unknown or cross-domain) resolves to
the ``content.rule_violation`` fallback whose facts are replaced with the single ``section_label``
fact, plus a WARN log.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Final

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
from a2a_t.core.template_uri import TemplateUri
from a2a_t.core.validation_pipeline import SEMANTIC_VALIDATION_STEP, ValidationResult
from a2a_t.llm.errors import LLMConfigError, LLMError
from a2a_t.llm.provider import LLMClient

__all__ = [
    "CONTENT_SEMANTIC_VALIDATION_STEP",
    "DefaultContentSemanticValidator",
    "build_content_validation_schema",
]

_LOGGER = logging.getLogger(__name__)

#: Prompt category of the content semantic validation (Java ``PROMPT_RESOURCE_ROOT``).
_PROMPT_CATEGORY: Final[str] = "content_validation"

#: System prompt file name of the content validation category.
_SYSTEM_PROMPT_FILE: Final[str] = "system.md"

#: User prompt file name of the content validation category.
_USER_PROMPT_FILE: Final[str] = "user.md"

#: Response key carrying the overall semantic verdict.
KEY_SEMANTIC_VERDICT: Final[str] = "semantic_verdict"

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

#: Fact key of the fallback code (``ErrorCatalog.CONTENT_RULE_VIOLATION``).
_SECTION_LABEL_FACT: Final[str] = "section_label"

#: Catalog domain every content code belongs to (Java ``CONTENT_CODE_DOMAIN``).
_CONTENT_CODE_DOMAIN: Final[str] = "content."

#: Literal bracket tokens the user prompt fills (Java ``PLACEHOLDER_PATTERN``).
_USER_PROMPT_PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\[(extension_name|input|template_uri|template_content|schema)\]"
)

#: Internal diagnostic step name surfaced with the retryable response-invalid code.
CONTENT_SEMANTIC_VALIDATION_STEP: Final[str] = SEMANTIC_VALIDATION_STEP

_DEFAULT_ACCESS: PackagedPromptResourceAccess | None = None


class ContentSemanticValidationError(RuntimeError):
    """Internal failure of the content semantic validation step.

    Signals an LLM infrastructure failure or a response that violates the output contract (missing
    required keys or wrong shapes). It never bubbles out of the public APIs: this module converts
    it into a :class:`~a2a_t.core.errors.exceptions.ContentValidationError` carrying the retryable
    ``llm.response_invalid`` error code.
    """


def build_content_validation_schema() -> dict[str, object]:
    """Build the constant three-key output contract of the content validation LLM call.

    The schema requires exactly the keys ``semantic_verdict``, ``errors`` and ``params`` and allows
    no additional properties; each reported error must be the constant triple
    ``{slot_name, code, facts}`` with string facts.
    """
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
    }
    properties: dict[str, object] = {
        KEY_SEMANTIC_VERDICT: {"type": "boolean"},
        KEY_ERRORS: {"type": "array", "items": error_item},
        KEY_PARAMS: {"type": "object"},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": [KEY_SEMANTIC_VERDICT, KEY_ERRORS, KEY_PARAMS],
        "additionalProperties": False,
    }


class DefaultContentSemanticValidator:
    """Default LLM-backed semantic validator of extension content (one structured call per attempt).

    The LLM client is injected through the :class:`~a2a_t.llm.provider.LLMClient` protocol and is
    never looked up globally; ``None`` fails with ``llm.not_configured`` at call time. The prompt
    loading defaults to the packaged access of the common resource access layer.
    """

    def __init__(
        self,
        llm_client: LLMClient | None,
        language: str,
        *,
        access: PromptResourceAccess | None = None,
    ) -> None:
        """Create the default content semantic validator.

        The ``content_validation`` prompt resources of the given language are loaded eagerly here
        (Java assembly-time contract): a missing resource fails fast with
        :class:`~a2a_t.core.errors.exceptions.ResourceNotFoundError` instead of on the first
        validation call.

        Args:
            llm_client: LLM client used for the single structured call; ``None`` fails with
                ``llm.not_configured`` at call time.
            language: language code for prompt resource loading and message rendering.
            access: resource access object resolving the prompt tree; ``None`` uses the packaged
                access (the Java classpath loader's default).

        Raises:
            TypeError: when the language is ``None`` (Java ``NullPointerException`` parity).
            a2a_t.core.errors.exceptions.ResourceNotFoundError: if the content validation prompt
                resources of the given language are missing.
        """
        if language is None:
            raise TypeError("language")
        self._llm_client = llm_client
        self._language = language
        self._access = _default_access() if access is None else access
        self._system_prompt = self._load_prompt_resource(_SYSTEM_PROMPT_FILE)
        self._user_prompt_template = self._load_prompt_resource(_USER_PROMPT_FILE)

    def validate(
        self,
        prompt: str,
        schema: Mapping[str, object],
        reference: TemplateUri,
        template_content: str,
    ) -> ValidationResult:
        """Validate one content prompt semantically and extract its parameters.

        Args:
            prompt: rendered content prompt text.
            schema: caller-provided parameter JSON schema embedded into the structured-call user
                prompt.
            reference: template URI the content is validated against.
            template_content: loaded template text used as a reference for structure/completeness
                checks.

        Returns:
            the semantic validation outcome carrying the verdict, the structured errors and the
            extracted parameters.

        Raises:
            ContentValidationError: with ``llm.not_configured`` when no LLM client is configured,
                ``llm.invocation_failed`` when the LLM invocation fails at the transport level, or
                the retryable ``llm.response_invalid`` when the response violates the output
                contract.
        """
        if self._llm_client is None:
            raise ContentValidationError(ErrorCatalog.LLM_NOT_CONFIGURED, language=self._language)
        try:
            response = self._llm_client.structured(
                messages=self._build_messages(prompt, schema, reference, template_content),
                json_schema=build_content_validation_schema(),
                temperature=None,
                max_tokens=None,
            )
        except LLMConfigError as exception:
            raise ContentValidationError(
                ErrorCatalog.LLM_NOT_CONFIGURED,
                language=self._language,
                cause=exception,
            ) from exception
        except LLMError as exception:
            facts = {"provider": type(self._llm_client).__name__, "reason": str(exception)}
            message = render(ErrorCatalog.LLM_INVOCATION_FAILED, facts, self._language)
            raise ContentValidationError(
                ErrorCatalog.LLM_INVOCATION_FAILED,
                facts,
                language=self._language,
                message=message,
                errors=[SlotValidationError("_llm", ErrorCatalog.LLM_INVOCATION_FAILED.value, message, facts)],
                cause=exception,
            ) from exception
        try:
            parsed = _parse_response(None if response is None else response.content)
        except ContentSemanticValidationError as exception:
            raise _response_invalid(exception, self._language) from exception
        result = _interpret(parsed, self._language)
        _LOGGER.info(
            "semantic_validation_completed verdict=%s error_count=%s param_count=%s",
            result.verdict,
            len(result.errors),
            len(result.params),
        )
        return result

    def _build_messages(
        self,
        prompt: str,
        schema: Mapping[str, object],
        reference: TemplateUri,
        template_content: str,
    ) -> list[dict[str, str]]:
        """Build the system and user messages of the single structured call."""
        filled_user_prompt = _USER_PROMPT_PLACEHOLDER_PATTERN.sub(
            lambda match: _placeholder_replacement(match, prompt, schema, reference, template_content),
            self._user_prompt_template,
        )
        return [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": filled_user_prompt},
        ]

    def _load_prompt_resource(self, file_name: str) -> str:
        """Load one prompt resource of the content validation category.

        Raises:
            a2a_t.core.errors.exceptions.ResourceNotFoundError: when the prompt resource of the
                language is missing from the package (the common layer's
                ``infra.resource_read_failed`` miss translated to the exception the validation
                pipeline maps to ``template.not_found``).
        """
        relative_path = f"prompt_resources/prompts/{_PROMPT_CATEGORY}/{self._language}/{file_name}"
        try:
            return self._access.load_prompt(_PROMPT_CATEGORY, self._language, file_name)
        except A2ATError as error:
            if error.code is not ErrorCatalog.INFRA_RESOURCE_READ_FAILED:
                raise
            raise ResourceNotFoundError(
                "Content validation prompt resource does not exist for language "
                f"{self._language}; set A2AT_LANGUAGE to a language with bundled prompt resources "
                "(zh-CN or en-US).",
                relative_path,
            ) from error


def _placeholder_replacement(
    match: re.Match[str],
    prompt: str,
    schema: Mapping[str, object],
    reference: TemplateUri,
    template_content: str,
) -> str:
    """Return the value one literal bracket token of the user prompt is replaced with."""
    token = match.group(1)
    if token == "extension_name":
        return reference.extension_name
    if token == "input":
        return prompt
    if token == "template_uri":
        return reference.uri
    if token == "template_content":
        return template_content
    if token == "schema":
        return _to_json(schema)
    return match.group(0)


def _to_json(schema: Mapping[str, object] | None) -> str:
    """Serialize one caller schema to compact JSON (Java ``ObjectMapper.writeValueAsString``).

    Raises:
        ContentSemanticValidationError: when the schema is not JSON-serializable.
    """
    try:
        return json.dumps(
            {} if schema is None else dict(schema),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ContentSemanticValidationError("Failed to serialize the caller parameter schema.") from error


def _parse_response(content: str | None) -> dict[str, object]:
    """Parse the LLM response content into the response mapping.

    Raises:
        ContentSemanticValidationError: when the content is not a JSON object.
    """
    if content is None or content.strip() == "":
        raise ContentSemanticValidationError("Semantic validation response is empty.")
    try:
        parsed: object = json.loads(content)
    except ValueError as error:
        raise ContentSemanticValidationError(f"Semantic validation LLM response is not valid JSON: {error}") from error
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ContentSemanticValidationError("Semantic validation LLM response is not a JSON object.")
    return parsed


def _interpret(response: Mapping[str, object], language: str) -> ValidationResult:
    """Interpret one parsed response against the output contract."""
    verdict = response.get(KEY_SEMANTIC_VERDICT)
    if not isinstance(verdict, bool):
        raise ContentSemanticValidationError("Semantic validation response key semantic_verdict must be a boolean.")
    raw_errors: object = response.get(KEY_ERRORS)
    if not isinstance(raw_errors, list):
        raise ContentSemanticValidationError("Semantic validation response key errors must be an array.")
    raw_params: object = response.get(KEY_PARAMS)
    if not isinstance(raw_params, Mapping):
        raise ContentSemanticValidationError("Semantic validation response key params must be an object.")
    errors = _parse_errors(raw_errors, language)
    params = _parse_params(raw_params)
    return ValidationResult(verdict, errors, params)


def _parse_errors(raw_errors: list[object], language: str) -> tuple[SlotValidationError, ...]:
    """Parse the LLM-reported errors, resolving every code against the closed catalog.

    Raises:
        ContentSemanticValidationError: when one reported error violates the error-triple shape.
    """
    errors: list[SlotValidationError] = []
    for raw_error in raw_errors:
        if not isinstance(raw_error, Mapping):
            raise ContentSemanticValidationError(
                "Semantic validation response errors must be objects with slot_name, code and facts."
            )
        slot_name: object = raw_error.get(KEY_SLOT_NAME)
        code: object = raw_error.get(KEY_CODE)
        if not isinstance(slot_name, str) or not isinstance(code, str):
            raise ContentSemanticValidationError(
                "Semantic validation response errors must carry string slot_name and code values."
            )
        raw_facts: object = raw_error.get(KEY_FACTS)
        if not isinstance(raw_facts, Mapping):
            raise ContentSemanticValidationError("Semantic validation response errors must carry a facts object.")
        facts = _string_facts(raw_facts)
        entry = _resolve_code(code)
        if entry.value != code:
            _LOGGER.warning(
                "semantic_validation_unknown_code original_code=%s fallback_code=%s",
                code,
                entry.value,
            )
            facts = {_SECTION_LABEL_FACT: slot_name}
        errors.append(SlotValidationError(slot_name, entry.value, render(entry, facts, language), facts))
    return tuple(errors)


def _resolve_code(code: str) -> ErrorCatalog:
    """Resolve one LLM-reported code to its catalog entry, falling back to ``content.rule_violation``.

    A code outside the closed ``content.*`` domain of the catalog — an unknown spelling or a
    cross-domain code such as a negotiation-domain code arriving in content validation — resolves
    to the fallback entry.
    """
    try:
        entry = by_code(code)
    except KeyError:
        return ErrorCatalog.CONTENT_RULE_VIOLATION
    if entry.value.startswith(_CONTENT_CODE_DOMAIN):
        return entry
    return ErrorCatalog.CONTENT_RULE_VIOLATION


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


def _parse_params(raw_params: Mapping[object, object]) -> dict[str, object]:
    """Parse the extracted parameters, keeping their values verbatim.

    Keys with a ``None`` value are preserved: a ``None`` parameter is the semantic validator's
    explicit signal that a schema slot is missing from the content, and downstream
    missing-parameter detection (negotiation triggering) relies on the key being present with a
    ``None`` value (Java ``parseParams``).
    """
    params: dict[str, object] = {}
    for key, value in raw_params.items():
        if not isinstance(key, str):
            raise ContentSemanticValidationError("Semantic validation response params keys must be strings.")
        params[key] = value
    return params


def _response_invalid(cause: BaseException, language: str) -> ContentValidationError:
    """Build the retryable ``llm.response_invalid`` failure wrapping one contract violation."""
    facts = {"step": CONTENT_SEMANTIC_VALIDATION_STEP}
    message = render(ErrorCatalog.LLM_RESPONSE_INVALID, facts, language)
    return ContentValidationError(
        ErrorCatalog.LLM_RESPONSE_INVALID,
        facts,
        language=language,
        message=message,
        errors=[SlotValidationError("_llm", ErrorCatalog.LLM_RESPONSE_INVALID.value, message, facts)],
        cause=cause,
    )


def _default_access() -> PackagedPromptResourceAccess:
    """Return the module-level packaged access used when no access object is injected."""
    global _DEFAULT_ACCESS
    if _DEFAULT_ACCESS is None:
        _DEFAULT_ACCESS = PackagedPromptResourceAccess()
    return _DEFAULT_ACCESS
