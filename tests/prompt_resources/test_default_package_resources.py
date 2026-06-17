from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class DefaultPromptResourcePackageTest(unittest.TestCase):
    def test_default_package_resources_include_expected_default_bundle(self) -> None:
        from a2a_t.common.prompt_resources.errors import PromptResourceNotFoundError
        from a2a_t.common.prompt_resources.prompt_resource_loader import PromptResourceLoader
        from a2a_t.common.prompt_resources.scenario_loader import ScenarioLoader
        from a2a_t.common.prompt_resources.slot_schema_loader import SlotSchemaLoader
        from a2a_t.common.prompt_resources.template_loader import TemplateLoader
        from a2a_t.prompt.common.models import PromptReference

        scenarios = ScenarioLoader().load(language="zh-CN")
        en_reference = PromptReference(scenario_code="subscribe_incident", language="en-US")
        zh_reference = PromptReference(scenario_code="subscribe_incident", language="zh-CN")

        with self.assertRaises(PromptResourceNotFoundError):
            TemplateLoader().load(reference=en_reference)
        with self.assertRaises(PromptResourceNotFoundError):
            SlotSchemaLoader().load(reference=en_reference)
        with self.assertRaises(PromptResourceNotFoundError):
            SlotSchemaLoader().load_json_schema(reference=en_reference)

        template_text = TemplateLoader().load(reference=zh_reference)
        scenario_prompts = PromptResourceLoader().load(
            analysis_action="scenario_recognition",
            language="en-US",
        )
        slot_prompts = PromptResourceLoader().load(
            analysis_action="slot_extraction",
            language="en-US",
        )

        self.assertTrue(any(item.scenario_code == "subscribe_incident" for item in scenarios))
        self.assertIn("{{", template_text)
        self.assertTrue(scenario_prompts.system_prompt.strip())
        self.assertTrue(slot_prompts.user_prompt.strip())

    def test_default_package_resources_include_zh_cn_negotiation_prompts(self) -> None:
        from a2a_t.common.prompt_resources.prompt_resource_loader import PromptResourceLoader

        loader = PromptResourceLoader()

        for analysis_action in (
            "clarification_negotiation",
            "feasibility_negotiation",
            "fulfillment_negotiation",
            "information_negotiation",
        ):
            messages = loader.load(
                analysis_action=analysis_action,
                language="zh-CN",
            )
            self.assertTrue(messages.system_prompt.strip(), analysis_action)
            self.assertTrue(messages.user_prompt.strip(), analysis_action)


if __name__ == "__main__":
    unittest.main()
