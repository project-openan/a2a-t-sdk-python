from __future__ import annotations

from typing import Any, Callable, Protocol

from a2a_t.server.prompt_compliance.errors import GuardrailExecutionError
from a2a_t.server.prompt_compliance.models import GuardrailProviderConfig, GuardrailRequest, GuardrailResult


class SafetyGuardrail(Protocol):
    """加工后 Prompt 检查的统一护栏接口 / Unified guardrail interface for processed prompt checks."""

    def check(self, prompt_text: str, context: dict[str, object] | None = None) -> GuardrailResult:
        """检查加工后 Prompt 是否通过安全护栏 / Check whether the processed prompt passes the safety guardrail."""


class GuardrailAdapter(Protocol):
    """面向厂商实现的内部 adapter 协议 / Internal adapter protocol for provider-specific guardrail implementations."""

    provider_name: str

    def check_input(self, request: GuardrailRequest) -> GuardrailResult:
        """执行输入侧护栏检查 / Run input-side guardrail checks."""


class NoopSafetyGuardrail:
    """始终放行的默认护栏实现 / Default guardrail that always passes."""

    def check(self, prompt_text: str, context: dict[str, object] | None = None) -> GuardrailResult:
        return GuardrailResult(passed=True)


class AdapterSafetyGuardrail:
    """将 provider adapter 桥接到公共护栏接口 / Bridge a provider adapter to the public safety guardrail interface."""

    def __init__(self, *, config: GuardrailProviderConfig, adapter: GuardrailAdapter) -> None:
        self._config = config
        self._adapter = adapter

    def check(self, prompt_text: str, context: dict[str, object] | None = None) -> GuardrailResult:
        return self._adapter.check_input(
            GuardrailRequest(
                text=prompt_text,
                metadata=context,
                policy_id=self._config.policy_id or None,
            )
        )


class TransportSafetyGuardrail:
    """基于配置传入 transport 可调用对象的护栏实现 / Guardrail backed by a transport callable provided by configuration."""

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
    """根据 provider 配置创建安全护栏实例的工厂 / Factory for creating safety guardrail adapters from provider configuration."""

    _providers: dict[str, Callable[[GuardrailProviderConfig], SafetyGuardrail]] = {}
    _reserved_providers: set[str] = {"aws_bedrock", "azure_content_safety"}

    @classmethod
    def register(
        cls,
        provider_name: str,
        provider: Callable[[str, dict[str, object] | None], GuardrailResult | dict[str, object]]
        | Callable[[GuardrailProviderConfig], SafetyGuardrail],
    ) -> None:
        cls._providers[provider_name] = cls._wrap_provider(provider)

    @classmethod
    def create(cls, config: GuardrailProviderConfig) -> SafetyGuardrail:
        provider_name = config.provider or "noop"
        if provider_name not in cls._providers:
            if provider_name in cls._reserved_providers:
                raise ValueError(f"Guardrail provider '{provider_name}' is reserved for future support and not implemented.")
            available = list(cls._providers.keys())
            raise ValueError(f"Unknown guardrail provider: {provider_name}. Available: {available}")
        return cls._providers[provider_name](config)

    @classmethod
    def available_types(cls) -> list[str]:
        return list(cls._providers.keys())

    @staticmethod
    def _wrap_provider(
        provider: Callable[[str, dict[str, object] | None], GuardrailResult | dict[str, object]]
        | Callable[[GuardrailProviderConfig], SafetyGuardrail],
    ) -> Callable[[GuardrailProviderConfig], SafetyGuardrail]:
        def builder(config: GuardrailProviderConfig) -> SafetyGuardrail:
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


def _build_google_model_armor_guardrail(config: GuardrailProviderConfig) -> SafetyGuardrail:
    from a2a_t.server.prompt_compliance.guardrail_providers import (
        GoogleModelArmorGateway,
        GoogleModelArmorGuardrailAdapter,
    )

    client = config.config.get("client")
    gateway = GoogleModelArmorGateway(config=config, client=client if client is not None else None)
    adapter = GoogleModelArmorGuardrailAdapter(config=config, gateway=gateway)
    return AdapterSafetyGuardrail(config=config, adapter=adapter)


SafetyGuardrailFactory.register("google_model_armor", _build_google_model_armor_guardrail)
