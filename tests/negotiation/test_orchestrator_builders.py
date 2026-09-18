from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.config.models import A2ATConfig, PromptRuntimeConfig


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


class FakeOrchestrator:
    last_kwargs: dict[str, object] | None = None

    def __init__(self, **kwargs: object) -> None:
        type(self).last_kwargs = dict(kwargs)

    def start_negotiation(self, input: object) -> dict[str, object]:
        return {
            "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/NL/v1": {
                "role": "fake",
                "message": "fake",
            }
        }


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
    def check(self, *, processed_prompt_text: str):
        from a2a_t.server.prompt_compliance.models import PromptComplianceResult

        return PromptComplianceResult(
            success=True,
        )


class FakeLogger:
    def info(self, message: str, *args: object) -> None:
        pass


class NegotiationOrchestratorBuilderTest(unittest.TestCase):
    def _config(self) -> A2ATConfig:
        # Explicit source type: the default flipped to packaged in 1.1.0 (D10 step 2), and these
        # builder tests only need the bundled packaged tree either way.
        return A2ATConfig(
            prompt=PromptRuntimeConfig(source_type="packaged"),
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
                type=NegotiationType.TARGET,
                content_text="Please clarify.",
                facts={},
            )
        )

        self.assertIn(
            "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/NL/v1", result
        )
        negotiation_data = result[
            "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/NL/v1"
        ]
        self.assertEqual(negotiation_data["role"], "client")
        self.assertEqual(len(store_factory.calls), 1)

    def test_server_builder_builds_working_orchestrator(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationType
        from a2a_t.negotiation.common.models import StartNegotiationInput
        from a2a_t.server.negotiation.negotiation_orchestrator_builder import ServerNegotiationOrchestratorBuilder

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

        self.assertIn(
            "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/NL/v1", result
        )
        negotiation_data = result[
            "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/NL/v1"
        ]
        self.assertEqual(negotiation_data["role"], "server")
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

    def test_server_builder_passes_logger_to_prompt_compliance_builder(self) -> None:
        from a2a_t.server.negotiation.negotiation_orchestrator_builder import ServerNegotiationOrchestratorBuilder

        prompt_checker = FakePromptChecker()
        prompt_compliance_builder = FakePromptComplianceBuilder(prompt_checker)
        logger = FakeLogger()
        builder = ServerNegotiationOrchestratorBuilder(
            prompt_compliance_builder=prompt_compliance_builder,
            store_factory=FakeStoreFactory(),
        )

        builder.build(
            config=self._config(),
            llm_client=object(),
            logger=logger,
        )

        self.assertIs(prompt_compliance_builder.calls[0]["logger"], logger)

    def test_client_builder_passes_logger_to_orchestrator(self) -> None:
        from a2a_t.client.negotiation.negotiation_orchestrator_builder import ClientNegotiationOrchestratorBuilder

        logger = FakeLogger()
        builder = ClientNegotiationOrchestratorBuilder(
            store_factory=FakeStoreFactory(),
            orchestrator_cls=FakeOrchestrator,
        )

        builder.build(logger=logger)

        assert FakeOrchestrator.last_kwargs is not None
        self.assertIs(FakeOrchestrator.last_kwargs["logger"], logger)

    def test_server_builder_passes_logger_to_orchestrator(self) -> None:
        from a2a_t.server.negotiation.negotiation_orchestrator_builder import ServerNegotiationOrchestratorBuilder

        logger = FakeLogger()
        builder = ServerNegotiationOrchestratorBuilder(
            prompt_compliance_builder=FakePromptComplianceBuilder(FakePromptChecker()),
            store_factory=FakeStoreFactory(),
            orchestrator_cls=FakeOrchestrator,
        )

        builder.build(
            config=self._config(),
            llm_client=object(),
            logger=logger,
        )

        assert FakeOrchestrator.last_kwargs is not None
        self.assertIs(FakeOrchestrator.last_kwargs["logger"], logger)


# --------------------------------------------------------------------------------------
# Deprecation shim round (D1): the retired state-machine packages behind the legacy
# orchestrator builders this suite pins emit a DeprecationWarning when imported and will be
# removed in the next release. The behavioral assertions above stay untouched; this only
# pins the warning contract of the deprecated entry points this file exercises (the common
# and store packages the builders assemble the legacy orchestrators from).
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "package_name",
    (
        "a2a_t.negotiation.common",
        "a2a_t.negotiation.store",
    ),
)
def test_importing_a_deprecated_negotiation_package_warns(package_name: str) -> None:
    module = importlib.import_module(package_name)

    with pytest.warns(DeprecationWarning, match=f"{package_name} package is deprecated since 1\\.1\\.0"):
        importlib.reload(module)
