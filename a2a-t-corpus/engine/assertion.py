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

"""Expectation comparison of the corpus engine (port of the Java ``ExpectationComparator``).

- ``result`` matches exactly (``success`` / ``error``);
- on success, ``data`` is a subset assertion: every listed key must equal; the asserted map is
  the payload root, or its nested ``data`` map when the payload carries one (FilledParamData).
  ``dataExact`` tightens the match to exactly the listed keys;
- on error, ``error.errCode`` matches the SDK catalog code exactly, ``error.errMessage`` matches
  exactly when present (otherwise any non-empty message passes) and ``error.errMessageContains``
  is a substring test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from engine.loader import ExpectedStep

if TYPE_CHECKING:
    from engine.engine import StepOutcome

__all__ = ["ExpectationComparator", "StepAssertion"]


@dataclass(frozen=True, slots=True)
class StepAssertion:
    """Comparison verdict: failed flag plus one-sentence pointer-style message."""

    passed: bool
    fail_message: str

    @classmethod
    def pass_(cls) -> "StepAssertion":
        """Return a passing verdict."""
        return cls(True, "")

    @classmethod
    def fail_(cls, message: str) -> "StepAssertion":
        """Return a failing verdict with its message."""
        return cls(False, message)


class ExpectationComparator:
    """Compares one step outcome against its expectation."""

    @staticmethod
    def compare(expected: ExpectedStep, outcome: StepOutcome) -> StepAssertion:
        """Return the verdict of one step against its expectation."""
        if outcome.actual_result == "skipped":
            return StepAssertion.fail_(
                f"step{outcome.step_no} was skipped because a previous step assertion failed"
            )
        if expected.result == "success":
            return ExpectationComparator._compare_success(expected, outcome)
        return ExpectationComparator._compare_error(expected, outcome)

    @staticmethod
    def _compare_success(expected: ExpectedStep, outcome: StepOutcome) -> StepAssertion:
        if outcome.actual_result != "success":
            actual = (
                f"engine crash({_exception_name(outcome)})"
                if outcome.crashed
                else f"error({outcome.err_code or 'no error code'})"
            )
            return StepAssertion.fail_(f"step{outcome.step_no} expected success, actual {actual}")
        target = _assertion_target(outcome.payload or {})
        for key, expected_value in expected.data.items():
            actual_value = target.get(key)
            if key not in target or not _objects_equal(expected_value, actual_value):
                return StepAssertion.fail_(
                    f"step{outcome.step_no}.data.{key} expected {expected_value} actual {actual_value}"
                )
        if expected.data_exact and set(target) != set(expected.data):
            only_actual = sorted(set(target) - set(expected.data))
            only_expected = sorted(set(expected.data) - set(target))
            return StepAssertion.fail_(
                f"step{outcome.step_no} data exact assertion failed (dataExact): "
                f"extra={only_actual} missing={only_expected}"
            )
        return StepAssertion.pass_()

    @staticmethod
    def _compare_error(expected: ExpectedStep, outcome: StepOutcome) -> StepAssertion:
        if outcome.actual_result != "error":
            expected_code = f"({expected.err_code})" if expected.err_code else ""
            return StepAssertion.fail_(
                f"step{outcome.step_no} expected error{expected_code}, actual success"
            )
        if outcome.crashed:
            return StepAssertion.fail_(
                f"step{outcome.step_no} expected error({expected.err_code}), "
                f"actual engine crash({_exception_name(outcome)})"
            )
        if expected.err_code and expected.err_code != outcome.err_code:
            return StepAssertion.fail_(
                f"step{outcome.step_no} errCode expected {expected.err_code} actual {outcome.err_code}"
            )
        if expected.err_message and expected.err_message != outcome.err_message:
            return StepAssertion.fail_(
                f"step{outcome.step_no} errMessage expected [{expected.err_message}] "
                f"actual [{outcome.err_message}]"
            )
        if not outcome.err_message:
            return StepAssertion.fail_(
                f"step{outcome.step_no} expected a non-empty error message, actual is empty"
            )
        if expected.err_message_contains and expected.err_message_contains not in (outcome.err_message or ""):
            return StepAssertion.fail_(
                f"step{outcome.step_no} errMessage does not contain "
                f"[{expected.err_message_contains}], actual [{outcome.err_message}]"
            )
        return StepAssertion.pass_()


def _assertion_target(payload: dict[str, object]) -> dict[str, object]:
    """Return the map the data subset assertion runs against.

    The nested ``data`` map of a FilledParamData-like payload is asserted when present; otherwise
    the payload root is.
    """
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _objects_equal(expected: Any, actual: Any) -> bool:
    return expected == actual


def _exception_name(outcome: StepOutcome) -> str:
    error_response = outcome.error_response
    if error_response is None:
        return "unknown error"
    name = error_response.get("exception")
    return str(name) if name is not None else "unknown error"
