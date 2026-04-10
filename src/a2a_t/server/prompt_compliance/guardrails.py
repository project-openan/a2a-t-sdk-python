from __future__ import annotations

from typing import Any, Callable, Protocol

from a2a_t.server.prompt_compliance.errors import GuardrailExecutionError
from a2a_t.server.prompt_compliance.models import GuardrailResult, PromptComplianceProviderConfig


class SafetyGuardrail(Protocol):
    """Unified guardrail interface for processed prompt checks."""

    def check(self, prompt_text: str, context: dict[str, object] | None = None) -> GuardrailResult:
        """Check whether the processed prompt passes the safety guardrail."""


class NoopSafetyGuardrail:
    """Default guardrail that always passes."""

    def check(self, prompt_text: str, context: dict[str, object] | None = None) -> GuardrailResult:
        return GuardrailResult(passed=True)


class TransportSafetyGuardrail:
    """Guardrail backed by a transport callable provided by configuration."""

    def __init__(self, transport: Callable[[str, dict[str, object] | None], GuardrailResult | dict[str, object]]) -> None:
        self._transport = transport

    def check(self, prompt_text: str, context: dict[str, object] | None = None) -> GuardrailResult:
        try:
            result = self._transport(prompt_text, context)
        except TimeoutError as exc:
            raise GuardrailExecutionError("Safety guardrail timed out", prompt_text=prompt_text) from exc
        except ConnectionError as exc:
            raise GuardrailExecutionError("Safety guardrail connection failed", prompt_text=prompt_text) from exc
        except OSError as exc:
            raise GuardrailExecutionError("Safety guardrail execution failed", prompt_text=prompt_text) from exc

        if isinstance(result, GuardrailResult):
            return result

        return GuardrailResult(
            passed=bool(result.get("passed", False)),
            category=self._optional_string(result.get("category")),
            reason=self._optional_string(result.get("reason")),
            raw_response=self._normalize_raw_response(result.get("raw_response")),
        )

    @staticmethod
    def _optional_string(value: object) -> str | None:
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _normalize_raw_response(value: object) -> dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        return {"value": value}


class SafetyGuardrailFactory:
    """Factory for creating safety guardrail adapters from provider configuration."""

    _providers: dict[str, Callable[[PromptComplianceProviderConfig], SafetyGuardrail]] = {}

    @classmethod
    def register(
        cls,
        provider_name: str,
        provider: Callable[[str, dict[str, object] | None], GuardrailResult | dict[str, object]]
        | Callable[[PromptComplianceProviderConfig], SafetyGuardrail],
    ) -> None:
        cls._providers[provider_name] = cls._wrap_provider(provider)

    @classmethod
    def create(cls, config: PromptComplianceProviderConfig) -> SafetyGuardrail:
        provider_name = config.provider or "noop"
        if provider_name not in cls._providers:
            available = list(cls._providers.keys())
            raise ValueError(f"Unknown guardrail provider: {provider_name}. Available: {available}")
        return cls._providers[provider_name](config)

    @classmethod
    def available_types(cls) -> list[str]:
        return list(cls._providers.keys())

    @staticmethod
    def _wrap_provider(
        provider: Callable[[str, dict[str, object] | None], GuardrailResult | dict[str, object]]
        | Callable[[PromptComplianceProviderConfig], SafetyGuardrail],
    ) -> Callable[[PromptComplianceProviderConfig], SafetyGuardrail]:
        def builder(config: PromptComplianceProviderConfig) -> SafetyGuardrail:
            transport = config.config.get("transport")
            if callable(transport):
                return TransportSafetyGuardrail(transport)

            candidate = provider(config)
            if hasattr(candidate, "check"):
                return candidate
            if callable(candidate):
                return TransportSafetyGuardrail(candidate)
            raise TypeError("Registered guardrail provider must return a guardrail or transport callable")

        return builder


SafetyGuardrailFactory.register("noop", lambda config: NoopSafetyGuardrail())
