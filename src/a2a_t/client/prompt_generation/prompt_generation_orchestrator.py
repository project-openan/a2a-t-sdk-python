from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from a2a_t.common.prompt_resources import PromptResourceAccess
from a2a_t.common.prompt_resources.models import PromptMessages, SlotSchema, slot_schema_from_json_schema
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog, by_code
from a2a_t.core.errors.exceptions import (
    A2ATBusinessError,
    A2ATError,
    PromptGenerationError,
    SlotValidationError,
)
from a2a_t.core.errors.input_limit import InputLimitConfig
from a2a_t.core.errors.messages import render as render_error_message
from a2a_t.core.metadata import (
    AUTHORIZATION_T_EXTENSION_URI,
    NOTIFICATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
    MetadataContent,
)
from a2a_t.core.prompt_resource_key import PromptResourceKey
from a2a_t.core.template_uri import TemplateUri
from a2a_t.llm.errors import LLMConfigError, LLMError, is_response_contract_violation
from a2a_t.prompt.analysis import ScenarioResolutionOrchestrator, SlotExtractor
from a2a_t.prompt.analysis.errors import PromptAnalysisError
from a2a_t.prompt.analysis.models import SlotExtractionResult
from a2a_t.prompt.analysis.scenario_resolution_orchestrator import (
    DEFAULT_SCENARIO_REASON,
    PREPARATION_STAGE,
)
from a2a_t.prompt.common.models import PromptReference
from a2a_t.prompt.task_rendering import TaskPromptRenderer
from a2a_t.prompt.task_rendering.errors import TaskPromptRenderError

from .generation_constants import GENERATION_STAGE, INPUT_STAGE, RENDER_STAGE, SCENARIO_STAGE
from .input_normalizer import InputNormalizer
from .models import PromptGenerationFailure, PromptGenerationResult

_LOGGER = logging.getLogger(__name__)

#: Step label reported in ``llm.response_invalid`` when the slot-extraction step fails.
_STEP_SLOT_EXTRACTION = "slot extraction"

#: Analysis action whose system/user prompts drive slot extraction.
_SLOT_EXTRACTION_ACTION = "slot_extraction"

#: Legacy slot-extraction error code for a required slot that could not be extracted (Java keeps
#: the same legacy spelling and maps it at the boundary).
_LEGACY_CODE_MISSING_INPUT = "missing_input"

#: Legacy slot-extraction error code for a value violating a closed constraint.
_LEGACY_CODE_INVALID_VALUE = "invalid_value"


class PromptGenerationOrchestrator:
    """Coordinate the full client-side prompt generation pipeline.

    Every resource — template text, slot schema and the slot-extraction instruction prompts — is
    loaded through the shared resource access layer (D31): templates and slot schemas follow the
    configured source routing, the instruction prompts are always the packaged SDK contract.
    """

    def __init__(
        self,
        *,
        config: PromptRuntimeConfig,
        resource_access: PromptResourceAccess,
        scenario_resolver: ScenarioResolutionOrchestrator,
        slot_extractor: SlotExtractor,
        input_normalizer: InputNormalizer | None = None,
        renderer: TaskPromptRenderer | None = None,
        input_limit: InputLimitConfig | None = None,
        logger: Any | None = None,
    ) -> None:
        if not isinstance(config, PromptRuntimeConfig):
            raise TypeError("config must be a PromptRuntimeConfig instance.")
        self._config = config
        self._resource_access = resource_access
        self._scenario_resolver = scenario_resolver
        self._slot_extractor = slot_extractor
        self._input_normalizer = input_normalizer or InputNormalizer()
        self._renderer = renderer or TaskPromptRenderer()
        self._input_limit = input_limit if input_limit is not None else InputLimitConfig()
        self._logger = logger if logger is not None else _LOGGER

    def generate(self, user_input: str | dict[str, object]) -> PromptGenerationResult:
        """Run prompt generation from input normalization through prompt rendering."""
        self._log_info("prompt_generation_started")
        if isinstance(user_input, str) and self._input_limit.is_too_long(user_input):
            return self._catalog_failure(
                entry=ErrorCatalog.INPUT_TEXT_TOO_LONG,
                facts=self._input_limit.too_long_facts(user_input),
                stage=INPUT_STAGE,
            )
        if self._is_debug_enabled():
            self._log_debug("prompt_generation_raw_user_input raw_user_input=%s", user_input)
        normalized_input = self._input_normalizer.normalize(user_input)
        language = self._config.language
        self._log_info(
            "prompt_generation_input_normalized input_kind=%s requested_language=%s",
            normalized_input.input_kind,
            language,
        )

        scenario_resolution = self._scenario_resolver.resolve(normalized_input.normalized_input)
        self._log_debug_if_available(
            "prompt_generation_scenario_raw_output scenario_raw_output=%s",
            self._scenario_resolver,
        )
        if (
            not scenario_resolution.success
            or scenario_resolution.reference is None
            or scenario_resolution.scenario is None
        ):
            failure = scenario_resolution.failure
            if failure is None:
                return self._catalog_failure(
                    entry=ErrorCatalog.SCENARIO_NOT_MATCHED,
                    facts={"reason": DEFAULT_SCENARIO_REASON},
                    stage=SCENARIO_STAGE,
                )
            # The resolver already rendered the catalog message for its own failure mode.
            return self._failure_result(code=failure.code, message=failure.message, stage=failure.stage)
        reference = scenario_resolution.reference
        scenario = scenario_resolution.scenario
        scenario_code = reference.scenario_code
        resolved_language = reference.language
        self._log_info(
            "prompt_generation_scenario_recognized scenario_code=%s language=%s",
            scenario_code,
            resolved_language,
        )
        try:
            resolved_language, template_text, slot_schema, slot_prompts = self._load_generation_resources(
                reference=reference,
            )
            reference = PromptReference(scenario_code=scenario_code, language=resolved_language)
        except _PromptGenerationResourceError as error:
            # At this point the scenario is known, so preserve it in the failure payload for callers.
            return self._finalize_result(
                PromptGenerationResult(
                    success=False,
                    prompt_text=None,
                    failure=PromptGenerationFailure(
                        code=error.entry.value,
                        message=render_error_message(error.entry, error.facts, resolved_language),
                        stage=error.stage,
                    ),
                )
            )

        try:
            extraction_result = self._slot_extractor.extract(
                normalized_input=normalized_input.normalized_input,
                reference=reference,
                template_text=template_text,
                slot_schema=slot_schema,
                system_prompt=slot_prompts.system_prompt,
                user_prompt=slot_prompts.user_prompt,
            )
        except PromptAnalysisError:
            return self._catalog_failure(
                entry=ErrorCatalog.LLM_RESPONSE_INVALID,
                facts={"step": _STEP_SLOT_EXTRACTION},
                stage=GENERATION_STAGE,
            )
        except Exception as error:
            return self._llm_failure_result(error, step=_STEP_SLOT_EXTRACTION)
        self._log_debug_if_available(
            "prompt_generation_slot_raw_output slot_raw_output=%s",
            self._slot_extractor,
        )
        rendered_prompt_text, render_error_message_text = self._render_prompt_text(
            template_text=template_text,
            slots=extraction_result.slots,
            scenario_code=scenario_code,
            language=resolved_language,
            description=scenario.description,
        )
        self._log_info(
            "prompt_generation_slots_extracted slots=%s slot_errors=%s",
            extraction_result.slots,
            extraction_result.slot_errors,
        )
        if rendered_prompt_text is None:
            return self._catalog_failure(
                entry=ErrorCatalog.TEMPLATE_RENDER_FAILED,
                facts={
                    "template_uri": scenario_code,
                    "reason": render_error_message_text or "Task prompt rendering failed.",
                },
                stage=RENDER_STAGE,
            )

        return self._finalize_result(
            PromptGenerationResult(
                success=True,
                prompt_text=rendered_prompt_text,
                failure=None,
            )
        )

    # ------------------------------------------------------------------
    # Template-directed MetadataContent generation (Java ``fromText`` /
    # ``fromDataWithSchema`` — bypasses scenario recognition entirely)
    # ------------------------------------------------------------------

    def generate_task_prompt_from_text(self, text: str, template_uri: str | TemplateUri) -> MetadataContent:
        """Generate a task prompt with metadata from natural-language input.

        Args:
            text: natural-language task input.
            template_uri: template URI identifying the target template, such as
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
        return self._generate_from_template_uri_with_metadata(text, template_uri, TASK_T_EXTENSION_URI)

    def generate_task_prompt_from_data_with_schema(
        self,
        data: Mapping[str, object],
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> MetadataContent:
        """Generate a task prompt with metadata from structured input and a data schema.

        Args:
            data: structured task input as a string-to-object mapping.
            schema: data schema describing the meaning of each input field; must not be empty.
            template_uri: template URI identifying the target template.

        Returns:
            metadata content carrying the resolved template URI, the rendered prompt text and the
            Task-T extension URI.

        Raises:
            TypeError: when the data, the schema or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or the schema is empty.
            PromptGenerationError: with one of the generation failure codes listed on
                :meth:`generate_task_prompt_from_text` (except ``input.text_too_long``).
        """
        return self._generate_from_data_with_schema(data, schema, template_uri, TASK_T_EXTENSION_URI)

    def generate_auth_prompt_from_text(self, text: str, template_uri: str | TemplateUri) -> MetadataContent:
        """Generate an authorization prompt with metadata from natural-language input.

        Args:
            text: natural-language authorization input.
            template_uri: template URI such as ``Authorization-T/authorization-policy-management/v1``.

        Returns:
            metadata content carrying the resolved template URI, the rendered prompt text and the
            Authorization-T extension URI.

        Raises:
            TypeError: when the text or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed.
            PromptGenerationError: with one of the generation failure codes listed on
                :meth:`generate_task_prompt_from_text`.
        """
        return self._generate_from_template_uri_with_metadata(text, template_uri, AUTHORIZATION_T_EXTENSION_URI)

    def generate_auth_prompt_from_data_with_schema(
        self,
        data: Mapping[str, object],
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
    ) -> MetadataContent:
        """Generate an authorization prompt with metadata from structured input and a data schema.

        Args:
            data: structured authorization input as a string-to-object mapping.
            schema: data schema describing the meaning of each input field; must not be empty.
            template_uri: template URI such as ``Authorization-T/authorization-policy-management/v1``.

        Returns:
            metadata content carrying the resolved template URI, the rendered prompt text and the
            Authorization-T extension URI.

        Raises:
            TypeError: when the data, the schema or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or the schema is empty.
            PromptGenerationError: with one of the generation failure codes listed on
                :meth:`generate_task_prompt_from_text` (except ``input.text_too_long``).
        """
        return self._generate_from_data_with_schema(data, schema, template_uri, AUTHORIZATION_T_EXTENSION_URI)

    def generate_notification_prompt_from_text(self, text: str, template_uri: str | TemplateUri) -> MetadataContent:
        """Generate a notification prompt with metadata from natural-language input.

        Args:
            text: natural-language notification input.
            template_uri: template URI such as ``Notification-T/network-layer/subscribe-incident/v1``.

        Returns:
            metadata content carrying the resolved template URI, the rendered prompt text and the
            Notification-T extension URI.

        Raises:
            TypeError: when the text or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed.
            PromptGenerationError: with one of the generation failure codes listed on
                :meth:`generate_task_prompt_from_text`.
        """
        return self._generate_from_template_uri_with_metadata(text, template_uri, NOTIFICATION_T_EXTENSION_URI)

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
            template_uri: template URI such as ``Notification-T/network-layer/subscribe-incident/v1``.

        Returns:
            metadata content carrying the resolved template URI, the rendered prompt text and the
            Notification-T extension URI.

        Raises:
            TypeError: when the data, the schema or the template URI is ``None``.
            ValueError: when the template URI is blank or malformed, or the schema is empty.
            PromptGenerationError: with one of the generation failure codes listed on
                :meth:`generate_task_prompt_from_text` (except ``input.text_too_long``).
        """
        return self._generate_from_data_with_schema(data, schema, template_uri, NOTIFICATION_T_EXTENSION_URI)

    def _generate_from_template_uri_with_metadata(
        self,
        text: str,
        template_uri: str | TemplateUri,
        extension_uri: str,
    ) -> MetadataContent:
        """Run the template-directed generation for one free-text input (Java fromText leg)."""
        parsed_template_uri = _template_uri_of(template_uri)
        if text is None:
            raise TypeError("text")
        if self._input_limit.is_too_long(text):
            raise PromptGenerationError(
                ErrorCatalog.INPUT_TEXT_TOO_LONG,
                self._input_limit.too_long_facts(text),
                language=self._config.language,
            )
        return self._generate_with_metadata(
            parsed_template_uri,
            extension_uri,
            user_input=text,
            data_schema=None,
        )

    def _generate_from_data_with_schema(
        self,
        data: Mapping[str, object],
        schema: Mapping[str, object],
        template_uri: str | TemplateUri,
        extension_uri: str,
    ) -> MetadataContent:
        """Run the template-directed generation for structured input plus a data schema."""
        parsed_template_uri = _template_uri_of(template_uri)
        if data is None:
            raise TypeError("data")
        if schema is None:
            raise TypeError("schema")
        if not schema:
            raise ValueError("Data schema must not be empty; it describes the meaning of each input field.")
        return self._generate_with_metadata(
            parsed_template_uri,
            extension_uri,
            user_input=data,
            data_schema=schema,
        )

    def _generate_with_metadata(
        self,
        template_uri: TemplateUri,
        extension_uri: str,
        *,
        user_input: object,
        data_schema: Mapping[str, object] | None,
    ) -> MetadataContent:
        """Generate one ``MetadataContent`` for the addressed template (Java ``generateWithMetadata``).

        Stage order: load the template text, run the template-directed slot extraction, map the
        reported slot errors, validate the schema-required slots, render the prompt — each stage
        translating its failure to the Java catch-point catalog code.
        """
        language = self._config.language
        template_identifier = template_uri.uri
        try:
            template_text = self._resource_access.template_text(template_identifier, language)
        except A2ATBusinessError as error:
            if error.code is ErrorCatalog.TEMPLATE_NOT_FOUND:
                raise self._generation_exception(
                    ErrorCatalog.TEMPLATE_NOT_FOUND,
                    {"template_uri": template_identifier, "language": language},
                ) from error
            raise self._generation_exception(
                ErrorCatalog.TEMPLATE_LOAD_FAILED,
                {"resource_path": template_identifier},
            ) from error
        except A2ATError as error:
            raise self._generation_exception(
                ErrorCatalog.TEMPLATE_LOAD_FAILED,
                {"resource_path": template_identifier},
            ) from error

        extraction_result = self._extract_slots_with_metadata(
            user_input=user_input,
            template_identifier=template_identifier,
            template_text=template_text,
            language=language,
            data_schema=data_schema,
        )
        extraction_errors = self._map_slot_errors(extraction_result.slot_errors, language)
        if extraction_errors:
            raise self._slot_errors_exception(extraction_errors)
        slots = extraction_result.slots
        self._validate_required_slots(slots, template_identifier, language)

        rendered_prompt_text, render_failure_reason = self._render_prompt_text(
            template_text=template_text,
            slots=slots,
            scenario_code=template_identifier,
            language=language,
            description="",
        )
        if rendered_prompt_text is None:
            raise self._generation_exception(
                ErrorCatalog.TEMPLATE_RENDER_FAILED,
                {
                    "template_uri": template_identifier,
                    "reason": render_failure_reason or "Task prompt rendering failed.",
                },
            )
        self._log_info(
            "prompt_metadata_generation_completed template_uri=%s extension_uri=%s",
            template_identifier,
            extension_uri,
        )
        return MetadataContent(
            template_uri=template_identifier,
            prompt_text=rendered_prompt_text,
            extension_uri=extension_uri,
        )

    def _extract_slots_with_metadata(
        self,
        *,
        user_input: object,
        template_identifier: str,
        template_text: str,
        language: str,
        data_schema: Mapping[str, object] | None,
    ) -> SlotExtractionResult:
        """Load the extraction resources and run the template-directed slot extraction.

        The slot schema miss maps to ``slot.schema_not_found`` and the instruction-prompt miss to
        ``template.load_failed`` (Java loads both inside the extractor, where the schema miss is the
        ``ResourceNotFoundException`` catch point of ``generateWithMetadata``); the extractor call
        itself maps its failures to the ``llm.*`` catch points.
        """
        reference = PromptReference(scenario_code=template_identifier, language=language)
        try:
            slot_json_schema = self._resource_access.slot_schema(template_identifier, language)
            slot_schema = slot_schema_from_json_schema(slot_json_schema, scenario_code=template_identifier)
        except A2ATBusinessError as error:
            raise self._generation_exception(
                error.code,
                dict(error.facts),
                message=str(error),
            ) from error
        except A2ATError as error:
            raise self._generation_exception(
                ErrorCatalog.TEMPLATE_LOAD_FAILED,
                {"resource_path": template_identifier},
            ) from error
        try:
            slot_prompts = PromptMessages(
                system_prompt=self._resource_access.load_prompt(_SLOT_EXTRACTION_ACTION, language, "system.md"),
                user_prompt=self._resource_access.load_prompt(_SLOT_EXTRACTION_ACTION, language, "user.md"),
            )
        except A2ATError as error:
            raise self._generation_exception(
                ErrorCatalog.TEMPLATE_LOAD_FAILED,
                {
                    "resource_path": PromptResourceKey.prompt(
                        _SLOT_EXTRACTION_ACTION, language, "system.md"
                    ).relative_path()
                },
            ) from error
        try:
            return self._slot_extractor.extract(
                normalized_input=_stringify_user_input(user_input),
                reference=reference,
                template_text=template_text,
                slot_schema=slot_schema,
                system_prompt=slot_prompts.system_prompt,
                user_prompt=slot_prompts.user_prompt,
                data_schema=data_schema,
            )
        except LLMConfigError as error:
            raise self._generation_exception(ErrorCatalog.LLM_NOT_CONFIGURED, {}) from error
        except LLMError as error:
            raise self._llm_exception(error) from error
        except A2ATError as error:
            raise self._generation_exception(
                ErrorCatalog.LLM_INVOCATION_FAILED,
                {"reason": str(error)},
            ) from error

    def _map_slot_errors(
        self,
        slot_errors: list[Any] | None,
        language: str,
    ) -> list[SlotValidationError]:
        """Map the extraction-time slot errors to catalog errors (Java ``mapSlotErrors``).

        Unknown codes returned by the LLM step are mapped to the ``slot.rule_violation`` fallback
        and logged as a warning; the human-readable message is always rendered from the catalog.
        """
        if not slot_errors:
            return []
        return [self._map_slot_error(error, language) for error in slot_errors if error is not None]

    def _map_slot_error(self, error: Any, language: str) -> SlotValidationError:
        """Map one extraction-time slot error to its catalog error (Java ``mapSlotError``).

        Java keeps this mapping closed: only the ``slot.not_provided`` / ``slot.constraint_violated``
        pairs (plus their legacy misspellings) survive, and every other code — unknown or an
        in-catalog code outside the slot-extraction contract — falls back to
        ``slot.rule_violation`` with a warning, so no unexpected code travels to callers.
        """
        slot_name = getattr(error, "slot_name", None)
        label = slot_name if isinstance(slot_name, str) else ""
        code = str(getattr(error, "code", "") or "")
        if code in {ErrorCatalog.SLOT_NOT_PROVIDED.value, _LEGACY_CODE_MISSING_INPUT}:
            entry = ErrorCatalog.SLOT_NOT_PROVIDED
        elif code in {ErrorCatalog.SLOT_CONSTRAINT_VIOLATED.value, _LEGACY_CODE_INVALID_VALUE}:
            entry = ErrorCatalog.SLOT_CONSTRAINT_VIOLATED
        else:
            _LOGGER.warning(
                "Unknown slot-extraction error code '%s' mapped to '%s'.",
                code,
                ErrorCatalog.SLOT_RULE_VIOLATION.value,
            )
            entry = ErrorCatalog.SLOT_RULE_VIOLATION
        facts = {"slot_label": label}
        parsed_facts = getattr(error, "facts", None)
        if isinstance(parsed_facts, dict):
            for key, value in parsed_facts.items():
                if isinstance(value, str) and value.strip():
                    facts[key] = value
        return SlotValidationError(
            slot_name=label,
            code=entry.value,
            message=render_error_message(entry, facts, language),
            facts=facts,
        )

    def _validate_required_slots(
        self,
        slots: Mapping[str, str | None] | None,
        template_identifier: str,
        language: str,
    ) -> None:
        """Fail with ``slot.not_provided`` when a schema-required slot is blank (Java parity).

        The slot schema is resolved through the same access seam as the extraction step; a miss
        surfaces as ``slot.schema_not_found`` before the required-slot inspection runs.
        """
        try:
            slot_json_schema = self._resource_access.slot_schema(template_identifier, language)
        except A2ATError as error:
            raise self._generation_exception(
                ErrorCatalog.SLOT_SCHEMA_NOT_FOUND,
                {"template_uri": template_identifier, "language": language},
            ) from error
        slot_schema = slot_schema_from_json_schema(slot_json_schema, scenario_code=template_identifier)
        effective_slots: Mapping[str, str | None] = slots if slots is not None else {}
        failed: list[SlotValidationError] = []
        for definition in slot_schema.slots:
            if definition is None or not definition.required:
                continue
            name = definition.name
            if name is None:
                continue
            value = effective_slots.get(name)
            if value is None or not str(value).strip():
                failed.append(self._not_provided_error(name, self._slot_label(definition), language))
        if failed:
            raise self._slot_errors_exception(failed)

    def _not_provided_error(self, slot_name: str, slot_label: str, language: str) -> SlotValidationError:
        """Build the ``slot.not_provided`` error of one missing required slot."""
        facts = {"slot_label": slot_label}
        return SlotValidationError(
            slot_name=slot_name,
            code=ErrorCatalog.SLOT_NOT_PROVIDED.value,
            message=render_error_message(ErrorCatalog.SLOT_NOT_PROVIDED, facts, language),
            facts=facts,
        )

    @staticmethod
    def _slot_label(definition: Any) -> str:
        """Return the label of one slot definition: its description, else its name."""
        description = getattr(definition, "description", None)
        if isinstance(description, str) and description.strip():
            return description
        name = getattr(definition, "name", None)
        return name if isinstance(name, str) else ""

    @staticmethod
    def _slot_errors_exception(errors: list[SlotValidationError]) -> PromptGenerationError:
        """Build the generation failure carrying the first slot error code and all details."""
        first = errors[0]
        return PromptGenerationError(
            _catalog_entry_of(first.code),
            first.facts,
            message="; ".join(error.message for error in errors),
            failed_parameters=errors,
        )

    def _generation_exception(
        self,
        entry: ErrorCatalog,
        facts: Mapping[str, object],
        *,
        message: str | None = None,
        cause: BaseException | None = None,
    ) -> PromptGenerationError:
        """Build one catalog-coded generation failure whose message is rendered from the template."""
        return PromptGenerationError(
            entry,
            facts,
            language=self._config.language,
            message=message,
            cause=cause,
        )

    def _llm_exception(self, error: LLMError) -> PromptGenerationError:
        """Translate one LLM-step failure into the Java client-orchestrator failure codes.

        Configuration failures map to ``llm.not_configured``, response-contract violations to
        ``llm.response_invalid`` (carrying the step label), and everything else to
        ``llm.invocation_failed`` (carrying the underlying message as the reason fact).
        """
        if isinstance(error, LLMConfigError):
            return self._generation_exception(ErrorCatalog.LLM_NOT_CONFIGURED, {}, cause=error)
        if is_response_contract_violation(error):
            return self._generation_exception(
                ErrorCatalog.LLM_RESPONSE_INVALID,
                {"step": _STEP_SLOT_EXTRACTION},
                cause=error,
            )
        return self._generation_exception(
            ErrorCatalog.LLM_INVOCATION_FAILED,
            {"reason": str(error)},
            cause=error,
        )

    def _load_generation_resources(
        self,
        *,
        reference: PromptReference,
    ) -> tuple[str, str, SlotSchema, PromptMessages]:
        """Load generation resources through the shared resource access layer.

        The access layer already raises the artifact-specific catalog codes — ``template.not_found``
        for a missing template, ``slot.schema_not_found`` for a missing slot schema — so the
        business failures pass straight through; every other access failure maps to
        ``template.load_failed`` with the failing resource path as the fact.
        """
        try:
            template_text = self._resource_access.template_text(reference.scenario_code, reference.language)
            slot_json_schema = self._resource_access.slot_schema(reference.scenario_code, reference.language)
            slot_schema = slot_schema_from_json_schema(
                slot_json_schema,
                scenario_code=reference.scenario_code,
            )
        except A2ATBusinessError as error:
            raise _PromptGenerationResourceError(
                entry=error.code,
                facts=error.facts,
                stage=PREPARATION_STAGE,
            ) from error
        except A2ATError as error:
            raise _PromptGenerationResourceError(
                entry=ErrorCatalog.TEMPLATE_LOAD_FAILED,
                facts={"resource_path": reference.scenario_code},
                stage=PREPARATION_STAGE,
            ) from error
        try:
            slot_prompts = PromptMessages(
                system_prompt=self._resource_access.load_prompt(
                    _SLOT_EXTRACTION_ACTION, reference.language, "system.md"
                ),
                user_prompt=self._resource_access.load_prompt(_SLOT_EXTRACTION_ACTION, reference.language, "user.md"),
            )
        except A2ATError as error:
            raise _PromptGenerationResourceError(
                entry=ErrorCatalog.TEMPLATE_LOAD_FAILED,
                facts={
                    "resource_path": PromptResourceKey.prompt(
                        _SLOT_EXTRACTION_ACTION, reference.language, "system.md"
                    ).relative_path()
                },
                stage=PREPARATION_STAGE,
            ) from error
        return reference.language, template_text, slot_schema, slot_prompts

    def _render_prompt_text(
        self,
        *,
        template_text: str,
        slots: dict[str, str | None],
        scenario_code: str,
        language: str,
        description: str,
    ) -> tuple[str | None, str | None]:
        """Render the final prompt text while preserving renderer failures as data."""
        try:
            return (
                self._renderer.render(
                    template_text=template_text,
                    slots=slots,
                    scenario_code=scenario_code,
                    language=language,
                    description=description,
                ),
                None,
            )
        except TaskPromptRenderError as error:
            return None, str(error)

    def _llm_failure_result(self, error: BaseException, *, step: str) -> PromptGenerationResult:
        """Translate one LLM-step failure into the Java client-orchestrator failure codes.

        Configuration failures map to ``llm.not_configured``, response-contract violations to
        ``llm.response_invalid`` (carrying the step label), and everything else to
        ``llm.invocation_failed`` (carrying the underlying message as the reason fact).
        """
        if isinstance(error, LLMConfigError):
            return self._catalog_failure(
                entry=ErrorCatalog.LLM_NOT_CONFIGURED,
                facts={},
                stage=GENERATION_STAGE,
            )
        if is_response_contract_violation(error):
            return self._catalog_failure(
                entry=ErrorCatalog.LLM_RESPONSE_INVALID,
                facts={"step": step},
                stage=GENERATION_STAGE,
            )
        return self._catalog_failure(
            entry=ErrorCatalog.LLM_INVOCATION_FAILED,
            facts={"reason": str(error)},
            stage=GENERATION_STAGE,
        )

    def _catalog_failure(
        self,
        *,
        entry: ErrorCatalog,
        facts: Mapping[str, object],
        stage: str,
    ) -> PromptGenerationResult:
        """Build a generation failure whose message is rendered from the code's template."""
        return self._failure_result(
            code=entry.value,
            message=render_error_message(entry, facts, self._config.language),
            stage=stage,
        )

    def _failure_result(
        self,
        *,
        code: str,
        message: str,
        stage: str,
    ) -> PromptGenerationResult:
        """Build a standardized generation failure result without scenario context."""
        return self._finalize_result(
            PromptGenerationResult(
                success=False,
                prompt_text=None,
                failure=PromptGenerationFailure(code=code, message=message, stage=stage),
            )
        )

    def _finalize_result(self, result: PromptGenerationResult) -> PromptGenerationResult:
        """Emit completion logs and return the final generation result unchanged."""
        failure_stage = result.failure.stage if result.failure is not None else None
        failure_code = result.failure.code if result.failure is not None else None
        self._log_info(
            "prompt_generation_completed success=%s stage=%s code=%s",
            result.success,
            failure_stage,
            failure_code,
        )
        return result

    def _log_info(self, message: str, *args: object) -> None:
        """Write an info log through the configured logger."""
        self._logger.info(message, *args)

    def _log_debug(self, message: str, *args: object) -> None:
        """Write a debug log only when prompt-generation debug mode is enabled."""
        if self._is_debug_enabled():
            self._logger.debug(message, *args)

    def _log_debug_if_available(self, message: str, source: Any) -> None:
        """Log captured raw model output when the dependency exposes it."""
        raw_content = getattr(source, "last_raw_response_content", None)
        if raw_content is not None:
            self._log_debug(message, raw_content)

    def _is_debug_enabled(self) -> bool:
        """Return whether prompt-generation debug logging is enabled."""
        return bool(getattr(self._config, "prompt_generation_debug", False))


class _PromptGenerationResourceError(Exception):
    """Carry catalog failure details before they are converted into API results."""

    def __init__(self, *, entry: ErrorCatalog, facts: Mapping[str, object], stage: str) -> None:
        super().__init__(entry.value)
        self.entry = entry
        self.facts = dict(facts)
        self.stage = stage


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


def _known_slot_code(code: str) -> ErrorCatalog | None:
    """Return the catalog entry of one in-catalog ``slot.*`` code, or ``None``."""
    try:
        entry = by_code(code)
    except KeyError:
        return None
    return entry if entry.value.startswith("slot.") else None


def _catalog_entry_of(code: str) -> ErrorCatalog:
    """Return the catalog entry carrying one slot error code, falling back to ``slot.rule_violation``."""
    entry = _known_slot_code(code)
    return entry if entry is not None else ErrorCatalog.SLOT_RULE_VIOLATION


def _stringify_user_input(user_input: object) -> str:
    """Render one extraction input as the text passed to the LLM (Java ``String.valueOf``)."""
    if isinstance(user_input, Mapping):
        return str(dict(user_input))
    return str(user_input)
