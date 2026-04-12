from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.server.prompt_compliance.errors import GuardrailExecutionError, GuardrailRejectedError
from a2a_t.server.prompt_compliance.guardrails import (
    AdapterSafetyGuardrail,
    GuardrailAdapter,
    SafetyGuardrailFactory,
)
from a2a_t.server.prompt_compliance.models import (
    GuardrailDecision,
    GuardrailProviderConfig,
    GuardrailRequest,
    GuardrailResult,
)


class FakeAdapter(GuardrailAdapter):
    provider_name = "fake_adapter"

    def __init__(self, result: GuardrailResult | None = None) -> None:
        self.result = result or GuardrailResult(
            passed=True,
            decision=GuardrailDecision.ALLOW,
            provider="fake_adapter",
        )
        self.requests: list[GuardrailRequest] = []

    def check_input(self, request: GuardrailRequest) -> GuardrailResult:
        self.requests.append(request)
        return self.result


class SafetyGuardrailFactoryTest(unittest.TestCase):
    def test_adapter_safety_guardrail_converts_input_to_guardrail_request(self) -> None:
        adapter = FakeAdapter()
        guardrail = AdapterSafetyGuardrail(
            config=GuardrailProviderConfig(provider="google_model_armor", policy_id="policy-1"),
            adapter=adapter,
        )

        result = guardrail.check("processed prompt body", {"request_id": "req-1"})

        self.assertEqual(
            adapter.requests,
            [
                GuardrailRequest(
                    text="processed prompt body",
                    metadata={"request_id": "req-1"},
                    policy_id="policy-1",
                )
            ],
        )
        self.assertEqual(result.provider, "fake_adapter")

    def test_factory_registers_noop_guardrail(self) -> None:
        available_types = SafetyGuardrailFactory.available_types()

        self.assertIn("noop", available_types)
        self.assertIn("google_model_armor", available_types)

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

    def test_factory_reports_unregistered_reserved_provider_names(self) -> None:
        with self.assertRaises(ValueError) as aws_error:
            SafetyGuardrailFactory.create(GuardrailProviderConfig(provider="aws_bedrock"))

        with self.assertRaises(ValueError) as azure_error:
            SafetyGuardrailFactory.create(GuardrailProviderConfig(provider="azure_content_safety"))

        self.assertIn("aws_bedrock", str(aws_error.exception))
        self.assertIn("azure_content_safety", str(azure_error.exception))
        self.assertIn("reserved", str(aws_error.exception))
        self.assertIn("not implemented", str(aws_error.exception))
        self.assertIn("reserved", str(azure_error.exception))
        self.assertIn("not implemented", str(azure_error.exception))

    def test_factory_does_not_expose_reserved_provider_names_as_available_types(self) -> None:
        available_types = SafetyGuardrailFactory.available_types()

        self.assertNotIn("aws_bedrock", available_types)
        self.assertNotIn("azure_content_safety", available_types)
        self.assertIn("google_model_armor", available_types)


if __name__ == "__main__":
    unittest.main()
