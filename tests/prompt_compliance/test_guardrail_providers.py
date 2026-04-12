from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.server.prompt_compliance.errors import GuardrailExecutionError
from a2a_t.server.prompt_compliance.guardrail_providers import (
    GoogleModelArmorGateway,
    GoogleModelArmorGuardrailAdapter,
)
from a2a_t.server.prompt_compliance.guardrails import AdapterSafetyGuardrail, SafetyGuardrailFactory
from a2a_t.server.prompt_compliance.models import (
    GuardrailDecision,
    GuardrailProviderConfig,
    GuardrailRequest,
)


class FakeGoogleClient:
    def __init__(self, response: object | Exception) -> None:
        self._response = response
        self.requests: list[dict[str, object]] = []

    def sanitize_user_prompt(self, request: dict[str, object]) -> object:
        self.requests.append(request)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class GoogleModelArmorGatewayTest(unittest.TestCase):
    def test_gateway_calls_injected_client_with_request_payload(self) -> None:
        client = FakeGoogleClient(response={"sanitization_result": {"filter_match_state": "NO_MATCH_FOUND"}})
        gateway = GoogleModelArmorGateway(
            config=GuardrailProviderConfig(
                provider="google_model_armor",
                policy_id="projects/p1/locations/global/templates/template-1",
            ),
            client=client,
        )

        response = gateway.scan_prompt(text="processed prompt", metadata={"request_id": "req-1"})

        self.assertEqual(response, {"sanitization_result": {"filter_match_state": "NO_MATCH_FOUND"}})
        self.assertEqual(
            client.requests,
            [
                {
                    "name": "projects/p1/locations/global/templates/template-1",
                    "user_prompt_data": {
                        "text": "processed prompt",
                    },
                }
            ],
        )

    def test_gateway_maps_timeout_to_execution_error(self) -> None:
        gateway = GoogleModelArmorGateway(
            config=GuardrailProviderConfig(provider="google_model_armor", policy_id="projects/p1/locations/global/templates/template-1"),
            client=FakeGoogleClient(response=TimeoutError("timeout")),
        )

        with self.assertRaises(GuardrailExecutionError):
            gateway.scan_prompt(text="processed prompt", metadata=None)


class GoogleModelArmorGuardrailAdapterTest(unittest.TestCase):
    def test_adapter_maps_allow_response(self) -> None:
        adapter = GoogleModelArmorGuardrailAdapter(
            config=GuardrailProviderConfig(
                provider="google_model_armor",
                policy_id="projects/p1/locations/global/templates/template-1",
            ),
            gateway=GoogleModelArmorGateway(
                config=GuardrailProviderConfig(
                    provider="google_model_armor",
                    policy_id="projects/p1/locations/global/templates/template-1",
                ),
                client=FakeGoogleClient(
                    response={"sanitization_result": {"filter_match_state": "NO_MATCH_FOUND"}}
                ),
            ),
        )

        result = adapter.check_input(
            GuardrailRequest(
                text="processed prompt",
                metadata={"request_id": "req-1"},
                policy_id="projects/p1/locations/global/templates/template-1",
            )
        )

        self.assertEqual(result.decision, GuardrailDecision.ALLOW)
        self.assertTrue(result.passed)
        self.assertEqual(result.provider, "google_model_armor")

    def test_adapter_maps_block_response(self) -> None:
        adapter = GoogleModelArmorGuardrailAdapter(
            config=GuardrailProviderConfig(
                provider="google_model_armor",
                policy_id="projects/p1/locations/global/templates/template-1",
            ),
            gateway=GoogleModelArmorGateway(
                config=GuardrailProviderConfig(
                    provider="google_model_armor",
                    policy_id="projects/p1/locations/global/templates/template-1",
                ),
                client=FakeGoogleClient(
                    response={
                        "sanitization_result": {
                            "filter_match_state": "MATCH_FOUND",
                            "filter_results": [{"tag": "prompt_injection"}],
                        }
                    }
                ),
            ),
        )

        result = adapter.check_input(
            GuardrailRequest(
                text="processed prompt",
                metadata=None,
                policy_id="projects/p1/locations/global/templates/template-1",
            )
        )

        self.assertEqual(result.decision, GuardrailDecision.BLOCK)
        self.assertFalse(result.passed)
        self.assertEqual(result.category, "prompt_injection")

    def test_adapter_maps_review_response(self) -> None:
        adapter = GoogleModelArmorGuardrailAdapter(
            config=GuardrailProviderConfig(
                provider="google_model_armor",
                policy_id="projects/p1/locations/global/templates/template-1",
            ),
            gateway=GoogleModelArmorGateway(
                config=GuardrailProviderConfig(
                    provider="google_model_armor",
                    policy_id="projects/p1/locations/global/templates/template-1",
                ),
                client=FakeGoogleClient(
                    response={"sanitization_result": {"filter_match_state": "MATCH_FOUND", "filter_results": []}}
                ),
            ),
        )

        result = adapter.check_input(
            GuardrailRequest(
                text="processed prompt",
                metadata=None,
                policy_id="projects/p1/locations/global/templates/template-1",
            )
        )

        self.assertEqual(result.decision, GuardrailDecision.REVIEW)
        self.assertFalse(result.passed)

    def test_factory_creates_google_model_armor_guardrail(self) -> None:
        guardrail = SafetyGuardrailFactory.create(
            GuardrailProviderConfig(
                provider="google_model_armor",
                policy_id="projects/p1/locations/global/templates/template-1",
                config={
                    "client": FakeGoogleClient(
                        response={"sanitization_result": {"filter_match_state": "NO_MATCH_FOUND"}}
                    )
                },
            )
        )

        self.assertIsInstance(guardrail, AdapterSafetyGuardrail)
