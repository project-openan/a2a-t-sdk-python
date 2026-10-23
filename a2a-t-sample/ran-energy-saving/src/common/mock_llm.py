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
``slots`` + ``slot_errors``           Task-T slot extraction — the ``slot_extraction``
                                      complete variant (default scenario input or from-data
                                      regeneration), the ``slot_extraction_missing`` variant for
                                      the missing-region scenario input, the
                                      ``slot_extraction_target`` / ``slot_extraction_feasibility``
                                      variants for the target/feasibility negotiation inputs
                                      (complete slot sets that preserve the distinguishing
                                      overlapping-segment / outage values in the rendered prompt),
                                      and the ``*_refill`` variants for the from-data refill carrying
                                      the accepted target / site-level context; told apart by the
                                      scenario natural-language constants of
                                      ``client.scenario_data`` and the refill values
``semantic_verdict`` property         content semantic validation — by the scenario
                                      natural-language input, the distinguishing
                                      target/feasibility values of the rendered prompt (carried in
                                      the message head cut at the template-content labels), or the
                                      refill values (``content_validation_target`` /
                                      ``content_validation_feasibility`` /
                                      ``content_validation_target_refill`` /
                                      ``content_validation_feasibility_refill`` /
                                      ``content_validation`` / ``content_validation_missing``),
                                      falling back to the v1 region display-string heuristic for
                                      heads without either
negotiation extraction signatures     Negotiation-T extraction (most-specific property set
                                      first, per ``build_extraction_schema``):
                                      ``{conclusion, feasibility_summary}`` ->
                                      ``negotiation_feasibility_accept_extraction``,
                                      ``{conclusion, confirmed_intent, failure_reason}`` ->
                                      ``negotiation_target_accept_extraction``,
                                      ``{conclusion, items}`` ->
                                      ``negotiation_accept_extraction``,
                                      ``{feasibility_negotiation_description, action}`` ->
                                      ``negotiation_feasibility_propose_extraction``,
                                      ``{target_negotiation_description}`` ->
                                      ``negotiation_target_propose_extraction``,
                                      ``{items, relationship}`` ->
                                      ``negotiation_propose_extraction``
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
from client.scenario_data import (
    NATURAL_LANGUAGE_PROMPT_INPUT_EN,
    NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_EN,
    NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_ZH,
    NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN,
    NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_ZH,
    NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN,
    NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_ZH,
    NATURAL_LANGUAGE_PROMPT_INPUT_ZH,
    NEGOTIATION_REGION_STRINGS,
)
from dotenv import dotenv_values

_RESOURCES_DIR = Path(__file__).resolve().parents[2] / "resources" / "mock_llm"

#: Routed stage names (``llm_calls`` entries). The complete variants keep their historical names.
STAGE_SLOT_EXTRACTION = "slot_extraction"
STAGE_SLOT_EXTRACTION_MISSING = "slot_extraction_missing"
STAGE_SLOT_EXTRACTION_TARGET = "slot_extraction_target"
STAGE_SLOT_EXTRACTION_FEASIBILITY = "slot_extraction_feasibility"
STAGE_SLOT_EXTRACTION_TARGET_REFILL = "slot_extraction_target_refill"
STAGE_SLOT_EXTRACTION_FEASIBILITY_REFILL = "slot_extraction_feasibility_refill"
STAGE_CONTENT_VALIDATION = "content_validation"
STAGE_CONTENT_VALIDATION_MISSING = "content_validation_missing"
STAGE_CONTENT_VALIDATION_TARGET = "content_validation_target"
STAGE_CONTENT_VALIDATION_FEASIBILITY = "content_validation_feasibility"
STAGE_CONTENT_VALIDATION_TARGET_REFILL = "content_validation_target_refill"
STAGE_CONTENT_VALIDATION_FEASIBILITY_REFILL = "content_validation_feasibility_refill"
STAGE_NEGOTIATION_PROPOSE_EXTRACTION = "negotiation_propose_extraction"
STAGE_NEGOTIATION_ACCEPT_EXTRACTION = "negotiation_accept_extraction"
STAGE_NEGOTIATION_TARGET_PROPOSE_EXTRACTION = "negotiation_target_propose_extraction"
STAGE_NEGOTIATION_TARGET_ACCEPT_EXTRACTION = "negotiation_target_accept_extraction"
STAGE_NEGOTIATION_FEASIBILITY_PROPOSE_EXTRACTION = "negotiation_feasibility_propose_extraction"
STAGE_NEGOTIATION_FEASIBILITY_ACCEPT_EXTRACTION = "negotiation_feasibility_accept_extraction"

#: Stage name -> response file name.
_STAGE_FILES: dict[str, str] = {
    STAGE_SLOT_EXTRACTION: "slot_extraction.json",
    STAGE_SLOT_EXTRACTION_MISSING: "slot_extraction_missing.json",
    STAGE_SLOT_EXTRACTION_TARGET: "slot_extraction_target.json",
    STAGE_SLOT_EXTRACTION_FEASIBILITY: "slot_extraction_feasibility.json",
    STAGE_SLOT_EXTRACTION_TARGET_REFILL: "slot_extraction_target_refill.json",
    STAGE_SLOT_EXTRACTION_FEASIBILITY_REFILL: "slot_extraction_feasibility_refill.json",
    STAGE_CONTENT_VALIDATION: "content_validation.json",
    STAGE_CONTENT_VALIDATION_MISSING: "content_validation_missing.json",
    STAGE_CONTENT_VALIDATION_TARGET: "content_validation_target.json",
    STAGE_CONTENT_VALIDATION_FEASIBILITY: "content_validation_feasibility.json",
    STAGE_CONTENT_VALIDATION_TARGET_REFILL: "content_validation_target_refill.json",
    STAGE_CONTENT_VALIDATION_FEASIBILITY_REFILL: "content_validation_feasibility_refill.json",
    STAGE_NEGOTIATION_PROPOSE_EXTRACTION: "negotiation_propose_extraction.json",
    STAGE_NEGOTIATION_ACCEPT_EXTRACTION: "negotiation_accept_extraction.json",
    STAGE_NEGOTIATION_TARGET_PROPOSE_EXTRACTION: "negotiation_target_propose_extraction.json",
    STAGE_NEGOTIATION_TARGET_ACCEPT_EXTRACTION: "negotiation_target_accept_extraction.json",
    STAGE_NEGOTIATION_FEASIBILITY_PROPOSE_EXTRACTION: "negotiation_feasibility_propose_extraction.json",
    STAGE_NEGOTIATION_FEASIBILITY_ACCEPT_EXTRACTION: "negotiation_feasibility_accept_extraction.json",
}

#: Missing-region scenario inputs that flip the slot-extraction routing to the missing variant.
_MISSING_SCENARIO_INPUTS = (
    NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN,
    NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_ZH,
)

#: Complete scenario inputs of the default scenario; checked before the missing ones because the
#: missing zh input is a substring of the complete zh input.
_COMPLETE_SCENARIO_INPUTS = (NATURAL_LANGUAGE_PROMPT_INPUT_EN, NATURAL_LANGUAGE_PROMPT_INPUT_ZH)

#: Target / feasibility negotiation inputs: complete inputs served by their own slot-extraction
#: variants, whose canned slots preserve the distinguishing values the rendered prompt needs.
_TARGET_SCENARIO_INPUTS = (NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN, NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_ZH)
_FEASIBILITY_SCENARIO_INPUTS = (
    NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_EN,
    NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_ZH,
)

#: Distinguishing values the target/feasibility rendered prompts carry (the dedicated
#: slot-extraction variants preserve them), so the content-validation routing also fires on real
#: rendered-prompt heads and not only on the scenario natural-language inputs. Each marker is a
#: value the FAILING input carries and the negotiated refill does not, so the continuation routes
#: to the complete variant: the target marker is the overlapping segment, the feasibility marker
#: the pre-proposal guaranteed rate.
_TARGET_VALIDATION_MARKER = "20:00~00:00,10Mbps"
_FEASIBILITY_VALIDATION_MARKERS = ("06:00~18:00,10Mbps",)

#: From-data Task-T refill markers. The refill data is a parameter map, so the accepted values are
#: the only distinguishing text: the corrected non-overlapping target for the target kind, and the
#: site-level outage for the feasibility kind. Checked after the natural-language scenario inputs
#: (the outage value also appears in the initial feasibility input) and before the default variant.
_TARGET_REFILL_MARKERS = ("23:00~00:00,10Mbps",)
_FEASIBILITY_REFILL_MARKERS = ("power outage from 00:00 to 18:00", "00:00~18:00 停电")

#: Negotiation-T extraction signatures routed most-specific property set first, pinned to the
#: property sets of ``a2a_t.negotiation.generation.json_schema_builder.build_extraction_schema``.
#: A bare ``{conclusion}`` signature is impossible and never routed.
_NEGOTIATION_EXTRACTION_SIGNATURES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"conclusion", "feasibility_summary"}), STAGE_NEGOTIATION_FEASIBILITY_ACCEPT_EXTRACTION),
    (frozenset({"conclusion", "confirmed_intent", "failure_reason"}), STAGE_NEGOTIATION_TARGET_ACCEPT_EXTRACTION),
    (frozenset({"conclusion", "items"}), STAGE_NEGOTIATION_ACCEPT_EXTRACTION),
    (
        frozenset({"feasibility_negotiation_description", "action"}),
        STAGE_NEGOTIATION_FEASIBILITY_PROPOSE_EXTRACTION,
    ),
    (frozenset({"target_negotiation_description"}), STAGE_NEGOTIATION_TARGET_PROPOSE_EXTRACTION),
    (frozenset({"items", "relationship"}), STAGE_NEGOTIATION_PROPOSE_EXTRACTION),
)

#: Localized labels starting the template-content block of the content validation user prompt.
_TEMPLATE_CONTENT_LABELS = ("Template Content:", "模板正文：")

#: Placeholder API key injected while the mock is installed (no network access happens).
_PLACEHOLDER_API_KEY = "mock-key-not-real"

#: Language the canned script was loaded for (region-string routing reads it).
_script_language = "zh-CN"

#: Routed stage name of every served structured call (test hook).
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
    """Clear the routed stage log (test hook)."""
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


def _head_before_markers(content: str, markers: tuple[str, ...]) -> str:
    """Return the leading part of one message, cut at the earliest of the given markers.

    The input block of every routed user message precedes the template content, so the head
    carries the caller input alone — a region display string inside a template example can never
    leak from the template content into the head.
    """
    cut = len(content)
    for marker in markers:
        index = content.find(marker)
        if 0 <= index < cut:
            cut = index
    return content[:cut]


def _first_user_content(messages: list[dict[str, str]]) -> str:
    """Return the content of the first user message of one structured call."""
    return next(message["content"] for message in messages if message.get("role") == "user")


def route_structured_call(
    messages: list[dict[str, str]],
    json_schema: dict[str, Any] | None = None,
) -> str:
    """Return the stage name one structured call is routed to, keyed on its output schema.

    The schema signature picks the stage family: slot extraction by the ``slots``/``slot_errors``
    properties, content validation by ``semantic_verdict``, and the Negotiation-T extraction
    stages by the most-specific matching signature of
    ``a2a_t.negotiation.generation.json_schema_builder.build_extraction_schema``. The scenario
    variant is told apart from the user message: slot extraction by the scenario natural-language
    input it carries, content validation by the scenario input — or the distinguishing
    target/feasibility values of a rendered prompt — in the message head (cut at the
    template-content labels, so template examples never flip the variant), with the v1 region
    display-string heuristic as the fallback for heads without either.

    Raises:
        ValueError: when no signature matches the call (an unmapped SDK call, including the
            impossible bare-``conclusion`` negotiation signature).
    """
    user_content = _first_user_content(messages)
    properties = json_schema.get("properties") if isinstance(json_schema, dict) else None
    property_names = set(properties) if isinstance(properties, dict) else set()

    if "slots" in property_names and "slot_errors" in property_names:
        if any(scenario_input in user_content for scenario_input in _COMPLETE_SCENARIO_INPUTS):
            return STAGE_SLOT_EXTRACTION
        if any(scenario_input in user_content for scenario_input in _MISSING_SCENARIO_INPUTS):
            return STAGE_SLOT_EXTRACTION_MISSING
        if any(scenario_input in user_content for scenario_input in _TARGET_SCENARIO_INPUTS):
            return STAGE_SLOT_EXTRACTION_TARGET
        if any(scenario_input in user_content for scenario_input in _FEASIBILITY_SCENARIO_INPUTS):
            return STAGE_SLOT_EXTRACTION_FEASIBILITY
        if any(marker in user_content for marker in _FEASIBILITY_REFILL_MARKERS):
            return STAGE_SLOT_EXTRACTION_FEASIBILITY_REFILL
        if any(marker in user_content for marker in _TARGET_REFILL_MARKERS):
            return STAGE_SLOT_EXTRACTION_TARGET_REFILL
        return STAGE_SLOT_EXTRACTION
    if "semantic_verdict" in property_names:
        head = _head_before_markers(user_content, _TEMPLATE_CONTENT_LABELS)
        if _is_target_validation_head(head):
            return STAGE_CONTENT_VALIDATION_TARGET
        if _is_feasibility_validation_head(head):
            return STAGE_CONTENT_VALIDATION_FEASIBILITY
        if any(marker in head for marker in _FEASIBILITY_REFILL_MARKERS):
            return STAGE_CONTENT_VALIDATION_FEASIBILITY_REFILL
        if any(marker in head for marker in _TARGET_REFILL_MARKERS):
            return STAGE_CONTENT_VALIDATION_TARGET_REFILL
        if any(scenario_input in head for scenario_input in _COMPLETE_SCENARIO_INPUTS):
            return STAGE_CONTENT_VALIDATION
        if any(scenario_input in head for scenario_input in _MISSING_SCENARIO_INPUTS):
            return STAGE_CONTENT_VALIDATION_MISSING
        region_string = NEGOTIATION_REGION_STRINGS.get(_script_language, "")
        if region_string and region_string not in head:
            return STAGE_CONTENT_VALIDATION_MISSING
        return STAGE_CONTENT_VALIDATION
    for signature, stage in _NEGOTIATION_EXTRACTION_SIGNATURES:
        if signature <= property_names:
            return stage
    raise ValueError(f"The scripted mock LLM cannot route this structured call: {sorted(property_names)}")


def _is_target_validation_head(head: str) -> bool:
    """Report whether one content-validation head carries the target negotiation input."""
    return _TARGET_VALIDATION_MARKER in head or any(
        scenario_input in head for scenario_input in _TARGET_SCENARIO_INPUTS
    )


def _is_feasibility_validation_head(head: str) -> bool:
    """Report whether one content-validation head carries the feasibility negotiation input."""
    return all(marker in head for marker in _FEASIBILITY_VALIDATION_MARKERS) or any(
        scenario_input in head for scenario_input in _FEASIBILITY_SCENARIO_INPUTS
    )


#: Sentence the information-propose input carries when more than one item is missing; the canned
#: propose-extraction response has no relationship by default, so the mock re-adds it here to keep
#: the from-text propose equivalent to the content the caller wrote.
_PROPOSE_RELATIONSHIP_MARKER = "Multiple items must be provided together."


def get_routed_response(
    messages: list[dict[str, str]],
    json_schema: dict[str, Any] | None,
) -> LLMResponse:
    """Serve the canned response routed by the output schema of one structured call."""
    stage = route_structured_call(messages, json_schema)
    llm_calls.append(stage)
    content = _script[stage]
    if stage == STAGE_NEGOTIATION_PROPOSE_EXTRACTION and _PROPOSE_RELATIONSHIP_MARKER in _first_user_content(
        messages
    ):
        extracted = json.loads(content)
        extracted["relationship"] = _PROPOSE_RELATIONSHIP_MARKER
        content = json.dumps(extracted, ensure_ascii=False)
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
    global _mock_enabled, _script, _script_language
    reset_call_log()
    _script_language = resolve_language(env_path=env_path)
    _script = _load_script(_script_language)
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
