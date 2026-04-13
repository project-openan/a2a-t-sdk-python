from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class DefaultPromptResourcePackageTest(unittest.TestCase):
    def test_default_package_resources_include_minimal_en_us_bundle(self) -> None:
        from a2a_t.prompt.resources.prompt_resource_loader import PromptResourceLoader
        from a2a_t.prompt.resources.scenario_loader import ScenarioLoader
        from a2a_t.prompt.resources.slot_schema_loader import SlotSchemaLoader
        from a2a_t.prompt.resources.template_loader import TemplateLoader

        scenarios = ScenarioLoader().load(version="0.0.1", language="en-US")
        template_text = TemplateLoader().load(scenario_code="energy_saving", version="0.0.1", language="en-US")
        slot_schema = SlotSchemaLoader().load(scenario_code="energy_saving", version="0.0.1", language="en-US")
        scenario_prompts = PromptResourceLoader().load(
            analysis_action="scenario_recognition",
            version="0.0.1",
            language="en-US",
        )
        slot_prompts = PromptResourceLoader().load(
            analysis_action="slot_extraction",
            version="0.0.1",
            language="en-US",
        )

        self.assertTrue(any(item.scenario_code == "energy_saving" for item in scenarios))
        self.assertIn("{site}", template_text)
        self.assertEqual(slot_schema.scenario_code, "energy_saving")
        self.assertTrue(scenario_prompts.system_prompt.strip())
        self.assertTrue(slot_prompts.user_prompt.strip())


if __name__ == "__main__":
    unittest.main()
