"""High-level LLM client facade with .env-backed defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a2a_t.config.errors import ConfigFileNotFoundError
from a2a_t.config.source import DotEnvConfigSource
from a2a_t.llm.base import LLMResponse
from a2a_t.llm.errors import LLMConfigError
from a2a_t.llm.factory import LLMAdapterFactory
from a2a_t.llm.session_store import InMemorySessionStore, ProviderScopedSessionStore, SessionStore

_MAX_HISTORY_WINDOW = 100
_MAX_SESSION_MAX_TOTAL = 3000
_MAX_SESSION_MAX_PER_PROVIDER = 1000


def _default_env_path() -> Path:
    return Path(__file__).resolve().parents[3] / "package_data" / ".env"


def _coerce_optional_int(value: str | None, key: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise LLMConfigError(f"{key} must be an integer") from exc


def _coerce_optional_float(value: str | None, key: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise LLMConfigError(f"{key} must be a float") from exc


@dataclass(frozen=True)
class LLMClientConfig:
    provider: str
    model: str
    api_key: str
    base_url: str | None
    history_window: int
    max_tokens: int | None
    temperature: float | None
    timeout_seconds: float | None
    session_max_total: int
    session_max_per_provider: int


class LLMClient:
    """Client for completion/chat/structured calls with shared session state."""

    def __init__(self, env_path: Path | None = None, session_store: SessionStore | None = None) -> None:
        self._env_path = env_path or _default_env_path()
        self._defaults = self._load_defaults(self._env_path)
        self._session_store = session_store or InMemorySessionStore(
            max_total=self._defaults.session_max_total,
            max_per_provider=self._defaults.session_max_per_provider,
        )

    def chat(
        self,
        message: str,
        system_prompt: str | None = None,
        session_id: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        history_window: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        runtime_config = self._build_runtime_config(
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            history_window=history_window,
            **kwargs,
        )
        adapter = LLMAdapterFactory.create(str(runtime_config["provider"]), runtime_config)
        return adapter.chat(
            message,
            system_prompt=system_prompt,
            session_id=session_id,
            temperature=runtime_config["temperature"],
            max_tokens=runtime_config["max_tokens"],
            history_window=runtime_config["history_window"],
        )

    def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        history_window: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        runtime_config = self._build_runtime_config(
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            history_window=history_window,
            **kwargs,
        )
        adapter = LLMAdapterFactory.create(str(runtime_config["provider"]), runtime_config)
        return adapter.complete(
            prompt,
            system_prompt=system_prompt,
            temperature=runtime_config["temperature"],
            max_tokens=runtime_config["max_tokens"],
        )

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        history_window: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        runtime_config = self._build_runtime_config(
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            history_window=history_window,
            **kwargs,
        )
        adapter = LLMAdapterFactory.create(str(runtime_config["provider"]), runtime_config)
        # 注意当前deepseek还不支持json schema模式，此处输入的json_schema仅能作为prompt级约束，无法作为协议级约束。
        return adapter.structured(
            messages=messages,
            json_schema=json_schema,
            temperature=runtime_config["temperature"],
            max_tokens=runtime_config["max_tokens"],
        )

    def reset_session(
        self,
        session_id: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        history_window: int | None = None,
        **kwargs: Any,
    ) -> None:
        runtime_config = self._build_runtime_config(
            provider=provider,
            model=model,
            history_window=history_window,
            **kwargs,
        )
        adapter = LLMAdapterFactory.create(str(runtime_config["provider"]), runtime_config)
        adapter.reset_session(session_id)

    def delete_session(
        self,
        session_id: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        history_window: int | None = None,
        **kwargs: Any,
    ) -> None:
        runtime_config = self._build_runtime_config(
            provider=provider,
            model=model,
            history_window=history_window,
            **kwargs,
        )
        adapter = LLMAdapterFactory.create(str(runtime_config["provider"]), runtime_config)
        adapter.delete_session(session_id)

    def _build_runtime_config(self, **overrides: Any) -> dict[str, Any]:
        provider = self._normalize_provider(overrides.get("provider") or self._defaults.provider)
        model = str(overrides.get("model") or self._defaults.model).strip()
        if not provider or not model:
            raise LLMConfigError("LLM client requires provider and model")

        history_window_value = overrides.get("history_window")
        if history_window_value is None:
            history_window_value = self._defaults.history_window
        history_window = self._coerce_bounded_int(
            history_window_value,
            "history_window",
            max_value=_MAX_HISTORY_WINDOW,
        )

        max_tokens = overrides.get("max_tokens")
        if max_tokens is None:
            max_tokens = self._defaults.max_tokens

        temperature = overrides.get("temperature")
        if temperature is None:
            temperature = self._defaults.temperature

        timeout_seconds = overrides.get("timeout_seconds")
        if timeout_seconds is None:
            timeout_seconds = self._defaults.timeout_seconds

        api_key = overrides.get("api_key")
        if api_key is None:
            api_key = self._defaults.api_key
        if not str(api_key or "").strip():
            raise LLMConfigError("LLM client requires a non-empty api_key")

        base_url = overrides.get("base_url")
        if base_url is None:
            base_url = self._defaults.base_url

        runtime_config = {
            "provider": provider,
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "history_window": history_window,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "timeout_seconds": timeout_seconds,
            "session_store": ProviderScopedSessionStore(provider, self._session_store),
        }
        return runtime_config

    def _load_defaults(self, env_path: Path) -> LLMClientConfig:
        try:
            values = DotEnvConfigSource.load(env_path)
        except ConfigFileNotFoundError as exc:
            raise LLMConfigError(f"LLM config file does not exist: {env_path}") from exc

        provider = self._normalize_provider(values.get("A2AT_LLM_PROVIDER", ""))
        model = str(values.get("A2AT_LLM_MODEL", "")).strip()
        if not provider or not model:
            raise LLMConfigError("A2AT_LLM_PROVIDER and A2AT_LLM_MODEL must be set in the .env file")

        history_window = self._coerce_bounded_int(
            values.get("A2AT_LLM_HISTORY_WINDOW", "10"),
            "A2AT_LLM_HISTORY_WINDOW",
            max_value=_MAX_HISTORY_WINDOW,
        )
        session_max_total = self._coerce_bounded_int(
            values.get("A2AT_LLM_SESSION_MAX_TOTAL", "300"),
            "A2AT_LLM_SESSION_MAX_TOTAL",
            max_value=_MAX_SESSION_MAX_TOTAL,
        )
        session_max_per_provider = self._coerce_bounded_int(
            values.get("A2AT_LLM_SESSION_MAX_PER_PROVIDER", "100"),
            "A2AT_LLM_SESSION_MAX_PER_PROVIDER",
            max_value=_MAX_SESSION_MAX_PER_PROVIDER,
        )
        if session_max_total < session_max_per_provider:
            raise LLMConfigError(
                "A2AT_LLM_SESSION_MAX_TOTAL must be greater than or equal to "
                "A2AT_LLM_SESSION_MAX_PER_PROVIDER"
            )

        return LLMClientConfig(
            provider=provider,
            model=model,
            api_key=str(values.get("A2AT_LLM_API_KEY", "")).strip(),
            base_url=str(values.get("A2AT_LLM_BASE_URL", "")).strip() or None,
            history_window=history_window,
            max_tokens=_coerce_optional_int(values.get("A2AT_LLM_MAX_TOKENS"), "A2AT_LLM_MAX_TOKENS"),
            temperature=_coerce_optional_float(values.get("A2AT_LLM_TEMPERATURE"), "A2AT_LLM_TEMPERATURE"),
            timeout_seconds=_coerce_optional_float(values.get("A2AT_LLM_TIMEOUT_SECONDS"), "A2AT_LLM_TIMEOUT_SECONDS"),
            session_max_total=session_max_total,
            session_max_per_provider=session_max_per_provider,
        )

    def _normalize_provider(self, value: object) -> str:
        provider = str(value or "").strip()
        if not provider:
            return provider

        available = set(LLMAdapterFactory.available_types())
        if provider not in available:
            raise LLMConfigError(f"Unsupported llm provider: {provider}. Available: {sorted(available)}")
        return provider

    def _coerce_bounded_int(self, value: object, key: str, *, max_value: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise LLMConfigError(f"{key} must be an integer") from exc
        if parsed <= 0:
            raise LLMConfigError(f"{key} must be greater than zero")
        if parsed > max_value:
            raise LLMConfigError(f"{key} must be less than or equal to {max_value}")
        return parsed
