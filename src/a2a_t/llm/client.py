"""High-level LLM client facade with .env-backed defaults."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a2a_t.config.errors import ConfigFileNotFoundError
from a2a_t.config.source import DotEnvConfigSource
from a2a_t.llm.base import LLMResponse
from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError
from a2a_t.llm.factory import LLMAdapterFactory
from a2a_t.llm.session_store import InMemorySessionStore, ProviderScopedSessionStore

_MAX_HISTORY_WINDOW = 100
_MAX_SESSION_MAX_TOTAL = 3000
_MAX_SESSION_MAX_PER_PROVIDER = 1000
_DEFAULT_SESSION_STORE: InMemorySessionStore | None = None
_DEFAULT_SESSION_STORE_LIMITS: tuple[int, int] | None = None


def _default_env_path() -> Path:
    """Return the default .env path used by the shared LLM client."""
    return Path(__file__).resolve().parents[3] / "package_data" / ".env"


def _get_or_create_default_session_store(*, max_total: int, max_per_provider: int) -> InMemorySessionStore:
    """Return the process-wide default session store with stable capacity settings."""
    global _DEFAULT_SESSION_STORE, _DEFAULT_SESSION_STORE_LIMITS

    requested_limits = (max_total, max_per_provider)
    if _DEFAULT_SESSION_STORE is None:
        _DEFAULT_SESSION_STORE = InMemorySessionStore(
            max_total=max_total,
            max_per_provider=max_per_provider,
        )
        _DEFAULT_SESSION_STORE_LIMITS = requested_limits
        return _DEFAULT_SESSION_STORE

    if _DEFAULT_SESSION_STORE_LIMITS != requested_limits:
        # A single shared default store avoids fragmented history, so conflicting limits are rejected.
        raise LLMConfigError(
            "Default global LLM session store is already initialized with different "
            "session capacity limits"
        )

    return _DEFAULT_SESSION_STORE


def _reset_default_session_store_for_tests() -> None:
    """Reset the shared session store singleton used by tests."""
    global _DEFAULT_SESSION_STORE, _DEFAULT_SESSION_STORE_LIMITS

    _DEFAULT_SESSION_STORE = None
    _DEFAULT_SESSION_STORE_LIMITS = None


def _coerce_optional_int(value: str | None, key: str) -> int | None:
    """Parse an optional integer environment value."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise LLMConfigError(f"{key} must be an integer") from exc


def _coerce_optional_float(value: str | None, key: str) -> float | None:
    """Parse an optional float environment value."""
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

    def __init__(self, env_path: Path | None = None, logger: Any | None = None) -> None:
        self._env_path = env_path or _default_env_path()
        self._defaults = self._load_defaults(self._env_path)
        self._logger = logger
        self._session_store = _get_or_create_default_session_store(
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
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
        history_window: int | None = None,
    ) -> LLMResponse:
        """Run a chat request with optional session history."""
        runtime_config = self._build_inference_runtime_config(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            history_window=history_window,
        )
        adapter = LLMAdapterFactory.create(str(runtime_config["provider"]), runtime_config)
        self._log_request(
            method="chat",
            runtime_config=runtime_config,
            payload={
                "message": message,
                "system_prompt": system_prompt,
                "session_id": session_id,
                "temperature": runtime_config["temperature"],
                "max_tokens": runtime_config["max_tokens"],
                "history_window": runtime_config["history_window"],
            },
        )
        response = adapter.chat(
            message,
            system_prompt=system_prompt,
            session_id=session_id,
            temperature=runtime_config["temperature"],
            max_tokens=runtime_config["max_tokens"],
            history_window=runtime_config["history_window"],
        )
        self._log_response(method="chat", runtime_config=runtime_config, response=response)
        return response

    def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> LLMResponse:
        """Run a one-shot completion request."""
        runtime_config = self._build_inference_runtime_config(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        adapter = LLMAdapterFactory.create(str(runtime_config["provider"]), runtime_config)
        self._log_request(
            method="complete",
            runtime_config=runtime_config,
            payload={
                "prompt": prompt,
                "system_prompt": system_prompt,
                "temperature": runtime_config["temperature"],
                "max_tokens": runtime_config["max_tokens"],
            },
        )
        response = adapter.complete(
            prompt,
            system_prompt=system_prompt,
            temperature=runtime_config["temperature"],
            max_tokens=runtime_config["max_tokens"],
        )
        self._log_response(method="complete", runtime_config=runtime_config, response=response)
        return response

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> LLMResponse:
        """Run a structured generation request constrained by a JSON schema."""
        runtime_config = self._build_inference_runtime_config(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        adapter = LLMAdapterFactory.create(str(runtime_config["provider"]), runtime_config)
        self._log_request(
            method="structured",
            runtime_config=runtime_config,
            payload={
                "messages": messages,
                "json_schema": json_schema,
                "temperature": runtime_config["temperature"],
                "max_tokens": runtime_config["max_tokens"],
            },
        )
        response = adapter.structured(
            messages=messages,
            json_schema=json_schema,
            temperature=runtime_config["temperature"],
            max_tokens=runtime_config["max_tokens"],
        )
        self._log_response(method="structured", runtime_config=runtime_config, response=response)
        return response

    def reset_session(self, session_id: str) -> None:
        """Clear the message history tracked for one session."""
        if self._session_store.reset(session_id) is None:
            raise LLMRuntimeError(f"unknown session_id: {session_id}")

    def delete_session(self, session_id: str) -> None:
        """Delete one tracked session from the shared store."""
        self._session_store.delete(session_id)

    def _build_inference_runtime_config(
        self,
        *,
        provider: str | None,
        model: str | None,
        api_key: str | None,
        base_url: str | None,
        temperature: float | None,
        max_tokens: int | None,
        timeout_seconds: float | None,
        history_window: int | None = None,
    ) -> dict[str, Any]:
        """Merge call overrides with defaults and validate the resulting runtime config."""
        resolved_provider = self._normalize_provider(provider or self._defaults.provider)
        resolved_model = str(model or self._defaults.model).strip()
        if not resolved_provider or not resolved_model:
            raise LLMConfigError("LLM client requires provider and model")

        history_window_value = history_window if history_window is not None else self._defaults.history_window
        resolved_history_window = self._coerce_bounded_int(
            history_window_value,
            "history_window",
            max_value=_MAX_HISTORY_WINDOW,
        )

        resolved_api_key = self._defaults.api_key if api_key is None else str(api_key).strip()
        if not resolved_api_key:
            raise LLMConfigError("LLM client requires a non-empty api_key")

        resolved_base_url = self._defaults.base_url if base_url is None else str(base_url).strip() or None
        resolved_max_tokens = self._defaults.max_tokens if max_tokens is None else max_tokens
        resolved_temperature = self._defaults.temperature if temperature is None else temperature
        resolved_timeout_seconds = self._defaults.timeout_seconds if timeout_seconds is None else timeout_seconds

        return {
            "provider": resolved_provider,
            "model": resolved_model,
            "api_key": resolved_api_key,
            "base_url": resolved_base_url,
            "history_window": resolved_history_window,
            "max_tokens": resolved_max_tokens,
            "temperature": resolved_temperature,
            "timeout_seconds": resolved_timeout_seconds,
            "session_store": ProviderScopedSessionStore(resolved_provider, self._session_store),
        }

    def _load_defaults(self, env_path: Path) -> LLMClientConfig:
        """Load and validate default LLM settings from the configured .env file."""
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
        """Normalize a provider name and validate it against registered adapters."""
        provider = str(value or "").strip()
        if not provider:
            return provider

        available = set(LLMAdapterFactory.available_types())
        if provider not in available:
            raise LLMConfigError(f"Unsupported llm provider: {provider}. Available: {sorted(available)}")
        return provider

    def _coerce_bounded_int(self, value: int | str, key: str, *, max_value: int) -> int:
        """Parse an integer config value and enforce a positive upper bound."""
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise LLMConfigError(f"{key} must be an integer") from exc
        if parsed <= 0:
            raise LLMConfigError(f"{key} must be greater than zero")
        if parsed > max_value:
            raise LLMConfigError(f"{key} must be less than or equal to {max_value}")
        return parsed

    def _log_request(self, *, method: str, runtime_config: dict[str, Any], payload: dict[str, Any]) -> None:
        """Emit a debug log for one outbound LLM request when logging is enabled."""
        if self._logger is None or not hasattr(self._logger, "debug"):
            return
        self._logger.debug(
            "[llm] llm_request method=%s provider=%s model=%s payload=%s",
            method,
            runtime_config["provider"],
            runtime_config["model"],
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )

    def _log_response(self, *, method: str, runtime_config: dict[str, Any], response: LLMResponse) -> None:
        """Emit a debug log for one inbound LLM response when logging is enabled."""
        if self._logger is None or not hasattr(self._logger, "debug"):
            return
        self._logger.debug(
            "[llm] llm_response method=%s provider=%s model=%s payload=%s",
            method,
            runtime_config["provider"],
            response.model,
            json.dumps(
                {
                    "content": response.content,
                    "usage": response.usage,
                    "session_id": response.session_id,
                    "metadata_keys": sorted(response.metadata.keys()),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
