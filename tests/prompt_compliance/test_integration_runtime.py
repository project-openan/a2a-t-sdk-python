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
from a2a_t.prompt.analysis import SlotExtractor
from a2a_t.prompt.resources import PromptResourceLoader, SlotSchemaLoader, TemplateLoader
from a2a_t.prompt.validation import GuardrailResult, SlotValidator
from a2a_t.server.prompt_compliance.extractor import PromptSlotExtractor
from a2a_t.server.prompt_compliance.parser import ProcessedPromptParser
from a2a_t.server.prompt_compliance.service import PromptComplianceService
from a2a_t.server.prompt_handler import PromptHandler
from tests.test_support import ManagedTempDirTestCase


class FakeSequencedLLMClient:
    def __init__(self, response_texts: list[str]) -> None:
        self._response_texts = list(response_texts)

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, object], **kwargs: object) -> LLMResponse:
        return LLMResponse(
            content=self._response_texts.pop(0),
            model="fake-model",
            usage={},
            metadata={},
        )


class FakeGuardrail:
    def check(self, prompt_text: str, context: dict[str, object] | None = None) -> GuardrailResult:
        return GuardrailResult(passed=True, error_code=None, error_message=None)


class PromptComplianceIntegrationRuntimeTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_temp_dir("prompt_resources")

    def _write_resource_file(self, relative_path: str, content: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_handler_process_succeeds_with_real_shared_components(self) -> None:
        self._write_resource_file("templates/energy_saving/0.0.1/en-US/template.md", "Site: {site}")
        self._write_resource_file("prompts/slot_extraction/0.0.1/en-US/system.md", "Extract slots.")
        self._write_resource_file("prompts/slot_extraction/0.0.1/en-US/user.md", "Return slots.")
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
                        }
                    ],
                },
                ensure_ascii=True,
            ),
        )

        service = PromptComplianceService(
            guardrail=FakeGuardrail(),
            parser=ProcessedPromptParser(),
            template_loader=TemplateLoader(root_dir=self.root),
            slot_schema_loader=SlotSchemaLoader(root_dir=self.root),
            prompt_resource_loader=PromptResourceLoader(root_dir=self.root),
            extractor=PromptSlotExtractor(
                slot_extractor=SlotExtractor(
                    llm_client=FakeSequencedLLMClient(
                        ['{"slots": {"site": "Site A"}, "slot_errors": []}']
                    )
                )
            ),
            validator=SlotValidator(),
        )
        handler = PromptHandler(validator=service)

        result = handler.process(
            "task-1",
            {
                "processed_prompt_text": (
                    "---\n"
                    "scenario_code: energy_saving\n"
                    "language: en-US\n"
                    "version: 0.0.1\n"
                    "description: Used for energy saving analysis.\n"
                    "---\n"
                    "processed body"
                )
            },
        )

        self.assertEqual(
            result,
            {
                "passed": True,
                "stage": "passed",
                "extracted_slots": {"site": "Site A"},
                "error_code": None,
                "error_message": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
