from __future__ import annotations

import json
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.common.models import PromptReference
from tests.test_support import ManagedTempDirTestCase


class FakePromptResourceSource:
    source_type = "fake"

    def __init__(self) -> None:
        self.text_values: dict[str, str] = {}
        self.json_values: dict[str, dict[str, object]] = {}
        self.text_reads: list[str] = []
        self.json_reads: list[str] = []

    def read_text(self, *, relative_path: str) -> str:
        self.text_reads.append(relative_path)
        return self.text_values[relative_path]

    def read_json(self, *, relative_path: str) -> dict[str, object]:
        self.json_reads.append(relative_path)
        return self.json_values[relative_path]

    def exists(self, *, relative_path: str) -> bool:
        return relative_path in self.text_values or relative_path in self.json_values


class PromptResourceLoaderTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_temp_dir("prompt_resources")

    def _write_json(self, relative_path: str, payload: dict[str, object]) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")

    def _write_text(self, relative_path: str, content: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_scenario_loader_reads_versioned_language_file(self) -> None:
        self._write_json(
            "scenarios/0.0.1/zh-CN/scenarios.json",
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
        )

        from a2a_t.common.prompt_resources.scenario_loader import ScenarioLoader

        loader = ScenarioLoader(root_dir=self.root)
        scenarios = loader.load(version="0.0.1", language="zh-CN")

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].scenario_code, "energy_saving")
        self.assertEqual(scenarios[0].scenario_name, "Energy Saving")

    def test_local_prompt_resource_source_reads_text_json_and_exists(self) -> None:
        self._write_text("templates/energy_saving/0.0.1/en-US/template.md", "Site: {site}\n")
        self._write_json("scenarios/0.0.1/en-US/scenarios.json", {"scenarios": []})

        from a2a_t.common.prompt_resources.source import LocalPromptResourceSource

        source = LocalPromptResourceSource(root_dir=self.root)

        self.assertTrue(source.exists(relative_path="templates/energy_saving/0.0.1/en-US/template.md"))
        self.assertEqual(
            source.read_text(relative_path="templates/energy_saving/0.0.1/en-US/template.md"),
            "Site: {site}\n",
        )
        self.assertEqual(
            source.read_json(relative_path="scenarios/0.0.1/en-US/scenarios.json"),
            {"scenarios": []},
        )

    def test_scenario_loader_reads_via_prompt_resource_source(self) -> None:
        source = FakePromptResourceSource()
        source.json_values["scenarios/0.0.1/zh-CN/scenarios.json"] = {
            "scenarios": [
                {
                    "scenario_code": "energy_saving",
                    "scenario_name": "Energy Saving",
                    "description": "Used for energy saving analysis.",
                    "example": "Analyze site power usage and suggest optimization.",
                }
            ]
        }

        from a2a_t.common.prompt_resources.scenario_loader import ScenarioLoader

        loader = ScenarioLoader(source=source)
        scenarios = loader.load(version="0.0.1", language="zh-CN")

        self.assertEqual(source.json_reads, ["scenarios/0.0.1/zh-CN/scenarios.json"])
        self.assertEqual(scenarios[0].scenario_code, "energy_saving")

    def test_template_loader_reads_template_markdown_text(self) -> None:
        self._write_text("templates/energy_saving/0.0.1/en-US/template.md", "Site: {site}\nTime Range: {time_range}\n")

        from a2a_t.common.prompt_resources.template_loader import TemplateLoader

        loader = TemplateLoader(root_dir=self.root)
        template_text = loader.load(
            reference=PromptReference(scenario_code="energy_saving", version="0.0.1", language="en-US")
        )

        self.assertEqual(template_text, "Site: {site}\nTime Range: {time_range}\n")

    def test_template_loader_reads_via_prompt_resource_source(self) -> None:
        source = FakePromptResourceSource()
        source.text_values["templates/energy_saving/0.0.1/en-US/template.md"] = "Site: {site}\n"

        from a2a_t.common.prompt_resources.template_loader import TemplateLoader

        loader = TemplateLoader(source=source)
        template_text = loader.load(
            reference=PromptReference(scenario_code="energy_saving", version="0.0.1", language="en-US")
        )

        self.assertEqual(source.text_reads, ["templates/energy_saving/0.0.1/en-US/template.md"])
        self.assertEqual(template_text, "Site: {site}\n")

    def test_prompt_resource_loader_reads_system_and_user_prompt_files(self) -> None:
        self._write_text("prompts/slot_extraction/0.0.1/en-US/system.md", "system prompt")
        self._write_text("prompts/slot_extraction/0.0.1/en-US/user.md", "user prompt")

        from a2a_t.common.prompt_resources.prompt_resource_loader import PromptResourceLoader

        loader = PromptResourceLoader(root_dir=self.root)
        messages = loader.load(analysis_action="slot_extraction", version="0.0.1", language="en-US")

        self.assertEqual(messages.system_prompt, "system prompt")
        self.assertEqual(messages.user_prompt, "user prompt")

    def test_prompt_resource_loader_reads_via_prompt_resource_source(self) -> None:
        source = FakePromptResourceSource()
        source.text_values["prompts/slot_extraction/0.0.1/en-US/system.md"] = "system prompt"
        source.text_values["prompts/slot_extraction/0.0.1/en-US/user.md"] = "user prompt"

        from a2a_t.common.prompt_resources.prompt_resource_loader import PromptResourceLoader

        loader = PromptResourceLoader(source=source)
        messages = loader.load(analysis_action="slot_extraction", version="0.0.1", language="en-US")

        self.assertEqual(
            source.text_reads,
            [
                "prompts/slot_extraction/0.0.1/en-US/system.md",
                "prompts/slot_extraction/0.0.1/en-US/user.md",
            ],
        )
        self.assertEqual(messages.system_prompt, "system prompt")
        self.assertEqual(messages.user_prompt, "user prompt")

    def test_slot_schema_loader_reads_unified_slot_schema(self) -> None:
        self._write_json(
            "slots/energy_saving/0.0.1/en-US/slot.json",
            {
                "scenario_code": "energy_saving",
                "version": "0.0.1",
                "slots": [
                    {
                        "name": "site",
                        "required": True,
                        "description": "Site name",
                        "example": "Shenzhen site A",
                        "value_constraint": "Must be a concrete site name.",
                        "type": "string",
                        "allowed_values": None,
                        "range": None,
                        "pattern": None,
                    }
                ],
            },
        )

        from a2a_t.common.prompt_resources.slot_schema_loader import SlotSchemaLoader

        loader = SlotSchemaLoader(root_dir=self.root)
        slot_schema = loader.load(
            reference=PromptReference(scenario_code="energy_saving", version="0.0.1", language="en-US")
        )

        self.assertEqual(slot_schema.scenario_code, "energy_saving")
        self.assertEqual(slot_schema.version, "0.0.1")
        self.assertEqual(len(slot_schema.slots), 1)
        self.assertEqual(slot_schema.slots[0].name, "site")
        self.assertEqual(slot_schema.slots[0].type, "string")

    def test_slot_schema_loader_reads_via_prompt_resource_source(self) -> None:
        source = FakePromptResourceSource()
        source.json_values["slots/energy_saving/0.0.1/en-US/slot.json"] = {
            "scenario_code": "energy_saving",
            "version": "0.0.1",
            "slots": [
                {
                    "name": "site",
                    "required": True,
                    "description": "Site name",
                    "example": "Shenzhen site A",
                    "value_constraint": "Must be a concrete site name.",
                    "type": "string",
                    "allowed_values": None,
                    "range": None,
                    "pattern": None,
                }
            ],
        }

        from a2a_t.common.prompt_resources.slot_schema_loader import SlotSchemaLoader

        loader = SlotSchemaLoader(source=source)
        slot_schema = loader.load(
            reference=PromptReference(scenario_code="energy_saving", version="0.0.1", language="en-US")
        )

        self.assertEqual(source.json_reads, ["slots/energy_saving/0.0.1/en-US/slot.json"])
        self.assertEqual(slot_schema.scenario_code, "energy_saving")

    def test_resource_registry_falls_back_to_en_us_for_generation_resources(self) -> None:
        self._write_text("templates/energy_saving/0.0.1/en-US/template.md", "Site: {site}\n")
        self._write_text("prompts/slot_extraction/0.0.1/en-US/system.md", "Extract slots.")
        self._write_text("prompts/slot_extraction/0.0.1/en-US/user.md", "Return slots.")
        self._write_json(
            "slots/energy_saving/0.0.1/en-US/slot.json",
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
        )

        from a2a_t.common.prompt_resources.registry import PromptResourceRegistry

        registry = PromptResourceRegistry(root_dir=self.root)
        resolved_reference, template_text, slot_schema, messages = registry.load_generation_resources(
            reference=PromptReference(scenario_code="energy_saving", version="0.0.1", language="zh-CN")
        )

        self.assertEqual(resolved_reference.language, "en-US")
        self.assertEqual(template_text, "Site: {site}\n")
        self.assertEqual(slot_schema.scenario_code, "energy_saving")
        self.assertEqual(messages.system_prompt, "Extract slots.")


if __name__ == "__main__":
    unittest.main()

