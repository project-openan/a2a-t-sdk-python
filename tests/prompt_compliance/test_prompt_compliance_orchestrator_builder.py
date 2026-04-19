from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.config.models import A2ATConfig, PromptComplianceConfig, PromptRuntimeConfig


class FakeRuntimeComponentsBuilder:
    def __init__(self, components: object) -> None:
        self.components = components
        self.calls: list[object] = []

    def build(self, *, config: A2ATConfig) -> object:
        self.calls.append(config)
        return self.components


class FakeSlotExtractor:
    def __init__(self, *, llm_client: object) -> None:
        self.llm_client = llm_client


class FakeOrchestrator:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class PromptComplianceOrchestratorBuilderTest(unittest.TestCase):
    def test_builder_uses_runtime_components_builder_and_injects_llm_client(self) -> None:
        from a2a_t.server.prompt_compliance.prompt_compliance_orchestrator_builder import PromptComplianceOrchestratorBuilder

        components = type(
            "Components",
            (),
            {
                "guardrail": object(),
                "template_loader": object(),
                "slot_schema_loader": object(),
                "prompt_resource_loader": object(),
                "slot_validator": object(),
            },
        )()
        runtime_builder = FakeRuntimeComponentsBuilder(components)
        llm_client = object()
        builder = PromptComplianceOrchestratorBuilder(
            runtime_components_builder=runtime_builder,
            slot_extractor_cls=FakeSlotExtractor,
            orchestrator_cls=FakeOrchestrator,
        )
        config = A2ATConfig(
            prompt=PromptRuntimeConfig(local_root_dir="./default-root"),
            prompt_compliance=PromptComplianceConfig(),
        )

        orchestrator = builder.build(
            config=config,
            llm_client=llm_client,
        )

        self.assertEqual(len(runtime_builder.calls), 1)
        self.assertIs(runtime_builder.calls[0], config)
        self.assertIsInstance(orchestrator.kwargs["extractor"], FakeSlotExtractor)
        self.assertIs(orchestrator.kwargs["extractor"].llm_client, llm_client)

    def test_builder_reuses_provided_runtime_components_without_rebuilding(self) -> None:
        from a2a_t.server.prompt_compliance.prompt_compliance_orchestrator_builder import PromptComplianceOrchestratorBuilder

        components = type(
            "Components",
            (),
            {
                "guardrail": object(),
                "template_loader": object(),
                "slot_schema_loader": object(),
                "prompt_resource_loader": object(),
                "slot_validator": object(),
            },
        )()
        runtime_builder = FakeRuntimeComponentsBuilder(components)
        llm_client = object()
        builder = PromptComplianceOrchestratorBuilder(
            runtime_components_builder=runtime_builder,
            slot_extractor_cls=FakeSlotExtractor,
            orchestrator_cls=FakeOrchestrator,
        )
        config = A2ATConfig(
            prompt=PromptRuntimeConfig(local_root_dir="./default-root"),
            prompt_compliance=PromptComplianceConfig(),
        )

        orchestrator = builder.build(
            config=config,
            llm_client=llm_client,
            runtime_components=components,
        )

        self.assertEqual(runtime_builder.calls, [])
        self.assertIs(orchestrator.kwargs["guardrail"], components.guardrail)
        self.assertIs(orchestrator.kwargs["prompt_resource_loader"], components.prompt_resource_loader)
        self.assertIs(orchestrator.kwargs["validator"], components.slot_validator)

if __name__ == "__main__":
    unittest.main()
