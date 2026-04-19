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


class FakeScenarioRecognizer:
    def __init__(self, *, llm_client: object) -> None:
        self.llm_client = llm_client


class FakeSlotExtractor:
    def __init__(self, *, llm_client: object) -> None:
        self.llm_client = llm_client


class FakeOrchestrator:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class PromptGenerationOrchestratorBuilderTest(unittest.TestCase):
    def test_builder_uses_runtime_components_builder_and_injects_llm_client(self) -> None:
        from a2a_t.client.prompt_generation.prompt_generation_orchestrator_builder import PromptGenerationOrchestratorBuilder

        components = type(
            "Components",
            (),
            {
                "scenario_loader": object(),
                "prompt_resource_loader": object(),
                "template_loader": object(),
                "slot_schema_loader": object(),
                "slot_validator": object(),
                "resource_registry": object(),
            },
        )()
        runtime_builder = FakeRuntimeComponentsBuilder(components)
        llm_client = object()

        builder = PromptGenerationOrchestratorBuilder(
            runtime_components_builder=runtime_builder,
            scenario_recognizer_cls=FakeScenarioRecognizer,
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
        self.assertEqual(orchestrator.kwargs["config"].local_root_dir, "./default-root")
        self.assertIsInstance(orchestrator.kwargs["scenario_recognizer"], FakeScenarioRecognizer)
        self.assertIsInstance(orchestrator.kwargs["slot_extractor"], FakeSlotExtractor)
        self.assertIs(orchestrator.kwargs["scenario_recognizer"].llm_client, llm_client)
        self.assertIs(orchestrator.kwargs["slot_extractor"].llm_client, llm_client)

if __name__ == "__main__":
    unittest.main()
