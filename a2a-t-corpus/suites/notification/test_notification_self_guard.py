"""Notification-T corpus self-guard (mirror of the Task-T ``CorpusSelfGuardTest``).

Validates every Notification-T scenario case file structurally without any LLM call: strict loading
plus the category/expectation contract. This gate runs in CI alongside the Task-T and Negotiation-T
ones.
"""

from __future__ import annotations

from typing import Final

from engine.constants import INPUT_CASE_FROM_DATA, INPUT_CASE_FROM_TEXT, NOTIFICATION_API_NAMES
from engine.discover import ScenarioScanner
from engine.loader import CaseFileLoader, InputCase

__all__ = [
    "CATEGORY_EXCEPTION",
    "CATEGORY_NORMAL",
    "check_category_contract",
    "test_notification_scenario_case_files_are_structurally_valid",
]

#: Categories carrying a cross-step expectation contract (case data is Chinese).
CATEGORY_NORMAL: Final[str] = "正常"

CATEGORY_EXCEPTION: Final[str] = "异常"


def test_notification_scenario_case_files_are_structurally_valid() -> None:
    """Load every Notification-T case file strictly and enforce the category/expectation contract."""
    problems: list[str] = []
    for flow_file in (INPUT_CASE_FROM_TEXT, INPUT_CASE_FROM_DATA):
        for scenario in ScenarioScanner.discover("notification", flow_file):
            cases = CaseFileLoader().load(scenario.input_file(flow_file), NOTIFICATION_API_NAMES)
            for input_case in cases:
                check_category_contract(input_case, problems)
    assert not problems, "Notification corpus self-guard failures:\n- " + "\n- ".join(problems)


def check_category_contract(input_case: InputCase, problems: list[str]) -> None:
    """Enforce the ``正常`` / ``异常`` category contracts of one case."""
    error_expected = 0
    for expected_step in input_case.expected:
        if expected_step.result == "error":
            error_expected += 1
            if not expected_step.err_code:
                problems.append(
                    f"{input_case.id}: error expectations must declare an errCode from the SDK error catalog"
                )
    if input_case.case_category == CATEGORY_EXCEPTION and error_expected == 0:
        problems.append(
            f"{input_case.id}: categorized as {CATEGORY_EXCEPTION} but no step expects an error"
        )
    if input_case.case_category == CATEGORY_NORMAL and error_expected > 0:
        problems.append(
            f"{input_case.id}: categorized as {CATEGORY_NORMAL} but a step expects an error; use "
            f"{CATEGORY_EXCEPTION} or \"boundary\""
        )
