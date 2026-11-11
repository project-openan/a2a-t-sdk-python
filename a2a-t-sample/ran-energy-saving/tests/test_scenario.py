"""Tests pinning the scenario scripts to the design sequence and the WYSIWYG request rule."""

from __future__ import annotations

from demo.scenario import (
    FINAL_STATE_NEGOTIATION_ENDED,
    FINAL_STATE_TASK_COMPLETED,
    MODIFY_REQUEST,
    SCENARIOS,
    SUPPORTED_SCENARIO_NAMES,
    ExpectedStep,
    resolve_user_request,
)
from shared.run_constants import FINAL_STATE_INPUT_INVALID, MAX_ROUNDS, SUPPORTED_LANGUAGES


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


def test_every_scenario_has_exactly_one_request_per_language() -> None:
    """The page shows this text and the client sends it; there is no second canonical prompt."""
    for scenario in SCENARIOS.values():
        assert set(scenario.user_request) == set(SUPPORTED_LANGUAGES)
        for language in SUPPORTED_LANGUAGES:
            text = resolve_user_request(scenario, language)
            assert text == text.strip() and "\n" not in text


def test_requests_state_concrete_values_instead_of_relative_dates() -> None:
    """A relative date is not a concrete outage time: the extractor reports it as not provided."""
    for scenario in SCENARIOS.values():
        for language in SUPPORTED_LANGUAGES:
            text = resolve_user_request(scenario, language).lower()
            assert "tomorrow" not in text
            assert "明天" not in text
    for text in MODIFY_REQUEST.values():
        assert "tomorrow" not in text.lower()
        assert "明天" not in text
        assert "03:00" in text and "05:00" in text


def test_information_sequence_blocks_the_missing_required_field() -> None:
    scenario = SCENARIOS["information"]

    assert scenario.expected_kind == "information"
    assert _sequence("information") == ()
    assert scenario.expected_final_state == FINAL_STATE_INPUT_INVALID


def test_target_sequence_corrects_the_target_in_accept() -> None:
    scenario = SCENARIOS["target"]

    assert scenario.expected_kind == "target"
    assert _sequence("target") == (
        ("server", "PROPOSE", 1),
        ("client", "ACCEPT", 1),
    )
    assert scenario.expected_final_state == FINAL_STATE_TASK_COMPLETED
    assert [decision.action for decision in scenario.scripted_client_decisions] == ["accept"]
    accept = scenario.scripted_client_decisions[0]
    assert accept.filled_params and accept.filled_params.get("guaranteedThroughputTarget")
    assert accept.confirmed_intent


def test_feasibility_sequence() -> None:
    scenario = SCENARIOS["feasibility"]

    assert scenario.expected_kind == "feasibility"
    assert _sequence("feasibility") == (
        ("server", "PROPOSE", 1),
        ("client", "ACCEPT", 1),
    )
    assert scenario.expected_final_state == FINAL_STATE_TASK_COMPLETED
    accept = scenario.scripted_client_decisions[0]
    assert accept.filled_params and accept.filled_params.get("guaranteedThroughputTarget")


def test_reject_sequence_ends_with_plain_message() -> None:
    scenario = SCENARIOS["reject"]

    assert _sequence("reject") == (
        ("server", "PROPOSE", 1),
        ("client", "REJECT", 1),
        ("server", "MESSAGE", None),
    )
    assert scenario.expected_final_state == FINAL_STATE_NEGOTIATION_ENDED
    assert [decision.action for decision in scenario.scripted_client_decisions] == ["reject"]


def test_abort_sequence_ends_with_the_client_abort() -> None:
    scenario = SCENARIOS["abort"]

    assert _sequence("abort") == (
        ("server", "PROPOSE", 1),
        ("client", "ABORT", 1),
        ("server", "MESSAGE", None),
    )
    assert scenario.expected_final_state == FINAL_STATE_NEGOTIATION_ENDED
    assert [decision.action for decision in scenario.scripted_client_decisions] == ["abort"]


def test_expected_step_is_hashable_frozen_dataclass() -> None:
    step = ExpectedStep(actor="server", performative="PROPOSE", round=1)

    assert step.round == 1
