from __future__ import annotations

import sys
from pathlib import Path
import unittest
import importlib.util


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.config.models import A2ATConfig, GuardrailProviderConfig, PromptComplianceConfig, PromptRuntimeConfig
from a2a_t.common.prompt_resources import LocalPromptResourceSource
from a2a_t.prompt.validation import SlotValidator


class PromptRuntimeComponentsBuilderTest(unittest.TestCase):
    def test_builder_creates_shared_runtime_components_from_config(self) -> None:
        from a2a_t.common.prompt_runtime import PromptRuntimeComponentsBuilder

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
        self.assertFalse(hasattr(components, "llm_client"))

    def test_builder_rejects_unsupported_source_type(self) -> None:
        from a2a_t.common.prompt_runtime import PromptRuntimeComponentsBuilder

        config = A2ATConfig(
            prompt=PromptRuntimeConfig(source_type="url"),
            prompt_compliance=PromptComplianceConfig(),
        )

        with self.assertRaises(ValueError):
            PromptRuntimeComponentsBuilder().build(config=config)

    def test_prompt_builders_shim_package_is_removed(self) -> None:
        self.assertIsNone(importlib.util.find_spec("a2a_t.prompt.builders"))


if __name__ == "__main__":
    unittest.main()

