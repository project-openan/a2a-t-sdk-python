from __future__ import annotations

import json
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from tests.test_support import ManagedTempDirTestCase


class PromptResourceLoaderTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_temp_dir("prompt_resources")

    def test_scenario_loader_reads_versioned_language_file(self) -> None:
        scenario_path = self.root / "scenarios" / "0.0.1" / "zh-CN" / "scenarios.json"
        scenario_path.parent.mkdir(parents=True, exist_ok=True)
        scenario_path.write_text(
            json.dumps(
                {
                    "scenarios": [
                        {
                            "scenario_code": "energy_saving",
                            "scenario_name": "节能",
                            "description": "用于识别节能分析请求。",
                            "example": "分析站点能耗并给出节能建议。",
                        }
                    ]
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )

        from a2a_t.prompt.resources.scenario_loader import ScenarioLoader

        loader = ScenarioLoader(root_dir=self.root)
        scenarios = loader.load(version="0.0.1", language="zh-CN")

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].scenario_code, "energy_saving")
        self.assertEqual(scenarios[0].scenario_name, "节能")

    def test_template_loader_reads_template_markdown_text(self) -> None:
        template_path = self.root / "templates" / "energy_saving" / "0.0.1" / "en-US" / "template.md"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text("Site: {site}\nTime Range: {time_range}\n", encoding="utf-8")

        from a2a_t.prompt.resources.template_loader import TemplateLoader

        loader = TemplateLoader(root_dir=self.root)
        template_text = loader.load(scenario_code="energy_saving", version="0.0.1", language="en-US")

        self.assertEqual(template_text, "Site: {site}\nTime Range: {time_range}\n")

    def test_prompt_resource_loader_reads_system_and_user_prompt_files(self) -> None:
        prompt_dir = self.root / "prompts" / "slot_extraction" / "0.0.1" / "en-US"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        (prompt_dir / "system.md").write_text("system prompt", encoding="utf-8")
        (prompt_dir / "user.md").write_text("user prompt", encoding="utf-8")

        from a2a_t.prompt.resources.prompt_resource_loader import PromptResourceLoader

        loader = PromptResourceLoader(root_dir=self.root)
        messages = loader.load(analysis_action="slot_extraction", version="0.0.1", language="en-US")

        self.assertEqual(messages.system_prompt, "system prompt")
        self.assertEqual(messages.user_prompt, "user prompt")

    def test_slot_schema_loader_reads_unified_slot_schema(self) -> None:
        slot_path = self.root / "slots" / "energy_saving" / "0.0.1" / "en-US" / "slot.json"
        slot_path.parent.mkdir(parents=True, exist_ok=True)
        slot_path.write_text(
            json.dumps(
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
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )

        from a2a_t.prompt.resources.slot_schema_loader import SlotSchemaLoader

        loader = SlotSchemaLoader(root_dir=self.root)
        slot_schema = loader.load(scenario_code="energy_saving", version="0.0.1", language="en-US")

        self.assertEqual(slot_schema.scenario_code, "energy_saving")
        self.assertEqual(slot_schema.version, "0.0.1")
        self.assertEqual(len(slot_schema.slots), 1)
        self.assertEqual(slot_schema.slots[0].name, "site")
        self.assertEqual(slot_schema.slots[0].type, "string")


if __name__ == "__main__":
    unittest.main()
