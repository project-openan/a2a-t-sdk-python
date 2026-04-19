from __future__ import annotations

import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.llm.base import LLMResponse
from a2a_t.prompt.analysis import SlotExtractor
from a2a_t.prompt.common.task_prompt_format import TaskPromptMetadata, format_task_prompt
from a2a_t.common.prompt_resources import PromptResourceLoader, SlotSchemaLoader, TemplateLoader
from a2a_t.prompt.validation import GuardrailResult, SlotValidator
from a2a_t.server.a2at_server import A2ATServer
from a2a_t.server.prompt_compliance.prompt_compliance_orchestrator import PromptComplianceOrchestrator
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


class FakePromptComplianceBuilder:
    def __init__(self, service: PromptComplianceOrchestrator) -> None:
        self._service = service

    def build(self, **kwargs: object) -> PromptComplianceOrchestrator:
        return self._service


class PromptComplianceIntegrationRuntimeTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_temp_dir("prompt_resources")

    def _write_resource_file(self, relative_path: str, content: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_handler_check_task_prompt_succeeds_with_real_shared_components(self) -> None:
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

        service = PromptComplianceOrchestrator(
            guardrail=FakeGuardrail(),
            template_loader=TemplateLoader(root_dir=self.root),
            slot_schema_loader=SlotSchemaLoader(root_dir=self.root),
            prompt_resource_loader=PromptResourceLoader(root_dir=self.root),
            extractor=SlotExtractor(
                llm_client=FakeSequencedLLMClient(
                    ['{"slots": {"site": "Site A"}, "slot_errors": []}']
                )
            ),
            validator=SlotValidator(),
        )
        with (
            patch("a2a_t.server.a2at_server.PromptComplianceOrchestratorBuilder", return_value=FakePromptComplianceBuilder(service)),
            patch("a2a_t.server.a2at_server.ServerNegotiationOrchestratorBuilder") as negotiation_builder_cls,
            patch("a2a_t.server.a2at_server.LLMClient", return_value=object()),
        ):
            negotiation_builder_cls.return_value.build.return_value = object()
            server = A2ATServer()

            result = server.check_task_prompt(
                processed_prompt_text=format_task_prompt(
                    body="processed body",
                    metadata=TaskPromptMetadata(
                        scenario_code="energy_saving",
                        language="en-US",
                        version="0.0.1",
                        description="Used for energy saving analysis.",
                    ),
                ),
            )

        self.assertEqual(
            result,
            {
                "passed": True,
                "need_negotiation": False,
                "negotiation_input": None,
                "stage": "passed",
                "extracted_slots": {"site": "Site A"},
                "error_code": None,
                "error_message": None,
            },
        )


if __name__ == "__main__":
    unittest.main()

