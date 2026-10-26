"""Integration of the server compliance flow over a real local resource root (D31 access layer)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.common.prompt_resources import create
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.llm.models import LLMClientConfig, LLMResponse
from a2a_t.prompt.analysis import SlotExtractor
from a2a_t.prompt.analysis.models import ScenarioResolutionResult
from a2a_t.prompt.common.models import PromptReference
from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator
from a2a_t.server.a2at_server import A2ATServer
from a2a_t.server.prompt_compliance.models import PromptComplianceFailure, PromptComplianceResult
from a2a_t.server.prompt_compliance.prompt_compliance_orchestrator import PromptComplianceOrchestrator
from tests.support import TEST_ENV_PATH, ManagedTempDirTestCase


def build_llm_config() -> LLMClientConfig:
    return LLMClientConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-test",
        base_url=None,
        history_window=10,
        max_tokens=None,
        temperature=None,
        timeout_seconds=None,
        session_max_total=300,
        session_max_per_provider=100,
    )


class FakeSequencedLLMClient:
    def __init__(self, response_texts: list[str]) -> None:
        self._response_texts = list(response_texts)

    def structured(
        self, *, messages: list[dict[str, str]], json_schema: dict[str, object], **kwargs: object
    ) -> LLMResponse:
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

    def _build_service(
        self,
        *,
        scenario_code: str,
        response_texts: list[str],
    ) -> PromptComplianceOrchestrator:
        return PromptComplianceOrchestrator(
            scenario_resolver=FakeScenarioResolver(
                ScenarioResolutionResult(
                    success=True,
                    reference=PromptReference(scenario_code=scenario_code, language="en-US"),
                    scenario=None,
                )
            ),
            resource_access=create(PromptRuntimeConfig(source_type="local_file", local_root_dir=str(self.root))),
            extractor=SlotExtractor(llm_client=FakeSequencedLLMClient(response_texts)),
            validator=JsonSchemaSlotValidator(),
        )

    def test_handler_check_task_prompt_succeeds_with_real_shared_components(self) -> None:
        self._write_resource_file(
            "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md", "Site: {site}"
        )
        self._write_resource_file(
            "slots/Task-T/network-layer/ran-energy-saving/v1/en-US/slot.json",
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

        service = self._build_service(
            scenario_code="ran-energy-saving",
            response_texts=['{"slots": {"site": "Site A"}, "slot_errors": []}'],
        )
        with (
            patch("a2a_t.server.a2at_server._default_env_path", return_value=TEST_ENV_PATH),
            patch("a2a_t.server.a2at_server.LLMConfigLoader.load", return_value=build_llm_config()),
            patch("a2a_t.server.a2at_server.LLMClientFactory.create", return_value=object()),
            patch(
                "a2a_t.server.a2at_server.PromptComplianceOrchestratorBuilder",
                return_value=FakePromptComplianceBuilder(service),
            ),
        ):
            server = A2ATServer()

            result = server.check_task_prompt(processed_prompt_text="processed body")

        self.assertEqual(result, PromptComplianceResult(success=True))

    def test_handler_check_task_prompt_returns_business_constraint_message_for_invalid_slot_value(self) -> None:
        self._write_resource_file(
            "templates/Notification-T/network-layer/subscribe-incident/v1/en-US/template.md",
            "Levels: {subscription_condition_incident_level}",
        )
        self._write_resource_file(
            "slots/Notification-T/network-layer/subscribe-incident/v1/en-US/slot.json",
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "subscription_condition_incident_level": {
                            "type": "string",
                            "pattern": '^\\s*\\[(?:\\s*"(?:critical|major)"\\s*(?:,\\s*"(?:critical|major)"\\s*)*)\\]\\s*$',
                            "x-a2at-slot-type": "list",
                            "x-a2at-value-constraint": "Must be a JSON array string containing one or more of: critical, major.",
                            "examples": ['["critical"]'],
                        }
                    },
                    "required": [],
                },
                ensure_ascii=True,
            ),
        )

        service = self._build_service(
            scenario_code="subscribe-incident",
            response_texts=['{"slots": {"subscription_condition_incident_level": "warning"}, "slot_errors": []}'],
        )
        with (
            patch("a2a_t.server.a2at_server._default_env_path", return_value=TEST_ENV_PATH),
            patch("a2a_t.server.a2at_server.LLMConfigLoader.load", return_value=build_llm_config()),
            patch("a2a_t.server.a2at_server.LLMClientFactory.create", return_value=object()),
            patch(
                "a2a_t.server.a2at_server.PromptComplianceOrchestratorBuilder",
                return_value=FakePromptComplianceBuilder(service),
            ),
        ):
            server = A2ATServer()

            result = server.check_task_prompt(processed_prompt_text="processed body")

        self.assertEqual(
            result,
            PromptComplianceResult(
                success=False,
                failure=PromptComplianceFailure(
                    code=ErrorCatalog.SLOT_CONSTRAINT_VIOLATED.value,
                    message="Must be a JSON array string containing one or more of: critical, major.",
                    stage="slot_validation",
                ),
            ),
        )

    def test_handler_check_task_prompt_succeeds_when_optional_subscribe_incident_slots_are_null(self) -> None:
        self._write_resource_file(
            "templates/Notification-T/network-layer/subscribe-incident/v1/en-US/template.md",
            "Name: {subscription_condition_incident_name}\nLevels: {subscription_condition_incident_level}",
        )
        self._write_resource_file(
            "slots/Notification-T/network-layer/subscribe-incident/v1/en-US/slot.json",
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
                            "examples": ['["fiber break"]'],
                        },
                        "subscription_condition_incident_level": {
                            "type": "string",
                            "pattern": '^\\s*\\[(?:\\s*"(?:critical|major)"\\s*(?:,\\s*"(?:critical|major)"\\s*)*)?\\]\\s*$',
                            "x-a2at-slot-type": "list",
                            "x-a2at-value-constraint": "Must be a JSON array string containing one or more of: critical, major.",
                            "examples": ['["critical"]'],
                        },
                    },
                    "required": [],
                },
                ensure_ascii=True,
            ),
        )

        service = self._build_service(
            scenario_code="subscribe-incident",
            response_texts=[
                '{"slots": {"subscription_condition_incident_name": null, "subscription_condition_incident_level": null}, "slot_errors": []}'
            ],
        )
        with (
            patch("a2a_t.server.a2at_server._default_env_path", return_value=TEST_ENV_PATH),
            patch("a2a_t.server.a2at_server.LLMConfigLoader.load", return_value=build_llm_config()),
            patch("a2a_t.server.a2at_server.LLMClientFactory.create", return_value=object()),
            patch(
                "a2a_t.server.a2at_server.PromptComplianceOrchestratorBuilder",
                return_value=FakePromptComplianceBuilder(service),
            ),
        ):
            server = A2ATServer()

            result = server.check_task_prompt(processed_prompt_text="processed body")

        self.assertEqual(result, PromptComplianceResult(success=True))


if __name__ == "__main__":
    import unittest

    unittest.main()
