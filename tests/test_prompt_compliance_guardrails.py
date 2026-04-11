from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.server.prompt_compliance.errors import GuardrailExecutionError, GuardrailRejectedError
from a2a_t.server.prompt_compliance.guardrails import SafetyGuardrailFactory
from a2a_t.server.prompt_compliance.models import GuardrailProviderConfig, GuardrailResult


class SafetyGuardrailFactoryTest(unittest.TestCase):
    def test_factory_registers_noop_guardrail(self) -> None:
        available_types = SafetyGuardrailFactory.available_types()

        self.assertIn("noop", available_types)

    def test_factory_creates_guardrail_and_normalizes_result(self) -> None:
        def transport(prompt_text: str, context: dict[str, object] | None) -> dict[str, object]:
            self.assertEqual(prompt_text, "processed prompt body")
            self.assertEqual(context, {"request_id": "req-1"})
            return {
                "passed": False,
                "category": "prompt_injection",
                "reason": "blocked by policy",
                "raw_response": {"provider_status": "blocked"},
            }

        SafetyGuardrailFactory.register("custom_guardrail", transport)
        guardrail = SafetyGuardrailFactory.create(
            GuardrailProviderConfig(
                provider="custom_guardrail",
                timeout=3.0,
                config={"transport": transport},
            )
        )

        result = guardrail.check("processed prompt body", {"request_id": "req-1"})

        self.assertEqual(
            result,
            GuardrailResult(
                passed=False,
                category="prompt_injection",
                reason="blocked by policy",
                raw_response={"provider_status": "blocked"},
            ),
        )

    def test_factory_creates_noop_guardrail(self) -> None:
        guardrail = SafetyGuardrailFactory.create(GuardrailProviderConfig(provider=""))

        result = guardrail.check("processed prompt body", {"request_id": "req-1"})

        self.assertEqual(result, GuardrailResult(passed=True))

    def test_guardrail_transient_failure_raises_execution_error(self) -> None:
        def transport(prompt_text: str, context: dict[str, object] | None) -> dict[str, object]:
            raise TimeoutError("guardrail timeout")

        SafetyGuardrailFactory.register("timeout_guardrail", transport)
        guardrail = SafetyGuardrailFactory.create(
            GuardrailProviderConfig(
                provider="timeout_guardrail",
                timeout=3.0,
                config={"transport": transport},
            )
        )

        with self.assertRaises(GuardrailExecutionError):
            guardrail.check("processed prompt body", {"request_id": "req-1"})

    def test_guardrail_rejection_is_explicitly_modeled(self) -> None:
        error = GuardrailRejectedError("blocked by policy", category="prompt_injection", provider="custom_guardrail")

        self.assertEqual(str(error), "blocked by policy")
        self.assertEqual(error.context["category"], "prompt_injection")
        self.assertEqual(error.context["provider"], "custom_guardrail")


if __name__ == "__main__":
    unittest.main()
