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

"""Orchestrator of the negotiation content layer (port of Java ``NegotiationGenerationOrchestrator``).

The orchestrator owns the two generation legs and the validation leg of the negotiation content
layer:

======================  =====================================================================
leg                     steps
======================  =====================================================================
from-data generation   reference check → template load → registry dispatch → render →
                        ``MetadataContent`` with the stamped negotiation context
from-text generation   reference check → input-length gate → template load → LLM content
                        extraction → registry dispatch → render → ``MetadataContent``
validation + filling   schema check → input-length gate → reference check → param extractor
======================  =====================================================================

The from-data leg is deterministic and never calls an LLM. The from-text leg runs one LLM
content-extraction step whose retry loop lives inside the injected extractor (P5-B design): a step
failing with one of the retryable codes ``negotiation.content_extract_failed``,
``llm.invocation_failed`` or ``llm.response_invalid`` is re-run up to the configured attempt limit,
any other code is re-raised immediately, and the exhaustion failure re-raises the original error
code — the observable semantics of the Java orchestrator's ``withRetry``, pinned by the exact
LLM-call-count tests of the extractor suite. The same holds for the semantic validation step of the
validation leg, whose retry is owned by the shared core
:class:`~a2a_t.core.validation_pipeline.ValidationPipeline` (P6).

Error translation catch points (Java parity, every catch of the Java orchestrator):

===============================  ==========================================================
catch point                      surfaced failure
===============================  ==========================================================
template load miss (either leg)  ``NegotiationGenerationError`` ``template.not_found`` with
                                 the ``template_uri`` and ``language`` facts
extraction prompt miss           ``NegotiationGenerationError`` ``template.not_found`` with
                                 the ``template_uri`` and ``language`` facts (the Java
                                 ``extractContent`` catch point)
render failure                   ``NegotiationGenerationError`` ``template.render_failed``
                                 with the ``template_uri`` and ``reason`` facts (the internal
                                 :class:`~a2a_t.negotiation.generation.prompt_renderer.NegotiationRenderError`
                                 never leaks)
oversized free-text input        ``NegotiationGenerationError`` ``input.text_too_long``
                                 (from-text leg) / ``NegotiationParamExtractionError``
                                 ``input.text_too_long`` (validation leg), both with the
                                 ``actual_length`` and ``max_chars`` facts, raised before any
                                 LLM call
validation pipeline failure      the extractor's own ``NegotiationParamExtractionError``
                                 re-thrown unchanged (logged at WARN)
wrong template URI               ``ValueError`` (Java ``IllegalArgumentException``): the URI
                                 does not address a negotiation template of the expected
                                 performative
null data / context / schema     ``TypeError`` (Java ``NullPointerException``)
===============================  ==========================================================

Two port-specific seams replace Java collaborators: the template loader is the common resource
access layer (D31 — the Java ``DefaultNegotiationTemplateLoader`` is not ported), and the parameter
extractor of the validation leg is the :class:`NegotiationParamExtractor` protocol below, whose
default implementation is the P6 validation pipeline
(:class:`a2a_t.negotiation.validation.param_extractor.ParamExtractor`, wired by the builder).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Protocol

from a2a_t.common.prompt_resources.resource_access import PromptResourceAccess
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import (
    A2ATBusinessError,
    NegotiationGenerationError,
    NegotiationParamExtractionError,
    ResourceNotFoundError,
)
from a2a_t.core.metadata import (
    NEGOTIATION_T_EXTENSION_URI,
    MetadataContent,
    NegotiationContext,
    NegotiationPerformative,
)
from a2a_t.core.template_uri import TemplateUri
from a2a_t.core.validation_pipeline import FilledParamData

from ..content.models import (
    NegotiationAbortData,
    NegotiationContent,
    NegotiationEndingData,
    NegotiationProposeData,
)
from ..content.vocabulary import Vocabulary
from ..resources.reference import NegotiationReference, uri_segment_of
from .content_extractor import NegotiationContentExtractor
from .generators import resolve
from .prompt_renderer import NegotiationRenderError

__all__ = [
    "NegotiationGenerationOrchestrator",
    "NegotiationParamExtractor",
]

logger = logging.getLogger(__name__)


class NegotiationParamExtractor(Protocol):
    """Validates one negotiation message and extracts its parameters.

    The collaboration seam of the validation leg (Java ``ParamExtractor`` of the validation
    package): the implementation runs the rule-level gate, the template loading gate, the
    retryable semantic validation step and the deterministic parameter merge, and surfaces every
    failure as a :class:`~a2a_t.core.errors.exceptions.NegotiationParamExtractionError` carrying
    its final catalog code — the orchestrator adds no translation of its own. The default
    implementation is delivered by the P6 validation pipeline.
    """

    def extract(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        reference: NegotiationReference,
    ) -> FilledParamData:
        """Validate one negotiation message and extract its filled parameters.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata;
                ``None`` is reported as the message not being a negotiation message.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            reference: reference the message is validated against.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            NegotiationParamExtractionError: with one of the codes ``negotiation.invalid_input``,
                ``negotiation.rule_violation``, ``negotiation.semantic_rejected``,
                ``llm.invocation_failed``, ``llm.response_invalid``, ``input.text_too_long`` or
                ``template.not_found`` when the validation pipeline fails.
        """
        ...


class NegotiationGenerationOrchestrator:
    """Orchestrates the negotiation content layer.

    Instances are created through :class:`a2a_t.negotiation.generation.builder.builder`, which
    wires the default collaborators and allows overriding each of them.
    """

    def __init__(
        self,
        *,
        language: str,
        max_text_chars: int,
        resource_access: PromptResourceAccess,
        content_extractor: NegotiationContentExtractor,
        param_extractor: NegotiationParamExtractor | None,
        vocabulary: Vocabulary,
    ) -> None:
        """Create one orchestrator over the given collaborators.

        Args:
            language: language of the generated and validated messages.
            max_text_chars: maximum accepted length in characters of free-text inputs before they
                reach an LLM step.
            resource_access: resource access object resolving the negotiation templates (D31).
            content_extractor: extractor turning free text into typed negotiation content.
            param_extractor: parameter extractor of the validation leg; ``None`` keeps that leg
                unwired (the P6 validation pipeline provides the default).
            vocabulary: vocabulary of the message language.
        """
        self._language = language
        self._max_text_chars = max_text_chars
        self._resource_access = resource_access
        self._content_extractor = content_extractor
        self._param_extractor = param_extractor
        self._vocabulary = vocabulary

    @property
    def language(self) -> str:
        """Language of the generated and validated messages."""
        return self._language

    # ------------------------------------------------------------------
    # From-data generation (deterministic, zero LLM)
    # ------------------------------------------------------------------

    def generate_propose_from_data(self, data: NegotiationProposeData, template_uri: TemplateUri) -> MetadataContent:
        """Generate a propose-phase negotiation message from typed data.

        This variant is deterministic and never calls an LLM: the typed content is validated,
        dispatched to the generator of the negotiation type addressed by the template URI and
        rendered from that template.

        Args:
            data: typed propose input carrying the negotiation context and the typed content.
            template_uri: template URI such as ``Negotiation-T/information-negotiation/propose/v1``;
                its phase segment must be ``propose``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI; the emitted negotiation context is the input context
            stamped with the performative of the addressed template, so it may differ from the
            context the caller passed in.

        Raises:
            TypeError: when the data, its context or the template URI is ``None``.
            ValueError: when the template URI's phase or type contradicts the method or the
                content type.
            NegotiationGenerationError: with the code ``template.not_found`` when no template
                exists for the URI, or the code ``template.render_failed`` when rendering the
                template fails.
        """
        if data is None:
            raise TypeError("Negotiation propose data must not be null.")
        return self._generate_from_data(data.context, data.content, template_uri, NegotiationPerformative.PROPOSE)

    def generate_accept_from_data(self, data: NegotiationEndingData, template_uri: TemplateUri) -> MetadataContent:
        """Generate an accept-phase negotiation message from typed data.

        This variant is deterministic and never calls an LLM. The content conclusion must be
        ``Accept``; a mismatched conclusion is a content error.

        Args:
            data: typed terminal input carrying the negotiation context and the typed ending
                content.
            template_uri: template URI such as ``Negotiation-T/information-negotiation/accept-reject/v1``;
                its phase segment must be ``accept-reject``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI; the emitted negotiation context is the input context
            stamped with the performative of the addressed template.

        Raises:
            TypeError: when the data, its context or the template URI is ``None``.
            ValueError: when the template URI's phase or type contradicts the method or the
                content.
            NegotiationGenerationError: with the code ``negotiation.conclusion_mismatch`` when the
                content conclusion is not ``Accept``, or ``negotiation.content_invalid`` when a
                required content field is blank or empty, or ``template.not_found`` /
                ``template.render_failed`` when loading or rendering the template fails.
        """
        if data is None:
            raise TypeError("Negotiation ending data must not be null.")
        return self._generate_from_data(data.context, data.content, template_uri, NegotiationPerformative.ACCEPT)

    def generate_reject_from_data(self, data: NegotiationEndingData, template_uri: TemplateUri) -> MetadataContent:
        """Generate a reject-phase negotiation message from typed data.

        This variant is deterministic and never calls an LLM. The content conclusion must be
        ``Reject``; a mismatched conclusion is a content error.

        Args:
            data: typed terminal input carrying the negotiation context and the typed ending
                content.
            template_uri: template URI such as ``Negotiation-T/feasibility-negotiation/accept-reject/v1``;
                its phase segment must be ``accept-reject``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI; the emitted negotiation context is the input context
            stamped with the performative of the addressed template.

        Raises:
            TypeError: when the data, its context or the template URI is ``None``.
            ValueError: when the template URI's phase or type contradicts the method or the
                content.
            NegotiationGenerationError: with the code ``negotiation.conclusion_mismatch`` when the
                content conclusion is not ``Reject``, or ``negotiation.content_invalid`` when a
                required content field is blank or empty, or ``template.not_found`` /
                ``template.render_failed`` when loading or rendering the template fails.
        """
        if data is None:
            raise TypeError("Negotiation ending data must not be null.")
        return self._generate_from_data(data.context, data.content, template_uri, NegotiationPerformative.REJECT)

    def generate_abort_from_data(self, data: NegotiationAbortData, template_uri: TemplateUri) -> MetadataContent:
        """Generate an abort negotiation message from typed data.

        This variant is deterministic and never calls an LLM. Abort messages are
        type-independent: the addressed template must be the common abort template and the content
        carries only the termination reason.

        Args:
            data: typed abort input carrying the negotiation context and the termination reason.
            template_uri: template URI of the common abort template ``Negotiation-T/common/abort/v1``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI; the emitted negotiation context is the input context
            stamped with the performative of the addressed template.

        Raises:
            TypeError: when the data, its context or the template URI is ``None``.
            ValueError: when the template URI does not address the common abort template.
            NegotiationGenerationError: with the code ``negotiation.content_invalid`` when the
                termination reason is blank, or ``template.not_found`` / ``template.render_failed``
                when loading or rendering the template fails.
        """
        if data is None:
            raise TypeError("Negotiation abort data must not be null.")
        return self._generate_from_data(data.context, data.content, template_uri, NegotiationPerformative.ABORT)

    # ------------------------------------------------------------------
    # From-text generation (one LLM content-extraction step)
    # ------------------------------------------------------------------

    def generate_propose_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: TemplateUri
    ) -> MetadataContent:
        """Generate a propose-phase negotiation message from free text.

        This variant runs one LLM content-extraction step constrained by the template URI and then
        renders deterministically like the from-data variant. The template is loaded before the
        LLM call and the extraction step is retried up to the configured attempt limit on the
        retryable failure codes.

        Args:
            text: free-text input describing the message content.
            context: negotiation context carried in the ``negotiationContext`` metadata entry of
                the generated message without any LLM involvement.
            template_uri: template URI such as ``Negotiation-T/target-negotiation/propose/v1``; its
                phase segment must be ``propose``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI; the emitted negotiation context is the input context
            stamped with the performative of the addressed template.

        Raises:
            TypeError: when the context or the template URI is ``None``.
            ValueError: when the template URI's phase contradicts the method.
            NegotiationGenerationError: with the code ``template.not_found`` when no template or
                prompt resource exists for the URI and language, one of the retryable codes when
                the extraction step fails after exhausting its retries,
                ``negotiation.field_missing`` when the extracted content misses a required field,
                ``negotiation.invalid_input`` when the text is blank or the extracted content
                contradicts the phase, ``template.render_failed`` when rendering the template
                fails, or ``input.text_too_long`` when the text exceeds the configured maximum
                length.
        """
        return self._generate_from_text(text, context, template_uri, NegotiationPerformative.PROPOSE)

    def generate_accept_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: TemplateUri
    ) -> MetadataContent:
        """Generate an accept-phase negotiation message from free text.

        This variant runs one LLM content-extraction step constrained by the template URI and then
        renders deterministically like the from-data variant. The template is loaded before the
        LLM call and the extracted conclusion must be ``Accept``.

        Args:
            text: free-text input describing the message content.
            context: negotiation context carried in the ``negotiationContext`` metadata entry of
                the generated message without any LLM involvement.
            template_uri: template URI such as ``Negotiation-T/information-negotiation/accept-reject/v1``;
                its phase segment must be ``accept-reject``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI; the emitted negotiation context is the input context
            stamped with the performative of the addressed template.

        Raises:
            TypeError: when the context or the template URI is ``None``.
            ValueError: when the template URI's phase contradicts the method.
            NegotiationGenerationError: with the code ``template.not_found`` when no template or
                prompt resource exists for the URI and language, one of the retryable codes when
                the extraction step fails after exhausting its retries,
                ``negotiation.field_missing`` when the extracted content misses a required field,
                ``negotiation.invalid_input`` when the text is blank,
                ``negotiation.conclusion_mismatch`` when the extracted conclusion is not
                ``Accept``, ``template.render_failed`` when rendering the template fails, or
                ``input.text_too_long`` when the text exceeds the configured maximum length.
        """
        return self._generate_from_text(text, context, template_uri, NegotiationPerformative.ACCEPT)

    def generate_reject_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: TemplateUri
    ) -> MetadataContent:
        """Generate a reject-phase negotiation message from free text.

        This variant runs one LLM content-extraction step constrained by the template URI and then
        renders deterministically like the from-data variant. The template is loaded before the
        LLM call and the extracted conclusion must be ``Reject``.

        Args:
            text: free-text input describing the message content.
            context: negotiation context carried in the ``negotiationContext`` metadata entry of
                the generated message without any LLM involvement.
            template_uri: template URI such as ``Negotiation-T/feasibility-negotiation/accept-reject/v1``;
                its phase segment must be ``accept-reject``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI; the emitted negotiation context is the input context
            stamped with the performative of the addressed template.

        Raises:
            TypeError: when the context or the template URI is ``None``.
            ValueError: when the template URI's phase contradicts the method.
            NegotiationGenerationError: with the code ``template.not_found`` when no template or
                prompt resource exists for the URI and language, one of the retryable codes when
                the extraction step fails after exhausting its retries,
                ``negotiation.field_missing`` when the extracted content misses a required field,
                ``negotiation.invalid_input`` when the text is blank,
                ``negotiation.conclusion_mismatch`` when the extracted conclusion is not
                ``Reject``, ``template.render_failed`` when rendering the template fails, or
                ``input.text_too_long`` when the text exceeds the configured maximum length.
        """
        return self._generate_from_text(text, context, template_uri, NegotiationPerformative.REJECT)

    def generate_abort_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: TemplateUri
    ) -> MetadataContent:
        """Generate an abort negotiation message from free text.

        This variant runs one LLM content-extraction step constrained by the common abort template
        and then renders deterministically like the from-data variant. The template is loaded
        before the LLM call and the extraction step is retried up to the configured attempt limit
        on the retryable failure codes.

        Args:
            text: free-text input stating the termination reason.
            context: negotiation context carried in the ``negotiationContext`` metadata entry of
                the generated message without any LLM involvement.
            template_uri: template URI of the common abort template ``Negotiation-T/common/abort/v1``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI; the emitted negotiation context is the input context
            stamped with the performative of the addressed template.

        Raises:
            TypeError: when the context or the template URI is ``None``.
            ValueError: when the template URI does not address the common abort template.
            NegotiationGenerationError: with the code ``template.not_found`` when no template or
                prompt resource exists for the URI and language, one of the retryable codes when
                the extraction step fails after exhausting its retries,
                ``negotiation.field_missing`` when the extracted content misses the termination
                reason, ``negotiation.invalid_input`` when the text is blank,
                ``template.render_failed`` when rendering the template fails, or
                ``input.text_too_long`` when the text exceeds the configured maximum length.
        """
        return self._generate_from_text(text, context, template_uri, NegotiationPerformative.ABORT)

    # ------------------------------------------------------------------
    # Validation and parameter filling
    # ------------------------------------------------------------------

    def validate_propose_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: TemplateUri,
    ) -> FilledParamData:
        """Validate a propose-phase negotiation message and extract its parameters.

        The pipeline checks the template URI before any LLM call, runs the deterministic rule gate,
        then performs one semantic validation LLM call (retried on the retryable failure codes) and
        merges the extracted parameters with the rule-level context parameters; context parameters
        win on conflict.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI declaring the expected negotiation type and phase; its
                phase segment must be ``propose``.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            TypeError: when the schema or the template URI is ``None``.
            ValueError: when the template URI's phase contradicts the method.
            NegotiationParamExtractionError: with the code ``input.text_too_long`` when the prompt
                exceeds the configured maximum length, or the failure of the injected parameter
                extractor unchanged otherwise (Java: ``negotiation.invalid_input``,
                ``negotiation.rule_violation``, ``negotiation.semantic_rejected``,
                ``llm.invocation_failed``, ``llm.response_invalid`` or ``template.not_found``).
        """
        return self._validate_prompt_and_data_filling(
            prompt, context, schema, template_uri, NegotiationPerformative.PROPOSE
        )

    def validate_accept_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: TemplateUri,
    ) -> FilledParamData:
        """Validate an accept-phase negotiation message and extract its parameters.

        The pipeline is the one of :meth:`validate_propose_prompt_and_data_filling` with the
        expected phase fixed to accept: the template URI must declare the ``accept-reject``
        segment and the message must satisfy the accept-phase semantic constraints.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI declaring the expected negotiation type and phase; its
                phase segment must be ``accept-reject``.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            TypeError: when the schema or the template URI is ``None``.
            ValueError: when the template URI's phase contradicts the method.
            NegotiationParamExtractionError: with the code ``input.text_too_long`` when the prompt
                exceeds the configured maximum length, or the failure of the injected parameter
                extractor unchanged otherwise.
        """
        return self._validate_prompt_and_data_filling(
            prompt, context, schema, template_uri, NegotiationPerformative.ACCEPT
        )

    def validate_reject_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: TemplateUri,
    ) -> FilledParamData:
        """Validate a reject-phase negotiation message and extract its parameters.

        The pipeline is the one of :meth:`validate_propose_prompt_and_data_filling` with the
        expected phase fixed to reject: the template URI must declare the ``accept-reject``
        segment and the message must satisfy the reject-phase semantic constraints.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI declaring the expected negotiation type and phase; its
                phase segment must be ``accept-reject``.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            TypeError: when the schema or the template URI is ``None``.
            ValueError: when the template URI's phase contradicts the method.
            NegotiationParamExtractionError: with the code ``input.text_too_long`` when the prompt
                exceeds the configured maximum length, or the failure of the injected parameter
                extractor unchanged otherwise.
        """
        return self._validate_prompt_and_data_filling(
            prompt, context, schema, template_uri, NegotiationPerformative.REJECT
        )

    def validate_abort_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: TemplateUri,
    ) -> FilledParamData:
        """Validate an abort negotiation message and extract its parameters.

        The pipeline is the one of :meth:`validate_propose_prompt_and_data_filling` with the
        expected phase fixed to abort: the template URI must address the common abort template and
        the message must satisfy the abort-phase semantic constraints.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI of the common abort template ``Negotiation-T/common/abort/v1``.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            TypeError: when the schema or the template URI is ``None``.
            ValueError: when the template URI does not address the common abort template.
            NegotiationParamExtractionError: with the code ``input.text_too_long`` when the prompt
                exceeds the configured maximum length, or the failure of the injected parameter
                extractor unchanged otherwise.
        """
        return self._validate_prompt_and_data_filling(
            prompt, context, schema, template_uri, NegotiationPerformative.ABORT
        )

    # ------------------------------------------------------------------
    # Legs
    # ------------------------------------------------------------------

    def _generate_from_data(
        self,
        context: NegotiationContext,
        content: NegotiationContent,
        template_uri: TemplateUri,
        performative: NegotiationPerformative,
    ) -> MetadataContent:
        """Run the deterministic from-data generation leg."""
        _require_context(context)
        try:
            reference = self._require_reference(template_uri, performative)
            template_text = self._load_template(reference)
            prompt_text = self._render_message(context, content, reference, template_text)
            return self._complete_generation(reference, prompt_text, context)
        except NegotiationGenerationError as failure:
            logger.warning("negotiation_generation_failed code=%s template_uri=%s", failure.code_str, template_uri.uri)
            raise

    def _generate_from_text(
        self,
        text: str | None,
        context: NegotiationContext,
        template_uri: TemplateUri,
        performative: NegotiationPerformative,
    ) -> MetadataContent:
        """Run the from-text generation leg: one LLM extraction step, then the deterministic render."""
        _require_context(context)
        self._require_text_within_limit(text)
        try:
            reference = self._require_reference(template_uri, performative)
            template_text = self._load_template(reference)
            content = self._extract_content(text, reference)
            prompt_text = self._render_message(context, content, reference, template_text)
            return self._complete_generation(reference, prompt_text, context)
        except NegotiationGenerationError as failure:
            logger.warning("negotiation_generation_failed code=%s template_uri=%s", failure.code_str, template_uri.uri)
            raise

    def _validate_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: TemplateUri,
        performative: NegotiationPerformative,
    ) -> FilledParamData:
        """Run the validation and parameter-filling leg."""
        if schema is None:
            raise TypeError("Parameter schema must not be null.")
        self._require_prompt_within_limit(prompt)
        reference = self._require_reference(template_uri, performative)
        extractor = self._require_param_extractor()
        try:
            return extractor.extract(prompt, context, schema, reference)
        except NegotiationParamExtractionError as failure:
            logger.warning(
                "negotiation_param_extraction_failed code=%s error_count=%s",
                failure.code_str,
                len(failure.errors),
            )
            raise

    def _require_param_extractor(self) -> NegotiationParamExtractor:
        """Return the wired parameter extractor, failing clearly when the leg is unwired.

        The builder wires the default validation pipeline (the P6
        :class:`~a2a_t.negotiation.validation.param_extractor.ParamExtractor` composed from the
        compliance checker, the semantic validator and the template loading gate); an orchestrator
        constructed directly without one is a wiring error (Java ``IllegalStateException`` parity: a
        plain runtime failure outside the coded business tree, never a fake partial validation
        result).
        """
        if self._param_extractor is None:
            raise RuntimeError(
                "Negotiation param extractor is not configured; inject the negotiation validation "
                "pipeline (compliance checker + semantic validator) through the orchestrator builder."
            )
        return self._param_extractor

    def _load_template(self, reference: NegotiationReference) -> str:
        """Load the template text addressed by one reference.

        Raises:
            NegotiationGenerationError: with ``template.not_found`` when the template does not
                exist (the raw :class:`~a2a_t.core.errors.exceptions.ResourceNotFoundError` of an
                injected access implementation and the common layer's coded miss both translate
                here, mirroring the Java orchestrator's ``loadTemplate`` catch point).
        """
        try:
            return self._resource_access.template_text(reference.template_uri, reference.language)
        except ResourceNotFoundError as error:
            raise _template_not_found(reference, error) from error
        except A2ATBusinessError as error:
            if error.code is ErrorCatalog.TEMPLATE_NOT_FOUND:
                raise _template_not_found(reference, error) from error
            raise

    def _extract_content(self, text: str | None, reference: NegotiationReference) -> NegotiationContent:
        """Run the LLM content-extraction step for one reference.

        Raises:
            NegotiationGenerationError: with ``template.not_found`` when the extraction prompts of
                the addressed category and language do not exist (the Java ``extractContent``
                catch point; the default extractor translates the miss itself, this guard covers
                injected extractors surfacing the raw resource failure).
        """
        try:
            return self._content_extractor.extract(text, reference)
        except ResourceNotFoundError as error:
            raise _template_not_found(reference, error) from error
        except A2ATBusinessError as error:
            if error.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED:
                raise _template_not_found(reference, error) from error
            raise

    def _render_message(
        self,
        context: NegotiationContext,
        content: NegotiationContent,
        reference: NegotiationReference,
        template_text: str,
    ) -> str:
        """Render the message of one content against its template.

        Raises:
            NegotiationGenerationError: with ``template.render_failed`` when the render step
                fails; the internal render error never leaks.
        """
        generator = resolve(reference.type, reference.performative, content, reference.language)
        try:
            return generator(context, content, template_text, self._vocabulary)
        except NegotiationRenderError as error:
            raise NegotiationGenerationError(
                ErrorCatalog.TEMPLATE_RENDER_FAILED,
                {"template_uri": reference.uri, "reason": str(error)},
                language=reference.language,
                cause=error,
            ) from error

    def _complete_generation(
        self, reference: NegotiationReference, prompt_text: str, context: NegotiationContext
    ) -> MetadataContent:
        """Build the generated message with its context stamped by the addressed performative.

        The operation is the source of truth for the performative: the emitted context is the
        input context stamped with the performative of the addressed template, overriding whatever
        the caller passed in.
        """
        stamped_context = context.with_performative(reference.performative)
        logger.info(
            "negotiation_generation_completed uri=%s type=%s performative=%s round=%s id=%s",
            reference.uri,
            reference.type,
            reference.performative,
            stamped_context.round,
            stamped_context.id,
        )
        return MetadataContent(reference.uri, prompt_text, NEGOTIATION_T_EXTENSION_URI, stamped_context)

    def _require_reference(
        self, template_uri: TemplateUri, performative: NegotiationPerformative
    ) -> NegotiationReference:
        """Derive the reference of a typed template URI against one expected performative.

        Raises:
            ValueError: when the URI does not address a negotiation template of the expected
                performative (Java ``IllegalArgumentException`` parity).
        """
        reference = NegotiationReference.from_template_uri(template_uri, performative, self._language)
        if reference is None:
            raise ValueError(
                "Template URI does not address a negotiation template of the expected performative "
                f"{performative} ({uri_segment_of(performative)}): {template_uri.uri}."
            )
        return reference

    def _require_text_within_limit(self, text: str | None) -> None:
        """Fail fast when a free-text input exceeds the configured maximum length.

        Raises:
            NegotiationGenerationError: with ``input.text_too_long`` and the ``actual_length`` and
                ``max_chars`` facts.
        """
        if _is_too_long(text, self._max_text_chars):
            raise NegotiationGenerationError(
                ErrorCatalog.INPUT_TEXT_TOO_LONG, _too_long_facts(text, self._max_text_chars)
            )

    def _require_prompt_within_limit(self, prompt: str | None) -> None:
        """Fail fast when a validation prompt exceeds the configured maximum length.

        Raises:
            NegotiationParamExtractionError: with ``input.text_too_long`` and the
                ``actual_length`` and ``max_chars`` facts.
        """
        if _is_too_long(prompt, self._max_text_chars):
            raise NegotiationParamExtractionError(
                ErrorCatalog.INPUT_TEXT_TOO_LONG, _too_long_facts(prompt, self._max_text_chars)
            )


def _require_context(context: NegotiationContext) -> None:
    """Reject a ``None`` negotiation context (Java ``NullPointerException`` parity)."""
    if context is None:
        raise TypeError("Negotiation context must not be null.")


def _template_not_found(reference: NegotiationReference, cause: BaseException) -> NegotiationGenerationError:
    """Create the ``template.not_found`` failure of one template lookup miss."""
    return NegotiationGenerationError(
        ErrorCatalog.TEMPLATE_NOT_FOUND,
        {"template_uri": reference.uri, "language": reference.language},
        language=reference.language,
        cause=cause,
    )


def _is_too_long(text: str | None, max_text_chars: int) -> bool:
    """Report whether one free-text input exceeds the given limit (``None`` is never too long)."""
    return text is not None and len(text) > max_text_chars


def _too_long_facts(text: str | None, max_text_chars: int) -> dict[str, str]:
    """Build the fact values of an input-length violation for the ``input.text_too_long`` code."""
    return {
        "actual_length": str(0 if text is None else len(text)),
        "max_chars": str(max_text_chars),
    }
