"""Corpus self-guard (port of the Java ``CorpusSelfGuardTest``).

Validates every Task-T scenario case file structurally without any LLM call, including the
committed non-default-language variants (``input_case_from_*_en-US.json``) when present. Unknown
keys, misaligned expectations, unknown api names, out-of-catalog error codes and
category/expectation contradictions all fail here — this is the fast gate CI runs even though the
live suites are deselected. Id uniqueness is enforced per file by the loader; ids may repeat
across the two flow files of the same scenario.
"""

from __future__ import annotations

from typing import Final

from engine.constants import (
    INPUT_CASE_FROM_DATA,
    INPUT_CASE_FROM_TEXT,
    TASK_API_NAMES,
    case_file_variants,
)
from engine.discover import ScenarioScanner
from engine.loader import CaseFileLoader, InputCase

__all__ = [
    "CATEGORY_EXCEPTION",
    "CATEGORY_NORMAL",
    "check_category_contract",
    "test_task_scenario_case_files_are_structurally_valid",
]

#: Categories carrying a cross-step expectation contract (the report/self-guard vocabulary is Chinese).
CATEGORY_NORMAL: Final[str] = "正常"

CATEGORY_EXCEPTION: Final[str] = "异常"


def test_task_scenario_case_files_are_structurally_valid() -> None:
    """Load every Task-T case file strictly and enforce the category/expectation contract."""
    problems: list[str] = []
    flow_files = (*case_file_variants(INPUT_CASE_FROM_TEXT), *case_file_variants(INPUT_CASE_FROM_DATA))
    for flow_file in flow_files:
        for scenario in ScenarioScanner.discover_optional("task", flow_file):
            cases = CaseFileLoader().load(scenario.input_file(flow_file), TASK_API_NAMES)
            for input_case in cases:
                check_category_contract(input_case, problems)
    assert not problems, "Corpus self-guard failures:\n- " + "\n- ".join(problems)


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
