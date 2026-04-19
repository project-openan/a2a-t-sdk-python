from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.config.models import A2ATConfig, PromptComplianceConfig, PromptRuntimeConfig


class FakePromptComplianceBuilder:
    def __init__(self, checker: object) -> None:
        self._checker = checker
        self.calls: list[dict[str, object]] = []

    def build(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return self._checker


class FakeRuntimeComponentsBuilder:
    def __init__(self, components: object) -> None:
        self._components = components
        self.calls: list[object] = []

    def build(self, *, config: A2ATConfig) -> object:
        self.calls.append(config)
        return self._components


class FakePromptRenderer:
    last_kwargs: dict[str, object] | None = None

    def __init__(self, **kwargs: object) -> None:
        type(self).last_kwargs = dict(kwargs)


class FakeStoreFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def build(self, *, env_path=None, logger=None):
        from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore

        self.calls.append(
            {
                "env_path": env_path,
                "logger": logger,
            }
        )
        return InMemoryNegotiationStateStore()


class FakePromptChecker:
    def check(self, *, processed_prompt_text: str, request_metadata: dict[str, object] | None = None):
        from a2a_t.server.prompt_compliance.result import PromptComplianceResult

        return PromptComplianceResult(
            passed=True,
            stage="passed",
            extracted_slots={},
        )


class NegotiationOrchestratorBuilderTest(unittest.TestCase):
    def _config(self) -> A2ATConfig:
        return A2ATConfig(
            prompt=PromptRuntimeConfig(),
            prompt_compliance=PromptComplianceConfig(),
        )

    def test_client_builder_builds_working_orchestrator(self) -> None:
        from a2a_t.client.negotiation.negotiation_orchestrator_builder import ClientNegotiationOrchestratorBuilder
        from a2a_t.negotiation.common.enums import NegotiationType
        from a2a_t.negotiation.common.models import StartNegotiationInput

        store_factory = FakeStoreFactory()
        builder = ClientNegotiationOrchestratorBuilder(store_factory=store_factory)
        orchestrator = builder.build()

        result = orchestrator.start_negotiation(
            StartNegotiationInput(
                type=NegotiationType.CLARIFICATION,
                content_text="Please clarify.",
                facts={},
            )
        )

        self.assertIn("https://github.com/a2aproject/telecommunication/extensions/NEGOTIATION-T", result)
        self.assertEqual(
            result["https://github.com/a2aproject/telecommunication/extensions/DATA-NEGOTIATION-T/v1"]["role"],
            "client",
        )
        self.assertEqual(len(store_factory.calls), 1)

    def test_server_builder_builds_working_orchestrator(self) -> None:
        from a2a_t.server.negotiation.negotiation_orchestrator_builder import ServerNegotiationOrchestratorBuilder
        from a2a_t.negotiation.common.enums import NegotiationType
        from a2a_t.negotiation.common.models import StartNegotiationInput

        prompt_checker = FakePromptChecker()
        prompt_compliance_builder = FakePromptComplianceBuilder(prompt_checker)
        store_factory = FakeStoreFactory()
        builder = ServerNegotiationOrchestratorBuilder(
            prompt_compliance_builder=prompt_compliance_builder,
            store_factory=store_factory,
        )

        orchestrator = builder.build(
            config=self._config(),
            llm_client=object(),
        )
        result = orchestrator.start_negotiation(
            StartNegotiationInput(
                type=NegotiationType.INFORMATION,
                content_text="Need more details.",
                facts={},
            )
        )

        self.assertIn("https://github.com/a2aproject/telecommunication/extensions/NEGOTIATION-T", result)
        self.assertEqual(
            result["https://github.com/a2aproject/telecommunication/extensions/DATA-NEGOTIATION-T/v1"]["role"],
            "server",
        )
        self.assertEqual(len(prompt_compliance_builder.calls), 1)
        self.assertEqual(len(store_factory.calls), 1)

    def test_server_builder_builds_shared_runtime_once_and_reuses_it(self) -> None:
        from a2a_t.server.negotiation.negotiation_orchestrator_builder import ServerNegotiationOrchestratorBuilder

        prompt_checker = FakePromptChecker()
        prompt_compliance_builder = FakePromptComplianceBuilder(prompt_checker)
        components = type(
            "Components",
            (),
            {},
        )()
        runtime_builder = FakeRuntimeComponentsBuilder(components)
        builder = ServerNegotiationOrchestratorBuilder(
            prompt_compliance_builder=prompt_compliance_builder,
            runtime_components_builder=runtime_builder,
            prompt_renderer_cls=FakePromptRenderer,
            store_factory=FakeStoreFactory(),
        )

        builder.build(
            config=self._config(),
            llm_client=object(),
        )

        self.assertEqual(len(runtime_builder.calls), 1)
        self.assertIs(prompt_compliance_builder.calls[0]["runtime_components"], components)
        self.assertNotIn("resource_root", prompt_compliance_builder.calls[0])
        self.assertEqual(FakePromptRenderer.last_kwargs, {})
