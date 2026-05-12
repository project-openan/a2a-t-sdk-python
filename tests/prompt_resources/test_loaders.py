from __future__ import annotations

import json
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.common.prompt_resources.errors import PromptResourceParseError
from a2a_t.prompt.common.models import PromptReference
from tests.test_support import ManagedTempDirTestCase


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

    def test_scenario_loader_reads_language_file(self) -> None:
        self._write_json(
            "scenarios/zh-CN/scenarios.json",
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
        scenarios = loader.load(language="zh-CN")

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].scenario_code, "energy_saving")
        self.assertEqual(scenarios[0].scenario_name, "Energy Saving")

    def test_template_loader_reads_template_markdown_text(self) -> None:
        self._write_text("templates/energy_saving/en-US/template.md", "Site: {site}\nTime Range: {time_range}\n")

        from a2a_t.common.prompt_resources.template_loader import TemplateLoader

        loader = TemplateLoader(root_dir=self.root)
        template_text = loader.load(
            reference=PromptReference(scenario_code="energy_saving", language="en-US")
        )

        self.assertEqual(template_text, "Site: {site}\nTime Range: {time_range}\n")

    def test_prompt_resource_loader_reads_system_and_user_prompt_files(self) -> None:
        self._write_text("prompts/slot_extraction/en-US/system.md", "system prompt")
        self._write_text("prompts/slot_extraction/en-US/user.md", "user prompt")

        from a2a_t.common.prompt_resources.prompt_resource_loader import PromptResourceLoader

        loader = PromptResourceLoader(root_dir=self.root)
        messages = loader.load(analysis_action="slot_extraction", language="en-US")

        self.assertEqual(messages.system_prompt, "system prompt")
        self.assertEqual(messages.user_prompt, "user prompt")

    def test_slot_schema_loader_rejects_legacy_slot_schema(self) -> None:
        self._write_json(
            "slots/energy_saving/en-US/slot.json",
            {
                "scenario_code": "energy_saving",
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
        with self.assertRaises(PromptResourceParseError):
            loader.load(
                reference=PromptReference(scenario_code="energy_saving", language="en-US")
            )

    def test_slot_schema_loader_reads_standard_json_schema_for_generation_flow(self) -> None:
        self._write_json(
            "slots/energy_saving/en-US/slot.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Site name",
                        "examples": ["Shenzhen site A"],
                        "minLength": 1,
                        "x-a2at-value-constraint": "Must be a concrete site name.",
                    },
                    "incident_level": {
                        "type": "string",
                        "description": "Incident level",
                        "examples": ["critical"],
                        "enum": ["critical", "major"],
                        "x-a2at-value-constraint": "Must be one of the supported levels.",
                    },
                },
                "required": ["site"],
            },
        )

        from a2a_t.common.prompt_resources.slot_schema_loader import SlotSchemaLoader

        loader = SlotSchemaLoader(root_dir=self.root)
        slot_schema = loader.load(
            reference=PromptReference(scenario_code="energy_saving", language="en-US")
        )

        self.assertEqual(slot_schema.scenario_code, "energy_saving")
        self.assertEqual([slot.name for slot in slot_schema.slots], ["site", "incident_level"])
        self.assertTrue(slot_schema.slots[0].required)
        self.assertEqual(slot_schema.slots[0].description, "Site name")
        self.assertEqual(slot_schema.slots[0].example, "Shenzhen site A")
        self.assertEqual(slot_schema.slots[0].value_constraint, "Must be a concrete site name.")
        self.assertEqual(slot_schema.slots[0].type, "string")
        self.assertIsNone(slot_schema.slots[0].allowed_values)
        self.assertFalse(slot_schema.slots[1].required)
        self.assertEqual(slot_schema.slots[1].allowed_values, ["critical", "major"])

    def test_generation_loaders_fail_when_requested_language_resources_are_missing(self) -> None:
        self._write_text("templates/energy_saving/en-US/template.md", "Site: {site}\n")
        self._write_text("prompts/slot_extraction/en-US/system.md", "Extract slots.")
        self._write_text("prompts/slot_extraction/en-US/user.md", "Return slots.")
        self._write_json(
            "slots/energy_saving/en-US/slot.json",
            {
                "scenario_code": "energy_saving",
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

        from a2a_t.common.prompt_resources.errors import PromptResourceNotFoundError
        from a2a_t.common.prompt_resources.prompt_resource_loader import PromptResourceLoader
        from a2a_t.common.prompt_resources.slot_schema_loader import SlotSchemaLoader
        from a2a_t.common.prompt_resources.template_loader import TemplateLoader

        with self.assertRaises(PromptResourceNotFoundError):
            TemplateLoader(root_dir=self.root).load(
                reference=PromptReference(scenario_code="energy_saving", language="zh-CN")
            )
        with self.assertRaises(PromptResourceNotFoundError):
            SlotSchemaLoader(root_dir=self.root).load(
                reference=PromptReference(scenario_code="energy_saving", language="zh-CN")
            )
        with self.assertRaises(PromptResourceNotFoundError):
            PromptResourceLoader(root_dir=self.root).load(
                analysis_action="slot_extraction",
                language="zh-CN",
            )

    def test_business_loaders_fall_back_to_packaged_defaults_when_custom_root_lacks_files(self) -> None:
        from a2a_t.common.prompt_resources.scenario_loader import ScenarioLoader
        from a2a_t.common.prompt_resources.slot_schema_loader import SlotSchemaLoader
        from a2a_t.common.prompt_resources.template_loader import TemplateLoader

        scenarios = ScenarioLoader(root_dir=self.root).load(language="zh-CN")
        template_text = TemplateLoader(root_dir=self.root).load(
            reference=PromptReference(scenario_code="subscribe_incident", language="zh-CN")
        )
        slot_schema = SlotSchemaLoader(root_dir=self.root).load(
            reference=PromptReference(scenario_code="subscribe_incident", language="zh-CN")
        )

        self.assertGreater(len(scenarios), 0)
        self.assertIn("subscribe_incident", {scenario.scenario_code for scenario in scenarios})
        self.assertIn("订阅", template_text)
        self.assertEqual(slot_schema.scenario_code, "subscribe_incident")

    def test_business_loaders_do_not_fall_back_on_custom_root_parse_errors(self) -> None:
        from a2a_t.common.prompt_resources.errors import PromptResourceParseError
        from a2a_t.common.prompt_resources.scenario_loader import ScenarioLoader

        self._write_text("scenarios/zh-CN/scenarios.json", "{not json")

        with self.assertRaises(PromptResourceParseError):
            ScenarioLoader(root_dir=self.root).load(language="zh-CN")

    def test_business_loaders_prefer_custom_root_over_packaged_defaults(self) -> None:
        from a2a_t.common.prompt_resources.scenario_loader import ScenarioLoader
        from a2a_t.common.prompt_resources.slot_schema_loader import SlotSchemaLoader
        from a2a_t.common.prompt_resources.template_loader import TemplateLoader

        self._write_json(
            "scenarios/zh-CN/scenarios.json",
            {
                "scenarios": [
                    {
                        "scenario_code": "subscribe_incident",
                        "scenario_name": "Custom Incident Subscription",
                        "description": "Custom scenario description.",
                        "example": "Custom scenario example.",
                    }
                ]
            },
        )
        self._write_text("templates/subscribe_incident/zh-CN/template.md", "CUSTOM TEMPLATE")
        self._write_json(
            "slots/subscribe_incident/zh-CN/slot.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "custom_field": {
                        "type": "string",
                        "description": "Custom field",
                        "examples": ["custom value"],
                    }
                },
                "required": ["custom_field"],
            },
        )

        scenarios = ScenarioLoader(root_dir=self.root).load(language="zh-CN")
        template_text = TemplateLoader(root_dir=self.root).load(
            reference=PromptReference(scenario_code="subscribe_incident", language="zh-CN")
        )
        slot_schema = SlotSchemaLoader(root_dir=self.root).load(
            reference=PromptReference(scenario_code="subscribe_incident", language="zh-CN")
        )

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].scenario_name, "Custom Incident Subscription")
        self.assertEqual(template_text, "CUSTOM TEMPLATE")
        self.assertEqual([slot.name for slot in slot_schema.slots], ["custom_field"])

    def test_public_package_no_longer_exports_source_catalog_provider_cache_or_registry_layers(self) -> None:
        import a2a_t.common.prompt_resources as prompt_resources

        self.assertFalse(hasattr(prompt_resources, "LocalPromptResourceSource"))
        self.assertFalse(hasattr(prompt_resources, "PromptResourceSource"))
        self.assertFalse(hasattr(prompt_resources, "LocalPromptResourceCatalog"))
        self.assertFalse(hasattr(prompt_resources, "PromptResourceCatalog"))
        self.assertFalse(hasattr(prompt_resources, "LocalPromptResourceProvider"))
        self.assertFalse(hasattr(prompt_resources, "PromptResourceProvider"))
        self.assertFalse(hasattr(prompt_resources, "PromptStore"))
        self.assertFalse(hasattr(prompt_resources, "ConflictResolutionPolicy"))
        self.assertFalse(hasattr(prompt_resources, "ExpirationPolicy"))
        self.assertFalse(hasattr(prompt_resources, "PromptResourceRegistry"))


if __name__ == "__main__":
    unittest.main()

