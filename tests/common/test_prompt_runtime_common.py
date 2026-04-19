from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.config.models import A2ATConfig, GuardrailProviderConfig, PromptComplianceConfig, PromptRuntimeConfig


class CommonPromptRuntimeComponentsBuilderTest(unittest.TestCase):
    def test_common_builder_creates_shared_runtime_components_from_config(self) -> None:
        from a2a_t.common.prompt_resources import LocalPromptResourceSource
        from a2a_t.common.prompt_runtime import PromptRuntimeComponentsBuilder
        from a2a_t.prompt.validation import SlotValidator

        config = A2ATConfig(
            prompt=PromptRuntimeConfig(
                language="zh-CN",
                prompt_resource_version="0.0.2",
                source_type="local_file",
                local_root_dir="./runtime-prompt-resources",
            ),
            prompt_compliance=PromptComplianceConfig(
                enabled=True,
                guardrail=GuardrailProviderConfig(provider="noop"),
            ),
        )

        components = PromptRuntimeComponentsBuilder().build(config=config)

        self.assertIsInstance(components.resource_source, LocalPromptResourceSource)
        self.assertEqual(components.resource_source.root_dir, Path("./runtime-prompt-resources"))
        self.assertIs(components.scenario_loader.source, components.resource_source)
        self.assertIs(components.template_loader.source, components.resource_source)
        self.assertIs(components.slot_schema_loader.source, components.resource_source)
        self.assertIs(components.prompt_resource_loader.source, components.resource_source)
        self.assertIsInstance(components.slot_validator, SlotValidator)
        self.assertTrue(hasattr(components.guardrail, "check"))
