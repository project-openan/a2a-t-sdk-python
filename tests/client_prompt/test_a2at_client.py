from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import inspect


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.client.prompt_generation.models import PromptGenerationResult, ValidationResult
from a2a_t.negotiation.common.enums import NegotiationStatus, NegotiationType
from a2a_t.negotiation.common.models import ContinueNegotiationInput, NegotiationContext, StartNegotiationInput


class FakePromptGenerationOrchestrator:
    def __init__(self, result: object) -> None:
        self._result = result
        self.calls: list[object] = []

    def generate(self, user_input: object) -> object:
        self.calls.append(user_input)
        return self._result


class FakePromptGenerationBuilder:
    def __init__(self, orchestrator: FakePromptGenerationOrchestrator) -> None:
        self._orchestrator = orchestrator
        self.calls: list[dict[str, object]] = []

    def build(self, **kwargs: object) -> FakePromptGenerationOrchestrator:
        self.calls.append(dict(kwargs))
        return self._orchestrator


class FakeNegotiationOrchestrator:
    def __init__(self) -> None:
        self.start_calls: list[object] = []
        self.receive_calls: list[dict[str, object]] = []
        self.continue_calls: list[object] = []

    def start_negotiation(self, input: StartNegotiationInput) -> dict[str, object]:
        self.start_calls.append(input)
        return {"started": True}

    def receive_negotiation(self, message: str, context: dict[str, object]) -> dict[str, object]:
        self.receive_calls.append({"message": message, "context": context})
        return {"received": True}

    def continue_negotiation(self, input: ContinueNegotiationInput) -> dict[str, object]:
        self.continue_calls.append(input)
        return {"continued": True}


class A2ATClientTest(unittest.TestCase):
    def test_a2at_client_delegates_all_public_methods_with_typed_negotiation_inputs(self) -> None:
        from a2a_t.client.a2at_client import A2ATClient

        prompt_result = PromptGenerationResult(
            success=True,
            prompt_text="prompt",
            scenario_code=None,
            language="en-US",
            input_kind="natural_language",
            slots={},
            validation=ValidationResult(passed=True, slot_errors=[]),
            failure=None,
        )
        prompt_orchestrator = FakePromptGenerationOrchestrator(prompt_result)
        prompt_builder = FakePromptGenerationBuilder(prompt_orchestrator)
        negotiation = FakeNegotiationOrchestrator()

        start_input = StartNegotiationInput(
            type=NegotiationType.CLARIFICATION,
            content_text="Clarify please",
            facts={},
        )
        continue_input = ContinueNegotiationInput(
            context=NegotiationContext.from_context(
                {
                    "negotiationType": "clarification",
                    "negotiationId": "neg-1",
                    "role": "client",
                    "round": 1,
                    "status": "in-progress",
                    "extra": {},
                }
            ),
            status=NegotiationStatus.IN_PROGRESS,
            content_text="Here is more detail.",
        )

        with (
            patch("a2a_t.client.a2at_client.PromptGenerationOrchestratorBuilder", return_value=prompt_builder),
            patch("a2a_t.client.a2at_client.ClientNegotiationOrchestratorBuilder") as negotiation_builder_cls,
            patch("a2a_t.client.a2at_client.LLMClient", return_value=object()),
        ):
            negotiation_builder_cls.return_value.build.return_value = negotiation
            client = A2ATClient()

            self.assertIs(client.generate_task_prompt("Analyze Site A."), prompt_result)
            self.assertEqual(client.start_negotiation(start_input), {"started": True})
            self.assertEqual(
                client.receive_negotiation(
                    "Clarify intent",
                    {
                        "negotiationType": "clarification",
                        "negotiationId": "neg-1",
                        "role": "client",
                        "round": 1,
                        "status": "in-progress",
                        "extra": {},
                    },
                ),
                {"received": True},
            )
            self.assertEqual(client.continue_negotiation(continue_input), {"continued": True})

        self.assertEqual(prompt_orchestrator.calls, ["Analyze Site A."])
        self.assertEqual(negotiation.start_calls, [start_input])
        self.assertEqual(negotiation.receive_calls[0]["message"], "Clarify intent")
        self.assertEqual(negotiation.continue_calls, [continue_input])

    def test_a2at_client_builds_prompt_generation_orchestrator_via_builder(self) -> None:
        from a2a_t.client.a2at_client import A2ATClient

        prompt_result = PromptGenerationResult(
            success=True,
            prompt_text="generated prompt",
            scenario_code="energy_saving",
            language="en-US",
            input_kind="natural_language",
            slots={},
            validation=ValidationResult(passed=True, slot_errors=[]),
            failure=None,
        )
        prompt_orchestrator = FakePromptGenerationOrchestrator(prompt_result)
        prompt_builder = FakePromptGenerationBuilder(prompt_orchestrator)
        negotiation = FakeNegotiationOrchestrator()

        with (
            patch("a2a_t.client.a2at_client.PromptGenerationOrchestratorBuilder", return_value=prompt_builder),
            patch("a2a_t.client.a2at_client.ClientNegotiationOrchestratorBuilder") as negotiation_builder_cls,
            patch("a2a_t.client.a2at_client.LLMClient", return_value=object()),
        ):
            negotiation_builder_cls.return_value.build.return_value = negotiation
            client = A2ATClient()

            actual = client.generate_task_prompt("Analyze Site A.")

        self.assertIs(actual, prompt_result)
        self.assertEqual(prompt_orchestrator.calls, ["Analyze Site A."])
        self.assertEqual(len(prompt_builder.calls), 1)
        self.assertIn("config", prompt_builder.calls[0])
        self.assertIn("llm_client", prompt_builder.calls[0])
        self.assertNotIn("resource_root", prompt_builder.calls[0])

    def test_a2at_client_init_keeps_only_runtime_configuration_inputs(self) -> None:
        from a2a_t.client.a2at_client import A2ATClient

        parameters = inspect.signature(A2ATClient).parameters

        self.assertEqual(
            list(parameters),
            ["env_path", "logger"],
        )

    def test_a2at_client_does_not_keep_resolved_llm_client_local_name(self) -> None:
        from a2a_t.client.a2at_client import A2ATClient

        source = inspect.getsource(A2ATClient.__init__)

        self.assertNotIn("resolved_llm_client", source)

    def test_client_package_only_exports_a2at_client(self) -> None:
        import a2a_t.client as client_package

        self.assertTrue(hasattr(client_package, "A2ATClient"))
        self.assertFalse(hasattr(client_package, "PromptClient"))
        self.assertFalse(hasattr(client_package, "ExtendedClient"))
        self.assertFalse(hasattr(client_package, "CompressionClient"))

    def test_root_package_does_not_lazy_export_removed_client_entrypoints(self) -> None:
        import a2a_t

        self.assertNotIn("prompt_client", a2a_t.__all__)
        self.assertNotIn("extended_client", a2a_t.__all__)
        self.assertNotIn("compression_client", a2a_t.__all__)
        with self.assertRaises(AttributeError):
            getattr(a2a_t, "prompt_client")


if __name__ == "__main__":
    unittest.main()
