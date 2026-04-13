from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.analysis.models import ScenarioRecognitionResult, SlotExtractionResult
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.prompt.validation.constants import MISSING_INPUT
from a2a_t.prompt.common.errors import PromptSourceError
from a2a_t.client.prompt.generation_constants import (
    GENERATION_STAGE,
    INVALID_LLM_OUTPUT,
    MISSING_REQUIRED_FIELDS,
    PROMPT_RESOURCE_ACCESS_ERROR,
    PROMPT_RESOURCE_PARSE_ERROR,
    SCENARIO_PARSE_FAILED,
    SCENARIO_STAGE,
    VALIDATION_STAGE,
)
from a2a_t.prompt.common.models import PromptReference
from a2a_t.prompt.resources.errors import PromptResourceParseError
from a2a_t.prompt.resources.models import PromptMessages, ScenarioDefinition, SlotDefinition, SlotSchema
from a2a_t.prompt.validation.models import SlotValidationError, SlotValidationResult


class FakeScenarioLoader:
    def load(self, *, version: str, language: str) -> list[ScenarioDefinition]:
        return [
            ScenarioDefinition(
                scenario_code="energy_saving",
                scenario_name="Energy Saving",
                description="Used for energy saving analysis.",
                example="Analyze site power usage and suggest optimization.",
            )
        ]


class FakePromptResourceLoader:
    def load(self, *, analysis_action: str, version: str, language: str) -> PromptMessages:
        if analysis_action == "scenario_recognition":
            return PromptMessages(system_prompt="Identify scenario.", user_prompt="Choose scenario.")
        return PromptMessages(system_prompt="Extract slots.", user_prompt="Return slots.")


class FakeTemplateLoader:
    def __init__(self) -> None:
        self.last_reference: PromptReference | None = None

    def load(self, *, reference: PromptReference) -> str:
        self.last_reference = reference
        return "Site: {site}\nNotes: {additional_notes}"


class FakeSlotSchemaLoader:
    def __init__(self) -> None:
        self.last_reference: PromptReference | None = None

    def load(self, *, reference: PromptReference) -> SlotSchema:
        self.last_reference = reference
        return SlotSchema(
            scenario_code="energy_saving",
            version="0.0.1",
            slots=[
                SlotDefinition(
                    name="site",
                    required=True,
                    description="Site name",
                    example="Site A",
                    value_constraint="Must be a concrete site name.",
                    type="string",
                    allowed_values=None,
                    range=None,
                    pattern=None,
                ),
                SlotDefinition(
                    name="additional_notes",
                    required=False,
                    description="Additional notes",
                    example="Focus on power system",
                    value_constraint="Free-form notes.",
                    type="string",
                    allowed_values=None,
                    range=None,
                    pattern=None,
                ),
            ],
        )


class FakeScenarioRecognizer:
    def __init__(self, result: ScenarioRecognitionResult) -> None:
        self._result = result
        self.last_raw_response_content = '{"matched": true}'

    def recognize(self, **kwargs: object) -> ScenarioRecognitionResult:
        return self._result


class FakeSlotExtractor:
    def __init__(self, result: SlotExtractionResult) -> None:
        self._result = result
        self.last_reference: PromptReference | None = None
        self.last_raw_response_content = '{"slots": {}}'

    def extract(self, **kwargs: object) -> SlotExtractionResult:
        self.last_reference = kwargs.get("reference")
        return self._result


class FakeSlotValidator:
    def __init__(self, result: SlotValidationResult) -> None:
        self._result = result

    def validate(self, **kwargs: object) -> SlotValidationResult:
        return self._result


class FakeLogger:
    def __init__(self) -> None:
        self.info_calls: list[str] = []
        self.debug_calls: list[str] = []

    def info(self, message: str, *args: object) -> None:
        self.info_calls.append(message % args if args else message)

    def debug(self, message: str, *args: object) -> None:
        self.debug_calls.append(message % args if args else message)


class FakeResourceRegistry:
    def __init__(
        self,
        *,
        scenario_result: object | None = None,
        generation_result: object | None = None,
    ) -> None:
        self._scenario_result = scenario_result
        self._generation_result = generation_result

    def load_scenario_resources(self, *, version: str, language: str) -> object:
        if isinstance(self._scenario_result, Exception):
            raise self._scenario_result
        return self._scenario_result

    def load_generation_resources(self, *, reference: PromptReference) -> object:
        if isinstance(self._generation_result, Exception):
            raise self._generation_result
        return self._generation_result


class FakePromptRuntimeConfig(PromptRuntimeConfig):
    __slots__ = ("prompt_generation_debug",)

    def __init__(
        self,
        *,
        language: str = "en-US",
        prompt_resource_version: str = "0.0.1",
        source_type: str = "local_file",
        local_root_dir: str = "./package_data/prompt_resources",
        prompt_generation_debug: bool = False,
    ) -> None:
        super().__init__(
            language=language,
            prompt_resource_version=prompt_resource_version,
            source_type=source_type,
            local_root_dir=local_root_dir,
        )
        self.prompt_generation_debug = prompt_generation_debug


class PromptGenerationOrchestratorTest(unittest.TestCase):
    def test_orchestrator_no_longer_keeps_obsolete_private_loader_methods(self) -> None:
        from a2a_t.client.prompt.prompt_generation_orchestrator import PromptGenerationOrchestrator

        self.assertFalse(hasattr(PromptGenerationOrchestrator, "_load_template"))
        self.assertFalse(hasattr(PromptGenerationOrchestrator, "_load_slot_schema"))
        self.assertFalse(hasattr(PromptGenerationOrchestrator, "_load_slot_prompts"))

    def _build_orchestrator(
        self,
        *,
        scenario_result: ScenarioRecognitionResult,
        extraction_result: SlotExtractionResult,
        validation_result: SlotValidationResult,
        resource_registry: FakeResourceRegistry | None = None,
        debug_enabled: bool = False,
        logger: FakeLogger | None = None,
    ):
        from a2a_t.client.prompt.prompt_generation_orchestrator import PromptGenerationOrchestrator

        self.template_loader = FakeTemplateLoader()
        self.slot_schema_loader = FakeSlotSchemaLoader()
        self.slot_extractor = FakeSlotExtractor(extraction_result)
        self.logger = logger or FakeLogger()

        return PromptGenerationOrchestrator(
            config=FakePromptRuntimeConfig(
                language="en-US",
                prompt_resource_version="0.0.1",
                prompt_generation_debug=debug_enabled,
            ),
            scenario_loader=FakeScenarioLoader(),
            prompt_resource_loader=FakePromptResourceLoader(),
            template_loader=self.template_loader,
            slot_schema_loader=self.slot_schema_loader,
            scenario_recognizer=FakeScenarioRecognizer(scenario_result),
            slot_extractor=self.slot_extractor,
            slot_validator=FakeSlotValidator(validation_result),
            resource_registry=resource_registry,
            logger=self.logger,
        )

    def test_orchestrator_requires_prompt_runtime_config(self) -> None:
        from a2a_t.client.prompt.prompt_generation_orchestrator import PromptGenerationOrchestrator

        with self.assertRaises(TypeError):
            PromptGenerationOrchestrator(
                config=object(),
                scenario_loader=FakeScenarioLoader(),
                prompt_resource_loader=FakePromptResourceLoader(),
                template_loader=FakeTemplateLoader(),
                slot_schema_loader=FakeSlotSchemaLoader(),
                scenario_recognizer=FakeScenarioRecognizer(
                    ScenarioRecognitionResult(
                        matched=True,
                        scenario_code="energy_saving",
                        error_message=None,
                    )
                ),
                slot_extractor=FakeSlotExtractor(
                    SlotExtractionResult(
                        slots={"site": "Site A", "additional_notes": None},
                        slot_errors=[],
                    )
                ),
                slot_validator=FakeSlotValidator(
                    SlotValidationResult(
                        passed=True,
                        slot_errors=[],
                    )
                ),
            )

    def test_generate_returns_success_result(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioRecognitionResult(
                matched=True,
                scenario_code="energy_saving",
                error_message=None,
            ),
            extraction_result=SlotExtractionResult(
                slots={"site": "Site A", "additional_notes": None},
                slot_errors=[],
            ),
            validation_result=SlotValidationResult(
                passed=True,
                slot_errors=[],
            ),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertTrue(result.success)
        self.assertEqual(
            self.template_loader.last_reference,
            PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"),
        )
        self.assertEqual(
            self.slot_schema_loader.last_reference,
            PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"),
        )
        self.assertEqual(
            self.slot_extractor.last_reference,
            PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"),
        )
        self.assertIsNone(result.failure)
        self.assertEqual(result.scenario_code, "energy_saving")
        self.assertEqual(result.language, "en-US")
        self.assertEqual(result.input_kind, "natural_language")
        self.assertEqual(result.validation.slot_errors, [])
        self.assertIn("scenario_code: energy_saving", result.prompt_text)
        self.assertIn("Site: Site A", result.prompt_text)
        self.assertTrue(any("input_kind=natural_language" in message for message in self.logger.info_calls))
        self.assertTrue(any("scenario_code=energy_saving" in message for message in self.logger.info_calls))
        self.assertTrue(any("slots={'site': 'Site A', 'additional_notes': None}" in message for message in self.logger.info_calls))
        self.assertTrue(any("slot_errors=[]" in message for message in self.logger.info_calls))
        self.assertTrue(any("success=True" in message for message in self.logger.info_calls))
        self.assertEqual(self.logger.debug_calls, [])

    def test_generate_returns_scenario_failure_when_no_match(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioRecognitionResult(
                matched=False,
                scenario_code=None,
                error_message="No matching scenario.",
            ),
            extraction_result=SlotExtractionResult(slots={}, slot_errors=[]),
            validation_result=SlotValidationResult(passed=False, slot_errors=[]),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertFalse(result.success)
        self.assertIsNone(result.prompt_text)
        self.assertIsNone(result.scenario_code)
        self.assertEqual(result.failure.code, SCENARIO_PARSE_FAILED)
        self.assertEqual(result.failure.stage, SCENARIO_STAGE)
        self.assertEqual(result.validation.slot_errors, [])

    def test_generate_rejects_unknown_scenario_code_before_loading_generation_resources(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioRecognitionResult(
                matched=True,
                scenario_code="unknown_scenario",
                error_message=None,
            ),
            extraction_result=SlotExtractionResult(slots={}, slot_errors=[]),
            validation_result=SlotValidationResult(passed=False, slot_errors=[]),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertFalse(result.success)
        self.assertIsNone(result.prompt_text)
        self.assertIsNone(self.template_loader.last_reference)
        self.assertIsNone(self.slot_schema_loader.last_reference)
        self.assertIsNone(self.slot_extractor.last_reference)
        self.assertEqual(result.failure.code, INVALID_LLM_OUTPUT)
        self.assertEqual(result.failure.stage, SCENARIO_STAGE)
        self.assertEqual(result.failure.message, "Scenario recognition returned unsupported scenario_code: unknown_scenario")

    def test_generate_returns_validation_failure_with_slot_errors_only(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioRecognitionResult(
                matched=True,
                scenario_code="energy_saving",
                error_message=None,
            ),
            extraction_result=SlotExtractionResult(
                slots={"site": None, "additional_notes": None},
                slot_errors=[],
            ),
            validation_result=SlotValidationResult(
                passed=False,
                slot_errors=[
                    SlotValidationError(
                        slot_name="site",
                        code=MISSING_INPUT,
                        message="Required slot 'site' is missing.",
                    )
                ],
            ),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertFalse(result.success)
        self.assertIsNone(result.prompt_text)
        self.assertEqual(result.failure.code, MISSING_REQUIRED_FIELDS)
        self.assertEqual(result.failure.stage, VALIDATION_STAGE)
        self.assertEqual(
            result.validation.slot_errors,
            [
                SlotValidationError(
                    slot_name="site",
                    code=MISSING_INPUT,
                    message="Required slot 'site' is missing.",
                )
            ],
        )
        self.assertFalse(any("missing_required_fields=" in message for message in self.logger.info_calls))
        self.assertTrue(any("slot_errors=[SlotValidationError(slot_name='site', code='missing_input', message=\"Required slot 'site' is missing.\")]" in message for message in self.logger.info_calls))

    def test_client_constants_shim_module_is_not_importable(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("a2a_t.client.prompt.constants")

    def test_legacy_private_constants_module_is_not_importable(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("a2a_t.client.prompt._constants")

    def test_generate_logs_raw_input_and_raw_llm_outputs_when_debug_enabled(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioRecognitionResult(
                matched=True,
                scenario_code="energy_saving",
                error_message=None,
            ),
            extraction_result=SlotExtractionResult(
                slots={"site": "Site A", "additional_notes": None},
                slot_errors=[],
            ),
            validation_result=SlotValidationResult(
                passed=True,
                slot_errors=[],
            ),
            debug_enabled=True,
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertTrue(result.success)
        self.assertTrue(any("raw_user_input=Analyze Site A energy usage." in message for message in self.logger.debug_calls))
        self.assertTrue(any('scenario_raw_output={"matched": true}' in message for message in self.logger.debug_calls))
        self.assertTrue(any('slot_raw_output={"slots": {}}' in message for message in self.logger.debug_calls))

    def test_generate_returns_scenario_failure_when_scenario_resources_are_invalid(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioRecognitionResult(
                matched=True,
                scenario_code="energy_saving",
                error_message=None,
            ),
            extraction_result=SlotExtractionResult(
                slots={"site": "Site A", "additional_notes": None},
                slot_errors=[],
            ),
            validation_result=SlotValidationResult(
                passed=True,
                slot_errors=[],
            ),
            resource_registry=FakeResourceRegistry(
                scenario_result=PromptResourceParseError("scenario resources are invalid"),
            ),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertFalse(result.success)
        self.assertEqual(result.failure.code, PROMPT_RESOURCE_PARSE_ERROR)
        self.assertEqual(result.failure.stage, SCENARIO_STAGE)
        self.assertEqual(result.failure.message, "scenario resources are invalid")

    def test_generate_returns_generation_failure_when_generation_resource_access_fails(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioRecognitionResult(
                matched=True,
                scenario_code="energy_saving",
                error_message=None,
            ),
            extraction_result=SlotExtractionResult(
                slots={"site": "Site A", "additional_notes": None},
                slot_errors=[],
            ),
            validation_result=SlotValidationResult(
                passed=True,
                slot_errors=[],
            ),
            resource_registry=FakeResourceRegistry(
                scenario_result=(
                    "en-US",
                    [
                        ScenarioDefinition(
                            scenario_code="energy_saving",
                            scenario_name="Energy Saving",
                            description="Used for energy saving analysis.",
                            example="Analyze site power usage and suggest optimization.",
                        )
                    ],
                    PromptMessages(system_prompt="Identify scenario.", user_prompt="Choose scenario."),
                ),
                generation_result=PromptSourceError("generation resource path escapes local root"),
            ),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertFalse(result.success)
        self.assertEqual(result.failure.code, PROMPT_RESOURCE_ACCESS_ERROR)
        self.assertEqual(result.failure.stage, GENERATION_STAGE)
        self.assertEqual(result.failure.message, "generation resource path escapes local root")


if __name__ == "__main__":
    unittest.main()
