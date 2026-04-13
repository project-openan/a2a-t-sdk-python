from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.analysis.models import ScenarioRecognitionResult, SlotExtractionResult
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
    def load(self, *, scenario_code: str, version: str, language: str) -> str:
        return "Site: {site}\nNotes: {additional_notes}"


class FakeSlotSchemaLoader:
    def load(self, *, scenario_code: str, version: str, language: str) -> SlotSchema:
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

    def recognize(self, **kwargs: object) -> ScenarioRecognitionResult:
        return self._result


class FakeSlotExtractor:
    def __init__(self, result: SlotExtractionResult) -> None:
        self._result = result

    def extract(self, **kwargs: object) -> SlotExtractionResult:
        return self._result


class FakeSlotValidator:
    def __init__(self, result: SlotValidationResult) -> None:
        self._result = result

    def validate(self, **kwargs: object) -> SlotValidationResult:
        return self._result


class PromptGenerationOrchestratorTest(unittest.TestCase):
    def _build_orchestrator(
        self,
        *,
        scenario_result: ScenarioRecognitionResult,
        extraction_result: SlotExtractionResult,
        validation_result: SlotValidationResult,
    ):
        from a2a_t.client.prompt.prompt_generation_orchestrator import PromptGenerationOrchestrator

        return PromptGenerationOrchestrator(
            config=type("Config", (), {"language": "en-US", "prompt_resource_version": "0.0.1"})(),
            scenario_loader=FakeScenarioLoader(),
            prompt_resource_loader=FakePromptResourceLoader(),
            template_loader=FakeTemplateLoader(),
            slot_schema_loader=FakeSlotSchemaLoader(),
            scenario_recognizer=FakeScenarioRecognizer(scenario_result),
            slot_extractor=FakeSlotExtractor(extraction_result),
            slot_validator=FakeSlotValidator(validation_result),
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
        self.assertIsNone(result.failure)
        self.assertEqual(result.scenario.code, "energy_saving")
        self.assertEqual(result.language, "en-US")
        self.assertEqual(result.input_kind, "natural_language")
        self.assertEqual(result.validation.missing_required_fields, [])
        self.assertIn("scenario_code: energy_saving", result.prompt_text)
        self.assertIn("Site: Site A", result.prompt_text)

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
        self.assertIsNone(result.scenario)
        self.assertEqual(result.failure.code, "SCENARIO_PARSE_FAILED")
        self.assertEqual(result.failure.stage, "scenario")
        self.assertEqual(result.validation.slot_errors, [])

    def test_generate_returns_validation_failure_and_derives_missing_required_fields(self) -> None:
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
                        code="missing_input",
                        message="Required slot 'site' is missing.",
                    )
                ],
            ),
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertFalse(result.success)
        self.assertIsNone(result.prompt_text)
        self.assertEqual(result.failure.code, "MISSING_REQUIRED_FIELDS")
        self.assertEqual(result.failure.stage, "validation")
        self.assertEqual(result.validation.missing_required_fields, ["site"])


if __name__ == "__main__":
    unittest.main()
