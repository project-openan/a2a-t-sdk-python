"""Mock LLM data provider for e2e tests when no real API key is available.

Only patches DotEnvConfigSource.load to inject a placeholder api_key (so
A2ATClient construction does not fail) and exposes is_mock_enabled() /
get_mock_response() for llm_logger to call.

Mock response files live under resources/mock_responses/.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from a2a_t.llm.models import LLMResponse
from dotenv import dotenv_values

_RESOURCES_DIR = Path(__file__).resolve().parents[2] / "resources" / "mock_responses"

_MOCK_RESPONSE_FILES = [
    "scenario_recognition.json",
    "slot_extraction.json",
    "semantic_validation.json",
]

_MOCK_RESPONSES: list[str] = []
_call_index = 0
_mock_enabled = False


def _resolve_language(*, env_path: Path | None = None) -> str:
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    return str(values.get("A2AT_LANGUAGE", "")).strip() or "zh-CN"


def _load_mock_responses(*, env_path: Path | None = None) -> list[str]:
    language = _resolve_language(env_path=env_path)
    language_dir = _RESOURCES_DIR / language
    if not language_dir.exists():
        raise FileNotFoundError(f"Mock responses not found for language: {language} (expected at {language_dir})")
    loaded: list[str] = []
    for filename in _MOCK_RESPONSE_FILES:
        file_path = language_dir / filename
        with file_path.open(encoding="utf-8") as f:
            loaded.append(json.dumps(json.load(f), ensure_ascii=False))
    return loaded


def is_mock_needed(*, env_path: Path | None = None) -> bool:
    """Check whether the LLM API key is missing/empty and mock fallback is needed."""
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    return not str(values.get("A2AT_LLM_API_KEY", "")).strip()


def is_mock_enabled() -> bool:
    """Return True if mock LLM has been installed (via install_mock_llm)."""
    return _mock_enabled


def get_mock_response() -> LLMResponse:
    """Return the next sequenced mock LLMResponse (cycles through scenario/slot/semantic)."""
    global _call_index
    content = _MOCK_RESPONSES[_call_index % len(_MOCK_RESPONSES)]
    _call_index += 1
    return LLMResponse(
        content=content,
        model="mock-llm",
        usage={"prompt_tokens": 0, "completion_tokens": 0},
        metadata={},
    )


def get_mock_payload(
    *,
    messages: list[dict[str, str]],
    json_schema: dict[str, Any],
    temperature: float | None,
    max_tokens: int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "mock-llm",
        "messages": messages,
        "json_schema": json_schema,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return payload


def install_mock_llm(*, env_path: Path | None = None) -> None:
    """Load mock responses and patch DotEnvConfigSource.load to inject a placeholder API key."""
    global _call_index, _MOCK_RESPONSES, _mock_enabled
    _call_index = 0
    _MOCK_RESPONSES = _load_mock_responses(env_path=env_path)
    _mock_enabled = True

    from a2a_t.config.source import DotEnvConfigSource

    _original_source_load = DotEnvConfigSource.load

    def _patched_source_load(path: Path) -> dict[str, str]:
        values = dict(_original_source_load(path))
        if not str(values.get("A2AT_LLM_API_KEY", "")).strip():
            values["A2AT_LLM_API_KEY"] = "mock-key-not-real"
        return values

    DotEnvConfigSource.load = staticmethod(_patched_source_load)  # type: ignore[method-assign]


def install_mock_llm_if_needed(*, env_path: Path | None = None) -> bool:
    """Install mock LLM if the API key is missing; returns True if mock was installed."""
    if not is_mock_needed(env_path=env_path):
        return False
    install_mock_llm(env_path=env_path)
    print("[mock-llm] A2AT_LLM_API_KEY not set, using mock LLM responses for e2e")
    return True
