from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.config.models import A2ATConfig, GuardrailProviderConfig, PromptComplianceConfig, PromptRuntimeConfig
from tests.test_support import ManagedTempDirTestCase


class CommonPromptRuntimeComponentsBuilderTest(unittest.TestCase):
    def test_common_builder_creates_shared_runtime_components_from_config(self) -> None:
        from a2a_t.common.prompt_runtime import PromptRuntimeComponentsBuilder
        from a2a_t.common.prompt_resources.prompt_resource_loader import PromptResourceLoader
        from a2a_t.common.prompt_resources.slot_json_schema_loader import SlotJsonSchemaLoader
        from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator

        config = A2ATConfig(
            prompt=PromptRuntimeConfig(
                language="zh-CN",
                source_type="local_file",
                local_root_dir="./runtime-prompt-resources",
            ),
            prompt_compliance=PromptComplianceConfig(
                enabled=True,
                guardrail=GuardrailProviderConfig(provider="noop"),
            ),
        )

        components = PromptRuntimeComponentsBuilder().build(config=config)

        self.assertFalse(hasattr(components, "resource_source"))
        self.assertEqual(components.scenario_loader.root_dir, Path("./runtime-prompt-resources"))
        self.assertEqual(components.template_loader.root_dir, Path("./runtime-prompt-resources"))
        self.assertEqual(components.slot_schema_loader.root_dir, Path("./runtime-prompt-resources"))
        self.assertIsInstance(components.slot_json_schema_loader, SlotJsonSchemaLoader)
        self.assertEqual(components.slot_json_schema_loader.root_dir, Path("./runtime-prompt-resources"))
        self.assertIsInstance(components.prompt_resource_loader, PromptResourceLoader)
        self.assertEqual(components.prompt_resource_loader.root_dir, PromptResourceLoader().root_dir)
        self.assertFalse(hasattr(components, "slot_validator"))
        self.assertIsInstance(components.json_schema_slot_validator, JsonSchemaSlotValidator)
        self.assertTrue(hasattr(components.guardrail, "check"))


class CommonPromptRuntimeComponentsBuilderRootScopeAdjustmentTest(ManagedTempDirTestCase):
    def _build_config(self) -> A2ATConfig:
        return A2ATConfig(
            prompt=PromptRuntimeConfig(
                language="en-US",
                source_type="local_file",
                local_root_dir=str(self.root),
            ),
            prompt_compliance=PromptComplianceConfig(
                enabled=True,
                guardrail=GuardrailProviderConfig(provider="noop"),
            ),
        )

    def _write_resource_file(self, relative_path: str, content: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_temp_dir("runtime_root_scope")

    def test_common_builder_loads_packaged_prompts_even_when_custom_root_has_no_prompts(self) -> None:
        from a2a_t.common.prompt_runtime import PromptRuntimeComponentsBuilder

        components = PromptRuntimeComponentsBuilder().build(config=self._build_config())

        scenario_prompts = components.prompt_resource_loader.load(
            analysis_action="scenario_recognition",
            language="en-US",
        )

        self.assertTrue(scenario_prompts.system_prompt.strip())
        self.assertTrue(scenario_prompts.user_prompt.strip())

    def test_common_builder_warns_when_custom_root_contains_prompts_directory(self) -> None:
        from a2a_t.common.prompt_runtime import PromptRuntimeComponentsBuilder

        self._write_resource_file("prompts/scenario_recognition/en-US/system.md", "custom system")

        with self.assertLogs("a2a_t.common.prompt_runtime.prompt_runtime_components_builder", level="WARNING") as logs:
            PromptRuntimeComponentsBuilder().build(config=self._build_config())

        self.assertTrue(any("prompts" in message for message in logs.output))

    def test_common_builder_does_not_warn_when_local_root_is_sdk_packaged_root(self) -> None:
        from a2a_t.common.prompt_runtime import PromptRuntimeComponentsBuilder
        from a2a_t.common.prompt_resources.prompt_resource_loader import PromptResourceLoader

        packaged_root = PromptResourceLoader().root_dir
        config = A2ATConfig(
            prompt=PromptRuntimeConfig(
                language="en-US",
                source_type="local_file",
                local_root_dir=str(packaged_root),
            ),
            prompt_compliance=PromptComplianceConfig(
                enabled=True,
                guardrail=GuardrailProviderConfig(provider="noop"),
            ),
        )

        with self.assertNoLogs("a2a_t.common.prompt_runtime.prompt_runtime_components_builder", level="WARNING"):
            PromptRuntimeComponentsBuilder().build(config=config)
