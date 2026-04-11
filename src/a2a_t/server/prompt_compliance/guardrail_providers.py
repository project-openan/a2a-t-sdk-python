from __future__ import annotations

from typing import Any

from a2a_t.server.prompt_compliance.config import GuardrailProviderConfig
from a2a_t.server.prompt_compliance.errors import GuardrailExecutionError
from a2a_t.server.prompt_compliance.guardrails import GuardrailAdapter
from a2a_t.server.prompt_compliance.models import GuardrailDecision, GuardrailRequest, GuardrailResult


class GoogleModelArmorGateway:
    """Thin gateway over the official Google Model Armor client."""

    def __init__(self, *, config: GuardrailProviderConfig, client: object | None = None) -> None:
        self._config = config
        self._client = client or self._build_client()

    def scan_prompt(self, *, text: str, metadata: dict[str, object] | None = None) -> object:
        request = {
            "name": self._config.policy_id,
            "user_prompt_data": {
                "text": text,
            },
        }

        try:
            return self._client.sanitize_user_prompt(request=request)
        except TimeoutError as exc:
            raise GuardrailExecutionError("Google Model Armor timed out", provider="google_model_armor") from exc
        except ConnectionError as exc:
            raise GuardrailExecutionError("Google Model Armor connection failed", provider="google_model_armor") from exc
        except OSError as exc:
            raise GuardrailExecutionError("Google Model Armor execution failed", provider="google_model_armor") from exc

    @staticmethod
    def _build_client() -> object:
        try:
            from google.cloud import modelarmor_v1
        except ImportError as exc:
            raise GuardrailExecutionError(
                "google-cloud-model-armor is required for google_model_armor provider",
                provider="google_model_armor",
            ) from exc

        return modelarmor_v1.ModelArmorClient()


class GoogleModelArmorGuardrailAdapter(GuardrailAdapter):
    """Normalize Google Model Armor input scan results to GuardrailResult."""

    provider_name = "google_model_armor"

    def __init__(self, *, config: GuardrailProviderConfig, gateway: GoogleModelArmorGateway) -> None:
        self._config = config
        self._gateway = gateway

    def check_input(self, request: GuardrailRequest) -> GuardrailResult:
        raw_response = self._gateway.scan_prompt(text=request.text, metadata=request.metadata)
        sanitization_result = self._read(raw_response, "sanitization_result") or {}
        filter_match_state = self._read(sanitization_result, "filter_match_state")
        filter_results = self._read(sanitization_result, "filter_results") or []

        if filter_match_state == "NO_MATCH_FOUND":
            decision = GuardrailDecision.ALLOW
            passed = True
            category = None
        elif filter_match_state == "MATCH_FOUND" and filter_results:
            decision = GuardrailDecision.BLOCK
            passed = False
            category = self._read(filter_results[0], "tag")
        else:
            decision = GuardrailDecision.REVIEW
            passed = False
            category = None

        return GuardrailResult(
            passed=passed,
            decision=decision,
            category=category,
            reason=None if passed else "Google Model Armor flagged the prompt.",
            raw_response=self._normalize_raw_response(raw_response),
            provider=self.provider_name,
            policy_id=request.policy_id or self._config.policy_id or None,
        )

    @staticmethod
    def _read(value: object, field: str) -> Any:
        if isinstance(value, dict):
            return value.get(field)
        return getattr(value, field, None)

    @staticmethod
    def _normalize_raw_response(value: object) -> dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        return {"value": value}
