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

"""Content validation of Task-T / Notification-T / Authorization-T prompts.

Port of the Java ``prompt/validation/DefaultContentValidator`` and the server-side
``InputLimitedContentValidator``: the validator orchestrates the shared core
:class:`~a2a_t.core.validation_pipeline.ValidationPipeline` with a no-op rule checker (extension
content carries no negotiation context to rule-check) and the LLM-backed
:class:`~a2a_t.server.prompt_compliance.content_semantic_validator.DefaultContentSemanticValidator`
of the ``content_validation`` prompt category.

Stage order: template URI normalization (fail-fast) → input length gate (the
:class:`InputLimitedContentValidator` wrapper, ``input.text_too_long``) → extension and version
gate (``negotiation.invalid_input``) → template content load (``template.not_found``) → the core
pipeline with the preloaded template body (retryable semantic gate, ``negotiation.semantic_rejected``
on a negative verdict, deterministic parameter merge). The template body is resolved through the
common resource access layer following the configured source routing (D31), while the
``content_validation`` prompt resources are the packaged SDK contract.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from a2a_t.common.prompt_resources.resource_access import PromptResourceAccess
from a2a_t.config.models import A2ATConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import (
    A2ATBusinessError,
    ContentValidationError,
    ResourceNotFoundError,
)
from a2a_t.core.errors.input_limit import InputLimitConfig
from a2a_t.core.errors.messages import render
from a2a_t.core.standard_templates import (
    AUTHORIZATION_EXTENSION_NAME,
    NOTIFICATION_EXTENSION_NAME,
    TASK_EXTENSION_NAME,
)
from a2a_t.core.template_uri import DEFAULT_TEMPLATE_VERSION, TemplateUri
from a2a_t.core.validation_pipeline import ContentValidator, FilledParamData, ValidationPipeline
from a2a_t.llm.provider import LLMClient

from .content_semantic_validator import DefaultContentSemanticValidator

__all__ = [
    "DefaultContentValidator",
    "InputLimitedContentValidator",
    "build_content_validator",
    "build_content_validators",
]

_LOGGER = logging.getLogger(__name__)


class _NoOpRuleChecker:
    """Rule-level checker of extension content: no rules, no context parameters (Java ``Map.of()``)."""

    def check(self, prompt: str) -> dict[str, object]:
        """Return the empty context parameter map without inspecting the prompt."""
        return {}


class DefaultContentValidator:
    """Default content validator orchestrating the full validation pipeline for one extension.

    The validator is bound to one extension name (``Task-T``, ``Notification-T`` or
    ``Authorization-T``): every validated template URI must address that extension and carry the
    default template version. The validation pipeline and the ``content_validation`` prompt
    resources are assembled eagerly in the constructor, so a missing prompt resource fails at
    assembly time instead of on the first :meth:`validate` call.
    """

    def __init__(
        self,
        *,
        extension_name: str,
        language: str,
        max_attempts: int,
        llm_client: LLMClient | None,
        resource_access: PromptResourceAccess,
    ) -> None:
        """Create one content validator for the given extension name and language.

        Args:
            extension_name: extension name used for the template URI gate, such as ``Task-T``.
            language: language code for prompt resource loading, template loading and message
                rendering.
            max_attempts: maximum retry attempts of the semantic validation step, at least 1.
            llm_client: LLM client for semantic validation; ``None`` makes the first
                :meth:`validate` call fail with ``llm.not_configured``.
            resource_access: resource access object resolving the template bodies (the D31 seam).

        Raises:
            ValueError: when the attempt limit is below 1.
            ContentValidationError: with ``template.not_found`` when the ``content_validation``
                prompt resources of the given language are missing.
        """
        if max_attempts < 1:
            raise ValueError(f"Semantic validation max attempts must be at least 1 but was {max_attempts}.")
        self._extension_name = extension_name
        self._language = language
        self._resource_access = resource_access
        try:
            self._pipeline: ValidationPipeline[TemplateUri] = ValidationPipeline(
                rule_checker=_NoOpRuleChecker(),
                semantic_validator=DefaultContentSemanticValidator(llm_client, language, access=resource_access),
                max_attempts=max_attempts,
                language=language,
            )
        except ResourceNotFoundError as error:
            facts = {"template_uri": error.resource_path, "language": language}
            raise ContentValidationError(
                ErrorCatalog.TEMPLATE_NOT_FOUND,
                facts,
                language=language,
                message=render(ErrorCatalog.TEMPLATE_NOT_FOUND, facts, language),
                cause=error,
            ) from error

    def validate(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate one content prompt and extract its filled parameters.

        Args:
            prompt: rendered content prompt text.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: URI of the template the content is validated against, such as
                ``Task-T/network-layer/ran-energy-saving/v1``; the raw string spelling is parsed
                fail-fast (D16).

        Returns:
            filled parameter data carrying the merged parameters.

        Raises:
            TypeError: when the template URI is ``None``.
            ValueError: when the template URI is blank or malformed.
            ContentValidationError: with ``negotiation.invalid_input`` when the template URI
                addresses another extension than the one this validator is configured for, or
                carries an unsupported version, or the prompt is blank or the schema is ``None``;
                ``template.not_found`` when the template cannot be loaded; or any other failure of
                the validation pipeline.
        """
        parsed_template_uri = _template_uri_of(template_uri)
        if parsed_template_uri.extension_name != self._extension_name:
            raise self._invalid_input(
                f"Template URI extension '{parsed_template_uri.extension_name}' does not match "
                f"expected extension '{self._extension_name}'."
            )
        if parsed_template_uri.template_version != DEFAULT_TEMPLATE_VERSION:
            raise self._invalid_input(f"Unsupported template URI version: {parsed_template_uri.template_version}")
        template_content = self._load_template_content(parsed_template_uri)
        return self._pipeline.validate(prompt, schema, parsed_template_uri, template_content)

    def _load_template_content(self, template_uri: TemplateUri) -> str:
        """Resolve the template body of one reference through the access layer.

        Raises:
            ContentValidationError: with ``template.not_found`` when the addressed template does
                not exist for the configured language.
        """
        try:
            return self._resource_access.template_text(template_uri.uri, self._language)
        except ResourceNotFoundError as error:
            raise self._template_not_found(template_uri, error) from error
        except A2ATBusinessError as error:
            if error.code is not ErrorCatalog.TEMPLATE_NOT_FOUND:
                raise
            raise self._template_not_found(template_uri, error) from error

    def _template_not_found(self, template_uri: TemplateUri, cause: BaseException) -> ContentValidationError:
        """Build the ``template.not_found`` failure of one template resolution miss."""
        facts = {"template_uri": template_uri.uri, "language": self._language}
        return ContentValidationError(
            ErrorCatalog.TEMPLATE_NOT_FOUND,
            facts,
            language=self._language,
            message=render(ErrorCatalog.TEMPLATE_NOT_FOUND, facts, self._language),
            cause=cause,
        )

    def _invalid_input(self, reason: str) -> ContentValidationError:
        """Build the ``negotiation.invalid_input`` failure of one gate rejection."""
        facts = {"reason": reason}
        return ContentValidationError(
            ErrorCatalog.NEGOTIATION_INVALID_INPUT,
            facts,
            language=self._language,
            message=render(ErrorCatalog.NEGOTIATION_INVALID_INPUT, facts, self._language),
        )


class InputLimitedContentValidator:
    """Free-text input gate wrapped around one delegated content validator.

    The gate rejects an oversized prompt with the code ``input.text_too_long`` before the delegated
    pipeline starts, so no LLM call is made for an input that could not fit the LLM context anyway.
    The limit is configured through ``A2AT_INPUT_TEXT_MAX_CHARS`` and defaults to 16384 characters.
    """

    def __init__(
        self,
        delegate: ContentValidator,
        max_text_chars: int,
        language: str | None = None,
    ) -> None:
        """Create one gating validator around the given delegate.

        Args:
            delegate: content validator carrying the actual validation pipeline.
            max_text_chars: maximum length in characters accepted for the prompt text.
            language: language used to render the rejection message, for example ``zh-CN``;
                ``None`` falls back to ``en-US``.
        """
        self._delegate = delegate
        self._input_limit = InputLimitConfig(max_text_chars)
        self._language = language

    @property
    def delegate(self) -> ContentValidator:
        """The wrapped content validator (exposed for diagnostics and tests)."""
        return self._delegate

    def validate(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate one content prompt and extract its filled parameters, gated by the input limit.

        Raises:
            ContentValidationError: with ``input.text_too_long`` when the prompt exceeds the
                configured maximum length, or any failure of the delegated validator.
        """
        if self._input_limit.is_too_long(prompt):
            facts = self._input_limit.too_long_facts(prompt)
            raise ContentValidationError(
                ErrorCatalog.INPUT_TEXT_TOO_LONG,
                facts,
                language=self._language,
                message=render(ErrorCatalog.INPUT_TEXT_TOO_LONG, facts, self._language),
            )
        return self._delegate.validate(prompt, schema, _template_uri_of(template_uri))


def build_content_validator(
    *,
    extension_name: str,
    config: A2ATConfig,
    llm_client: LLMClient | None,
    resource_access: PromptResourceAccess | None = None,
) -> InputLimitedContentValidator:
    """Assemble one input-limited content validator for the given extension.

    The wiring mirrors the Java server builder's ``buildContentValidator``: the validator enforces
    the extension prefix, reuses the configured language, the LLM retry attempt limit, the input
    length limit and the LLM client, and resolves the template bodies through the configured
    resource source (D31: the common access layer instead of the Java classpath loader).

    Args:
        extension_name: extension name the validator enforces, such as ``Task-T``.
        config: unified SDK configuration.
        llm_client: LLM client for the semantic validation step.
        resource_access: resource access object resolving the template bodies; ``None`` routes one
            access from the configured prompt source type.

    Returns:
        the assembled input-limited content validator.
    """
    from a2a_t.common.prompt_resources import create

    effective_access = create(config.prompt) if resource_access is None else resource_access
    return _build_gated_validator(
        extension_name=extension_name,
        config=config,
        llm_client=llm_client,
        resource_access=effective_access,
    )


def build_content_validators(
    *,
    config: A2ATConfig,
    llm_client: LLMClient | None,
    resource_access: PromptResourceAccess | None = None,
) -> dict[str, InputLimitedContentValidator]:
    """Assemble the task, notification and authorization content validators (Java server wiring).

    Args:
        config: unified SDK configuration.
        llm_client: LLM client shared by the semantic validation steps.
        resource_access: resource access object resolving the template bodies; ``None`` routes one
            access from the configured prompt source type and shares it across the three
            validators.

    Returns:
        the validators keyed by extension name (``Task-T``, ``Notification-T``, ``Authorization-T``).

    Raises:
        ContentValidationError: with ``template.not_found`` when the ``content_validation`` prompt
            resources of the configured language are missing (eager assembly).
    """
    from a2a_t.common.prompt_resources import create

    effective_access = create(config.prompt) if resource_access is None else resource_access
    return {
        extension_name: _build_gated_validator(
            extension_name=extension_name,
            config=config,
            llm_client=llm_client,
            resource_access=effective_access,
        )
        for extension_name in (TASK_EXTENSION_NAME, NOTIFICATION_EXTENSION_NAME, AUTHORIZATION_EXTENSION_NAME)
    }


def _build_gated_validator(
    *,
    extension_name: str,
    config: A2ATConfig,
    llm_client: LLMClient | None,
    resource_access: PromptResourceAccess,
) -> InputLimitedContentValidator:
    """Assemble one input-limited content validator over one already-routed resource access."""
    return InputLimitedContentValidator(
        DefaultContentValidator(
            extension_name=extension_name,
            language=config.prompt.language,
            max_attempts=config.llm.max_attempts,
            llm_client=llm_client,
            resource_access=resource_access,
        ),
        config.input_limits.max_text_chars,
        config.prompt.language,
    )


def _template_uri_of(template_uri: str | TemplateUri | None) -> TemplateUri:
    """Normalize the template URI boundary argument into its typed form, fail-fast (D16).

    Mirrors the Java facades' ``parseTemplateUri`` helper: ``None`` is a ``TypeError``, a malformed
    URI a ``ValueError``; a typed :class:`~a2a_t.core.template_uri.TemplateUri` is the accepted dual
    internal spelling.
    """
    if template_uri is None:
        raise TypeError("templateUri")
    if isinstance(template_uri, TemplateUri):
        return template_uri
    parsed = TemplateUri.parse(template_uri)
    if parsed is None:
        raise ValueError(f"Unparseable template URI: {template_uri}")
    return parsed
