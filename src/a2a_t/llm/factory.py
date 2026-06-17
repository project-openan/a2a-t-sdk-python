"""Factories for creating LLM provider clients."""

from __future__ import annotations

from importlib import import_module
from typing import Any, cast

from a2a_t.llm.errors import LLMConfigError
from a2a_t.llm.models import LLMClientConfig
from a2a_t.llm.provider import LLMClient


class LLMClientFactory:
    """Registry and factory for provider-facing LLM clients."""

    _clients: dict[str, type[LLMClient]] = {}
    _client_imports: dict[str, tuple[str, str]] = {
        "deepseek": ("a2a_t.llm.providers.openai", "OpenAICompatibleClient"),
    }
    _client_defaults: dict[str, dict[str, Any]] = {
        "deepseek": {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
        }
    }

    @classmethod
    def register(cls, provider: str, client_class: type[LLMClient]) -> None:
        """Register a provider client class."""
        normalized_provider = cls._normalize_provider(provider)
        if normalized_provider in cls._clients:
            raise LLMConfigError(f"LLM provider '{normalized_provider}' is already registered")
        cls._clients[normalized_provider] = client_class

    @classmethod
    def create(
        cls,
        provider: str,
        config: LLMClientConfig,
        *,
        logger: Any | None = None,
    ) -> LLMClient:
        """Create an LLM client for a registered provider."""
        normalized_provider = cls._normalize_provider(provider)
        client_class = cls._resolve(normalized_provider)
        resolved_config = cls._apply_client_defaults(normalized_provider, config)
        return client_class(resolved_config, logger=logger)

    @classmethod
    def available_providers(cls) -> list[str]:
        """List built-in and registered provider names."""
        return sorted({*cls._client_imports.keys(), *cls._client_defaults.keys(), *cls._clients.keys()})

    @classmethod
    def _apply_client_defaults(cls, provider: str, config: LLMClientConfig) -> LLMClientConfig:
        """Return a config copy enriched with provider defaults."""
        defaults = cls._client_defaults.get(provider)
        if defaults is None:
            return config
        values = dict(config.__dict__)
        for key, value in defaults.items():
            if values.get(key) in (None, ""):
                values[key] = value
        return LLMClientConfig(**values)

    @classmethod
    def _normalize_provider(cls, value: object) -> str:
        """Normalize and validate a provider identifier."""
        provider = str(value or "").strip()
        if not provider:
            raise LLMConfigError("LLM provider must be non-empty")
        if provider.lower() != provider or any(character.isspace() for character in provider):
            raise LLMConfigError("LLM provider must use lowercase non-whitespace characters")
        return provider

    @classmethod
    def _resolve(cls, provider: str) -> type[LLMClient]:
        """Resolve a provider client class, importing built-ins lazily."""
        client_class = cls._clients.get(provider)
        if client_class is not None:
            return client_class

        import_target = cls._client_imports.get(provider)
        if import_target is None:
            available = cls.available_providers()
            raise LLMConfigError(f"Unknown llm provider: {provider}. Available: {available}")

        module_name, class_name = import_target
        module = import_module(module_name)
        client_class = cast(type[LLMClient], getattr(module, class_name))
        cls._clients[provider] = client_class
        return client_class
