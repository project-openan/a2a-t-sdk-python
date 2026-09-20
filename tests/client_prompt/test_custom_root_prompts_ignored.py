"""CustomRootPromptsIgnored integration test in the prompt-pipeline context (D9/D31).

Port of the Java ``CustomRootPromptsIgnoredTest`` scenario onto the prompt generation pipeline: a
local root carrying a modified ``prompts/`` copy must never change the instruction prompts the SDK
sends to the LLM — the LLM prompts are a packaged SDK contract — and assembling the pipeline with
such a root emits the ignored-directories warning. Both prompt-consuming steps of the pipeline are
covered: scenario recognition and slot extraction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.client.prompt_generation.prompt_generation_orchestrator_builder import (
    PromptGenerationOrchestratorBuilder,
)
from a2a_t.common.prompt_resources import PackagedPromptResourceAccess
from a2a_t.config.models import A2ATConfig, PromptRuntimeConfig
from a2a_t.llm.models import LLMResponse
from tests.support import ManagedTempDirTestCase

ACCESS_LOGGER = "a2a_t.common.prompt_resources.resource_access"

_SCENARIO_ENTRY = {
    "scenario_code": "ran-energy-saving",
    "scenario_name": "Energy Saving",
    "description": "Used for energy saving analysis.",
    "example": "Analyze site power usage and suggest optimization.",
}

_SLOT_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"site": {"type": "string", "examples": ["Site A"]}},
    "required": ["site"],
}


class RecordingLLMClient:
    """LLM stand-in recording every message list it is asked to run."""

    def __init__(self, response_texts: list[str]) -> None:
        self._response_texts = list(response_texts)
        self.recorded_messages: list[list[dict[str, str]]] = []

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, object],
        **kwargs: object,
    ) -> LLMResponse:
        self.recorded_messages.append(messages)
        return LLMResponse(
            content=self._response_texts.pop(0),
            model="recording-test-model",
            usage={},
            metadata={},
        )

    def system_content_of_call(self, index: int) -> str:
        messages = self.recorded_messages[index]
        return next(message["content"] for message in messages if message["role"] == "system")

    def user_content_of_call(self, index: int) -> str:
        messages = self.recorded_messages[index]
        return next(message["content"] for message in messages if message["role"] == "user")


class CustomRootPromptsIgnoredTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_temp_dir("custom_root_prompts_ignored")

    def _write_resource_file(self, relative_path: str, content: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_local_root_with_modified_prompts_copy(self) -> None:
        """Materialize a working local root whose ``prompts/`` copy is modified."""
        self._write_resource_file(
            "scenarios/en-US/scenarios.json",
            json.dumps({"scenarios": [_SCENARIO_ENTRY]}, ensure_ascii=True),
        )
        self._write_resource_file(
            "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md",
            "Site: {site}",
        )
        self._write_resource_file(
            "slots/Task-T/network-layer/ran-energy-saving/v1/en-US/slot.json",
            json.dumps(_SLOT_JSON_SCHEMA, ensure_ascii=True),
        )
        for action in ("scenario_recognition", "slot_extraction"):
            self._write_resource_file(f"prompts/{action}/en-US/system.md", f"CUSTOM {action} SYSTEM PROMPT")
            self._write_resource_file(f"prompts/{action}/en-US/user.md", f"CUSTOM {action} USER PROMPT")

    def _build_config(self) -> A2ATConfig:
        return A2ATConfig(
            prompt=PromptRuntimeConfig(
                language="en-US",
                source_type="local_file",
                local_root_dir=str(self.root),
            ),
        )

    def test_pipeline_keeps_using_the_packaged_prompts_and_warns_about_the_local_copy(self) -> None:
        self._write_local_root_with_modified_prompts_copy()
        llm = RecordingLLMClient(
            [
                '{"matched": true, "scenario_code": "ran-energy-saving", "error_message": null}',
                '{"slots": {"site": "Site A"}, "slot_errors": []}',
            ]
        )
        packaged = PackagedPromptResourceAccess()

        with self.assertLogs(ACCESS_LOGGER, level="WARNING") as logs:
            orchestrator = PromptGenerationOrchestratorBuilder().build(
                config=self._build_config(),
                llm_client=llm,
            )

        self.assertTrue(
            any("prompt_resource_local_directories_ignored" in message for message in logs.output),
            logs.output,
        )

        result = orchestrator.generate("Analyze Site A energy usage.")

        self.assertTrue(result.success)
        self.assertEqual(result.prompt_text, "Site: Site A")
        # Both prompt-consuming steps sent the packaged prompts, never the modified local copy.
        self.assertEqual(
            llm.system_content_of_call(0),
            packaged.load_prompt("scenario_recognition", "en-US", "system.md"),
        )
        self.assertEqual(
            llm.system_content_of_call(1),
            packaged.load_prompt("slot_extraction", "en-US", "system.md"),
        )
        self.assertNotEqual(llm.system_content_of_call(0), "CUSTOM scenario_recognition SYSTEM PROMPT")
        self.assertNotEqual(llm.system_content_of_call(1), "CUSTOM slot_extraction SYSTEM PROMPT")
        self.assertIn("Analyze Site A energy usage.", llm.user_content_of_call(0))


if __name__ == "__main__":
    import unittest

    unittest.main()
