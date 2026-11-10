"""Tests for the scripted SDK LLM provider and its schema/scenario routing."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import Any, cast

import pytest
from a2a_t.client.a2at_client import A2ATClient
from a2a_t.core.errors.exceptions import ContentValidationError
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import (
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    NEGOTIATION_ABORT_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.llm.models import LLMClientConfig
from a2a_t.server.a2at_server import A2ATServer
from demo.scenario import LANGUAGE_ZH, SCENARIOS, Scenario
from demo.scripted_llm import (
    SCRIPTED_PROVIDER_NAME,
    STAGE_CONTENT_VALIDATION,
    STAGE_CONTENT_VALIDATION_FEASIBILITY,
    STAGE_CONTENT_VALIDATION_MISSING,
    STAGE_CONTENT_VALIDATION_OUTAGE,
    STAGE_CONTENT_VALIDATION_OUTAGE_BASELINE,
    STAGE_CONTENT_VALIDATION_TARGET,
    STAGE_NEGOTIATION_ABORT_EXTRACTION,
    STAGE_NEGOTIATION_ACCEPT_EXTRACTION,
    STAGE_NEGOTIATION_FEASIBILITY_ACCEPT_EXTRACTION,
    STAGE_NEGOTIATION_FEASIBILITY_PROPOSE_EXTRACTION,
    STAGE_NEGOTIATION_FEASIBILITY_REJECT_EXTRACTION,
    STAGE_NEGOTIATION_PROPOSE_EXTRACTION,
    STAGE_NEGOTIATION_REJECT_EXTRACTION,
    STAGE_NEGOTIATION_SEMANTIC_VALIDATION,
    STAGE_NEGOTIATION_TARGET_ACCEPT_EXTRACTION,
    STAGE_NEGOTIATION_TARGET_PROPOSE_EXTRACTION,
    STAGE_NEGOTIATION_TARGET_REJECT_EXTRACTION,
    STAGE_SLOT_EXTRACTION,
    STAGE_SLOT_EXTRACTION_FEASIBILITY,
    STAGE_SLOT_EXTRACTION_MISSING,
    STAGE_SLOT_EXTRACTION_OUTAGE_BASELINE,
    STAGE_SLOT_EXTRACTION_TARGET,
    STAGE_SLOT_EXTRACTION_UNKNOWN,
    ScriptedLLMClient,
    register_scripted_provider,
    route_structured_call,
)
from protocol.messages import TASK_TEMPLATE_URI
from protocol.negotiation_policy import NegotiationKind, negotiation_kind_for_errors
from protocol.server_app import prepare_sdk_env_path
from shared.config import SampleConfig
from shared.task_schema import TASK_PARAM_SCHEMA

SLOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"slots": {"type": "object"}, "slot_errors": {"type": "array"}},
}

VALIDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"semantic_verdict": {"type": "object"}},
}

_LLM_CONFIG = LLMClientConfig(
    provider=SCRIPTED_PROVIDER_NAME,
    model=SCRIPTED_PROVIDER_NAME,
    api_key="not-needed",
    base_url=None,
    history_window=10,
    max_tokens=None,
    temperature=0.0,
    timeout_seconds=60.0,
    session_max_total=300,
    session_max_per_provider=100,
)

#: Expected slot-extraction and content-validation stage per scenario.
_EXPECTED_STAGES: dict[str, tuple[str, str]] = {
    "night": (STAGE_SLOT_EXTRACTION, STAGE_CONTENT_VALIDATION),
    "information": (STAGE_SLOT_EXTRACTION_MISSING, STAGE_CONTENT_VALIDATION_MISSING),
    "unknown_area": (STAGE_SLOT_EXTRACTION_UNKNOWN, STAGE_CONTENT_VALIDATION),
    "target": (STAGE_SLOT_EXTRACTION_TARGET, STAGE_CONTENT_VALIDATION_TARGET),
    "feasibility": (STAGE_SLOT_EXTRACTION_FEASIBILITY, STAGE_CONTENT_VALIDATION_FEASIBILITY),
    "reject": (STAGE_SLOT_EXTRACTION_UNKNOWN, STAGE_CONTENT_VALIDATION),
    "abort": (STAGE_SLOT_EXTRACTION_UNKNOWN, STAGE_CONTENT_VALIDATION),
    "outage": (STAGE_SLOT_EXTRACTION_OUTAGE_BASELINE, STAGE_CONTENT_VALIDATION_OUTAGE_BASELINE),
}

#: Marker of the two-site baseline Task-T (phase 1 of the planned-outage run).
_OUTAGE_BASELINE_CONTENT = (
    "## Operation Type\n\nCreate\n\n## Task Context\n"
    "Site list: Site 1,Site 2; time-segment-based guaranteed throughput target: "
    "{00:00~24:00,10Mbps}"
)


def _messages(content: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": "route me"}, {"role": "user", "content": content}]


@pytest.fixture
def client() -> ScriptedLLMClient:
    return ScriptedLLMClient(_LLM_CONFIG, language="en-US")


def test_outage_content_validation_extracts_modify(client: ScriptedLLMClient) -> None:
    from demo.scenario import MODIFY_REQUEST

    response = client.structured(
        messages=_messages(MODIFY_REQUEST["en-US"]),
        json_schema=VALIDATION_SCHEMA,
    )

    assert client.calls[-1] == STAGE_CONTENT_VALIDATION_OUTAGE
    payload = json.loads(response.content)
    assert payload["params"]["operationType"] == "Modify"


def test_outage_baseline_content_validation_uses_the_create_fixture(
    client: ScriptedLLMClient,
) -> None:
    response = client.structured(
        messages=_messages(_OUTAGE_BASELINE_CONTENT),
        json_schema=VALIDATION_SCHEMA,
    )

    assert client.calls[-1] == STAGE_CONTENT_VALIDATION_OUTAGE_BASELINE
    payload = json.loads(response.content)
    assert payload["params"]["operationType"] == "Create"
    assert payload["params"]["guaranteedThroughputTarget"] == "{00:00~24:00,10Mbps}"


def test_all_five_scenarios_are_defined() -> None:
    assert tuple(SCENARIOS) == (
        "night",
        "information",
        "unknown_area",
        "target",
        "feasibility",
        "reject",
        "abort",
        "outage",
    )


@pytest.mark.parametrize("scenario_name", tuple(SCENARIOS))
def test_slot_extraction_routes_by_scenario(
    scenario_name: str,
    client: ScriptedLLMClient,
) -> None:
    scenario: Scenario = SCENARIOS[scenario_name]
    expected_slot_stage, _ = _EXPECTED_STAGES[scenario_name]

    response = client.structured(
        messages=_messages(scenario.user_request["en-US"]),
        json_schema=SLOT_SCHEMA,
    )

    assert client.calls[-1] == expected_slot_stage
    assert json.loads(response.content)


@pytest.mark.parametrize("scenario_name", tuple(SCENARIOS))
def test_content_validation_routes_by_scenario(
    scenario_name: str,
    client: ScriptedLLMClient,
) -> None:
    scenario = SCENARIOS[scenario_name]
    _, expected_validation_stage = _EXPECTED_STAGES[scenario_name]

    response = client.structured(
        messages=_messages(scenario.user_request["en-US"]),
        json_schema=VALIDATION_SCHEMA,
    )

    assert client.calls[-1] == expected_validation_stage
    assert json.loads(response.content)


def test_a_free_text_vendor_region_replaces_the_neutral_slot_placeholder(
    client: ScriptedLLMClient,
) -> None:
    """The processed path sends free text; the canned response must echo the vendor region."""
    request = (
        "Create an energy saving task for A2ATestRegion with these guaranteed throughputs: "
        "{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,10Mbps}. "
        "Output the wireless network energy saving plan and return the intent report."
    )

    response = client.structured(messages=_messages(request), json_schema=SLOT_SCHEMA)

    slots = json.loads(response.content)["slots"]
    assert "A2ATestRegion" in slots["task_object"]
    assert "Region A" not in slots["task_object"]


def test_a_free_text_window_replaces_the_canned_task_context(
    client: ScriptedLLMClient,
) -> None:
    """A custom simulator-compatible window must reach the canned slot response."""
    request = (
        "Create an energy saving task for A2ATestRegion with these guaranteed throughputs: "
        "{00:00~06:00,30Mbps}. Output the wireless network energy saving plan and return the "
        "intent report."
    )

    response = client.structured(messages=_messages(request), json_schema=SLOT_SCHEMA)

    slots = json.loads(response.content)["slots"]
    assert "{00:00~06:00,30Mbps}" in slots["task_context"]
    assert "17:30" not in slots["task_context"]


def test_the_unknown_area_region_is_not_rewritten(client: ScriptedLLMClient) -> None:
    """The deliberate Region Z gap must survive the vendor-region rewrite."""
    response = client.structured(
        messages=_messages(SCENARIOS["unknown_area"].user_request["en-US"]),
        json_schema=SLOT_SCHEMA,
    )

    slots = json.loads(response.content)["slots"]
    assert "Region Z" in slots["task_object"]


def test_complete_input_routes_to_default_slot_extraction() -> None:
    from demo.scenario import EXAMPLE_REQUESTS, SCENARIO_NIGHT

    stage = route_structured_call(_messages(EXAMPLE_REQUESTS[SCENARIO_NIGHT]["en-US"]), SLOT_SCHEMA, language="en-US")

    assert stage == STAGE_SLOT_EXTRACTION


@pytest.mark.parametrize(
    ("properties", "expected_stage"),
    [
        ({"conclusion", "feasibility_summary"}, STAGE_NEGOTIATION_FEASIBILITY_ACCEPT_EXTRACTION),
        (
            {"conclusion", "confirmed_intent", "failure_reason"},
            STAGE_NEGOTIATION_TARGET_ACCEPT_EXTRACTION,
        ),
        ({"conclusion", "items"}, STAGE_NEGOTIATION_ACCEPT_EXTRACTION),
        (
            {"feasibility_negotiation_description", "action"},
            STAGE_NEGOTIATION_FEASIBILITY_PROPOSE_EXTRACTION,
        ),
        ({"target_negotiation_description"}, STAGE_NEGOTIATION_TARGET_PROPOSE_EXTRACTION),
        ({"items", "relationship"}, STAGE_NEGOTIATION_PROPOSE_EXTRACTION),
    ],
)
def test_negotiation_extraction_signatures_route(
    properties: set[str],
    expected_stage: str,
    client: ScriptedLLMClient,
) -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {name: {"type": "string"} for name in properties},
    }

    response = client.structured(messages=_messages("negotiator input"), json_schema=schema)

    assert client.calls[-1] == expected_stage
    assert json.loads(response.content)


def test_unroutable_schema_raises() -> None:
    with pytest.raises(ValueError, match="cannot route"):
        route_structured_call(_messages("unknown"), {"properties": {"unrelated": {}}}, language="en-US")


def test_register_scripted_provider_is_idempotent() -> None:
    register_scripted_provider()
    register_scripted_provider()

    from a2a_t.llm.factory import LLMClientFactory

    assert SCRIPTED_PROVIDER_NAME in LLMClientFactory.available_providers()


# ----------------------------------------------------------------------
# Negotiation semantic validation routing (prerequisite of Task 4)
# ----------------------------------------------------------------------

#: Output schema of the SDK negotiation semantic validation (the caller schema is nested in params).
NEGOTIATION_VALIDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "semantic_verdict": {"type": "boolean"},
        "negotiation_type": {"type": ["string", "null"]},
        "errors": {"type": "array"},
        "params": {"type": "object", "properties": {}},
    },
    "required": ["semantic_verdict", "negotiation_type", "errors", "params"],
}


def _validation_prompt(*, phase: str, kind: str, template_uri: str) -> str:
    return (
        f"Negotiation phase: {phase}\n"
        f"Declared negotiation type: {kind}\n"
        f"Declared template identifier: {template_uri}\n"
        "Template content: template body\n\n"
        "Parameter schema:\n{}\n\n"
        "Negotiation message to validate:\nnegotiation message\n"
    )


@pytest.mark.parametrize(
    ("phase", "kind", "expected_kind", "template_uri"),
    [
        ("propose", "information", "information", INFORMATION_NEGOTIATION_PROPOSE_URI),
        ("accept", "information", "information", INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI),
        ("reject", "target", "target", "Negotiation-T/target-negotiation/accept-reject/v1"),
        ("abort", "common", None, NEGOTIATION_ABORT_URI),
    ],
)
def test_negotiation_semantic_validation_routes_and_declares_the_type(
    phase: str,
    kind: str,
    expected_kind: str | None,
    template_uri: str,
    client: ScriptedLLMClient,
) -> None:
    prompt = _validation_prompt(phase=phase, kind=kind, template_uri=template_uri)

    response = client.structured(
        messages=_messages(prompt),
        json_schema=NEGOTIATION_VALIDATION_SCHEMA,
    )

    assert client.calls[-1] == STAGE_NEGOTIATION_SEMANTIC_VALIDATION
    payload = json.loads(response.content)
    assert payload == {
        "semantic_verdict": True,
        "negotiation_type": expected_kind,
        "errors": [],
        "params": {},
    }


@pytest.mark.parametrize(
    ("phase", "properties", "expected_stage"),
    [
        ("accept", {"conclusion", "items"}, STAGE_NEGOTIATION_ACCEPT_EXTRACTION),
        ("reject", {"conclusion", "items"}, STAGE_NEGOTIATION_REJECT_EXTRACTION),
        ("accept", {"conclusion", "confirmed_intent", "failure_reason"}, STAGE_NEGOTIATION_TARGET_ACCEPT_EXTRACTION),
        (
            "reject",
            {"conclusion", "confirmed_intent", "failure_reason"},
            STAGE_NEGOTIATION_TARGET_REJECT_EXTRACTION,
        ),
        (
            "accept",
            {"conclusion", "feasibility_summary"},
            STAGE_NEGOTIATION_FEASIBILITY_ACCEPT_EXTRACTION,
        ),
        (
            "reject",
            {"conclusion", "feasibility_summary"},
            STAGE_NEGOTIATION_FEASIBILITY_REJECT_EXTRACTION,
        ),
        ("abort", {"termination_reason"}, STAGE_NEGOTIATION_ABORT_EXTRACTION),
    ],
)
def test_terminal_extraction_routes_by_phase(
    phase: str,
    properties: set[str],
    expected_stage: str,
    client: ScriptedLLMClient,
) -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {name: {"type": "string"} for name in properties},
    }

    response = client.structured(
        messages=_messages(f"Negotiation phase: {phase}\n\nInput text:\nnegotiation message"),
        json_schema=schema,
    )

    assert client.calls[-1] == expected_stage
    assert json.loads(response.content)


@pytest.fixture
def real_negotiation_server(offline_config: SampleConfig) -> A2ATServer:
    """Build a real A2ATServer on the scripted SDK provider (no network)."""
    register_scripted_provider()
    return A2ATServer(env_path=prepare_sdk_env_path(offline_config))


def _negotiation_context(performative: NegotiationPerformative) -> NegotiationContext:
    return NegotiationContext(
        id=str(uuid.uuid4()),
        round=1,
        max_rounds=3,
        performative=performative,
    )


def test_information_client_extraction_succeeds_and_server_maps_to_information(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The missing area is required by both sides and maps to the information negotiation.

    The guaranteed rate is optional (the vendor treats an empty target list as its default
    non-disruptive mode), so the demo's incomplete request now omits the task object instead. The
    client must still render a prompt from the vague area, and the server must report
    ``content.param_missing`` for the region and map it to the information negotiation.
    """
    assert "guaranteedThroughputTarget" not in cast(list[str], TASK_PARAM_SCHEMA["required"])
    assert "region" in cast(list[str], TASK_PARAM_SCHEMA["required"])
    register_scripted_provider()
    monkeypatch.setenv("A2AT_LANGUAGE", offline_config.language)
    env_path = prepare_sdk_env_path(offline_config)
    client = A2ATClient(env_path=env_path)
    server = A2ATServer(env_path=env_path)
    request = SCENARIOS["information"].user_request["en-US"]

    content = client.generate_task_prompt_from_text(request, TASK_TEMPLATE_URI)
    prompt_text = content.prompt_text
    assert prompt_text is not None

    assert "test area" in prompt_text
    assert "07:00~17:30,10Mbps" not in prompt_text
    with pytest.raises(ContentValidationError) as excinfo:
        server.validate_task_prompt_and_data_filling(
            prompt_text,
            TASK_PARAM_SCHEMA,
            TASK_TEMPLATE_URI,
        )

    errors = excinfo.value.errors
    assert [error.slot_name for error in errors] == ["region"]
    assert errors[0].code == "content.param_missing"
    assert negotiation_kind_for_errors(errors) is NegotiationKind.INFORMATION


def test_the_planned_outage_modify_keeps_the_site_list_in_the_task_context(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The overlay's site-level outage rule reads the site list from the Task Context.

    A site list that stays in the Task Object leaves the Task Context of an outage request without
    one, and the declarative rule then blocks the generated Task-T.
    """
    from demo.scenario import MODIFY_REQUEST

    register_scripted_provider()
    monkeypatch.setenv("A2AT_LANGUAGE", offline_config.language)
    client = A2ATClient(env_path=prepare_sdk_env_path(offline_config))

    content = client.generate_task_prompt_from_text(MODIFY_REQUEST["en-US"], TASK_TEMPLATE_URI)

    prompt_text = content.prompt_text
    assert prompt_text is not None
    context = prompt_text[prompt_text.index("## Task Context") :]
    assert 'Site list 1: ["Site 1", "Site 2"]' in context


def test_real_server_validates_accept_offline(real_negotiation_server: A2ATServer) -> None:
    result = real_negotiation_server.validate_accept_prompt_and_data_filling(
        "Accept. region: Region A.",
        _negotiation_context(NegotiationPerformative.ACCEPT),
        {"type": "object", "properties": {}},
        INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    )

    assert isinstance(result, FilledParamData)


def test_real_server_validates_reject_offline(real_negotiation_server: A2ATServer) -> None:
    result = real_negotiation_server.validate_reject_prompt_and_data_filling(
        "Reject. guaranteedThroughputTarget: The client cannot provide the missing throughput target.",
        _negotiation_context(NegotiationPerformative.REJECT),
        {"type": "object", "properties": {}},
        INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    )

    assert isinstance(result, FilledParamData)


def test_real_server_validates_propose_offline(real_negotiation_server: A2ATServer) -> None:
    result = real_negotiation_server.validate_propose_prompt_and_data_filling(
        "Propose. Please accept the revised plan for cell-001.",
        _negotiation_context(NegotiationPerformative.PROPOSE),
        {"type": "object", "properties": {}},
        TARGET_NEGOTIATION_PROPOSE_URI,
    )

    assert isinstance(result, FilledParamData)


def test_real_server_validates_abort_offline(real_negotiation_server: A2ATServer) -> None:
    result = real_negotiation_server.validate_abort_prompt_and_data_filling(
        "Abort. The negotiation reached its round limit.",
        _negotiation_context(NegotiationPerformative.ABORT),
        {"type": "object", "properties": {}},
        NEGOTIATION_ABORT_URI,
    )

    assert isinstance(result, FilledParamData)


# ----------------------------------------------------------------------
# zh-CN (default language) validation and generation
# ----------------------------------------------------------------------

#: Real-server validation cases: performative -> (context performative, template URI, prompt).
_ZH_VALIDATION_CASES: tuple[tuple[str, NegotiationPerformative, str, str], ...] = (
    (
        "accept",
        NegotiationPerformative.ACCEPT,
        INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        "Accept. region: 区域 A。",
    ),
    (
        "reject",
        NegotiationPerformative.REJECT,
        INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        "Reject. guaranteedThroughputTarget: 客户端无法提供缺失的保障速率目标。",
    ),
    (
        "propose",
        NegotiationPerformative.PROPOSE,
        TARGET_NEGOTIATION_PROPOSE_URI,
        "Propose. 请接受修订后的方案。",
    ),
    (
        "abort",
        NegotiationPerformative.ABORT,
        NEGOTIATION_ABORT_URI,
        "Abort. 协商达到轮次上限。",
    ),
)


def _zh_validation_prompt(*, phase: str, kind: str, template_uri: str) -> str:
    return (
        f"协商阶段：{phase}\n"
        f"声明的协商类型：{kind}\n"
        f"声明的模板标识：{template_uri}\n"
        "模板正文：模板内容\n\n"
        "参数 schema：{}\n\n"
        "待校验的协商报文：\n协商报文\n"
    )


@pytest.mark.parametrize(
    ("phase", "kind", "expected_kind", "template_uri"),
    [
        ("propose", "information", "information", INFORMATION_NEGOTIATION_PROPOSE_URI),
        ("accept", "target", "target", "Negotiation-T/target-negotiation/accept-reject/v1"),
        ("reject", "feasibility", "feasibility", "Negotiation-T/feasibility-negotiation/accept-reject/v1"),
        ("abort", "common", None, NEGOTIATION_ABORT_URI),
    ],
)
def test_negotiation_semantic_validation_routes_zh_cn(
    phase: str,
    kind: str,
    expected_kind: str | None,
    template_uri: str,
    client: ScriptedLLMClient,
) -> None:
    prompt = _zh_validation_prompt(phase=phase, kind=kind, template_uri=template_uri)

    response = client.structured(messages=_messages(prompt), json_schema=NEGOTIATION_VALIDATION_SCHEMA)

    assert client.calls[-1] == STAGE_NEGOTIATION_SEMANTIC_VALIDATION
    payload = json.loads(response.content)
    assert payload["negotiation_type"] == expected_kind
    assert payload["semantic_verdict"] is True


@pytest.fixture
def zh_real_server(offline_config: SampleConfig) -> A2ATServer:
    """Build a real A2ATServer on the scripted provider in the default zh-CN language."""
    register_scripted_provider()
    return A2ATServer(env_path=prepare_sdk_env_path(replace(offline_config, language=LANGUAGE_ZH)))


@pytest.mark.parametrize(("phase", "performative", "template_uri", "prompt"), _ZH_VALIDATION_CASES)
def test_real_server_validates_negotiation_messages_offline_zh_cn(
    zh_real_server: A2ATServer,
    phase: str,
    performative: NegotiationPerformative,
    template_uri: str,
    prompt: str,
) -> None:
    context = _negotiation_context(performative)
    schema: dict[str, object] = {"type": "object", "properties": {}}
    validators = {
        "accept": zh_real_server.validate_accept_prompt_and_data_filling,
        "reject": zh_real_server.validate_reject_prompt_and_data_filling,
        "propose": zh_real_server.validate_propose_prompt_and_data_filling,
        "abort": zh_real_server.validate_abort_prompt_and_data_filling,
    }

    result = validators[phase](prompt, context, schema, template_uri)

    assert isinstance(result, FilledParamData)


def test_real_client_generates_a_reject_offline_zh_cn(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_scripted_provider()
    monkeypatch.setenv("A2AT_LANGUAGE", LANGUAGE_ZH)
    client = A2ATClient(env_path=prepare_sdk_env_path(replace(offline_config, language=LANGUAGE_ZH)))
    context = _negotiation_context(NegotiationPerformative.REJECT)

    content = client.generate_negotiation_reject_prompt_from_text(
        "Reject. guaranteedThroughputTarget: 客户端无法提供缺失的保障速率目标。",
        context,
        INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    )

    assert content.prompt_text
    assert "Reject" in content.prompt_text


def test_unknown_area_propose_lists_the_served_areas(client: ScriptedLLMClient) -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"items": {"type": "array"}, "relationship": {"type": "string"}},
    }
    proposal = (
        'The requested area "Region Z" is not recognized by the RAN. '
        "Served areas: Region A; Region B."
    )

    response = client.structured(messages=_messages(proposal), json_schema=schema)
    payload = json.loads(response.content)
    value = payload["items"][0]["value"]

    assert "Region A" in value
    assert "Region B" in value


def test_unknown_area_propose_lists_the_served_areas_in_chinese() -> None:
    client = ScriptedLLMClient(_LLM_CONFIG, language="zh-CN")
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"items": {"type": "array"}, "relationship": {"type": "string"}},
    }
    proposal = (
        "无法识别区域“未知区域”。可用区域：区域 A；区域 B。"
        "未找到相近区域，请确认一个可用区域，并保留原保障速率时段。"
    )

    response = client.structured(messages=_messages(proposal), json_schema=schema)
    payload = json.loads(response.content)
    value = payload["items"][0]["value"]

    assert "区域 A" in value
    assert "区域 B" in value
