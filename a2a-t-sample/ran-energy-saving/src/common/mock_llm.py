"""Schema-routed scripted mock LLM for the offline Task-T RAN energy saving demo.

In this demo the client calls
``generate_task_prompt_from_text`` (template-directed slot extraction) and the server calls
``validate_task_prompt_and_data_filling`` (content semantic validation). Both steps issue
structured LLM calls, so the mock routes every call by the **output schema signature** instead
of relying on call order — the two processes (client / server) each start their own call
sequence at zero, so a sequenced mock would serve the wrong response.

=====================================  ==========================================
schema signature                      served response
=====================================  ==========================================
``slots`` + ``slot_errors``           Task-T slot extraction
``semantic_verdict`` property         content semantic validation
=====================================  ==========================================

The response language is read from ``A2AT_LANGUAGE`` in the ``.env`` file (``zh-CN`` / ``en-US``);
both language trees live under ``resources/mock_llm/``. When ``A2AT_LLM_API_KEY`` is empty
the mock injects a placeholder API key so the OpenAI client can be constructed without network
access. A missing ``.env`` is a hard error (copy ``env.example`` to ``.env`` first).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from a2a_t.config.errors import ConfigFileNotFoundError
from a2a_t.llm.models import LLMResponse
from dotenv import dotenv_values

_RESOURCES_DIR = Path(__file__).resolve().parents[2] / "resources" / "mock_llm"

#: Stage name -> response file name.
_STAGE_FILES: dict[str, str] = {
    "slot_extraction": "slot_extraction.json",
    "content_validation": "content_validation.json",
}

#: Placeholder API key injected while the mock is installed (no network access happens).
_PLACEHOLDER_API_KEY = "mock-key-not-real"

#: Routed stage name of every served structured call (test seam).
llm_calls: list[str] = []

_mock_enabled = False
_script: dict[str, str] = {}


def resolve_language(*, env_path: Path | None = None) -> str:
    """Resolve the configured ``A2AT_LANGUAGE`` (defaults to ``zh-CN``).

    Raises:
        ConfigFileNotFoundError: when the resolved ``.env`` file does not exist.
    """
    resolved = env_path or Path.cwd() / ".env"
    if not resolved.exists():
        raise ConfigFileNotFoundError(resolved)
    values = dotenv_values(resolved)
    return str(values.get("A2AT_LANGUAGE", "")).strip() or "zh-CN"


def is_mock_needed(*, env_path: Path | None = None) -> bool:
    """Check whether the LLM API key is missing/empty and the mock fallback is needed."""
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    return not str(values.get("A2AT_LLM_API_KEY", "")).strip()


def is_mock_enabled() -> bool:
    """Return ``True`` once the mock LLM has been installed."""
    return _mock_enabled


def reset_call_log() -> None:
    """Clear the routed stage log (test seam)."""
    llm_calls.clear()


def _load_script(language: str) -> dict[str, str]:
    language_dir = _RESOURCES_DIR / language
    if not language_dir.exists():
        raise FileNotFoundError(f"Mock responses not found for language: {language} (expected at {language_dir})")
    script: dict[str, str] = {}
    for stage, file_name in _STAGE_FILES.items():
        with (language_dir / file_name).open(encoding="utf-8") as handle:
            script[stage] = json.dumps(json.load(handle), ensure_ascii=False)
    return script


def route_structured_call(
    messages: list[dict[str, str]],
    json_schema: dict[str, Any] | None = None,
) -> str:
    """Return the stage name one structured call is routed to, keyed on its output schema."""
    properties = json_schema.get("properties") if isinstance(json_schema, dict) else None
    property_names = set(properties) if isinstance(properties, dict) else set()

    if "slots" in property_names and "slot_errors" in property_names:
        return "slot_extraction"
    if "semantic_verdict" in property_names:
        return "content_validation"
    raise ValueError(f"The scripted mock LLM cannot route this structured call: {sorted(property_names)}")


def get_routed_response(
    messages: list[dict[str, str]],
    json_schema: dict[str, Any] | None,
) -> LLMResponse:
    """Serve the canned response routed by the output schema of one structured call."""
    stage = route_structured_call(messages, json_schema)
    llm_calls.append(stage)
    return LLMResponse(
        content=_script[stage],
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
    """Build the payload shape logged for a mock request."""
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
    """Load the canned responses of the configured language and patch the config source offline.

    Raises:
        ConfigFileNotFoundError: when the ``.env`` file does not exist.
        FileNotFoundError: when the response tree of the configured language does not exist.
    """
    global _mock_enabled, _script
    reset_call_log()
    _script = _load_script(resolve_language(env_path=env_path))
    _mock_enabled = True

    from a2a_t.config.source import DotEnvConfigSource

    original_source_load = DotEnvConfigSource.load

    def _patched_source_load(path: Path) -> dict[str, str]:
        values: dict[str, str] = dict(original_source_load(path))
        if not str(values.get("A2AT_LLM_API_KEY", "")).strip():
            values["A2AT_LLM_API_KEY"] = _PLACEHOLDER_API_KEY
        return values

    DotEnvConfigSource.load = staticmethod(_patched_source_load)  # type: ignore[method-assign]


def install_mock_llm_if_needed(*, env_path: Path | None = None) -> bool:
    """Install the mock LLM when the API key is missing; returns whether it was installed."""
    if not is_mock_needed(env_path=env_path):
        return False
    install_mock_llm(env_path=env_path)
    print("[mock-llm] A2AT_LLM_API_KEY not set, using scripted mock LLM responses")
    return True
