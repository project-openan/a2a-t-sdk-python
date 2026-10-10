"""Scripted mock LLM for the offline negotiation demo.

The demo runs fully offline: when the ``.env`` carries no usable LLM API key, this module patches
the SDK seams the same way the fault-management sample does —

* ``DotEnvConfigSource.load`` tolerates a missing ``.env``, injects a placeholder API key (so the
  OpenAI client constructs without network access) and defaults the prompt source type to the
  packaged resource tree;
* ``OpenAIClient.structured`` serves canned responses from ``resources/mock_responses/`` instead of
  calling the LLM API.

The canned response is selected per call by routing on the **output schema signature** of the
structured call (language-neutral, because the user prompts are localized) and, for the Task-T
stages, on the filled-params marker inside the input block of the user message:

=====================================  =======================================
schema signature                       served response
=====================================  =======================================
``slots`` + ``slot_errors`` properties  Task-T slot extraction (missing/filled
                                       variant, told apart by the marker)
``semantic_verdict`` property           content_validation semantic validation
                                       (missing/filled variant)
``conclusion`` property                negotiation ending extraction (accept)
``items`` property                     negotiation propose extraction
=====================================  =======================================

Every call is recorded in :data:`llm_calls` (the routed stage name), so the demo tests can assert
the exact LLM call count of each flow — the from-data negotiation generation itself makes no LLM
call at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from a2a_t.llm.models import LLMResponse
from dotenv import dotenv_values

from .scenario_data import FILLED_PARAMS_MARKER, resolve_language

_RESOURCES_DIR = Path(__file__).resolve().parents[3] / "resources" / "mock_responses"

#: Scripted response file names of the routed stages.
_SLOT_EXTRACTION_MISSING = "slot_extraction_missing.json"
_SLOT_EXTRACTION_FILLED = "slot_extraction_filled.json"
_CONTENT_VALIDATION_MISSING = "content_validation_missing.json"
_CONTENT_VALIDATION_FILLED = "content_validation_filled.json"
_NEGOTIATION_PROPOSE_EXTRACTION = "negotiation_propose_extraction.json"
_NEGOTIATION_ACCEPT_EXTRACTION = "negotiation_accept_extraction.json"

#: Placeholder API key injected while the mock is installed (no network access happens).
_PLACEHOLDER_API_KEY = "mock-key-not-real"

#: Stage names recorded in :data:`llm_calls`.
STAGE_SLOT_EXTRACTION_MISSING = "slot_extraction_missing"
STAGE_SLOT_EXTRACTION_FILLED = "slot_extraction_filled"
STAGE_CONTENT_VALIDATION_MISSING = "content_validation_missing"
STAGE_CONTENT_VALIDATION_FILLED = "content_validation_filled"
STAGE_NEGOTIATION_PROPOSE_EXTRACTION = "negotiation_propose_extraction"
STAGE_NEGOTIATION_ACCEPT_EXTRACTION = "negotiation_accept_extraction"

#: Localized labels starting the template-content block of the content validation user prompt.
_TEMPLATE_CONTENT_LABELS = ("Template Content:", "模板正文：")

#: Routed stage name of every served structured call (test seam).
llm_calls: list[str] = []

_mock_enabled = False


def is_mock_needed(*, env_path: Path | None = None) -> bool:
    """Check whether the LLM API key is missing/empty and the mock fallback is needed."""
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    return not str(values.get("A2AT_LLM_API_KEY", "")).strip()


def is_mock_enabled() -> bool:
    """Return ``True`` once the scripted mock LLM has been installed."""
    return _mock_enabled


def reset_call_log() -> None:
    """Clear the routed stage log (test seam)."""
    llm_calls.clear()


def _load_script(language: str) -> dict[str, str]:
    """Load the scripted responses of one language as JSON strings."""
    language_dir = _RESOURCES_DIR / language
    if not language_dir.exists():
        raise FileNotFoundError(f"Mock responses not found for language: {language} (expected at {language_dir})")
    script: dict[str, str] = {}
    for file_name in (
        _SLOT_EXTRACTION_MISSING,
        _SLOT_EXTRACTION_FILLED,
        _CONTENT_VALIDATION_MISSING,
        _CONTENT_VALIDATION_FILLED,
        _NEGOTIATION_PROPOSE_EXTRACTION,
        _NEGOTIATION_ACCEPT_EXTRACTION,
    ):
        with (language_dir / file_name).open(encoding="utf-8") as file:
            script[file_name] = json.dumps(json.load(file), ensure_ascii=False)
    return script


def route_structured_call(
    messages: list[dict[str, str]],
    json_schema: dict[str, Any] | None = None,
) -> str:
    """Return the stage name one structured call is routed to.

    The routing key is the output schema signature of the call (language-neutral); the Task-T
    stages additionally tell the missing-params and filled-params variants apart by the
    filled-params marker inside the input block of the user message.

    Raises:
        ValueError: when no signature matches the call (an unmapped SDK call).
    """
    user_content = next(message["content"] for message in messages if message.get("role") == "user")
    properties = json_schema.get("properties") if isinstance(json_schema, dict) else None
    property_names = set(properties) if isinstance(properties, dict) else set()

    if "slots" in property_names and "slot_errors" in property_names:
        input_head = _head_before_markers(user_content, ("\n\n[slots]",))
        return (
            STAGE_SLOT_EXTRACTION_FILLED
            if FILLED_PARAMS_MARKER in input_head
            else STAGE_SLOT_EXTRACTION_MISSING
        )
    if "semantic_verdict" in property_names:
        input_head = _head_before_markers(user_content, _TEMPLATE_CONTENT_LABELS)
        return (
            STAGE_CONTENT_VALIDATION_FILLED
            if FILLED_PARAMS_MARKER in input_head
            else STAGE_CONTENT_VALIDATION_MISSING
        )
    if "conclusion" in property_names:
        return STAGE_NEGOTIATION_ACCEPT_EXTRACTION
    if "items" in property_names:
        return STAGE_NEGOTIATION_PROPOSE_EXTRACTION
    raise ValueError(f"The scripted mock LLM cannot route this structured call: {sorted(property_names)}")


def _head_before_markers(content: str, markers: tuple[str, ...]) -> str:
    """Return the leading part of one message, cut at the earliest of the given markers.

    The input block of every routed user message precedes the slot definitions or the template
    content, so the head carries the caller input alone — the filled-params marker can never leak
    from a template example or a slot description into the head.
    """
    cut = len(content)
    for marker in markers:
        index = content.find(marker)
        if 0 <= index < cut:
            cut = index
    return content[:cut]


def install_mock_llm(
    *,
    env_path: Path | None = None,
    language: str | None = None,
    force_language: bool = False,
) -> None:
    """Install the scripted mock LLM responses and the offline config-source patch.

    Args:
        env_path: optional ``.env`` file path the language is resolved from.
        language: explicit script language; ``None`` resolves it from the ``.env`` file.
        force_language: force the language into the patched config values even when the ``.env``
            file declares a different one (the demo's ``--language`` override).
    """
    global _mock_enabled
    resolved_language = language or resolve_language(env_path=env_path)
    script = _load_script(resolved_language)
    _stage_to_file = {
        STAGE_SLOT_EXTRACTION_MISSING: _SLOT_EXTRACTION_MISSING,
        STAGE_SLOT_EXTRACTION_FILLED: _SLOT_EXTRACTION_FILLED,
        STAGE_CONTENT_VALIDATION_MISSING: _CONTENT_VALIDATION_MISSING,
        STAGE_CONTENT_VALIDATION_FILLED: _CONTENT_VALIDATION_FILLED,
        STAGE_NEGOTIATION_PROPOSE_EXTRACTION: _NEGOTIATION_PROPOSE_EXTRACTION,
        STAGE_NEGOTIATION_ACCEPT_EXTRACTION: _NEGOTIATION_ACCEPT_EXTRACTION,
    }
    reset_call_log()
    _mock_enabled = True

    from a2a_t.config.source import DotEnvConfigSource
    from a2a_t.llm.providers.openai import OpenAIClient

    original_source_load = DotEnvConfigSource.load

    def _patched_source_load(path: Path) -> dict[str, str]:
        values: dict[str, str] = dict(original_source_load(path)) if path.exists() else {}
        if not str(values.get("A2AT_LLM_API_KEY", "")).strip():
            values["A2AT_LLM_API_KEY"] = _PLACEHOLDER_API_KEY
        values.setdefault("A2AT_LLM_PROVIDER", "openai")
        values.setdefault("A2AT_LLM_MODEL", "mock-llm")
        values.setdefault("A2AT_PROMPT_SOURCE_TYPE", "packaged")
        if force_language:
            values["A2AT_LANGUAGE"] = resolved_language
        else:
            values.setdefault("A2AT_LANGUAGE", resolved_language)
        return values

    def _patched_structured(
        self: OpenAIClient,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        stage = route_structured_call(messages, json_schema)
        llm_calls.append(stage)
        return LLMResponse(
            content=script[_stage_to_file[stage]],
            model="mock-llm",
            usage={"prompt_tokens": 0, "completion_tokens": 0},
            metadata={},
        )

    DotEnvConfigSource.load = staticmethod(_patched_source_load)  # type: ignore[method-assign]
    OpenAIClient.structured = _patched_structured  # type: ignore[method-assign]


def install_mock_llm_if_needed(
    *,
    env_path: Path | None = None,
    language: str | None = None,
    force_language: bool = False,
) -> bool:
    """Install the scripted mock LLM when the API key is missing; returns whether it was installed."""
    if not is_mock_needed(env_path=env_path):
        return False
    install_mock_llm(env_path=env_path, language=language, force_language=force_language)
    print("[mock-llm] A2AT_LLM_API_KEY not set, using scripted mock LLM responses")
    return True
