from __future__ import annotations

import json
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.llm.base import LLMResponse
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.prompt.analysis import ScenarioRecognizer, SlotExtractor
from a2a_t.common.prompt_resources import PromptResourceLoader, ScenarioLoader, SlotSchemaLoader, TemplateLoader
from a2a_t.prompt.validation import SlotValidator
from tests.test_support import ManagedTempDirTestCase


class FakeSequencedLLMClient:
    def __init__(self, response_texts: list[str]) -> None:
        self._response_texts = list(response_texts)
        self.calls: list[dict[str, object]] = []

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, object], **kwargs: object) -> LLMResponse:
        self.calls.append({"messages": messages, "json_schema": json_schema, "kwargs": kwargs})
        return LLMResponse(
            content=self._response_texts.pop(0),
            model="fake-model",
            usage={},
            metadata={},
        )


class PromptGenerationOrchestratorFallbackTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_temp_dir("prompt_resources")

    def _write_resource_file(self, relative_path: str, content: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_generate_falls_back_to_en_us_when_requested_language_resources_are_missing(self) -> None:
        self._write_resource_file(
            "scenarios/0.0.1/en-US/scenarios.json",
            json.dumps(
                {
                    "scenarios": [
                        {
                            "scenario_code": "energy_saving",
                            "scenario_name": "Energy Saving",
                            "description": "Used for energy saving analysis.",
                            "example": "Analyze site power usage and suggest optimization.",
                        }
                    ]
                },
                ensure_ascii=True,
            ),
        )
        self._write_resource_file("prompts/scenario_recognition/0.0.1/en-US/system.md", "Identify scenario.")
        self._write_resource_file("prompts/scenario_recognition/0.0.1/en-US/user.md", "Choose scenario.")
        self._write_resource_file("prompts/slot_extraction/0.0.1/en-US/system.md", "Extract slots.")
        self._write_resource_file("prompts/slot_extraction/0.0.1/en-US/user.md", "Return slots.")
        self._write_resource_file("templates/energy_saving/0.0.1/en-US/template.md", "Site: {site}\nNotes: {additional_notes}")
        self._write_resource_file(
            "slots/energy_saving/0.0.1/en-US/slot.json",
            json.dumps(
                {
                    "scenario_code": "energy_saving",
                    "version": "0.0.1",
                    "slots": [
                        {
                            "name": "site",
                            "required": True,
                            "description": "Site name",
                            "example": "Site A",
                            "value_constraint": "Must be a concrete site name.",
                            "type": "string",
                            "allowed_values": None,
                            "range": None,
                            "pattern": None,
                        },
                        {
                            "name": "additional_notes",
                            "required": False,
                            "description": "Additional notes",
                            "example": "Focus on power system",
                            "value_constraint": "Free-form notes.",
                            "type": "string",
                            "allowed_values": None,
                            "range": None,
                            "pattern": None,
                        },
                    ],
                },
                ensure_ascii=True,
            ),
        )

        llm_client = FakeSequencedLLMClient(
            [
                '{"matched": true, "scenario_code": "energy_saving", "error_message": null}',
                '{"slots": {"site": "Site A", "additional_notes": null}, "slot_errors": []}',
            ]
        )

        from a2a_t.client.prompt_generation.prompt_generation_orchestrator import PromptGenerationOrchestrator

        orchestrator = PromptGenerationOrchestrator(
            config=PromptRuntimeConfig(language="zh-CN", prompt_resource_version="0.0.1"),
            scenario_loader=ScenarioLoader(root_dir=self.root),
            prompt_resource_loader=PromptResourceLoader(root_dir=self.root),
            template_loader=TemplateLoader(root_dir=self.root),
            slot_schema_loader=SlotSchemaLoader(root_dir=self.root),
            scenario_recognizer=ScenarioRecognizer(llm_client=llm_client),
            slot_extractor=SlotExtractor(llm_client=llm_client),
            slot_validator=SlotValidator(),
        )

        result = orchestrator.generate("Analyze Site A.")

        self.assertTrue(result.success)
        self.assertEqual(result.language, "en-US")
        self.assertIn("language: en-US", result.prompt_text)

    def test_generate_returns_template_not_found_when_template_is_missing_after_fallback(self) -> None:
        self._write_resource_file(
            "scenarios/0.0.1/en-US/scenarios.json",
            json.dumps(
                {
                    "scenarios": [
                        {
                            "scenario_code": "energy_saving",
                            "scenario_name": "Energy Saving",
                            "description": "Used for energy saving analysis.",
                            "example": "Analyze site power usage and suggest optimization.",
                        }
                    ]
                },
                ensure_ascii=True,
            ),
        )
        self._write_resource_file("prompts/scenario_recognition/0.0.1/en-US/system.md", "Identify scenario.")
        self._write_resource_file("prompts/scenario_recognition/0.0.1/en-US/user.md", "Choose scenario.")

        llm_client = FakeSequencedLLMClient(
            ['{"matched": true, "scenario_code": "energy_saving", "error_message": null}']
        )

        from a2a_t.client.prompt_generation.prompt_generation_orchestrator import PromptGenerationOrchestrator

        orchestrator = PromptGenerationOrchestrator(
            config=PromptRuntimeConfig(language="zh-CN", prompt_resource_version="0.0.1"),
            scenario_loader=ScenarioLoader(root_dir=self.root),
            prompt_resource_loader=PromptResourceLoader(root_dir=self.root),
            template_loader=TemplateLoader(root_dir=self.root),
            slot_schema_loader=SlotSchemaLoader(root_dir=self.root),
            scenario_recognizer=ScenarioRecognizer(llm_client=llm_client),
            slot_extractor=SlotExtractor(llm_client=llm_client),
            slot_validator=SlotValidator(),
        )

        result = orchestrator.generate("Analyze Site A.")

        self.assertFalse(result.success)
        self.assertEqual(result.failure.code, "TEMPLATE_NOT_FOUND")
        self.assertEqual(result.failure.stage, "generation")


if __name__ == "__main__":
    unittest.main()

