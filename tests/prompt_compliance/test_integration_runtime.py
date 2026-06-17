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


from a2a_t.common.prompt_resources import PromptResourceLoader, SlotSchemaLoader, TemplateLoader
from a2a_t.common.prompt_resources.models import ScenarioDefinition
from a2a_t.llm.models import LLMResponse
from a2a_t.prompt.analysis import SlotExtractor
from a2a_t.prompt.analysis.models import ScenarioResolutionResult
from a2a_t.prompt.common.models import PromptReference
from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator
from a2a_t.server.a2at_server import A2ATServer
from a2a_t.server.prompt_compliance.prompt_compliance_orchestrator import PromptComplianceOrchestrator
from tests.test_support import ManagedTempDirTestCase, TEST_ENV_PATH


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


class FakeScenarioResolver:
    def __init__(self, result: ScenarioResolutionResult) -> None:
        self._result = result

    def resolve(self, normalized_input: str) -> ScenarioResolutionResult:
        return self._result


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
        self._write_resource_file("templates/energy_saving/en-US/template.md", "Site: {site}")
        self._write_resource_file("prompts/slot_extraction/en-US/system.md", "Extract slots.")
        self._write_resource_file("prompts/slot_extraction/en-US/user.md", "Return slots.")
        self._write_resource_file(
            "slots/energy_saving/en-US/slot.json",
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "site": {
                            "type": "string",
                            "description": "Site name",
                            "examples": ["Site A"],
                            "minLength": 1,
                            "x-a2at-value-constraint": "Must be a concrete site name.",
                        }
                    },
                    "required": ["site"],
                },
                ensure_ascii=True,
            ),
        )

        service = PromptComplianceOrchestrator(
            scenario_resolver=FakeScenarioResolver(
                ScenarioResolutionResult(
                    success=True,
                    reference=PromptReference(scenario_code="energy_saving", language="en-US"),
                    scenario=ScenarioDefinition(
                        scenario_code="energy_saving",
                        scenario_name="Energy Saving",
                        description="Used for energy saving analysis.",
                        example="Analyze site power usage and suggest optimization.",
                    ),
                )
            ),
            template_loader=TemplateLoader(root_dir=self.root),
            slot_schema_loader=SlotSchemaLoader(root_dir=self.root),
            prompt_resource_loader=PromptResourceLoader(root_dir=self.root),
            extractor=SlotExtractor(
                llm_client=FakeSequencedLLMClient(
                    ['{"slots": {"site": "Site A"}, "slot_errors": []}']
                )
            ),
            validator=JsonSchemaSlotValidator(),
        )
        with (
            patch("a2a_t.server.a2at_server._default_env_path", return_value=TEST_ENV_PATH),
            patch("a2a_t.server.a2at_server.PromptComplianceOrchestratorBuilder", return_value=FakePromptComplianceBuilder(service)),
            patch("a2a_t.server.a2at_server.ServerNegotiationOrchestratorBuilder") as negotiation_builder_cls,
            patch("a2a_t.server.a2at_server.LLMClient", return_value=object()),
        ):
            negotiation_builder_cls.return_value.build.return_value = object()
            server = A2ATServer()

            result = server.check_task_prompt(processed_prompt_text="processed body")

        self.assertEqual(result, {"success": True})

    def test_handler_check_task_prompt_returns_business_constraint_message_for_invalid_slot_value(self) -> None:
        self._write_resource_file("templates/subscribe_incident/en-US/template.md", "Levels: {subscription_condition_incident_level}")
        self._write_resource_file("prompts/slot_extraction/en-US/system.md", "Extract slots.")
        self._write_resource_file("prompts/slot_extraction/en-US/user.md", "Return slots.")
        self._write_resource_file(
            "slots/subscribe_incident/en-US/slot.json",
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "subscription_condition_incident_level": {
                            "type": "string",
                            "pattern": "^\\s*\\[(?:\\s*\"(?:critical|major)\"\\s*(?:,\\s*\"(?:critical|major)\"\\s*)*)\\]\\s*$",
                            "x-a2at-slot-type": "list",
                            "x-a2at-value-constraint": "Must be a JSON array string containing one or more of: critical, major.",
                            "examples": ["[\"critical\"]"],
                        }
                    },
                    "required": [],
                },
                ensure_ascii=True,
            ),
        )

        service = PromptComplianceOrchestrator(
            scenario_resolver=FakeScenarioResolver(
                ScenarioResolutionResult(
                    success=True,
                    reference=PromptReference(scenario_code="subscribe_incident", language="en-US"),
                    scenario=ScenarioDefinition(
                        scenario_code="subscribe_incident",
                        scenario_name="Subscribe Incident",
                        description="Subscribe incidents by condition.",
                        example="Subscribe to critical incidents.",
                    ),
                )
            ),
            template_loader=TemplateLoader(root_dir=self.root),
            slot_schema_loader=SlotSchemaLoader(root_dir=self.root),
            prompt_resource_loader=PromptResourceLoader(root_dir=self.root),
            extractor=SlotExtractor(
                llm_client=FakeSequencedLLMClient(
                    ['{"slots": {"subscription_condition_incident_level": "warning"}, "slot_errors": []}']
                )
            ),
            validator=JsonSchemaSlotValidator(),
        )
        with (
            patch("a2a_t.server.a2at_server._default_env_path", return_value=TEST_ENV_PATH),
            patch("a2a_t.server.a2at_server.PromptComplianceOrchestratorBuilder", return_value=FakePromptComplianceBuilder(service)),
            patch("a2a_t.server.a2at_server.ServerNegotiationOrchestratorBuilder") as negotiation_builder_cls,
            patch("a2a_t.server.a2at_server.LLMClient", return_value=object()),
        ):
            negotiation_builder_cls.return_value.build.return_value = object()
            server = A2ATServer()

            result = server.check_task_prompt(processed_prompt_text="processed body")

        self.assertEqual(
            result,
            {
                "success": False,
                "failure": {
                    "code": "slot_validation_error",
                    "message": "Must be a JSON array string containing one or more of: critical, major.",
                    "stage": "slot_validation",
                },
            },
        )

    def test_handler_check_task_prompt_succeeds_when_optional_subscribe_incident_slots_are_null(self) -> None:
        self._write_resource_file("templates/subscribe_incident/en-US/template.md", "Name: {subscription_condition_incident_name}\nLevels: {subscription_condition_incident_level}")
        self._write_resource_file("prompts/slot_extraction/en-US/system.md", "Extract slots.")
        self._write_resource_file("prompts/slot_extraction/en-US/user.md", "Return slots.")
        self._write_resource_file(
            "slots/subscribe_incident/en-US/slot.json",
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "subscription_condition_incident_name": {
                            "type": "string",
                            "x-a2at-slot-type": "list",
                            "x-a2at-value-constraint": "Must be a valid incident name list.",
                            "examples": ["[\"fiber break\"]"],
                        },
                        "subscription_condition_incident_level": {
                            "type": "string",
                            "pattern": "^\\s*\\[(?:\\s*\"(?:critical|major)\"\\s*(?:,\\s*\"(?:critical|major)\"\\s*)*)?\\]\\s*$",
                            "x-a2at-slot-type": "list",
                            "x-a2at-value-constraint": "Must be a JSON array string containing one or more of: critical, major.",
                            "examples": ["[\"critical\"]"],
                        },
                    },
                    "required": [],
                },
                ensure_ascii=True,
            ),
        )

        service = PromptComplianceOrchestrator(
            scenario_resolver=FakeScenarioResolver(
                ScenarioResolutionResult(
                    success=True,
                    reference=PromptReference(scenario_code="subscribe_incident", language="en-US"),
                    scenario=ScenarioDefinition(
                        scenario_code="subscribe_incident",
                        scenario_name="Subscribe Incident",
                        description="Subscribe incidents by condition.",
                        example="Subscribe to critical incidents.",
                    ),
                )
            ),
            template_loader=TemplateLoader(root_dir=self.root),
            slot_schema_loader=SlotSchemaLoader(root_dir=self.root),
            prompt_resource_loader=PromptResourceLoader(root_dir=self.root),
            extractor=SlotExtractor(
                llm_client=FakeSequencedLLMClient(
                    [
                        '{"slots": {"subscription_condition_incident_name": null, "subscription_condition_incident_level": null}, "slot_errors": []}'
                    ]
                )
            ),
            validator=JsonSchemaSlotValidator(),
        )
        with (
            patch("a2a_t.server.a2at_server._default_env_path", return_value=TEST_ENV_PATH),
            patch("a2a_t.server.a2at_server.PromptComplianceOrchestratorBuilder", return_value=FakePromptComplianceBuilder(service)),
            patch("a2a_t.server.a2at_server.ServerNegotiationOrchestratorBuilder") as negotiation_builder_cls,
            patch("a2a_t.server.a2at_server.LLMClient", return_value=object()),
        ):
            negotiation_builder_cls.return_value.build.return_value = object()
            server = A2ATServer()

            result = server.check_task_prompt(processed_prompt_text="processed body")

        self.assertEqual(result, {"success": True})


if __name__ == "__main__":
    unittest.main()
