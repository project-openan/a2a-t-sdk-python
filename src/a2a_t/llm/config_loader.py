"""Load LLM client configuration from .env files."""

from __future__ import annotations

from pathlib import Path

from a2a_t.config.errors import ConfigFileNotFoundError
from a2a_t.config.source import DotEnvConfigSource
from a2a_t.llm.errors import LLMConfigError
from a2a_t.llm.models import LLMClientConfig

_MAX_HISTORY_WINDOW = 100
_MAX_SESSION_MAX_TOTAL = 3000
_MAX_SESSION_MAX_PER_PROVIDER = 1000


def default_env_path() -> Path:
    """Return the default .env path used by LLM integrations."""
    return Path(__file__).resolve().parents[3] / "package_data" / ".env"


def coerce_optional_int(value: str | None, key: str) -> int | None:
    """Parse an optional integer environment value."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise LLMConfigError(f"{key} must be an integer") from exc


def coerce_optional_float(value: str | None, key: str) -> float | None:
    """Parse an optional float environment value."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise LLMConfigError(f"{key} must be a float") from exc


def coerce_bounded_int(value: int | str, key: str, *, max_value: int) -> int:
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


class LLMConfigLoader:
    """Load and validate default LLM settings from a .env file."""

    @classmethod
    def load(cls, env_path: Path | None = None) -> LLMClientConfig:
        resolved_env_path = env_path or default_env_path()
        try:
            values = DotEnvConfigSource.load(resolved_env_path)
        except ConfigFileNotFoundError as exc:
            raise LLMConfigError(f"LLM config file does not exist: {resolved_env_path}") from exc

        provider = str(values.get("A2AT_LLM_PROVIDER", "")).strip()
        model = str(values.get("A2AT_LLM_MODEL", "")).strip()
        api_key = str(values.get("A2AT_LLM_API_KEY", "")).strip()
        if not provider or not model:
            raise LLMConfigError("A2AT_LLM_PROVIDER and A2AT_LLM_MODEL must be set in the .env file")
        if not api_key:
            raise LLMConfigError("A2AT_LLM_API_KEY must be set in the .env file")

        history_window = coerce_bounded_int(
            values.get("A2AT_LLM_HISTORY_WINDOW", "10"),
            "A2AT_LLM_HISTORY_WINDOW",
            max_value=_MAX_HISTORY_WINDOW,
        )
        session_max_total = coerce_bounded_int(
            values.get("A2AT_LLM_SESSION_MAX_TOTAL", "300"),
            "A2AT_LLM_SESSION_MAX_TOTAL",
            max_value=_MAX_SESSION_MAX_TOTAL,
        )
        session_max_per_provider = coerce_bounded_int(
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
            api_key=api_key,
            base_url=str(values.get("A2AT_LLM_BASE_URL", "")).strip() or None,
            history_window=history_window,
            max_tokens=coerce_optional_int(values.get("A2AT_LLM_MAX_TOKENS"), "A2AT_LLM_MAX_TOKENS"),
            temperature=coerce_optional_float(values.get("A2AT_LLM_TEMPERATURE"), "A2AT_LLM_TEMPERATURE"),
            timeout_seconds=coerce_optional_float(
                values.get("A2AT_LLM_TIMEOUT_SECONDS"),
                "A2AT_LLM_TIMEOUT_SECONDS",
            ),
            session_max_total=session_max_total,
            session_max_per_provider=session_max_per_provider,
        )
