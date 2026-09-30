"""High-level server facade for prompt compliance and negotiation APIs (port of Java ``A2ATServer``).

Besides the task prompt compliance API, the facade exposes the three extension content validators
(``validate_{task,notification,auth}_prompt_and_data_filling``, the shared core validation pipeline
behind an input-length gate), the twelve negotiation content methods
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
from a2a_t.core.standard_templates import (
    AUTHORIZATION_EXTENSION_NAME,
    NOTIFICATION_EXTENSION_NAME,
    TASK_EXTENSION_NAME,
)
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

from .negotiation.negotiation_orchestrator_builder import ServerNegotiationOrchestratorBuilder
from .prompt_compliance.content_validator import InputLimitedContentValidator, build_content_validators
from .prompt_compliance.models import PromptComplianceResult
from .prompt_compliance.prompt_compliance_orchestrator_builder import PromptComplianceOrchestratorBuilder

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
    """Return the default .env path used by the high-level server."""
    return Path(__file__).resolve().parents[3] / "package_data" / ".env"


def _deprecation_message(method: str) -> str:
    """Return the D1 deprecation message of one legacy negotiation facade method."""
    return (
        f"A2ATServer.{method} is deprecated since 1.1.0 and will be removed in the next release; "
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


class A2ATServer:
    """Server-side facade of the A2A-T SDK: prompt compliance, validation, negotiation, and queries.

    The facade is the single server entry point of the SDK and exposes four groups of operations:

    - **Task prompt compliance** — :meth:`check_task_prompt` validates one processed task prompt
      against the scenario, template and slot constraints and reports the outcome in the returned
      result object (the compliance checks are LLM-assisted, so a rejection is an expected outcome
      reported through the result rather than an exception).
    - **Content validation and parameter filling** — the three
      ``validate_{task,notification,auth}_prompt_and_data_filling`` methods, one per non-negotiation
      extension, each running the shared validation pipeline behind an input-length gate and
      extracting the parameters per a caller-provided JSON schema.
    - **Negotiation content** — the twelve Negotiation-T methods: eight message-generation methods
      (``generate_negotiation_{propose,accept,reject,abort}_prompt_from_{data,text}``) and four
      message-validation methods (``validate_{...}_prompt_and_data_filling``), identical to the
      client surface because both peers of a negotiation generate and validate messages.
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
        """Create one server facade from a ``.env`` file.

        The configuration, the LLM client and both orchestrators are resolved eagerly so that a
        misconfiguration surfaces at construction instead of on the first call. The negotiation
        content service, the template query service and the extension content validators are
        assembled lazily on first use: each captures resource snapshots at that point, so
        constructing the facade stays side-effect free for the compliance-only flows.

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
        self._prompt_compliance_orchestrator = PromptComplianceOrchestratorBuilder().build(
            config=self._config,
            llm_client=self._llm_client,
            logger=logger,
        )
        self._negotiation_orchestrator = ServerNegotiationOrchestratorBuilder().build(
            config=self._config,
            llm_client=self._llm_client,
            env_path=resolved_env_path,
            logger=logger,
        )
        # Lazily assembled negotiation content service and template query service: both capture
        # resource snapshots at first use, so constructing the facade stays side-effect free for
        # the compliance-only flows. Tests may inject either seam before the first call.
        self._negotiation_content_service: NegotiationContentService | None = None
        self._template_query_service: TemplateQueryService | None = None
        # Lazily assembled content validators of the non-negotiation extensions (Task-T /
        # Notification-T / Authorization-T), keyed by extension name; assigning the dict before the
        # first validate call injects test doubles instead.
        self._content_validators: dict[str, InputLimitedContentValidator] | None = None

    def check_task_prompt(self, *, processed_prompt_text: str) -> PromptComplianceResult:
        """Validate one processed task prompt and return the compliance result.

        The pipeline checks the input length, resolves the scenario of the prompt against the
        bundled scenario catalog, and validates the prompt content against the scenario, template
        and slot constraints (an LLM-assisted semantic step). Failures are reported in the returned
        result object instead of being raised: a prompt being rejected is an expected outcome of a
        compliance check, not an exceptional one.

        Args:
            processed_prompt_text: the processed task prompt text submitted by the client.

        Returns:
            the compliance result: on success it carries no failure, on rejection the structured
                failure (catalog code, rendered message, compliance stage) instead — an oversized
                input, an unmatchable scenario, a missing slot and a failed LLM step all report
                through the result.
        """
        result = self._prompt_compliance_orchestrator.check(
            processed_prompt_text=processed_prompt_text,
        )
        return result

    # ------------------------------------------------------------------
    # Content validation and parameter filling (Task-T / Notification-T /
    # Authorization-T)
    # ------------------------------------------------------------------

    def validate_task_prompt_and_data_filling(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate a task prompt and extract its parameters.

        The pipeline parses the template URI fail-fast, rejects an oversized prompt with
        ``input.text_too_long`` before any LLM call, gates the URI on the ``Task-T`` extension
        prefix and the default template version, loads the addressed template body and runs the
        shared validation pipeline: one retryable semantic validation LLM call that also extracts
        the parameters, then the deterministic parameter merge.

        Args:
            prompt: rendered task prompt text to validate.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI string declaring the expected task template; its prefix
                segment must be ``Task-T``.

        Returns:
            filled parameter data carrying the merged parameters.

        Raises:
            TypeError: when the template URI is ``None``.
            ValueError: when the template URI is blank or malformed.
            ContentValidationError: with the code ``negotiation.invalid_input`` when the prompt is
                blank, the schema is ``None``, or the template URI addresses another extension or
                carries an unsupported version, ``negotiation.semantic_rejected`` when the semantic
                validation rejects the content, ``llm.invocation_failed`` or
                ``llm.response_invalid`` when the semantic step fails after exhausting its retries,
                ``template.not_found`` when the template or the validation prompt resources are
                missing, or ``input.text_too_long`` when the prompt exceeds the configured maximum
                length.
        """
        return self._content_validator(TASK_EXTENSION_NAME).validate(prompt, schema, _parse_template_uri(template_uri))

    def validate_notification_prompt_and_data_filling(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate a notification prompt and extract its parameters.

        The pipeline is the one of :meth:`validate_task_prompt_and_data_filling` with the extension
        prefix fixed to ``Notification-T``: the template URI must address a notification template
        and the message must satisfy the notification semantic constraints.

        Args:
            prompt: rendered notification prompt text to validate.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI string declaring the expected notification template; its
                prefix segment must be ``Notification-T``.

        Returns:
            filled parameter data carrying the merged parameters.

        Raises:
            TypeError: when the template URI is ``None``.
            ValueError: when the template URI is blank or malformed.
            ContentValidationError: with one of the failure codes listed on
                :meth:`validate_task_prompt_and_data_filling`.
        """
        return self._content_validator(NOTIFICATION_EXTENSION_NAME).validate(
            prompt, schema, _parse_template_uri(template_uri)
        )

    def validate_auth_prompt_and_data_filling(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> FilledParamData:
        """Validate an authorization prompt and extract its parameters.

        The pipeline is the one of :meth:`validate_task_prompt_and_data_filling` with the extension
        prefix fixed to ``Authorization-T``: the template URI must address an authorization
        template and the message must satisfy the authorization semantic constraints.

        Args:
            prompt: rendered authorization prompt text to validate.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI string declaring the expected authorization template; its
                prefix segment must be ``Authorization-T``.

        Returns:
            filled parameter data carrying the merged parameters.

        Raises:
            TypeError: when the template URI is ``None``.
            ValueError: when the template URI is blank or malformed.
            ContentValidationError: with one of the failure codes listed on
                :meth:`validate_task_prompt_and_data_filling`.
        """
        return self._content_validator(AUTHORIZATION_EXTENSION_NAME).validate(
            prompt, schema, _parse_template_uri(template_uri)
        )

    def start_negotiation(self, input: StartNegotiationInput) -> dict[str, object]:
        """Start a server-side negotiation round.

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

        Deprecated (D1): the state-machine negotiation demo is retired; this method emits a
        :class:`DeprecationWarning`, forwards to the legacy orchestrator unchanged, and will be
        removed in the next release. Use the negotiation content API instead —
        :meth:`validate_propose_prompt_and_data_filling` and its siblings.

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

        Deprecated (D1): the state-machine negotiation demo is retired; this method emits a
        :class:`DeprecationWarning`, forwards to the legacy orchestrator unchanged, and will be
        removed in the next release. Use the negotiation content API instead —
        :meth:`generate_negotiation_accept_prompt_from_data` and its siblings.

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

        The pipeline checks the template URI format before any LLM call, runs the deterministic
        rule gate, then performs one semantic validation LLM call (retried on the retryable failure
        codes) and merges the extracted parameters with the rule-level context parameters; context
        parameters win on conflict.

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

    def _content_validator(self, extension_name: str) -> InputLimitedContentValidator:
        """Return the content validator of one extension, assembling the set on first use.

        The three validators (Task-T, Notification-T, Authorization-T) are built from the facade's
        config and LLM client through the validator builder seam; assigning
        ``_content_validators`` before the first call injects test doubles instead.
        """
        if self._content_validators is None:
            self._content_validators = build_content_validators(
                config=self._config,
                llm_client=self._llm_client,
            )
        validator = self._content_validators.get(extension_name)
        if validator is None:
            raise ValueError(f"No content validator is configured for extension {extension_name!r}.")
        return validator
