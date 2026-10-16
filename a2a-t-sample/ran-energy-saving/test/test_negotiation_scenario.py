"""Offline tests of the negotiation scenario plumbing of the ran-energy-saving demo.

Covers the scripted mock LLM's variant routing (slot extraction by the scenario input with
dedicated target/feasibility variants preserving the distinguishing values, content validation
4-way by the scenario input or the rendered distinguishing values with the region display-string
fallback, Negotiation-T extraction by the SDK schema signature), the scenario data of the
``negotiation`` variant (missing-region inputs, filled params, accept answers) and of the
target/feasibility negotiation kinds (complete inputs, per-type accessors), the pinned per-type
call sequences, and the client-side feasibility of generating the missing-region and
negotiation-kind task prompts with the A2A-T SDK.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from a2a_t.core.metadata import NegotiationPerformative
from a2a_t.negotiation.content.enums import NegotiationType
from a2a_t.negotiation.generation.json_schema_builder import build_extraction_schema

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
RESOURCES_DIR = PROJECT_ROOT / "resources" / "mock_llm"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from client import scenario_data
from client.scenario_data import (
    CHANNEL_DATA,
    CHANNEL_ENV_KEY,
    CHANNEL_TEXT,
    ENERGY_SAVING_TEMPLATE_URI,
    NATURAL_LANGUAGE_PROMPT_INPUT_EN,
    NATURAL_LANGUAGE_PROMPT_INPUT_ZH,
    NEGOTIATION_INPUT_KEYS,
    NEGOTIATION_KIND_ENV_KEY,
    NEGOTIATION_REGION_STRINGS,
    SCENARIO_DEFAULT,
    SCENARIO_ENV_KEY,
    SCENARIO_NEGOTIATION,
    SUPPORTED_CHANNELS,
    SUPPORTED_NEGOTIATION_KINDS,
    SUPPORTED_SCENARIOS,
    build_prompt_input,
    negotiation_answers,
    negotiation_feasibility_contents,
    negotiation_feasibility_description,
    negotiation_feasibility_summary,
    negotiation_filled_params,
    negotiation_target_confirm_request,
    negotiation_target_confirmation,
    negotiation_target_description,
    resolve_channel,
    resolve_negotiation_kind,
    resolve_scenario,
)
from common import mock_llm
from common.llm_logger import install_llm_logger
from common.mock_llm import llm_calls, route_structured_call

#: An offline ``.env`` (no API key) written per test; the mock force-installs against it.
_OFFLINE_ENV_TEXT = "A2AT_LANGUAGE={language}\nA2AT_LLM_PROVIDER=openai\nA2AT_LLM_MODEL=mock-llm\nA2AT_LLM_API_KEY=\n"

#: The region slot of each language's packaged slot schema (the slot the negotiation asks for).
_REGION_SLOT_NAMES = {"en-US": "task_object", "zh-CN": "任务对象"}

#: Region placeholder of the missing-variant canned response per language. The packaged slot
#: schema requires the region slot, so the non-blank placeholder keeps the real client
#: generation path working while the region is genuinely unknown; it carries no region display
#: string, so the content-validation routing is unaffected.
_REGION_PLACEHOLDERS = {"en-US": "(to be provided)", "zh-CN": "（待提供）"}


def _write_offline_env(directory: Path, language: str) -> Path:
    """Write the offline ``.env`` of one language and return its path."""
    env_path = directory / ".env"
    env_path.write_text(_OFFLINE_ENV_TEXT.format(language=language), encoding="utf-8")
    return env_path


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Force-install the scripted mock LLM (en-US) for every test, regardless of any real ``.env``."""
    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.delenv(SCENARIO_ENV_KEY, raising=False)
    monkeypatch.delenv(CHANNEL_ENV_KEY, raising=False)
    monkeypatch.delenv(NEGOTIATION_KIND_ENV_KEY, raising=False)
    env_path = _write_offline_env(tmp_path, "en-US")
    mock_llm.install_mock_llm(env_path=env_path)
    install_llm_logger(role="test", verbose=False)
    return env_path


def _slot_extraction_schema() -> dict[str, object]:
    """Output-schema signature of a Task-T slot extraction call."""
    return {
        "type": "object",
        "properties": {"slots": {"type": "object"}, "slot_errors": {"type": "array"}},
    }


def _content_validation_schema() -> dict[str, object]:
    """Output-schema signature of a content semantic validation call."""
    return {
        "type": "object",
        "properties": {
            "semantic_verdict": {"type": "boolean"},
            "errors": {"type": "array"},
            "params": {"type": "object"},
        },
    }


def _slot_extraction_messages(user_input: str) -> list[dict[str, str]]:
    """User-message layout of a real slot extraction call (``[input]`` block + ``[slots]`` tail)."""
    return [
        {"role": "system", "content": "slot extraction system prompt"},
        {
            "role": "user",
            "content": (
                "[user_prompt]\ninstruction text\n\n"
                f"[scenario_code]\n{ENERGY_SAVING_TEMPLATE_URI}\n\n"
                "[language]\nen-US\n\n"
                f"[input]\n{user_input}\n\n"
                '[slots]\n- name: task_object\n  required: true\n  example: Area: "'
                + NEGOTIATION_REGION_STRINGS["en-US"]
                + '"'
            ),
        },
    ]


def _content_validation_messages(
    prompt_text: str, template_content: str, *, template_content_label: str = "Template Content: "
) -> list[dict[str, str]]:
    """User-message layout of a real content validation call (head + template-content tail).

    ``template_content_label`` is the localized label starting the template-content block; the
    real zh-CN SDK template uses the full-width ``模板正文：`` with no trailing space.
    """
    return [
        {"role": "system", "content": "content validation system prompt"},
        {
            "role": "user",
            "content": (
                "Extension Name: Task-T\n"
                f"Input Content: {prompt_text}\n"
                f"Template URI: {ENERGY_SAVING_TEMPLATE_URI}\n"
                f"{template_content_label}{template_content}\n"
                "Parameter Schema: {}"
            ),
        },
    ]


#: Template tail carrying the region example, as the packaged ran-energy-saving template does.
_REGION_TEMPLATE_TAIL = (
    "## Task Object\n\nRequirement:\n\n1. Area name: (required)\n    - Must be a concrete and "
    f'physically real community name such as "{NEGOTIATION_REGION_STRINGS["en-US"]}"'
)

#: Collapse-rendered round-1 prompt: the Task Object slot carries the region placeholder, so the
#: rendered text carries no region display string.
_MISSING_REGION_PROMPT = (
    f"## Operation Type\n\nCreate\n\n## Task Object\n\n{_REGION_PLACEHOLDERS['en-US']}"
    "\n\n## Task Target\n\nMaximize energy saving."
)

#: Collapse-rendered round-2 prompt with the region slot filled.
_FILLED_REGION_PROMPT = (
    f"## Operation Type\n\nCreate\n\n## Task Object\n\nThe area is the "
    f"{NEGOTIATION_REGION_STRINGS['en-US']}.\n\n## Task Target\n\nMaximize energy saving."
)


# --- mock routing --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("user_input", "expected_stage"),
    [
        (NATURAL_LANGUAGE_PROMPT_INPUT_EN, mock_llm.STAGE_SLOT_EXTRACTION),
        (NATURAL_LANGUAGE_PROMPT_INPUT_ZH, mock_llm.STAGE_SLOT_EXTRACTION),
        (scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN, mock_llm.STAGE_SLOT_EXTRACTION_MISSING),
        (scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_ZH, mock_llm.STAGE_SLOT_EXTRACTION_MISSING),
        (scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN, mock_llm.STAGE_SLOT_EXTRACTION_TARGET),
        (scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_ZH, mock_llm.STAGE_SLOT_EXTRACTION_TARGET),
        (scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_EN, mock_llm.STAGE_SLOT_EXTRACTION_FEASIBILITY),
        (scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_ZH, mock_llm.STAGE_SLOT_EXTRACTION_FEASIBILITY),
    ],
    ids=[
        "complete-en",
        "complete-zh",
        "missing-en",
        "missing-zh",
        "target-en",
        "target-zh",
        "feasibility-en",
        "feasibility-zh",
    ],
)
def test_slot_extraction_routing_selects_the_variant_by_the_scenario_input(
    user_input: str, expected_stage: str
) -> None:
    messages = _slot_extraction_messages(user_input)

    assert route_structured_call(messages, _slot_extraction_schema()) == expected_stage


def test_slot_extraction_routing_defaults_to_the_complete_variant_without_the_scenario_input() -> None:
    """A from-data extraction (e.g. the round-2 filled-params regeneration) keeps the complete variant."""
    messages = _slot_extraction_messages('{"operationType": "create", "region": "Songshanhu Administration Committee"}')

    assert route_structured_call(messages, _slot_extraction_schema()) == mock_llm.STAGE_SLOT_EXTRACTION


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_content_validation_routing_selects_the_variant_by_the_region_string(tmp_path: Path, language: str) -> None:
    mock_llm.install_mock_llm(env_path=_write_offline_env(tmp_path, language))
    region_string = NEGOTIATION_REGION_STRINGS[language]
    filled = _content_validation_messages(f"## Task Object\n\nThe area is the {region_string}.", _REGION_TEMPLATE_TAIL)
    missing = _content_validation_messages(_MISSING_REGION_PROMPT, _REGION_TEMPLATE_TAIL)

    assert route_structured_call(filled, _content_validation_schema()) == mock_llm.STAGE_CONTENT_VALIDATION
    assert route_structured_call(missing, _content_validation_schema()) == mock_llm.STAGE_CONTENT_VALIDATION_MISSING


@pytest.mark.parametrize("template_content_label", ["Template Content: ", "模板正文："], ids=["en-label", "zh-label"])
def test_content_validation_routing_ignores_the_region_string_in_the_template_tail(
    template_content_label: str,
) -> None:
    """A region example inside the template content must not flip the missing-params variant."""
    messages = _content_validation_messages(
        _MISSING_REGION_PROMPT, _REGION_TEMPLATE_TAIL, template_content_label=template_content_label
    )

    assert route_structured_call(messages, _content_validation_schema()) == mock_llm.STAGE_CONTENT_VALIDATION_MISSING


@pytest.mark.parametrize(
    ("user_input", "expected_stage"),
    [
        (scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN, mock_llm.STAGE_CONTENT_VALIDATION_TARGET),
        (scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_ZH, mock_llm.STAGE_CONTENT_VALIDATION_TARGET),
        (scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_EN, mock_llm.STAGE_CONTENT_VALIDATION_FEASIBILITY),
        (scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_ZH, mock_llm.STAGE_CONTENT_VALIDATION_FEASIBILITY),
        (NATURAL_LANGUAGE_PROMPT_INPUT_EN, mock_llm.STAGE_CONTENT_VALIDATION),
        (NATURAL_LANGUAGE_PROMPT_INPUT_ZH, mock_llm.STAGE_CONTENT_VALIDATION),
        (scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN, mock_llm.STAGE_CONTENT_VALIDATION_MISSING),
        (scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_ZH, mock_llm.STAGE_CONTENT_VALIDATION_MISSING),
    ],
    ids=[
        "target-en",
        "target-zh",
        "feasibility-en",
        "feasibility-zh",
        "complete-en",
        "complete-zh",
        "missing-en",
        "missing-zh",
    ],
)
def test_content_validation_routing_selects_the_variant_by_the_negotiation_input(
    user_input: str, expected_stage: str
) -> None:
    """The 4-way variant routing matches the known scenario NL input in the message head."""
    messages = _content_validation_messages(user_input, _REGION_TEMPLATE_TAIL)

    assert route_structured_call(messages, _content_validation_schema()) == expected_stage


#: Slot name of the throughput-context and goal sections per language (the slots whose values
#: distinguish the target/feasibility rendered prompts).
_SLOT_SECTION_NAMES = {
    "en-US": {"context": "task_context", "target": "task_target"},
    "zh-CN": {"context": "任务上下文", "target": "任务目标"},
}


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_content_validation_routing_matches_the_rendered_distinguishing_values(language: str) -> None:
    """A rendered target/feasibility prompt head routes to its variant without the NL input.

    The dedicated slot-extraction variants keep the distinguishing values, so the server's
    content validation of the real rendered prompt (never carrying the scenario NL input)
    still reaches the target/feasibility variant.
    """
    context_slot = _SLOT_SECTION_NAMES[language]["context"]
    target_slot = _SLOT_SECTION_NAMES[language]["target"]
    target_slots = _resource(language, "slot_extraction_target.json")["slots"]
    feasibility_slots = _resource(language, "slot_extraction_feasibility.json")["slots"]
    target_prompt = f"## Task Context\n\n{target_slots[context_slot]}"
    feasibility_prompt = (
        f"## Task Target\n\n{feasibility_slots[target_slot]}\n\n## Task Context\n\n{feasibility_slots[context_slot]}"
    )

    assert (
        route_structured_call(
            _content_validation_messages(str(target_prompt), _REGION_TEMPLATE_TAIL), _content_validation_schema()
        )
        == mock_llm.STAGE_CONTENT_VALIDATION_TARGET
    )
    assert (
        route_structured_call(
            _content_validation_messages(str(feasibility_prompt), _REGION_TEMPLATE_TAIL), _content_validation_schema()
        )
        == mock_llm.STAGE_CONTENT_VALIDATION_FEASIBILITY
    )


@pytest.mark.parametrize(
    ("negotiation_type", "performative", "expected_stage"),
    [
        (NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, mock_llm.STAGE_NEGOTIATION_PROPOSE_EXTRACTION),
        (NegotiationType.INFORMATION, NegotiationPerformative.ACCEPT, mock_llm.STAGE_NEGOTIATION_ACCEPT_EXTRACTION),
        (
            NegotiationType.TARGET,
            NegotiationPerformative.PROPOSE,
            mock_llm.STAGE_NEGOTIATION_TARGET_PROPOSE_EXTRACTION,
        ),
        (NegotiationType.TARGET, NegotiationPerformative.ACCEPT, mock_llm.STAGE_NEGOTIATION_TARGET_ACCEPT_EXTRACTION),
        (
            NegotiationType.FEASIBILITY,
            NegotiationPerformative.PROPOSE,
            mock_llm.STAGE_NEGOTIATION_FEASIBILITY_PROPOSE_EXTRACTION,
        ),
        (
            NegotiationType.FEASIBILITY,
            NegotiationPerformative.ACCEPT,
            mock_llm.STAGE_NEGOTIATION_FEASIBILITY_ACCEPT_EXTRACTION,
        ),
    ],
    ids=["info-propose", "info-accept", "target-propose", "target-accept", "feasibility-propose", "feasibility-accept"],
)
def test_negotiation_extraction_routing_follows_the_sdk_schema_signature(
    negotiation_type: NegotiationType, performative: NegotiationPerformative, expected_stage: str
) -> None:
    """Every Negotiation-T extraction routes by its distinct ``build_extraction_schema`` signature."""
    messages = [{"role": "user", "content": "negotiation extraction call"}]
    json_schema = build_extraction_schema(negotiation_type, performative)

    assert route_structured_call(messages, json_schema) == expected_stage


def test_route_structured_call_rejects_unmapped_calls() -> None:
    messages = [{"role": "user", "content": "unrelated"}]
    json_schema = {"type": "object", "properties": {"unexpected": {"type": "string"}}}

    with pytest.raises(ValueError, match="cannot route"):
        route_structured_call(messages, json_schema)


def test_route_structured_call_rejects_the_bare_conclusion_signature() -> None:
    """A bare ``{conclusion}`` negotiation signature is impossible and never routed (plan R5)."""
    messages = [{"role": "user", "content": "unrelated"}]
    json_schema = {"type": "object", "properties": {"conclusion": {"type": "string"}}}

    with pytest.raises(ValueError, match="cannot route"):
        route_structured_call(messages, json_schema)


def test_negotiation_variant_call_sequence_is_pinned() -> None:
    """slot-extraction(missing input) -> content-validation(missing prompt) -> content-validation(filled)."""
    mock_llm.reset_call_log()

    mock_llm.get_routed_response(
        _slot_extraction_messages(scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN), _slot_extraction_schema()
    )
    mock_llm.get_routed_response(
        _content_validation_messages(_MISSING_REGION_PROMPT, _REGION_TEMPLATE_TAIL), _content_validation_schema()
    )
    mock_llm.get_routed_response(
        _content_validation_messages(_FILLED_REGION_PROMPT, _REGION_TEMPLATE_TAIL), _content_validation_schema()
    )

    assert llm_calls == [
        mock_llm.STAGE_SLOT_EXTRACTION_MISSING,
        mock_llm.STAGE_CONTENT_VALIDATION_MISSING,
        mock_llm.STAGE_CONTENT_VALIDATION,
    ]


def _run_mock_sequence(steps: list[tuple[list[dict[str, str]], dict[str, object]]]) -> None:
    """Serve one scripted call sequence through the mock (test seam for the call-sequence pins)."""
    for messages, schema in steps:
        mock_llm.get_routed_response(messages, schema)


def test_target_negotiation_call_sequences_are_pinned_from_data_and_from_text() -> None:
    """fromData target run; fromText adds the server propose and the client accept extractions."""
    target_input = scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN
    from_data_slot_messages = _slot_extraction_messages('{"operationType": "create"}')
    filled_validation_messages = _content_validation_messages(_FILLED_REGION_PROMPT, _REGION_TEMPLATE_TAIL)
    propose_schema = build_extraction_schema(NegotiationType.TARGET, NegotiationPerformative.PROPOSE)
    accept_schema = build_extraction_schema(NegotiationType.TARGET, NegotiationPerformative.ACCEPT)

    # fromData: client slot extraction (target input) -> server target validation -> the client's
    # from-data prompt regeneration slot call -> the server's filled-prompt validation.
    mock_llm.reset_call_log()
    _run_mock_sequence(
        [
            (_slot_extraction_messages(target_input), _slot_extraction_schema()),
            (_content_validation_messages(target_input, _REGION_TEMPLATE_TAIL), _content_validation_schema()),
            (from_data_slot_messages, _slot_extraction_schema()),
            (filled_validation_messages, _content_validation_schema()),
        ]
    )
    assert llm_calls == [
        mock_llm.STAGE_SLOT_EXTRACTION_TARGET,
        mock_llm.STAGE_CONTENT_VALIDATION_TARGET,
        mock_llm.STAGE_SLOT_EXTRACTION,
        mock_llm.STAGE_CONTENT_VALIDATION,
    ]

    # fromText: the server propose extraction joins between the target validation and the round-2
    # slot call, and the client accept extraction joins before the final validation.
    mock_llm.reset_call_log()
    _run_mock_sequence(
        [
            (_slot_extraction_messages(target_input), _slot_extraction_schema()),
            (_content_validation_messages(target_input, _REGION_TEMPLATE_TAIL), _content_validation_schema()),
            ([{"role": "user", "content": "server propose rendering"}], propose_schema),
            (from_data_slot_messages, _slot_extraction_schema()),
            ([{"role": "user", "content": "client accept rendering"}], accept_schema),
            (filled_validation_messages, _content_validation_schema()),
        ]
    )
    assert llm_calls == [
        mock_llm.STAGE_SLOT_EXTRACTION_TARGET,
        mock_llm.STAGE_CONTENT_VALIDATION_TARGET,
        mock_llm.STAGE_NEGOTIATION_TARGET_PROPOSE_EXTRACTION,
        mock_llm.STAGE_SLOT_EXTRACTION,
        mock_llm.STAGE_NEGOTIATION_TARGET_ACCEPT_EXTRACTION,
        mock_llm.STAGE_CONTENT_VALIDATION,
    ]


def test_feasibility_negotiation_call_sequences_are_pinned_from_data_and_from_text() -> None:
    """fromData feasibility run; fromText adds the server propose and the client accept extractions."""
    feasibility_input = scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_EN
    from_data_slot_messages = _slot_extraction_messages('{"operationType": "create"}')
    filled_validation_messages = _content_validation_messages(_FILLED_REGION_PROMPT, _REGION_TEMPLATE_TAIL)
    propose_schema = build_extraction_schema(NegotiationType.FEASIBILITY, NegotiationPerformative.PROPOSE)
    accept_schema = build_extraction_schema(NegotiationType.FEASIBILITY, NegotiationPerformative.ACCEPT)

    # fromData: client slot extraction (feasibility input) -> server feasibility validation ->
    # the client's from-data prompt regeneration slot call -> the server's filled-prompt validation.
    mock_llm.reset_call_log()
    _run_mock_sequence(
        [
            (_slot_extraction_messages(feasibility_input), _slot_extraction_schema()),
            (_content_validation_messages(feasibility_input, _REGION_TEMPLATE_TAIL), _content_validation_schema()),
            (from_data_slot_messages, _slot_extraction_schema()),
            (filled_validation_messages, _content_validation_schema()),
        ]
    )
    assert llm_calls == [
        mock_llm.STAGE_SLOT_EXTRACTION_FEASIBILITY,
        mock_llm.STAGE_CONTENT_VALIDATION_FEASIBILITY,
        mock_llm.STAGE_SLOT_EXTRACTION,
        mock_llm.STAGE_CONTENT_VALIDATION,
    ]

    # fromText: the server propose extraction joins between the feasibility validation and the
    # round-2 slot call, and the client accept extraction joins before the final validation.
    mock_llm.reset_call_log()
    _run_mock_sequence(
        [
            (_slot_extraction_messages(feasibility_input), _slot_extraction_schema()),
            (_content_validation_messages(feasibility_input, _REGION_TEMPLATE_TAIL), _content_validation_schema()),
            ([{"role": "user", "content": "server propose rendering"}], propose_schema),
            (from_data_slot_messages, _slot_extraction_schema()),
            ([{"role": "user", "content": "client accept rendering"}], accept_schema),
            (filled_validation_messages, _content_validation_schema()),
        ]
    )
    assert llm_calls == [
        mock_llm.STAGE_SLOT_EXTRACTION_FEASIBILITY,
        mock_llm.STAGE_CONTENT_VALIDATION_FEASIBILITY,
        mock_llm.STAGE_NEGOTIATION_FEASIBILITY_PROPOSE_EXTRACTION,
        mock_llm.STAGE_SLOT_EXTRACTION,
        mock_llm.STAGE_NEGOTIATION_FEASIBILITY_ACCEPT_EXTRACTION,
        mock_llm.STAGE_CONTENT_VALIDATION,
    ]


# --- mock resources ------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_missing_mock_resources_mirror_the_complete_variants(language: str) -> None:
    region_slot = _REGION_SLOT_NAMES[language]
    complete_slots = json.loads((RESOURCES_DIR / language / "slot_extraction.json").read_text(encoding="utf-8"))
    missing_slots = json.loads((RESOURCES_DIR / language / "slot_extraction_missing.json").read_text(encoding="utf-8"))
    complete_validation = json.loads((RESOURCES_DIR / language / "content_validation.json").read_text(encoding="utf-8"))
    missing_validation = json.loads(
        (RESOURCES_DIR / language / "content_validation_missing.json").read_text(encoding="utf-8")
    )

    # The region slot carries the region placeholder (the packaged slot schema requires the slot,
    # so client generation must succeed with the region genuinely unknown); every other slot
    # stays unchanged and no slot error is reported.
    assert missing_slots["slots"][region_slot] == _REGION_PLACEHOLDERS[language]
    assert _REGION_PLACEHOLDERS[language] not in json.dumps(complete_slots["slots"], ensure_ascii=False)
    # The placeholder carries no region display string in any language.
    assert not any(region in _REGION_PLACEHOLDERS[language] for region in NEGOTIATION_REGION_STRINGS.values())
    assert missing_slots["slot_errors"] == []
    assert {name: value for name, value in complete_slots["slots"].items() if name != region_slot} == {
        name: value for name, value in missing_slots["slots"].items() if name != region_slot
    }
    # The content validation reports the missing region param over the four schema params.
    assert missing_validation["semantic_verdict"] is False
    assert missing_validation["errors"] == [
        {"slot_name": "region", "code": "content.param_missing", "facts": {"section_label": region_slot}}
    ]
    assert missing_validation["params"] == {**complete_validation["params"], "region": None}


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_negotiation_slot_resources_preserve_the_distinguishing_values(language: str) -> None:
    region_slot = _REGION_SLOT_NAMES[language]
    context_slot = _SLOT_SECTION_NAMES[language]["context"]
    target_slot = _SLOT_SECTION_NAMES[language]["target"]
    complete = _resource(language, "slot_extraction.json")
    target = _resource(language, "slot_extraction_target.json")
    feasibility = _resource(language, "slot_extraction_feasibility.json")

    # The negotiation slot variants keep the complete slot set with no slot errors and the
    # region slot filled (complete inputs).
    assert target["slot_errors"] == []
    assert feasibility["slot_errors"] == []
    assert set(target["slots"]) == set(complete["slots"])  # type: ignore[arg-type]
    assert set(feasibility["slots"]) == set(complete["slots"])  # type: ignore[arg-type]
    assert target["slots"][region_slot] == complete["slots"][region_slot]
    assert feasibility["slots"][region_slot] == complete["slots"][region_slot]
    # Every slot equals the complete variant except the distinguishing ones: the target variant
    # keeps the 100Mbps throughput, the feasibility variant the 30% goal and the 50Mbps throughput.
    for variant, distinguishing in (
        (target, {context_slot: "100Mbps"}),
        (feasibility, {context_slot: "50Mbps", target_slot: "30%"}),
    ):
        for name, value in complete["slots"].items():  # type: ignore[union-attr]
            if name in distinguishing:
                assert distinguishing[name] in str(variant["slots"][name])
            else:
                assert variant["slots"][name] == value


#: Localized section label per language of the sections the new content-validation variants
#: report (the zh labels follow the v1 ``任务对象`` section-label convention).
_VARIANT_SECTION_LABELS = {
    "en-US": {"task_context": "task_context", "task_target": "task_target"},
    "zh-CN": {"task_context": "任务上下文", "task_target": "任务目标"},
}


def _extraction_resource_schemas() -> dict[str, dict[str, object]]:
    """Canned Negotiation-T extraction file name -> the SDK schema whose properties it must carry."""
    return {
        "negotiation_propose_extraction.json": build_extraction_schema(
            NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE
        ),
        "negotiation_accept_extraction.json": build_extraction_schema(
            NegotiationType.INFORMATION, NegotiationPerformative.ACCEPT
        ),
        "negotiation_target_propose_extraction.json": build_extraction_schema(
            NegotiationType.TARGET, NegotiationPerformative.PROPOSE
        ),
        "negotiation_target_accept_extraction.json": build_extraction_schema(
            NegotiationType.TARGET, NegotiationPerformative.ACCEPT
        ),
        "negotiation_feasibility_propose_extraction.json": build_extraction_schema(
            NegotiationType.FEASIBILITY, NegotiationPerformative.PROPOSE
        ),
        "negotiation_feasibility_accept_extraction.json": build_extraction_schema(
            NegotiationType.FEASIBILITY, NegotiationPerformative.ACCEPT
        ),
    }


def _resource(language: str, file_name: str) -> dict[str, object]:
    """Load one canned mock resource of one language."""
    resource: dict[str, object] = json.loads((RESOURCES_DIR / language / file_name).read_text(encoding="utf-8"))
    return resource


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_negotiation_extraction_resources_match_the_sdk_schema_properties(language: str) -> None:
    """Every canned extraction response carries exactly the properties of its SDK schema."""
    for file_name, schema in _extraction_resource_schemas().items():
        resource = _resource(language, file_name)
        assert set(resource) == set(schema["properties"]), file_name


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_information_extraction_resources_mirror_the_scenario_answers(language: str) -> None:
    propose = _resource(language, "negotiation_propose_extraction.json")
    accept = _resource(language, "negotiation_accept_extraction.json")

    # The propose asks for the region (plus one more item) with no relationship note.
    assert len(propose["items"]) == 2  # type: ignore[arg-type]
    assert propose["relationship"] is None
    # The accept confirms the region exactly as the scenario answers carry it.
    assert accept == {
        "conclusion": "Accept",
        "items": [{"name": "region", "value": NEGOTIATION_REGION_STRINGS[language]}],
    }


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_content_validation_variant_resources_carry_the_trigger_codes(language: str) -> None:
    complete = _resource(language, "content_validation.json")
    target = _resource(language, "content_validation_target.json")
    feasibility = _resource(language, "content_validation_feasibility.json")

    # The target variant reports the value-not-allowed error on the throughput param; the
    # feasibility variant reports the semantic-conflict error on the energy goal param.
    assert target["semantic_verdict"] is False
    assert target["errors"] == [
        {
            "slot_name": "guaranteedThroughputTarget",
            "code": "content.value_not_allowed",
            "facts": {"section_label": _VARIANT_SECTION_LABELS[language]["task_context"]},
        }
    ]
    assert target["params"] == {**complete["params"], "guaranteedThroughputTarget": None}
    assert feasibility["semantic_verdict"] is False
    assert feasibility["errors"] == [
        {
            "slot_name": "energySavingGoal",
            "code": "content.semantic_conflict",
            "facts": {"section_label": _VARIANT_SECTION_LABELS[language]["task_target"]},
        }
    ]
    assert feasibility["params"] == {**complete["params"], "energySavingGoal": None}


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_negotiation_accessors_match_the_canned_extraction_responses(tmp_path: Path, language: str) -> None:
    env_path = _write_offline_env(tmp_path, language)
    target_propose = _resource(language, "negotiation_target_propose_extraction.json")
    target_accept = _resource(language, "negotiation_target_accept_extraction.json")
    feasibility_propose = _resource(language, "negotiation_feasibility_propose_extraction.json")
    feasibility_accept = _resource(language, "negotiation_feasibility_accept_extraction.json")

    # The client-generated accept texts equal the canned accept-extraction content, so the
    # from-data and from-text channels produce equivalent messages.
    assert target_accept["conclusion"] == "Accept"
    assert target_accept["failure_reason"] is None
    assert negotiation_target_confirmation(env_path=env_path) == target_accept["confirmed_intent"]
    assert feasibility_accept["conclusion"] == "Accept"
    assert negotiation_feasibility_summary(env_path=env_path) == feasibility_accept["feasibility_summary"]
    # The server-side propose texts mirror the canned propose extraction (confirm-request round:
    # all three conditional lists stay null; evaluation request: details/request stay null).
    assert negotiation_target_description(env_path=env_path) == target_propose["target_negotiation_description"]
    assert negotiation_target_confirm_request(env_path=env_path) == target_propose["target_confirm_request"]
    assert target_propose["intent_understanding"] is None
    assert target_propose["alignment_and_clarification"] is None
    assert target_propose["request_for_clarification"] is None
    assert feasibility_propose["action"] == "REQUEST_FEASIBILITY_EVALUATION"
    assert (
        negotiation_feasibility_description(env_path=env_path)
        == feasibility_propose["feasibility_negotiation_description"]
    )
    assert negotiation_feasibility_contents(env_path=env_path) == [
        (item["name"], item["value"])
        for item in feasibility_propose["contents_to_evaluate"]  # type: ignore[union-attr]
    ]
    assert feasibility_propose["infeasibility_details_and_proposal"] is None
    assert feasibility_propose["feasibility_confirm_request"] is None


# --- scenario data -------------------------------------------------------------------------


def test_supported_scenarios_constant() -> None:
    assert SUPPORTED_SCENARIOS == (SCENARIO_DEFAULT, SCENARIO_NEGOTIATION)


def test_resolve_scenario_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .env next to the cwd

    # No .env and no OS environment entry -> default.
    assert resolve_scenario() == SCENARIO_DEFAULT

    # Unknown value -> default.
    env_path = tmp_path / ".env"
    env_path.write_text("A2AT_SAMPLE_SCENARIO=bogus\n", encoding="utf-8")
    assert resolve_scenario(env_path=env_path) == SCENARIO_DEFAULT

    # Known value -> itself.
    env_path.write_text("A2AT_SAMPLE_SCENARIO=negotiation\n", encoding="utf-8")
    assert resolve_scenario(env_path=env_path) == SCENARIO_NEGOTIATION

    # OS environment fallback when the .env does not carry the key.
    env_path.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")
    monkeypatch.setenv(SCENARIO_ENV_KEY, SCENARIO_NEGOTIATION)
    assert resolve_scenario(env_path=env_path) == SCENARIO_NEGOTIATION

    # The .env value wins over the OS environment.
    env_path.write_text("A2AT_SAMPLE_SCENARIO=default\n", encoding="utf-8")
    assert resolve_scenario(env_path=env_path) == SCENARIO_DEFAULT


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_build_prompt_input_negotiation_returns_the_missing_region_input(tmp_path: Path, language: str) -> None:
    env_path = _write_offline_env(tmp_path, language)
    complete = {
        "en-US": NATURAL_LANGUAGE_PROMPT_INPUT_EN,
        "zh-CN": NATURAL_LANGUAGE_PROMPT_INPUT_ZH,
    }[language]
    missing = {
        "en-US": scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN,
        "zh-CN": scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_ZH,
    }[language]

    assert build_prompt_input(env_path=env_path) == complete
    assert build_prompt_input(env_path=env_path, scenario=SCENARIO_NEGOTIATION) == missing
    assert build_prompt_input(env_path=env_path, scenario=resolve_scenario(env_path=env_path)) == complete
    # The missing input omits the region display string; the complete one carries it.
    assert NEGOTIATION_REGION_STRINGS[language] not in missing
    assert NEGOTIATION_REGION_STRINGS[language] in complete


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_negotiation_filled_params_and_answers_are_region_filled(tmp_path: Path, language: str) -> None:
    env_path = _write_offline_env(tmp_path, language)

    params = negotiation_filled_params(env_path=env_path)
    answers = negotiation_answers(env_path=env_path)

    assert set(params) == {"operationType", "region", "energySavingGoal", "guaranteedThroughputTarget"}
    assert params["region"] == NEGOTIATION_REGION_STRINGS[language]
    assert answers == {"region": NEGOTIATION_REGION_STRINGS[language]}
    # The filled params mirror the complete content_validation resource of the language.
    resource_params = json.loads((RESOURCES_DIR / language / "content_validation.json").read_text(encoding="utf-8"))[
        "params"
    ]
    assert params == resource_params


def test_negotiation_kinds_and_channels_constants() -> None:
    assert SUPPORTED_NEGOTIATION_KINDS == ("information", "target", "feasibility")
    assert SUPPORTED_CHANNELS == ("data", "text")
    assert set(NEGOTIATION_INPUT_KEYS) == set(SUPPORTED_NEGOTIATION_KINDS)


def _kind_input(kind: str, language: str) -> str:
    """Return the negotiation-kind NL input of one language via the exported mapping."""
    en_input, zh_input = NEGOTIATION_INPUT_KEYS[kind]
    return en_input if language == "en-US" else zh_input


def test_resolve_channel_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .env next to the cwd

    # No .env and no OS environment entry -> data.
    assert resolve_channel() == CHANNEL_DATA

    # Unknown value -> data.
    env_path = tmp_path / ".env"
    env_path.write_text("A2AT_SAMPLE_CHANNEL=bogus\n", encoding="utf-8")
    assert resolve_channel(env_path=env_path) == CHANNEL_DATA

    # Known value -> itself.
    env_path.write_text("A2AT_SAMPLE_CHANNEL=text\n", encoding="utf-8")
    assert resolve_channel(env_path=env_path) == CHANNEL_TEXT

    # OS environment fallback when the .env does not carry the key.
    env_path.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")
    monkeypatch.setenv(CHANNEL_ENV_KEY, CHANNEL_TEXT)
    assert resolve_channel(env_path=env_path) == CHANNEL_TEXT

    # The .env value wins over the OS environment.
    env_path.write_text("A2AT_SAMPLE_CHANNEL=data\n", encoding="utf-8")
    assert resolve_channel(env_path=env_path) == CHANNEL_DATA


def test_resolve_negotiation_kind_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .env next to the cwd

    # No .env and no OS environment entry -> information.
    assert resolve_negotiation_kind() == "information"

    # Unknown value -> information.
    env_path = tmp_path / ".env"
    env_path.write_text("A2AT_SAMPLE_NEGOTIATION_KIND=bogus\n", encoding="utf-8")
    assert resolve_negotiation_kind(env_path=env_path) == "information"

    # Known value -> itself.
    env_path.write_text("A2AT_SAMPLE_NEGOTIATION_KIND=target\n", encoding="utf-8")
    assert resolve_negotiation_kind(env_path=env_path) == "target"

    # OS environment fallback when the .env does not carry the key.
    env_path.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")
    monkeypatch.setenv(NEGOTIATION_KIND_ENV_KEY, "feasibility")
    assert resolve_negotiation_kind(env_path=env_path) == "feasibility"

    # The .env value wins over the OS environment.
    env_path.write_text("A2AT_SAMPLE_NEGOTIATION_KIND=information\n", encoding="utf-8")
    assert resolve_negotiation_kind(env_path=env_path) == "information"


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_build_prompt_input_selects_the_input_by_the_negotiation_kind(tmp_path: Path, language: str) -> None:
    env_path = _write_offline_env(tmp_path, language)
    complete = NATURAL_LANGUAGE_PROMPT_INPUT_EN if language == "en-US" else NATURAL_LANGUAGE_PROMPT_INPUT_ZH

    # The target/feasibility kinds select their own input regardless of the scenario.
    assert build_prompt_input(env_path=env_path, negotiation_kind="target") == _kind_input("target", language)
    assert build_prompt_input(env_path=env_path, negotiation_kind="feasibility") == _kind_input("feasibility", language)
    assert build_prompt_input(
        env_path=env_path, scenario=SCENARIO_NEGOTIATION, negotiation_kind="target"
    ) == _kind_input("target", language)
    # The information kind keeps the v1 scenario behavior (complete input on the default scenario,
    # missing-region input on the negotiation scenario).
    assert build_prompt_input(env_path=env_path, negotiation_kind="information") == complete
    assert build_prompt_input(
        env_path=env_path, scenario=SCENARIO_NEGOTIATION, negotiation_kind="information"
    ) == _kind_input("information", language)

    # The .env-resolved kind selects the input the same way an explicit override does.
    kind_env_path = tmp_path / "kind.env"
    kind_env_path.write_text(
        _OFFLINE_ENV_TEXT.format(language=language) + "A2AT_SAMPLE_NEGOTIATION_KIND=feasibility\n", encoding="utf-8"
    )
    assert build_prompt_input(env_path=kind_env_path) == _kind_input("feasibility", language)

    # An unknown kind falls back to the information behavior like resolve_negotiation_kind does.
    bogus_env_path = tmp_path / "bogus.env"
    bogus_env_path.write_text(
        _OFFLINE_ENV_TEXT.format(language=language) + "A2AT_SAMPLE_NEGOTIATION_KIND=bogus\n", encoding="utf-8"
    )
    assert build_prompt_input(env_path=bogus_env_path) == complete


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_negotiation_kind_inputs_and_accessors_are_complete(tmp_path: Path, language: str) -> None:
    env_path = _write_offline_env(tmp_path, language)
    target_input = _kind_input("target", language)
    feasibility_input = _kind_input("feasibility", language)

    # Both kind inputs are complete inputs: they carry the region display string.
    assert NEGOTIATION_REGION_STRINGS[language] in target_input
    assert NEGOTIATION_REGION_STRINGS[language] in feasibility_input
    # The target input carries the 100Mbps target; the feasibility input carries both the 30%
    # goal and the 50Mbps throughput.
    assert "100Mbps" in target_input
    assert "30%" in feasibility_input
    assert "50Mbps" in feasibility_input
    # Each kind input maps to its own content-validation stage via the router.
    assert (
        route_structured_call(
            _content_validation_messages(target_input, _REGION_TEMPLATE_TAIL), _content_validation_schema()
        )
        == mock_llm.STAGE_CONTENT_VALIDATION_TARGET
    )
    assert (
        route_structured_call(
            _content_validation_messages(feasibility_input, _REGION_TEMPLATE_TAIL), _content_validation_schema()
        )
        == mock_llm.STAGE_CONTENT_VALIDATION_FEASIBILITY
    )
    # All per-type accessors return non-empty values for both languages.
    assert negotiation_target_description(env_path=env_path)
    assert negotiation_target_confirm_request(env_path=env_path)
    assert negotiation_target_confirmation(env_path=env_path)
    assert len(negotiation_feasibility_contents(env_path=env_path)) == 2
    assert negotiation_feasibility_summary(env_path=env_path)


# --- client-side feasibility ---------------------------------------------------------------


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_negotiation_client_generation_renders_the_missing_region_prompt(tmp_path: Path, language: str) -> None:
    """generate_task_prompt_from_text renders the missing-region prompt through the real client path."""
    from a2a_t.client.a2at_client import A2ATClient

    env_path = _write_offline_env(tmp_path, language)
    mock_llm.install_mock_llm(env_path=env_path)
    missing_input = {
        "en-US": scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN,
        "zh-CN": scenario_data.NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_ZH,
    }[language]

    client = A2ATClient(env_path=env_path)
    prompt = client.generate_task_prompt_from_text(missing_input, ENERGY_SAVING_TEMPLATE_URI)

    # The required region slot renders as the region placeholder, never as a region display
    # string, so the server-side content validation routes to the missing variant.
    assert NEGOTIATION_REGION_STRINGS[language] not in prompt.prompt_text
    assert _REGION_PLACEHOLDERS[language] in prompt.prompt_text


@pytest.mark.parametrize(
    ("kind", "expected_slot_stage", "expected_validation_stage"),
    [
        ("target", mock_llm.STAGE_SLOT_EXTRACTION_TARGET, mock_llm.STAGE_CONTENT_VALIDATION_TARGET),
        ("feasibility", mock_llm.STAGE_SLOT_EXTRACTION_FEASIBILITY, mock_llm.STAGE_CONTENT_VALIDATION_FEASIBILITY),
    ],
    ids=["target", "feasibility"],
)
@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_negotiation_kind_client_generation_routes_to_its_content_validation_variant(
    tmp_path: Path, language: str, kind: str, expected_slot_stage: str, expected_validation_stage: str
) -> None:
    """The real client path renders the distinguishing values, so the round-1 trigger works.

    ``generate_task_prompt_from_text`` serves the kind's slot-extraction variant whose rendered
    prompt carries the distinguishing values, so the server-side content validation of that
    rendered prompt routes to the kind's variant — the data-driven negotiation trigger.
    """
    from a2a_t.client.a2at_client import A2ATClient

    env_path = _write_offline_env(tmp_path, language)
    mock_llm.install_mock_llm(env_path=env_path)
    mock_llm.reset_call_log()

    client = A2ATClient(env_path=env_path)
    prompt = client.generate_task_prompt_from_text(_kind_input(kind, language), ENERGY_SAVING_TEMPLATE_URI)

    # The kind's NL input routes its slot-extraction call to the dedicated variant...
    assert llm_calls == [expected_slot_stage]
    # ...whose distinguishing values in the rendered prompt drive the server-side routing.
    assert (
        route_structured_call(
            _content_validation_messages(prompt.prompt_text, _REGION_TEMPLATE_TAIL), _content_validation_schema()
        )
        == expected_validation_stage
    )
