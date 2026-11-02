"""Tests pinning the five scenario scripts to the design sequence."""

from __future__ import annotations

from demo.scenario import (
    FINAL_STATE_NEGOTIATION_ABORTED,
    FINAL_STATE_NEGOTIATION_ENDED,
    FINAL_STATE_TASK_COMPLETED,
    SCENARIO_OUTAGE,
    SCENARIOS,
    SUPPORTED_SCENARIO_NAMES,
    ExpectedStep,
    accept_filled_params,
    complete_initial_params,
    follow_up_initial_params,
    resolve_presentation_request,
    resolve_user_request,
)
from shared.run_constants import MAX_ROUNDS, SUPPORTED_LANGUAGES
from shared.task_schema import TASK_PARAM_SCHEMA


def _sequence(name: str) -> tuple[tuple[str, str, int | None], ...]:
    return tuple((step.actor, step.performative, step.round) for step in SCENARIOS[name].expected_sequence)


def test_max_rounds_is_three() -> None:
    assert MAX_ROUNDS == 3


def test_scenario_names_and_request_languages() -> None:
    assert SUPPORTED_SCENARIO_NAMES == (
        "night",
        "information",
        "unknown_area",
        "target",
        "feasibility",
        "reject",
        "abort",
        "outage",
    )
    for scenario in SCENARIOS.values():
        for language in SUPPORTED_LANGUAGES:
            assert resolve_user_request(scenario, language)


def test_every_request_supplies_the_required_task_description() -> None:
    """The Task-T template requires ``task_description``; every request must state it explicitly.

    A live extraction model follows the SDK rule "do not generate values not present in the input",
    so an omitted clause makes ``generate_task_prompt_from_text`` fail before Task-T is sent.
    """
    markers = {
        "en-US": "output a wireless network energy saving plan",
        "zh-CN": "输出无线网络节能方案",
    }
    for scenario in SCENARIOS.values():
        for language, marker in markers.items():
            assert marker in resolve_user_request(scenario, language)


def test_presentation_requests_are_concise_and_keep_the_wire_prompt_separate() -> None:
    """The page shows what an engineer types; Task-T generation keeps the richer prompt."""
    for scenario in SCENARIOS.values():
        for language in SUPPORTED_LANGUAGES:
            presented = resolve_presentation_request(scenario.name, language)
            assert presented
            assert presented != resolve_user_request(scenario, language)
            assert "output a wireless network energy saving plan" not in presented
            assert "预期输出" not in presented


def test_accept_params_match_the_task_schema_operation_values() -> None:
    """The refilled Task-T prompt is re-validated; its operation value must match the enum."""
    properties = TASK_PARAM_SCHEMA.get("properties")
    assert isinstance(properties, dict)
    operation_type = properties.get("operationType")
    assert isinstance(operation_type, dict)
    operation_enum = operation_type.get("enum")
    assert isinstance(operation_enum, list)
    for language in SUPPORTED_LANGUAGES:
        params = accept_filled_params("information", language)
        assert params["operationType"] in operation_enum
        assert str(params["task_description"]).strip(), "data-based Task-T requires task_description"


def test_outage_is_a_two_phase_create_then_modify_scenario() -> None:
    """The planned-outage run creates a baseline, then sends a Modify Task-T on the same Task."""
    for language in SUPPORTED_LANGUAGES:
        baseline = complete_initial_params(SCENARIO_OUTAGE, language)
        follow_up = follow_up_initial_params(SCENARIO_OUTAGE, language)
        assert baseline is not None
        assert follow_up is not None
        assert baseline["operationType"] == "Create"
        assert "backgroundEvent" not in baseline
        assert "00:00~24:00,10Mbps" in str(baseline["guaranteedThroughputTarget"])
        assert follow_up["operationType"] == "Modify"
        assert "03:00~05:00,2Mbps" in str(follow_up["guaranteedThroughputTarget"])
        assert follow_up["backgroundEvent"]
    for name in SUPPORTED_SCENARIO_NAMES:
        if name != SCENARIO_OUTAGE:
            assert follow_up_initial_params(name, "en-US") is None


def test_information_sequence() -> None:
    scenario = SCENARIOS["information"]

    assert scenario.expected_kind == "information"
    assert _sequence("information") == (
        ("server", "PROPOSE", 1),
        ("client", "ACCEPT", 1),
    )
    assert scenario.expected_final_state == FINAL_STATE_TASK_COMPLETED


def test_target_sequence_keeps_counter_propose_in_round_one() -> None:
    scenario = SCENARIOS["target"]

    assert scenario.expected_kind == "target"
    assert _sequence("target") == (
        ("server", "PROPOSE", 1),
        ("client", "PROPOSE", 1),
        ("server", "PROPOSE", 2),
        ("client", "ACCEPT", 2),
    )
    assert scenario.expected_final_state == FINAL_STATE_TASK_COMPLETED
    assert [decision.action for decision in scenario.scripted_client_decisions] == ["counter", "accept"]


def test_feasibility_sequence() -> None:
    scenario = SCENARIOS["feasibility"]

    assert scenario.expected_kind == "feasibility"
    assert _sequence("feasibility") == (
        ("server", "PROPOSE", 1),
        ("client", "ACCEPT", 1),
    )
    assert scenario.expected_final_state == FINAL_STATE_TASK_COMPLETED


def test_reject_sequence_ends_with_plain_message() -> None:
    scenario = SCENARIOS["reject"]

    assert _sequence("reject") == (
        ("server", "PROPOSE", 1),
        ("client", "REJECT", 1),
        ("server", "MESSAGE", None),
    )
    assert scenario.expected_final_state == FINAL_STATE_NEGOTIATION_ENDED
    assert [decision.action for decision in scenario.scripted_client_decisions] == ["reject"]


def test_abort_sequence_aborts_at_max_rounds() -> None:
    scenario = SCENARIOS["abort"]
    sequence = _sequence("abort")

    assert sequence[-1] == ("server", "ABORT", MAX_ROUNDS)
    assert sequence == (
        ("server", "PROPOSE", 1),
        ("client", "PROPOSE", 1),
        ("server", "PROPOSE", 2),
        ("client", "PROPOSE", 2),
        ("server", "PROPOSE", 3),
        ("client", "PROPOSE", 3),
        ("server", "ABORT", 3),
    )
    assert scenario.expected_final_state == FINAL_STATE_NEGOTIATION_ABORTED
    assert [decision.action for decision in scenario.scripted_client_decisions] == ["counter", "counter", "counter"]


def test_expected_step_is_hashable_frozen_dataclass() -> None:
    step = ExpectedStep(actor="server", performative="PROPOSE", round=1)

    assert step.round == 1
