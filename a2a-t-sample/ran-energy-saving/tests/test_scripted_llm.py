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
    STAGE_SLOT_EXTRACTION_OUTAGE,
    STAGE_SLOT_EXTRACTION_TARGET,
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
    "unknown_area": (STAGE_SLOT_EXTRACTION, STAGE_CONTENT_VALIDATION),
    "target": (STAGE_SLOT_EXTRACTION_TARGET, STAGE_CONTENT_VALIDATION_TARGET),
    "feasibility": (STAGE_SLOT_EXTRACTION_FEASIBILITY, STAGE_CONTENT_VALIDATION_FEASIBILITY),
    "reject": (STAGE_SLOT_EXTRACTION_MISSING, STAGE_CONTENT_VALIDATION_MISSING),
    "abort": (STAGE_SLOT_EXTRACTION_TARGET, STAGE_CONTENT_VALIDATION_TARGET),
    "outage": (STAGE_SLOT_EXTRACTION_OUTAGE, STAGE_CONTENT_VALIDATION_OUTAGE),
}


def _messages(content: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": "route me"}, {"role": "user", "content": content}]


@pytest.fixture
def client() -> ScriptedLLMClient:
    return ScriptedLLMClient(_LLM_CONFIG, language="en-US")


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


def test_complete_input_routes_to_default_slot_extraction() -> None:
    from demo.scenario import NATURAL_LANGUAGE_PROMPT_INPUT_EN

    stage = route_structured_call(_messages(NATURAL_LANGUAGE_PROMPT_INPUT_EN), SLOT_SCHEMA, language="en-US")

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
    """The missing throughput target is optional to the client but required by the server.

    The region stays in the request (the Task-T template marks ``task_object`` required), so the
    client's ``fromText`` extraction must render a prompt; the omitted ``guaranteedThroughputTarget``
    (the template's optional ``task_context``) must then make the server report
    ``content.param_missing`` and map to the information negotiation.
    """
    assert "guaranteedThroughputTarget" in cast(list[str], TASK_PARAM_SCHEMA["required"])
    register_scripted_provider()
    monkeypatch.setenv("A2AT_LANGUAGE", offline_config.language)
    env_path = prepare_sdk_env_path(offline_config)
    client = A2ATClient(env_path=env_path)
    server = A2ATServer(env_path=env_path)
    request = SCENARIOS["information"].user_request["en-US"]

    content = client.generate_task_prompt_from_text(request, TASK_TEMPLATE_URI)
    prompt_text = content.prompt_text
    assert prompt_text is not None

    assert "Songshanhu Administration Committee" in prompt_text
    assert "07:00~17:30,10Mbps" not in prompt_text
    with pytest.raises(ContentValidationError) as excinfo:
        server.validate_task_prompt_and_data_filling(
            prompt_text,
            TASK_PARAM_SCHEMA,
            TASK_TEMPLATE_URI,
        )

    errors = excinfo.value.errors
    assert [error.slot_name for error in errors] == ["guaranteedThroughputTarget"]
    assert errors[0].code == "content.param_missing"
    assert negotiation_kind_for_errors(errors) is NegotiationKind.INFORMATION


def test_real_server_validates_accept_offline(real_negotiation_server: A2ATServer) -> None:
    result = real_negotiation_server.validate_accept_prompt_and_data_filling(
        "Accept. region: Songshanhu Administration Committee.",
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
        "Accept. region: 松山湖管委会。",
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
