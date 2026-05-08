from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.analysis.models import ScenarioDefinition, ScenarioResolutionFailure, ScenarioResolutionResult, SlotExtractionResult
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.prompt.analysis.errors import PromptAnalysisError
from a2a_t.prompt.common.errors import PromptSourceError
from a2a_t.client.prompt_generation.generation_constants import (
    GENERATION_STAGE,
    INVALID_LLM_OUTPUT,
    LLM_EXECUTION_FAILED,
    PROMPT_NOT_FOUND,
    PROMPT_RESOURCE_ACCESS_ERROR,
    PROMPT_RESOURCE_PARSE_ERROR,
    RENDER_FAILED,
    RENDER_STAGE,
    SCENARIO_PARSE_FAILED,
    SCENARIO_STAGE,
    SLOT_SCHEMA_NOT_FOUND,
    TEMPLATE_NOT_FOUND,
)
from a2a_t.prompt.common.models import PromptReference
from a2a_t.common.prompt_resources.errors import PromptResourceParseError
from a2a_t.common.prompt_resources.models import PromptMessages, ScenarioDefinition, SlotDefinition, SlotSchema


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


class FakeScenarioResolver:
    def __init__(self, result: ScenarioResolutionResult) -> None:
        self._result = result
        self.calls: list[str] = []
        self.last_raw_response_content = '{"matched": true}'

    def resolve(self, normalized_input: str) -> ScenarioResolutionResult:
        self.calls.append(normalized_input)
        return self._result


class FakeSlotExtractor:
    def __init__(self, result: SlotExtractionResult) -> None:
        self._result = result
        self.last_reference: PromptReference | None = None
        self.last_raw_response_content = '{"slots": {}}'

    def extract(self, **kwargs: object) -> SlotExtractionResult:
        self.last_reference = kwargs.get("reference")
        return self._result


class RaisingSlotExtractor:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.last_raw_response_content: str | None = None

    def extract(self, **kwargs: object) -> SlotExtractionResult:
        raise self._error


class FakeLogger:
    def __init__(self) -> None:
        self.info_calls: list[str] = []
        self.debug_calls: list[str] = []

    def info(self, message: str, *args: object) -> None:
        self.info_calls.append(message % args if args else message)

    def debug(self, message: str, *args: object) -> None:
        self.debug_calls.append(message % args if args else message)


class FakeRenderer:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def render(self, **kwargs: object) -> str:
        raise self._error


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
    def test_public_generation_error_codes_are_lowercase(self) -> None:
        public_error_codes = [
            SCENARIO_PARSE_FAILED,
            TEMPLATE_NOT_FOUND,
            SLOT_SCHEMA_NOT_FOUND,
            PROMPT_NOT_FOUND,
            PROMPT_RESOURCE_PARSE_ERROR,
            PROMPT_RESOURCE_ACCESS_ERROR,
            INVALID_LLM_OUTPUT,
            LLM_EXECUTION_FAILED,
            RENDER_FAILED,
        ]

        self.assertEqual(public_error_codes, [code.lower() for code in public_error_codes])

    def _build_orchestrator(
        self,
        *,
        scenario_result: ScenarioResolutionResult,
        extraction_result: SlotExtractionResult,
        resource_registry: FakeResourceRegistry | None = None,
        debug_enabled: bool = False,
        logger: FakeLogger | None = None,
        slot_extractor: object | None = None,
        renderer: object | None = None,
    ):
        from a2a_t.client.prompt_generation.prompt_generation_orchestrator import PromptGenerationOrchestrator

        self.template_loader = FakeTemplateLoader()
        self.slot_schema_loader = FakeSlotSchemaLoader()
        self.slot_extractor = slot_extractor or FakeSlotExtractor(extraction_result)
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
            scenario_resolver=FakeScenarioResolver(scenario_result),
            slot_extractor=self.slot_extractor,
            resource_registry=resource_registry,
            renderer=renderer,
            logger=self.logger,
        )

    def test_orchestrator_requires_prompt_runtime_config(self) -> None:
        from a2a_t.client.prompt_generation.prompt_generation_orchestrator import PromptGenerationOrchestrator

        with self.assertRaises(TypeError):
            PromptGenerationOrchestrator(
                config=object(),
                scenario_loader=FakeScenarioLoader(),
                prompt_resource_loader=FakePromptResourceLoader(),
                template_loader=FakeTemplateLoader(),
                slot_schema_loader=FakeSlotSchemaLoader(),
                scenario_resolver=FakeScenarioResolver(
                    ScenarioResolutionResult(
                        success=True,
                        reference=PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"),
                        scenario=ScenarioDefinition(
                            scenario_code="energy_saving",
                            scenario_name="Energy Saving",
                            description="Used for energy saving analysis.",
                            example="Analyze site power usage and suggest optimization.",
                        ),
                    )
                ),
                slot_extractor=FakeSlotExtractor(
                    SlotExtractionResult(
                        slots={"site": "Site A", "additional_notes": None},
                        slot_errors=[],
                    )
                ),
            )

    def test_generate_returns_success_result(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioResolutionResult(
                success=True,
                reference=PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"),
                scenario=ScenarioDefinition(
                    scenario_code="energy_saving",
                    scenario_name="Energy Saving",
                    description="Used for energy saving analysis.",
                    example="Analyze site power usage and suggest optimization.",
                ),
            ),
            extraction_result=SlotExtractionResult(
                slots={"site": "Site A", "additional_notes": None},
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
        self.assertEqual(result.prompt_text, "Site: Site A\nNotes: ")

    def test_generate_returns_success_result_when_extracted_slots_are_missing(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioResolutionResult(
                success=True,
                reference=PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"),
                scenario=ScenarioDefinition(
                    scenario_code="energy_saving",
                    scenario_name="Energy Saving",
                    description="Used for energy saving analysis.",
                    example="Analyze site power usage and suggest optimization.",
                ),
            ),
            extraction_result=SlotExtractionResult(
                slots={"site": None, "additional_notes": None},
                slot_errors=[],
            ),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertTrue(result.success)
        self.assertEqual(result.prompt_text, "Site: \nNotes: ")
        self.assertIsNone(result.failure)

    def test_generate_returns_scenario_failure_when_resolution_fails(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioResolutionResult(
                success=False,
                failure=ScenarioResolutionFailure(
                    code="scenario_parse_failed",
                    message="No matching scenario.",
                    stage=SCENARIO_STAGE,
                ),
            ),
            extraction_result=SlotExtractionResult(slots={}, slot_errors=[]),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertFalse(result.success)
        self.assertIsNone(result.prompt_text)
        self.assertEqual(result.failure.code, SCENARIO_PARSE_FAILED)
        self.assertEqual(result.failure.stage, SCENARIO_STAGE)

    def test_generate_returns_scenario_failure_when_scenario_resources_are_invalid(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioResolutionResult(
                success=False,
                failure=ScenarioResolutionFailure(
                    code=PROMPT_RESOURCE_PARSE_ERROR,
                    message="scenario resources are invalid",
                    stage=SCENARIO_STAGE,
                ),
            ),
            extraction_result=SlotExtractionResult(
                slots={"site": "Site A", "additional_notes": None},
                slot_errors=[],
            ),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertFalse(result.success)
        self.assertEqual(result.failure.code, PROMPT_RESOURCE_PARSE_ERROR)
        self.assertEqual(result.failure.stage, SCENARIO_STAGE)
        self.assertEqual(result.failure.message, "scenario resources are invalid")

    def test_generate_returns_generation_failure_when_generation_resource_access_fails(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioResolutionResult(
                success=True,
                reference=PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"),
                scenario=ScenarioDefinition(
                    scenario_code="energy_saving",
                    scenario_name="Energy Saving",
                    description="Used for energy saving analysis.",
                    example="Analyze site power usage and suggest optimization.",
                ),
            ),
            extraction_result=SlotExtractionResult(
                slots={"site": "Site A", "additional_notes": None},
                slot_errors=[],
            ),
            resource_registry=FakeResourceRegistry(
                generation_result=PromptSourceError("generation resource path escapes local root"),
            ),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertFalse(result.success)
        self.assertEqual(result.failure.code, PROMPT_RESOURCE_ACCESS_ERROR)
        self.assertEqual(result.failure.stage, GENERATION_STAGE)
        self.assertEqual(result.failure.message, "generation resource path escapes local root")

    def test_generate_returns_generation_failure_when_slot_extraction_payload_is_invalid(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioResolutionResult(
                success=True,
                reference=PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"),
                scenario=ScenarioDefinition(
                    scenario_code="energy_saving",
                    scenario_name="Energy Saving",
                    description="Used for energy saving analysis.",
                    example="Analyze site power usage and suggest optimization.",
                ),
            ),
            extraction_result=SlotExtractionResult(slots={}, slot_errors=[]),
            slot_extractor=RaisingSlotExtractor(PromptAnalysisError("slot extraction returned invalid JSON")),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertFalse(result.success)
        self.assertEqual(result.failure.code, INVALID_LLM_OUTPUT)
        self.assertEqual(result.failure.stage, GENERATION_STAGE)
        self.assertEqual(result.failure.message, "slot extraction returned invalid JSON")

    def test_generate_returns_generation_failure_when_slot_extraction_runtime_fails(self) -> None:
        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioResolutionResult(
                success=True,
                reference=PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"),
                scenario=ScenarioDefinition(
                    scenario_code="energy_saving",
                    scenario_name="Energy Saving",
                    description="Used for energy saving analysis.",
                    example="Analyze site power usage and suggest optimization.",
                ),
            ),
            extraction_result=SlotExtractionResult(slots={}, slot_errors=[]),
            slot_extractor=RaisingSlotExtractor(RuntimeError("llm transport down")),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertFalse(result.success)
        self.assertEqual(result.failure.code, LLM_EXECUTION_FAILED)
        self.assertEqual(result.failure.stage, GENERATION_STAGE)
        self.assertEqual(result.failure.message, "llm transport down")

    def test_generate_returns_render_failure_when_renderer_rejects_slots(self) -> None:
        from a2a_t.prompt.task_rendering import TaskPromptRenderError

        orchestrator = self._build_orchestrator(
            scenario_result=ScenarioResolutionResult(
                success=True,
                reference=PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"),
                scenario=ScenarioDefinition(
                    scenario_code="energy_saving",
                    scenario_name="Energy Saving",
                    description="Used for energy saving analysis.",
                    example="Analyze site power usage and suggest optimization.",
                ),
            ),
            extraction_result=SlotExtractionResult(
                slots={"site": "Site A", "additional_notes": None},
                slot_errors=[],
            ),
            renderer=FakeRenderer(TaskPromptRenderError("Template references unknown slot: time_range")),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertFalse(result.success)
        self.assertEqual(result.failure.code, RENDER_FAILED)
        self.assertEqual(result.failure.stage, RENDER_STAGE)
        self.assertEqual(result.failure.message, "Template references unknown slot: time_range")


if __name__ == "__main__":
    unittest.main()
