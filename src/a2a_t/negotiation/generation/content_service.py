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

"""Shared service for the negotiation content layer (port of Java ``NegotiationContentService``).

Consumed by both the client and the server facade: each facade method delegates to exactly one
service method, so the negotiation content-layer surface is defined once instead of being
copy-pasted per facade. The service itself is a thin typing over the
:class:`~a2a_t.negotiation.generation.orchestrator.NegotiationGenerationOrchestrator` pipeline and
adds no behavior — apart from the template URI boundary: every method takes the template URI in its
raw string spelling (D16, Java 1.1.0 ``#176``: SDK-facing APIs favor strings) and parses it
fail-fast into the typed :class:`~a2a_t.core.template_uri.TemplateUri` the orchestrator consumes,
mirroring the Java facades' ``parseTemplateUri`` helper. A typed :class:`TemplateUri` is accepted
as the dual internal spelling.
"""

from __future__ import annotations

from collections.abc import Mapping

from a2a_t.config.models import A2ATConfig
from a2a_t.core.metadata import MetadataContent, NegotiationContext
from a2a_t.core.template_uri import TemplateUri
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.llm.provider import LLMClient

from ..content.models import (
    NegotiationAbortData,
    NegotiationEndingData,
    NegotiationProposeData,
)
from .builder import NegotiationGenerationOrchestratorBuilder
from .orchestrator import NegotiationGenerationOrchestrator

__all__ = ["NegotiationContentService"]


class NegotiationContentService:
    """The 12-method negotiation content-layer surface shared by the client and server facades.

    Eight generation methods (``generate_{propose,accept,reject,abort}_from_{data,text}``) and four
    validation methods (``validate_{propose,accept,reject,abort}_prompt_and_data_filling``) over
    one injected orchestrator.
    """

    def __init__(self, orchestrator: NegotiationGenerationOrchestrator) -> None:
        """Create one service over the given negotiation generation orchestrator.

        Args:
            orchestrator: negotiation generation orchestrator carrying the actual pipelines.

        Raises:
            TypeError: when the orchestrator is ``None`` (Java ``NullPointerException`` parity).
        """
        if orchestrator is None:
            raise TypeError("Negotiation orchestrator must not be null.")
        self._orchestrator = orchestrator

    @classmethod
    def build_orchestrator(cls, config: A2ATConfig, llm_client: LLMClient | None) -> NegotiationGenerationOrchestrator:
        """Assemble the default negotiation generation orchestrator from the unified SDK config.

        The wiring is shared by the client and the server builder: the message language comes from
        the prompt runtime config (negotiation templates and vocabularies route through the common
        resource access layer, D31), the retry attempt limit comes from the LLM config, and the LLM
        client is passed by the caller and may be ``None`` when the provider is ``local_rule``.

        Args:
            config: unified SDK config.
            llm_client: LLM client for the LLM-backed steps; ``None`` keeps those steps unavailable.

        Returns:
            the assembled negotiation generation orchestrator.
        """
        return NegotiationGenerationOrchestratorBuilder(
            language=config.prompt.language,
            llm_client=llm_client,
            max_attempts=config.llm.max_attempts,
            max_text_chars=config.input_limits.max_text_chars,
        ).build()

    # ------------------------------------------------------------------
    # From-data generation (deterministic, zero LLM)
    # ------------------------------------------------------------------

    def generate_propose_from_data(
        self, data: NegotiationProposeData, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate a propose negotiation message from typed data, deterministically without any LLM call.

        Args:
            data: typed propose input carrying the negotiation context and the typed content.
            template_uri: template URI whose performative segment must be ``propose``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the data, its context or the template URI is ``None``.
            ValueError: when the template URI's performative or type contradicts the method or the
                content type, or the template URI is unparseable.
            NegotiationGenerationError: with the code ``negotiation.content_invalid`` when a
                required content field is blank or empty, or ``negotiation.invalid_input`` when the
                content combines a non-blank confirm request with conditional sections, or
                ``template.not_found`` / ``template.render_failed`` when loading or rendering the
                template fails.
        """
        return self._orchestrator.generate_propose_from_data(data, _template_uri_of(template_uri))

    def generate_accept_from_data(
        self, data: NegotiationEndingData, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate an accept negotiation message from typed data, deterministically without any LLM call.

        Args:
            data: typed terminal input whose content conclusion must be ``Accept``.
            template_uri: template URI whose performative segment must be ``accept-reject``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the data, its context or the template URI is ``None``.
            ValueError: when the template URI's performative or type contradicts the method or the
                content, or the template URI is unparseable.
            NegotiationGenerationError: with the code ``negotiation.conclusion_mismatch`` when the
                content conclusion is not ``Accept``, or ``negotiation.content_invalid`` when a
                required content field is blank or empty, or ``template.not_found`` /
                ``template.render_failed`` when loading or rendering the template fails.
        """
        return self._orchestrator.generate_accept_from_data(data, _template_uri_of(template_uri))

    def generate_reject_from_data(
        self, data: NegotiationEndingData, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate a reject negotiation message from typed data, deterministically without any LLM call.

        Args:
            data: typed terminal input whose content conclusion must be ``Reject``.
            template_uri: template URI whose performative segment must be ``accept-reject``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the data, its context or the template URI is ``None``.
            ValueError: when the template URI's performative or type contradicts the method or the
                content, or the template URI is unparseable.
            NegotiationGenerationError: with the code ``negotiation.conclusion_mismatch`` when the
                content conclusion is not ``Reject``, or ``negotiation.content_invalid`` when a
                required content field is blank or empty, or ``template.not_found`` /
                ``template.render_failed`` when loading or rendering the template fails.
        """
        return self._orchestrator.generate_reject_from_data(data, _template_uri_of(template_uri))

    def generate_abort_from_data(self, data: NegotiationAbortData, template_uri: str | TemplateUri) -> MetadataContent:
        """Generate an abort negotiation message from typed data, deterministically without any LLM call.

        Args:
            data: typed abort input carrying the termination reason.
            template_uri: template URI of the common abort template ``Negotiation-T/common/abort/v1``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the data, its context or the template URI is ``None``.
            ValueError: when the template URI does not address the common abort template, or the
                template URI is unparseable.
            NegotiationGenerationError: with the code ``negotiation.content_invalid`` when the
                termination reason is blank, or ``template.not_found`` / ``template.render_failed``
                when loading or rendering the template fails.
        """
        return self._orchestrator.generate_abort_from_data(data, _template_uri_of(template_uri))

    # ------------------------------------------------------------------
    # From-text generation (one LLM content-extraction step)
    # ------------------------------------------------------------------

    def generate_propose_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate a propose negotiation message from free text through one LLM content-extraction step.

        Args:
            text: free-text input describing the message content.
            context: negotiation context carried in the ``negotiationContext`` metadata entry of
                the generated message without any LLM involvement.
            template_uri: template URI whose performative segment must be ``propose``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the context or the template URI is ``None``.
            ValueError: when the template URI contradicts the method or is unparseable.
            NegotiationGenerationError: with the code ``template.not_found``, one of the retryable
                codes when loading or extracting fails, ``template.render_failed`` when rendering
                the template fails, ``negotiation.field_missing`` when the extracted content misses
                a required field, ``negotiation.invalid_input`` when the text is blank or the
                extracted content contradicts the performative, or ``input.text_too_long`` when the
                text exceeds the configured maximum length.
        """
        return self._orchestrator.generate_propose_from_text(text, context, _template_uri_of(template_uri))

    def generate_accept_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate an accept negotiation message from free text through one LLM content-extraction step.

        Args:
            text: free-text input describing the message content.
            context: negotiation context carried in the ``negotiationContext`` metadata entry of
                the generated message without any LLM involvement.
            template_uri: template URI whose performative segment must be ``accept-reject``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the context or the template URI is ``None``.
            ValueError: when the template URI contradicts the method or is unparseable.
            NegotiationGenerationError: with the code ``template.not_found``, one of the retryable
                codes when loading or extracting fails, ``template.render_failed`` when rendering
                the template fails, ``negotiation.field_missing`` when the extracted content misses
                a required field, ``negotiation.invalid_input`` when the text is blank,
                ``negotiation.conclusion_mismatch`` when the extracted conclusion is not
                ``Accept``, or ``input.text_too_long`` when the text exceeds the configured maximum
                length.
        """
        return self._orchestrator.generate_accept_from_text(text, context, _template_uri_of(template_uri))

    def generate_reject_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate a reject negotiation message from free text through one LLM content-extraction step.

        Args:
            text: free-text input describing the message content.
            context: negotiation context carried in the ``negotiationContext`` metadata entry of
                the generated message without any LLM involvement.
            template_uri: template URI whose performative segment must be ``accept-reject``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the context or the template URI is ``None``.
            ValueError: when the template URI contradicts the method or is unparseable.
            NegotiationGenerationError: with the code ``template.not_found``, one of the retryable
                codes when loading or extracting fails, ``template.render_failed`` when rendering
                the template fails, ``negotiation.field_missing`` when the extracted content misses
                a required field, ``negotiation.invalid_input`` when the text is blank,
                ``negotiation.conclusion_mismatch`` when the extracted conclusion is not
                ``Reject``, or ``input.text_too_long`` when the text exceeds the configured maximum
                length.
        """
        return self._orchestrator.generate_reject_from_text(text, context, _template_uri_of(template_uri))

    def generate_abort_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate an abort negotiation message from free text through one LLM content-extraction step.

        Args:
            text: free-text input stating the termination reason.
            context: negotiation context carried in the ``negotiationContext`` metadata entry of
                the generated message without any LLM involvement.
            template_uri: template URI of the common abort template ``Negotiation-T/common/abort/v1``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the context or the template URI is ``None``.
            ValueError: when the template URI does not address the common abort template, or the
                template URI is unparseable.
            NegotiationGenerationError: with the code ``template.not_found``, one of the retryable
                codes when loading or extracting fails, ``template.render_failed`` when rendering
                the template fails, ``negotiation.field_missing`` when the extracted content misses
                the termination reason, ``negotiation.invalid_input`` when the text is blank, or
                ``input.text_too_long`` when the text exceeds the configured maximum length.
        """
        return self._orchestrator.generate_abort_from_text(text, context, _template_uri_of(template_uri))

    # ------------------------------------------------------------------
    # Validation and parameter filling
    # ------------------------------------------------------------------

    def validate_propose_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate a propose negotiation message and extract its parameters.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata;
                ``None`` is reported as not being a negotiation message.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI whose performative segment must be ``propose``.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            TypeError: when the schema or the template URI is ``None``.
            ValueError: when the template URI contradicts the method or is unparseable.
            NegotiationParamExtractionError: with the code ``input.text_too_long`` when the prompt
                exceeds the configured maximum length, or the failure of the wired validation
                pipeline (Java: ``negotiation.invalid_input``, ``negotiation.rule_violation``,
                ``negotiation.semantic_rejected``, ``llm.invocation_failed``,
                ``llm.response_invalid`` or ``template.not_found``).
        """
        return self._orchestrator.validate_propose_prompt_and_data_filling(
            prompt, context, schema, _template_uri_of(template_uri)
        )

    def validate_accept_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate an accept negotiation message and extract its parameters.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata;
                ``None`` is reported as not being a negotiation message.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI whose performative segment must be ``accept-reject``.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            TypeError: when the schema or the template URI is ``None``.
            ValueError: when the template URI contradicts the method or is unparseable.
            NegotiationParamExtractionError: with the code ``input.text_too_long`` when the prompt
                exceeds the configured maximum length, or the failure of the wired validation
                pipeline.
        """
        return self._orchestrator.validate_accept_prompt_and_data_filling(
            prompt, context, schema, _template_uri_of(template_uri)
        )

    def validate_reject_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate a reject negotiation message and extract its parameters.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata;
                ``None`` is reported as not being a negotiation message.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI whose performative segment must be ``accept-reject``.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            TypeError: when the schema or the template URI is ``None``.
            ValueError: when the template URI contradicts the method or is unparseable.
            NegotiationParamExtractionError: with the code ``input.text_too_long`` when the prompt
                exceeds the configured maximum length, or the failure of the wired validation
                pipeline.
        """
        return self._orchestrator.validate_reject_prompt_and_data_filling(
            prompt, context, schema, _template_uri_of(template_uri)
        )

    def validate_abort_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate an abort negotiation message and extract its parameters.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata;
                ``None`` is reported as not being a negotiation message.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI of the common abort template ``Negotiation-T/common/abort/v1``.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            TypeError: when the schema or the template URI is ``None``.
            ValueError: when the template URI does not address the common abort template, or the
                template URI is unparseable.
            NegotiationParamExtractionError: with the code ``input.text_too_long`` when the prompt
                exceeds the configured maximum length, or the failure of the wired validation
                pipeline.
        """
        return self._orchestrator.validate_abort_prompt_and_data_filling(
            prompt, context, schema, _template_uri_of(template_uri)
        )


def _template_uri_of(template_uri: str | TemplateUri | None) -> TemplateUri:
    """Normalize the template URI boundary argument into its typed form, fail-fast (D16).

    The raw string spelling is parsed with the core URI type; an unparseable URI is a programming
    error rejected immediately, mirroring the Java facades' ``parseTemplateUri`` helper (``None``
    is a ``NullPointerException``, a malformed URI an ``IllegalArgumentException``). A typed
    :class:`~a2a_t.core.template_uri.TemplateUri` is the accepted dual internal spelling.

    Raises:
        TypeError: when the template URI is ``None`` — carrying the Java-parity message of
            ``NegotiationReference.fromTemplateUri``, the seam that rejects the null URI in Java.
        ValueError: when the raw template URI is unparseable.
    """
    if template_uri is None:
        raise TypeError("Template URI must not be null.")
    if isinstance(template_uri, TemplateUri):
        return template_uri
    parsed = TemplateUri.parse(template_uri)
    if parsed is None:
        raise ValueError(f"Unparseable template URI: {template_uri}")
    return parsed
