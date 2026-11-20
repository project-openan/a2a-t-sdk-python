# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Strict loader for one ``input_case_*.json`` file (port of the Java ``CaseFileLoader``).

Every structural violation is collected and reported in a single fail-fast message naming the
file, the case id and the offending construct: unknown keys, missing or blank fields, mismatched
input/expected lengths, unknown api names, out-of-catalog error codes and malformed ``$fromStep``
references all fail at load time. The wire keys stay camelCase because they are the shared
Java/Python corpus contract; the dataclass fields below follow Python naming.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from a2a_t.core.errors.catalog import ErrorCatalog

__all__ = [
    "CaseFileLoader",
    "CorpusLoadException",
    "ExpectedStep",
    "InputCase",
    "InputStep",
    "catalog_codes",
]

#: Case-level metadata keys of the shared corpus contract.
CASE_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "caseDimension", "caseDesc", "caseCategory", "input", "expected"}
)

#: Keys of one input step.
STEP_KEYS: Final[frozenset[str]] = frozenset({"api", "args"})

#: Keys of one expectation.
EXPECTED_KEYS: Final[frozenset[str]] = frozenset({"result", "data", "dataExact", "error"})

#: Keys of an error expectation.
ERROR_KEYS: Final[frozenset[str]] = frozenset({"errCode", "errMessage", "errMessageContains"})

#: Closed set of per-step expected results.
KNOWN_RESULTS: Final[frozenset[str]] = frozenset({"success", "error"})

#: The closed set of SDK error catalog codes; used when an expected error code is asserted.
_CATALOG_CODES: Final[frozenset[str]] = frozenset(member.value for member in ErrorCatalog)


class CorpusLoadException(Exception):
    """Fail-fast loading failure of one corpus case file (the Java name kept for parity)."""


def catalog_codes() -> frozenset[str]:
    """Return the closed set of SDK error catalog codes."""
    return _CATALOG_CODES


@dataclass(frozen=True, slots=True)
class InputStep:
    """One SDK API step; ``args`` carries the request arguments with optional ``$fromStep`` references."""

    api: str
    args: dict[str, object]


@dataclass(frozen=True, slots=True)
class ExpectedStep:
    """One per-step expectation, aligned by index with the matching :class:`InputStep`."""

    result: str
    data: dict[str, object]
    data_exact: bool
    err_code: str
    err_message: str
    err_message_contains: str


@dataclass(frozen=True, slots=True)
class InputCase:
    """One workflow case record as declared by ``input_case_*.json``."""

    id: str
    case_dimension: str
    case_desc: str
    case_category: str
    input: list[InputStep] = field(default_factory=list)
    expected: list[ExpectedStep] = field(default_factory=list)


class CaseFileLoader:
    """Strict loader of one ``input_case_*.json`` case array."""

    def load(self, file: Path, registered_apis: set[str] | frozenset[str]) -> list[InputCase]:
        """Parse and validate one case file; every violation fails in one aggregated message."""
        try:
            payload: Any = _read_json(file)
        except CorpusLoadException:
            raise
        if not isinstance(payload, list) or not payload:
            raise CorpusLoadException(f"{file} top level must be a case array")

        problems: list[str] = []
        cases: list[InputCase] = []
        ids: set[str] = set()
        for index, node in enumerate(payload):
            case_label = f"case[{index}]"
            if not isinstance(node, dict):
                problems.append(f"{case_label} must be an object")
                continue
            unknown_keys = _unknown_keys(node, CASE_KEYS)
            if unknown_keys:
                problems.append(f"{case_label} unknown keys: {', '.join(unknown_keys)}")
                continue
            case_id = _required_text(node, "id", case_label, problems)
            if case_id:
                if case_id in ids:
                    problems.append(f"{case_label} duplicate id within the scenario: {case_id}")
                ids.add(case_id)
            _required_text(node, "caseDimension", case_label, problems)
            _required_text(node, "caseDesc", case_label, problems)
            _required_text(node, "caseCategory", case_label, problems)

            input_steps = _parse_input_steps(node.get("input"), case_label, problems, registered_apis)
            expected_steps = _parse_expected_steps(node.get("expected"), case_label, problems, len(input_steps))

            if case_id:
                _append_case_context(problems, case_id, case_label)
            cases.append(
                InputCase(
                    id=_safe_text(node.get("id")),
                    case_dimension=_safe_text(node.get("caseDimension")),
                    case_desc=_safe_text(node.get("caseDesc")),
                    case_category=_safe_text(node.get("caseCategory")),
                    input=input_steps,
                    expected=expected_steps,
                )
            )

        if problems:
            raise CorpusLoadException(f"Corpus file validation failed {file}:\n- " + "\n- ".join(problems))
        return cases


def _read_json(file: Path) -> Any:
    """Read one corpus file as parsed JSON, translating read/parse errors to load failures."""
    import json

    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CorpusLoadException(f"{file} is not valid JSON: {error}") from None
    except OSError as error:
        raise CorpusLoadException(f"Failed to read corpus file: {file} - {error}") from None


def _parse_input_steps(
    input_node: Any,
    case_label: str,
    problems: list[str],
    registered_apis: set[str] | frozenset[str],
) -> list[InputStep]:
    steps: list[InputStep] = []
    if not isinstance(input_node, list) or not input_node:
        problems.append(f"{case_label} input must be a non-empty step array")
        return steps
    for index, step_node in enumerate(input_node):
        label = f"{case_label}.input[{index}]"
        if not isinstance(step_node, dict):
            problems.append(f"{label} must be an object {{api, args}}")
            continue
        unknown_keys = _unknown_keys(step_node, STEP_KEYS)
        if unknown_keys:
            problems.append(f"{label} unknown keys: {', '.join(unknown_keys)}")
            continue
        api = _required_text(step_node, "api", label, problems)
        if api and api not in registered_apis:
            available = ", ".join(sorted(registered_apis))
            problems.append(f"{label} api not registered: {api} (available: {available})")
        args: dict[str, object] = {}
        raw_args = step_node.get("args")
        if isinstance(raw_args, dict):
            args = dict(raw_args)
            _validate_from_step_refs(args, label, problems, index + 1)
        else:
            problems.append(f"{label} args must be an object")
        steps.append(InputStep(api=_safe_text(step_node.get("api")), args=args))
    return steps


def _parse_expected_steps(
    expected_node: Any,
    case_label: str,
    problems: list[str],
    input_size: int,
) -> list[ExpectedStep]:
    expected: list[ExpectedStep] = []
    if not isinstance(expected_node, list) or not expected_node:
        problems.append(f"{case_label} expected must be a non-empty array")
        return expected
    if input_size > 0 and len(expected_node) != input_size:
        problems.append(f"{case_label} input/expected length mismatch: {input_size} != {len(expected_node)}")
    for index, step_node in enumerate(expected_node):
        label = f"{case_label}.expected[{index}]"
        if not isinstance(step_node, dict):
            problems.append(f"{label} must be an object {{result, data?, dataExact?, error?}}")
            continue
        unknown_keys = _unknown_keys(step_node, EXPECTED_KEYS)
        if unknown_keys:
            problems.append(f"{label} unknown keys: {', '.join(unknown_keys)}")
            continue
        result = _required_text(step_node, "result", label, problems)
        if result and result not in KNOWN_RESULTS:
            problems.append(f"{label} result must be success or error: {result}")
        data: dict[str, object] = {}
        raw_data = step_node.get("data")
        if raw_data is not None:
            if isinstance(raw_data, dict):
                data = dict(raw_data)
            else:
                problems.append(f"{label} data must be an object")
        data_exact = False
        raw_data_exact = step_node.get("dataExact")
        if raw_data_exact is not None:
            if isinstance(raw_data_exact, bool):
                data_exact = raw_data_exact
            else:
                problems.append(f"{label} dataExact must be a boolean")
        err_code = ""
        err_message = ""
        err_message_contains = ""
        raw_error = step_node.get("error")
        if raw_error is not None:
            if not isinstance(raw_error, dict):
                problems.append(f"{label} error must be an object {{errCode?, errMessage?, errMessageContains?}}")
            else:
                unknown_error_keys = _unknown_keys(raw_error, ERROR_KEYS)
                if unknown_error_keys:
                    problems.append(f"{label}.error unknown keys: {', '.join(unknown_error_keys)}")
                err_code = _optional_text(raw_error, "errCode", f"{label}.error", problems)
                err_message = _optional_text(raw_error, "errMessage", f"{label}.error", problems)
                err_message_contains = _optional_text(
                    raw_error, "errMessageContains", f"{label}.error", problems
                )
                if err_code and err_code not in _CATALOG_CODES:
                    problems.append(f"{label}.error errCode not in the SDK error catalog: {err_code}")
        expected.append(
            ExpectedStep(
                result=result or "",
                data=data,
                data_exact=data_exact,
                err_code=err_code,
                err_message=err_message,
                err_message_contains=err_message_contains,
            )
        )
    return expected


def _validate_from_step_refs(args: dict[str, object], label: str, problems: list[str], step_number: int) -> None:
    """Validate every ``$fromStep`` reference nested in one step's arguments."""
    _validate_from_step_value(args, label, problems, step_number)


def _validate_from_step_value(value: Any, label: str, problems: list[str], step_number: int) -> None:
    if isinstance(value, dict):
        if "$fromStep" in value or "$field" in value:
            from_step = value.get("$fromStep")
            field = value.get("$field")
            reference_only = set(value) == {"$fromStep", "$field"}
            if not reference_only:
                problems.append(f"{label} a $fromStep reference must contain exactly the two keys $fromStep and $field")
                return
            if not isinstance(from_step, int) or isinstance(from_step, bool) or from_step <= 0:
                problems.append(f"{label} $fromStep must be a positive integer: {from_step}")
            elif from_step >= step_number:
                problems.append(
                    f"{label} $fromStep={from_step} must reference an earlier step (current step {step_number})"
                )
            if not isinstance(field, str) or not field.strip():
                problems.append(f"{label} $field must be a non-blank string: {field}")
            return
        for entry in value.values():
            _validate_from_step_value(entry, label, problems, step_number)
    elif isinstance(value, list):
        for entry in value:
            _validate_from_step_value(entry, label, problems, step_number)


def _unknown_keys(node: dict[str, Any], allowed: set[str] | frozenset[str]) -> list[str]:
    return [name for name in node if name not in allowed]


def _required_text(node: dict[str, Any], key: str, label: str, problems: list[str]) -> str:
    value = node.get(key)
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{label} missing non-blank field: {key}")
        return ""
    return value


def _optional_text(node: dict[str, Any], key: str, label: str, problems: list[str]) -> str:
    value = node.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        problems.append(f"{label} field {key} must be a string")
        return ""
    return value


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _append_case_context(problems: list[str], case_id: str, case_label: str) -> None:
    """Append the case id to every problem raised while parsing this case, for grep-friendly diagnostics."""
    for index, problem in enumerate(problems):
        if problem.startswith(case_label) and "id=" not in problem:
            problems[index] = f"{problem} (id={case_id})"
