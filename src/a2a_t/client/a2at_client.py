"""High-level client facade for prompt generation and negotiation APIs (port of Java ``A2ATClient``).

Besides the scenario-recognition task prompt generation API, the facade exposes the six
template-directed metadata prompt APIs (``generate_{task,auth,notification}_prompt_from_text`` and
``..._from_data_with_schema``, each bypassing scenario recognition for one addressed template), the
twelve negotiation content methods
of the shared :class:`~a2a_t.negotiation.generation.content_service.NegotiationContentService`
(eight generation plus four validation methods) and the extension-agnostic template queries of the
:class:`~a2a_t.common.prompt_resources.catalog.TemplateQueryService`. The three legacy
``start/receive/continue_negotiation`` methods of the deprecated state-machine negotiation demo
are kept for one release with a :class:`DeprecationWarning` and will be removed in the next
release (port decision D1).
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from a2a_t.common.prompt_resources import PromptTemplate, TemplateQueryService
from a2a_t.config.models import A2ATConfig
from a2a_t.core.metadata import MetadataContent, NegotiationContext
from a2a_t.core.template_uri import TemplateUri
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.llm.config_loader import LLMConfigLoader
from a2a_t.llm.factory import LLMClientFactory
from a2a_t.negotiation.common.models import ContinueNegotiationInput, StartNegotiationInput
from a2a_t.negotiation.content.models import (
    NegotiationAbortData,
    NegotiationEndingData,
    NegotiationProposeData,
)
from a2a_t.negotiation.generation import NegotiationContentService
from a2a_t.observability.llm_decorator import A2ATLLMClientDecorator
from a2a_t.observability.negotiation_metrics import report_terminal_negotiation_rounds

from .negotiation.negotiation_orchestrator_builder import ClientNegotiationOrchestratorBuilder
from .prompt_generation.models import PromptGenerationResult
from .prompt_generation.prompt_generation_orchestrator_builder import PromptGenerationOrchestratorBuilder

logger = logging.getLogger("a2at.observability")


def _report_negotiation_rounds_metric(result: MetadataContent) -> None:
    """Auto-report ``a2at.negotiation.total_rounds`` for terminal messages (spec 5.5).

    Wrapped in try/except: observability must never break the business flow (spec 8.2).
    """
    try:
        report_terminal_negotiation_rounds(result.negotiation_context)
    except Exception:  # noqa: BLE001 - metrics must never break the business flow
        logger.warning("a2at: negotiation metric report failed", exc_info=True)


def _default_env_path() -> Path:
    """Return the default .env path used by the high-level client."""
    return Path(__file__).resolve().parents[3] / "package_data" / ".env"


def _deprecation_message(method: str) -> str:
    """Return the D1 deprecation message of one legacy negotiation facade method."""
    return (
        f"A2ATClient.{method} is deprecated since 1.1.0 and will be removed in the next release; "
        "the state-machine negotiation demo it belongs to is retired. Use the negotiation content "
        "API instead: generate_negotiation_{propose,accept,reject,abort}_prompt_from_data / "
        "..._from_text and validate_{propose,accept,reject,abort}_prompt_and_data_filling, backed "
        "by a2a_t.negotiation.generation.NegotiationContentService."
    )


def _parse_template_uri(template_uri: str | TemplateUri | None) -> TemplateUri:
    """Parse the raw template URI string boundary fail-fast (D16, Java ``parseTemplateUri``).

    Raises:
        TypeError: when the template URI is ``None``.
        ValueError: when the raw template URI is blank or malformed.
    """
    if template_uri is None:
        raise TypeError("templateUri")
    if isinstance(template_uri, TemplateUri):
        return template_uri
    parsed = TemplateUri.parse(template_uri)
    if parsed is None:
        raise ValueError(f"Unparseable template URI: {template_uri}")
    return parsed


class A2ATClient:
    """Client-side facade of the A2A-T SDK: prompt generation, negotiation, and template queries.

    The facade is the single client entry point of the SDK and exposes four groups of operations:

    - **Scenario-recognition prompt generation** — :meth:`generate_task_prompt` normalizes the
      caller input, recognizes the scenario, extracts the slots with one LLM call and renders the
      task prompt. Failures of this LLM-driven pipeline are reported in the returned result object
      instead of being raised (the result/exception dual track of the port).
    - **Template-directed prompt generation** — the six ``generate_{task,auth,notification}_prompt``
      methods, each bypassing scenario recognition for one template addressed by its URI, from free
      text or from structured input with a data schema.
    - **Negotiation content** — the twelve Negotiation-T methods: eight message-generation methods
      (``generate_negotiation_{propose,accept,reject,abort}_prompt_from_{data,text}``) and four
      message-validation methods (``validate_{...}_prompt_and_data_filling``).
    - **Template queries** — :meth:`get_prompts` and :meth:`get_prompt`, the extension-agnostic
      catalog queries that never throw.

    Every method that addresses a template takes the template URI in its raw string spelling and
    parses it fail-fast; use the constants of :mod:`a2a_t.core.standard_templates` instead of
    hand-written URI strings. Business failures are raised as catalog-coded exceptions of the
    :mod:`a2a_t.core.errors.exceptions` tree (catch :class:`~a2a_t.core.errors.exceptions.A2ATError`
    and branch on ``code_str``); programming errors stay ``TypeError`` / ``ValueError``.

    The three legacy ``start/receive/continue_negotiation`` methods of the retired state-machine
    negotiation demo are kept for one release with a :class:`DeprecationWarning` and will be
    removed in the next release.
    """

    def __init__(
        self,
        *,
        env_path: Path | None = None,
        logger: Any | None = None,
    ) -> None:
        """Create one client facade from a ``.env`` file.

        The configuration, the LLM client and both orchestrators are resolved eagerly so that a
        misconfiguration surfaces at construction instead of on the first call. The negotiation
        content service and the template query service are assembled lazily on first use: both
        capture resource snapshots at that point, so constructing the facade stays side-effect free
        for the prompt-only flows.

        Args:
            env_path: path of the ``.env`` file carrying the SDK configuration; ``None`` uses the
                packaged ``package_data/.env`` default. The resolved file must exist — copy
                ``env.example`` to ``.env`` first (every key is optional, an empty file works).
            logger: optional logger injected into the LLM client and the orchestrators; ``None``
                uses each component's module-level logger.

        Raises:
            ConfigFileNotFoundError: when the resolved ``.env`` path does not exist.
            ConfigError: when a configuration value is invalid.
        """
        resolved_env_path = env_path or _default_env_path()
        self._config = A2ATConfig.load(resolved_env_path)
        llm_config = LLMConfigLoader.load(resolved_env_path)
        # A2ATLLMClientDecorator (spec 2.2): stashes the token usage of every structured call
        # (client entry span attrs) and records the L2 gen_ai.client.token.usage metric.
        self._llm_client: Any = A2ATLLMClientDecorator(
            LLMClientFactory.create(llm_config.provider, llm_config, logger=logger)
        )
        self._prompt_generation_orchestrator = PromptGenerationOrchestratorBuilder().build(
            config=self._config,
            llm_client=self._llm_client,
            logger=logger,
        )
        self._negotiation_orchestrator = ClientNegotiationOrchestratorBuilder().build(
            env_path=resolved_env_path,
            logger=logger,
        )
        # Lazily assembled negotiation content service and template query service: both capture
        # resource snapshots at first use, so constructing the facade stays side-effect free for
        # the prompt-only flows. Tests may inject either seam before the first call.
        self._negotiation_content_service: NegotiationContentService | None = None
        self._template_query_service: TemplateQueryService | None = None

    def generate_task_prompt(self, user_input: str | dict[str, object]) -> PromptGenerationResult:
        """Generate a processed task prompt from user input through scenario recognition.

        The pipeline normalizes the input, recognizes the scenario against the bundled scenario
        catalog, extracts the scenario slots with one LLM call, validates the extracted slots
        against the scenario slot schema and renders the task prompt from the scenario template.

        Failures of this LLM-driven pipeline are reported in the returned result object instead of
        being raised: an LLM step failing is an expected outcome, not an exceptional one.

        Args:
            user_input: natural-language task request, or a structured mapping of already-known
                input fields.

        Returns:
            the generation result: on success it carries the rendered prompt text, on failure the
                structured failure (catalog code, rendered message, stage) instead — an oversized
                input, a failed LLM step and an unmatchable scenario all report through the result.

        Raises:
            TypeError: when the user input is neither a string nor a mapping.
            ValueError: when the user input is a blank string or an empty mapping.
        """
        return self._prompt_generation_orchestrator.generate(user_input)

    # ------------------------------------------------------------------
    # Template-directed prompt generation (fromText / fromDataWithSchema)
    # ------------------------------------------------------------------

    def generate_task_prompt_from_text(self, text: str, template_uri: str | TemplateUri) -> MetadataContent:
        """Generate a task prompt with metadata from natural-language input.

        Bypasses scenario recognition: the template identified by the template URI directs one
        slot-extraction LLM call against its bundled slot schema, the result is validated against
        the schema-required slots and rendered from the template.

        Args:
            text: natural-language task input.
            template_uri: template URI string identifying the target template, such as
                ``Task-T/network-layer/ran-energy-saving/v1``.

        Returns:
            metadata content carrying the resolved template URI, the rendered prompt text and the
            Task-T extension URI.

        Raises:
            TypeError: when the text or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed.
            PromptGenerationError: with the code ``template.not_found``, ``template.load_failed``,
                ``slot.schema_not_found``, ``llm.invocation_failed``, ``llm.response_invalid``,
                ``llm.not_configured``, ``slot.not_provided``, ``slot.constraint_violated``,
                ``slot.rule_violation`` or ``template.render_failed`` when generating the prompt
                fails, or ``input.text_too_long`` when the text exceeds the configured maximum
                length.
        """
        if text is None:
            raise TypeError("text")
        return self._prompt_generation_orchestrator.generate_task_prompt_from_text(
            text, _parse_template_uri(template_uri)
        )

    def generate_task_prompt_from_data_with_schema(
        self,
        data: Mapping[str, object],
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> MetadataContent:
        """Generate a task prompt with metadata from structured input and a data schema.

        Bypasses scenario recognition: the structured input and the caller-provided data schema
        (field name to description) guide one slot-extraction LLM call against the template's
        bundled slot schema, the result is validated against the schema-required slots and rendered
        from the template.

        Args:
            data: structured task input as a string-to-object mapping.
            schema: data schema describing the meaning of each input field; must not be empty.
            template_uri: template URI string identifying the target template, such as
                ``Task-T/network-layer/ran-energy-saving/v1``.

        Returns:
            metadata content carrying the resolved template URI, the rendered prompt text and the
            Task-T extension URI.

        Raises:
            TypeError: when the data, the schema or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or the schema is empty.
            PromptGenerationError: with one of the generation failure codes listed on
                :meth:`generate_task_prompt_from_text` (except ``input.text_too_long``).
        """
        if data is None:
            raise TypeError("data")
        if schema is None:
            raise TypeError("schema")
        return self._prompt_generation_orchestrator.generate_task_prompt_from_data_with_schema(
            data, schema, _parse_template_uri(template_uri)
        )

    def generate_auth_prompt_from_text(self, text: str, template_uri: str | TemplateUri) -> MetadataContent:
        """Generate an authorization prompt with metadata from natural-language input.

        Authorization-T slot schemas are bundled with the SDK resources, so the entry point works
        out of the box with the packaged resource source.

        Args:
            text: natural-language authorization input.
            template_uri: template URI string such as
                ``Authorization-T/authorization-policy-management/v1``.

        Returns:
            metadata content carrying the resolved template URI, the rendered prompt text and the
            Authorization-T extension URI.

        Raises:
            TypeError: when the text or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed.
            PromptGenerationError: with one of the generation failure codes listed on
                :meth:`generate_task_prompt_from_text`.
        """
        if text is None:
            raise TypeError("text")
        return self._prompt_generation_orchestrator.generate_auth_prompt_from_text(
            text, _parse_template_uri(template_uri)
        )

    def generate_auth_prompt_from_data_with_schema(
        self,
        data: Mapping[str, object],
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> MetadataContent:
        """Generate an authorization prompt with metadata from structured input and a data schema.

        Authorization-T slot schemas are bundled with the SDK resources, so the entry point works
        out of the box with the packaged resource source.

        Args:
            data: structured authorization input as a string-to-object mapping.
            schema: data schema describing the meaning of each input field; must not be empty.
            template_uri: template URI string such as
                ``Authorization-T/authorization-policy-management/v1``.

        Returns:
            metadata content carrying the resolved template URI, the rendered prompt text and the
            Authorization-T extension URI.

        Raises:
            TypeError: when the data, the schema or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or the schema is empty.
            PromptGenerationError: with one of the generation failure codes listed on
                :meth:`generate_task_prompt_from_text` (except ``input.text_too_long``).
        """
        if data is None:
            raise TypeError("data")
        if schema is None:
            raise TypeError("schema")
        return self._prompt_generation_orchestrator.generate_auth_prompt_from_data_with_schema(
            data, schema, _parse_template_uri(template_uri)
        )

    def generate_notification_prompt_from_text(self, text: str, template_uri: str | TemplateUri) -> MetadataContent:
        """Generate a notification prompt with metadata from natural-language input.

        Args:
            text: natural-language notification input.
            template_uri: template URI string such as
                ``Notification-T/network-layer/subscribe-incident/v1``.

        Returns:
            metadata content carrying the resolved template URI, the rendered prompt text and the
            Notification-T extension URI.

        Raises:
            TypeError: when the text or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed.
            PromptGenerationError: with one of the generation failure codes listed on
                :meth:`generate_task_prompt_from_text`.
        """
        if text is None:
            raise TypeError("text")
        return self._prompt_generation_orchestrator.generate_notification_prompt_from_text(
            text, _parse_template_uri(template_uri)
        )

    def generate_notification_prompt_from_data_with_schema(
        self,
        data: Mapping[str, object],
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> MetadataContent:
        """Generate a notification prompt with metadata from structured input and a data schema.

        Args:
            data: structured notification input as a string-to-object mapping.
            schema: data schema describing the meaning of each input field; must not be empty.
            template_uri: template URI string such as
                ``Notification-T/network-layer/subscribe-incident/v1``.

        Returns:
            metadata content carrying the resolved template URI, the rendered prompt text and the
            Notification-T extension URI.

        Raises:
            TypeError: when the data, the schema or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or the schema is empty.
            PromptGenerationError: with one of the generation failure codes listed on
                :meth:`generate_task_prompt_from_text` (except ``input.text_too_long``).
        """
        if data is None:
            raise TypeError("data")
        if schema is None:
            raise TypeError("schema")
        return self._prompt_generation_orchestrator.generate_notification_prompt_from_data_with_schema(
            data, schema, _parse_template_uri(template_uri)
        )

    def start_negotiation(self, input: StartNegotiationInput) -> dict[str, object]:
        """Start a client-side negotiation round.

        Deprecated (D1): the state-machine negotiation demo is retired; this method emits a
        :class:`DeprecationWarning`, forwards to the legacy orchestrator unchanged, and will be
        removed in the next release. Use the negotiation content API instead —
        :meth:`generate_negotiation_propose_prompt_from_data` and its siblings.

        Args:
            input: legacy negotiation start input.

        Returns:
            the legacy orchestrator's round result as a plain mapping.
        """
        warnings.warn(_deprecation_message("start_negotiation"), DeprecationWarning, stacklevel=2)
        return self._negotiation_orchestrator.start_negotiation(input)

    def receive_negotiation(self, message: str, context: dict[str, object]) -> dict[str, object]:
        """Process a negotiation message received from the remote peer.

        Deprecated (D1): the state-machine negotiation demo is retired; this method forwards to the
        legacy orchestrator unchanged and will be removed in the next release. Use the negotiation
        content API instead — :meth:`validate_propose_prompt_and_data_filling` and its siblings.

        Args:
            message: received negotiation message text.
            context: legacy negotiation context mapping.

        Returns:
            the legacy orchestrator's round result as a plain mapping.
        """
        warnings.warn(_deprecation_message("receive_negotiation"), DeprecationWarning, stacklevel=2)
        return self._negotiation_orchestrator.receive_negotiation(message, context)

    def continue_negotiation(self, input: ContinueNegotiationInput) -> dict[str, object]:
        """Continue an existing negotiation with a local response.

        Deprecated (D1): the state-machine negotiation demo is retired; this method forwards to the
        legacy orchestrator unchanged and will be removed in the next release. Use the negotiation
        content API instead — :meth:`generate_negotiation_accept_prompt_from_data` and its
        siblings.

        Args:
            input: legacy negotiation continuation input.

        Returns:
            the legacy orchestrator's round result as a plain mapping.
        """
        warnings.warn(_deprecation_message("continue_negotiation"), DeprecationWarning, stacklevel=2)
        return self._negotiation_orchestrator.continue_negotiation(input)

    # ------------------------------------------------------------------
    # Negotiation content generation (from data: deterministic, zero LLM)
    # ------------------------------------------------------------------

    def generate_negotiation_propose_prompt_from_data(
        self, data: NegotiationProposeData, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate a propose-phase negotiation message from typed data.

        Deterministic and never calls an LLM: the typed content is validated, dispatched to the
        generator of the negotiation type addressed by the template URI and rendered from that
        template.

        Args:
            data: typed propose input carrying the negotiation context and the typed content.
            template_uri: template URI string such as
                ``Negotiation-T/information-negotiation/propose/v1``; its performative segment
                must be ``propose``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the data, its context or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or its performative or type
                contradicts the method or the content type.
            NegotiationGenerationError: with the code ``template.not_found``,
                ``negotiation.content_invalid``, ``negotiation.invalid_input`` or
                ``template.render_failed`` when generation fails.
        """
        return self._negotiation_content().generate_propose_from_data(data, _parse_template_uri(template_uri))

    def generate_negotiation_accept_prompt_from_data(
        self, data: NegotiationEndingData, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate an accept-phase negotiation message from typed data.

        Deterministic and never calls an LLM. The content conclusion must be ``Accept``; a
        mismatched conclusion is a content error.

        Args:
            data: typed terminal input whose content conclusion must be ``Accept``.
            template_uri: template URI string such as
                ``Negotiation-T/information-negotiation/accept-reject/v1``; its performative
                segment must be ``accept-reject``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the data, its context or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or its performative or type
                contradicts the method or the content.
            NegotiationGenerationError: with the code ``template.not_found``,
                ``negotiation.conclusion_mismatch``, ``negotiation.content_invalid`` or
                ``template.render_failed`` when generation fails.
        """
        result = self._negotiation_content().generate_accept_from_data(data, _parse_template_uri(template_uri))
        _report_negotiation_rounds_metric(result)
        return result

    def generate_negotiation_reject_prompt_from_data(
        self, data: NegotiationEndingData, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate a reject-phase negotiation message from typed data.

        Deterministic and never calls an LLM. The content conclusion must be ``Reject``; a
        mismatched conclusion is a content error.

        Args:
            data: typed terminal input whose content conclusion must be ``Reject``.
            template_uri: template URI string such as
                ``Negotiation-T/feasibility-negotiation/accept-reject/v1``; its performative
                segment must be ``accept-reject``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the data, its context or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or its performative or type
                contradicts the method or the content.
            NegotiationGenerationError: with the code ``template.not_found``,
                ``negotiation.conclusion_mismatch``, ``negotiation.content_invalid`` or
                ``template.render_failed`` when generation fails.
        """
        result = self._negotiation_content().generate_reject_from_data(data, _parse_template_uri(template_uri))
        _report_negotiation_rounds_metric(result)
        return result

    def generate_negotiation_abort_prompt_from_data(
        self, data: NegotiationAbortData, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate an abort negotiation message from typed data.

        Deterministic and never calls an LLM. Abort messages are type-independent: the addressed
        template must be the common abort template and the content carries only the termination
        reason.

        Args:
            data: typed abort input carrying the negotiation context and the termination reason.
            template_uri: template URI string of the common abort template
                ``Negotiation-T/common/abort/v1``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the data, its context or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or does not address the common
                abort template.
            NegotiationGenerationError: with the code ``template.not_found``,
                ``negotiation.content_invalid`` or ``template.render_failed`` when generation
                fails.
        """
        result = self._negotiation_content().generate_abort_from_data(data, _parse_template_uri(template_uri))
        _report_negotiation_rounds_metric(result)
        return result

    # ------------------------------------------------------------------
    # Negotiation content generation (from text: one LLM extraction step)
    # ------------------------------------------------------------------

    def generate_negotiation_propose_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate a propose-phase negotiation message from free text.

        Runs one LLM content-extraction step constrained by the template URI and then renders
        deterministically like the from-data variant. The template is loaded before the LLM call
        and the extraction step is retried up to the configured attempt limit on the retryable
        failure codes.

        Args:
            text: free-text input describing the message content.
            context: negotiation context carried in the ``negotiationContext`` metadata entry of
                the generated message without any LLM involvement.
            template_uri: template URI string such as
                ``Negotiation-T/target-negotiation/propose/v1``; its performative segment must be
                ``propose``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the context or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or its performative
                contradicts the method.
            NegotiationGenerationError: with the code ``template.not_found``, one of the retryable
                codes when the extraction step fails after exhausting its retries,
                ``template.render_failed``, ``negotiation.field_missing``,
                ``negotiation.invalid_input`` or ``input.text_too_long``.
        """
        return self._negotiation_content().generate_propose_from_text(text, context, _parse_template_uri(template_uri))

    def generate_negotiation_accept_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate an accept-phase negotiation message from free text.

        Runs one LLM content-extraction step constrained by the template URI and then renders
        deterministically like the from-data variant. The template is loaded before the LLM call
        and the extracted conclusion must be ``Accept``.

        Args:
            text: free-text input describing the message content.
            context: negotiation context carried in the ``negotiationContext`` metadata entry of
                the generated message without any LLM involvement.
            template_uri: template URI string such as
                ``Negotiation-T/information-negotiation/accept-reject/v1``; its performative
                segment must be ``accept-reject``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the context or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or its performative
                contradicts the method.
            NegotiationGenerationError: with the code ``template.not_found``, one of the retryable
                codes when the extraction step fails after exhausting its retries,
                ``template.render_failed``, ``negotiation.field_missing``,
            ``negotiation.invalid_input``, ``negotiation.conclusion_mismatch`` or
            ``input.text_too_long``.
        """
        result = self._negotiation_content().generate_accept_from_text(text, context, _parse_template_uri(template_uri))
        _report_negotiation_rounds_metric(result)
        return result

    def generate_negotiation_reject_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate a reject-phase negotiation message from free text.

        Runs one LLM content-extraction step constrained by the template URI and then renders
        deterministically like the from-data variant. The template is loaded before the LLM call
        and the extracted conclusion must be ``Reject``.

        Args:
            text: free-text input describing the message content.
            context: negotiation context carried in the ``negotiationContext`` metadata entry of
                the generated message without any LLM involvement.
            template_uri: template URI string such as
                ``Negotiation-T/feasibility-negotiation/accept-reject/v1``; its performative
                segment must be ``accept-reject``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the context or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or its performative
                contradicts the method.
            NegotiationGenerationError: with the code ``template.not_found``, one of the retryable
                codes when the extraction step fails after exhausting its retries,
                ``template.render_failed``, ``negotiation.field_missing``,
            ``negotiation.invalid_input``, ``negotiation.conclusion_mismatch`` or
            ``input.text_too_long``.
        """
        result = self._negotiation_content().generate_reject_from_text(text, context, _parse_template_uri(template_uri))
        _report_negotiation_rounds_metric(result)
        return result

    def generate_negotiation_abort_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str | TemplateUri
    ) -> MetadataContent:
        """Generate an abort negotiation message from free text.

        Runs one LLM content-extraction step constrained by the common abort template and then
        renders deterministically like the from-data variant. The template is loaded before the
        LLM call and the extraction step is retried up to the configured attempt limit on the
        retryable failure codes.

        Args:
            text: free-text input stating the termination reason.
            context: negotiation context carried in the ``negotiationContext`` metadata entry of
                the generated message without any LLM involvement.
            template_uri: template URI string of the common abort template
                ``Negotiation-T/common/abort/v1``.

        Returns:
            generated message carrying the template URI, the rendered message text and the
            negotiation extension URI.

        Raises:
            TypeError: when the context or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or does not address the common
                abort template.
            NegotiationGenerationError: with the code ``template.not_found``, one of the retryable
                codes when the extraction step fails after exhausting its retries,
                ``template.render_failed``, ``negotiation.field_missing``,
                ``negotiation.invalid_input`` or ``input.text_too_long``.
        """
        result = self._negotiation_content().generate_abort_from_text(text, context, _parse_template_uri(template_uri))
        _report_negotiation_rounds_metric(result)
        return result

    # ------------------------------------------------------------------
    # Negotiation validation and parameter filling
    # ------------------------------------------------------------------

    def validate_propose_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate a propose-phase negotiation message and extract its parameters.

        The pipeline checks the template URI before any LLM call, runs the deterministic rule gate
        on the negotiation context carried in the ``negotiationContext`` metadata entry, then
        performs one LLM semantic validation call that also extracts the parameters, and finally
        merges the parameters with the context parameters taking precedence.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata;
                ``None`` is reported as not being a negotiation message.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI string declaring the expected negotiation type and
                performative; its performative segment must be ``propose``.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            TypeError: when the schema or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or its performative
                contradicts the method.
            NegotiationParamExtractionError: with the code ``negotiation.invalid_input``,
                ``negotiation.rule_violation``, one of the closed ``negotiation.*`` codes,
                ``negotiation.semantic_rejected``, ``llm.invocation_failed``,
                ``llm.response_invalid``, ``template.not_found`` or ``input.text_too_long``.
        """
        return self._negotiation_content().validate_propose_prompt_and_data_filling(
            prompt, context, schema, _parse_template_uri(template_uri)
        )

    def validate_accept_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate an accept-phase negotiation message and extract its parameters.

        The pipeline is the one of :meth:`validate_propose_prompt_and_data_filling` with the
        expected performative fixed to accept: the template URI must declare the ``accept-reject``
        segment and the message must satisfy the accept-phase semantic constraints.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata;
                ``None`` is reported as not being a negotiation message.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI string declaring the expected negotiation type and
                performative; its performative segment must be ``accept-reject``.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            TypeError: when the schema or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or its performative
                contradicts the method.
            NegotiationParamExtractionError: with the code ``negotiation.invalid_input``,
                ``negotiation.rule_violation``, one of the closed ``negotiation.*`` codes,
                ``negotiation.semantic_rejected``, ``llm.invocation_failed``,
                ``llm.response_invalid``, ``template.not_found`` or ``input.text_too_long``.
        """
        return self._negotiation_content().validate_accept_prompt_and_data_filling(
            prompt, context, schema, _parse_template_uri(template_uri)
        )

    def validate_reject_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate a reject-phase negotiation message and extract its parameters.

        The pipeline is the one of :meth:`validate_propose_prompt_and_data_filling` with the
        expected performative fixed to reject: the template URI must declare the ``accept-reject``
        segment and the message must satisfy the reject-phase semantic constraints.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata;
                ``None`` is reported as not being a negotiation message.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI string declaring the expected negotiation type and
                performative; its performative segment must be ``accept-reject``.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            TypeError: when the schema or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or its performative
                contradicts the method.
            NegotiationParamExtractionError: with the code ``negotiation.invalid_input``,
                ``negotiation.rule_violation``, one of the closed ``negotiation.*`` codes,
                ``negotiation.semantic_rejected``, ``llm.invocation_failed``,
                ``llm.response_invalid``, ``template.not_found`` or ``input.text_too_long``.
        """
        return self._negotiation_content().validate_reject_prompt_and_data_filling(
            prompt, context, schema, _parse_template_uri(template_uri)
        )

    def validate_abort_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate an abort negotiation message and extract its parameters.

        The pipeline is the one of :meth:`validate_propose_prompt_and_data_filling` with the
        expected performative fixed to abort: the template URI must address the common abort
        template and the message must satisfy the abort-phase semantic constraints.

        Args:
            prompt: rendered negotiation message text to validate.
            context: negotiation context carried alongside the message in the A2A-T metadata;
                ``None`` is reported as not being a negotiation message.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI string of the common abort template
                ``Negotiation-T/common/abort/v1``.

        Returns:
            filled parameter data carrying the context parameters and the extracted parameters.

        Raises:
            TypeError: when the schema or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or does not address the common
                abort template.
            NegotiationParamExtractionError: with the code ``negotiation.invalid_input``,
                ``negotiation.rule_violation``, one of the closed ``negotiation.*`` codes,
                ``negotiation.semantic_rejected``, ``llm.invocation_failed``,
                ``llm.response_invalid``, ``template.not_found`` or ``input.text_too_long``.
        """
        return self._negotiation_content().validate_abort_prompt_and_data_filling(
            prompt, context, schema, _parse_template_uri(template_uri)
        )

    # ------------------------------------------------------------------
    # Template queries
    # ------------------------------------------------------------------

    def get_prompts(self) -> list[PromptTemplate]:
        """List every template available for the configured language across all A2A-T extensions.

        This query never throws: the extension directories are discovered from the configured
        resource tree itself, so templates of extensions added later are included automatically.
        Templates that exist nowhere for the language are skipped and an empty list is returned
        when no template can be loaded at all.

        Returns:
            the loadable templates of the configured language across all extensions, sorted by
            template URI; empty when none can be loaded.
        """
        return self._template_queries().get_prompts()

    def get_prompt(self, template_uri: str | TemplateUri) -> PromptTemplate | None:
        """Load one template by its URI, regardless of the extension.

        This query never throws for a well-formed template URI: a missing template yields ``None``
        together with a warning log instead of a failure.

        Args:
            template_uri: template URI string such as
                ``Negotiation-T/target-negotiation/propose/v1`` or
                ``Task-T/network-layer/ran-energy-saving/v1``.

        Returns:
            the addressed template, or ``None`` when no template exists for it in the configured
            language.

        Raises:
            TypeError: when the template URI is ``None``.
            ValueError: when the template URI is blank or malformed.
        """
        return self._template_queries().get_prompt(_parse_template_uri(template_uri))

    # ------------------------------------------------------------------
    # Lazy service seams
    # ------------------------------------------------------------------

    def _negotiation_content(self) -> NegotiationContentService:
        """Return the negotiation content service, assembling it on first use.

        The service is built from the facade's config and LLM client through the service's
        builder seam; assigning ``_negotiation_content_service`` before the first call injects a
        test double instead.
        """
        if self._negotiation_content_service is None:
            self._negotiation_content_service = NegotiationContentService(
                NegotiationContentService.build_orchestrator(self._config, self._llm_client)
            )
        return self._negotiation_content_service

    def _template_queries(self) -> TemplateQueryService:
        """Return the template query service, assembling it on first use.

        The service captures the template snapshot of the configured language and resource source
        through the common resource access layer; assigning ``_template_query_service`` before the
        first call injects a test double instead.
        """
        if self._template_query_service is None:
            prompt_config = self._config.prompt
            self._template_query_service = TemplateQueryService.from_config(
                prompt_config.language,
                prompt_config.source_type,
                prompt_config.local_root_dir,
            )
        return self._template_query_service
