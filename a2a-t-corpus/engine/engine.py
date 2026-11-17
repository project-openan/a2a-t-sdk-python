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

"""Case engine of the corpus (port of the Java ``WorkflowEngine`` and ``StepOutcome``).

Dispatches the ordered steps of one case to the registered SDK APIs, records the full transcript
and compares every step against its expectation. Cases are fully isolated: one failing case never
aborts the run. The step outcomes carry the outcome/`success`/`error`/`skipped` result, the resolved
request, the serialized payload or exception dump, the machine-readable error code (``None`` when
the failure is not an SDK coded error) and the LLM calls recorded during the step.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from engine.assertion import ExpectationComparator
from engine.errors_serializer import to_map
from engine.from_step import FromStepResolver
from engine.loader import InputCase, InputStep
from engine.recorder import RecordedLlmCall, RecordingMeteredLlmClient
from engine.registry import ApiRegistry

from a2a_t.core.errors.exceptions import A2ATError

__all__ = ["CaseResult", "Interaction", "StepOutcome", "WorkflowEngine"]


@dataclass(slots=True)
class StepOutcome:
    """Result of executing one input step.

    Attributes:
        step_no: 1-based step number.
        api: executed API method name.
        actual_result: ``success`` / ``error`` / ``skipped``.
        request: resolved request arguments actually passed to the SDK.
        payload: serialized response payload on success.
        error_response: serialized exception dump on error.
        err_code: machine-readable error code; ``None`` when the failure is not an SDK coded error.
        err_message: failure message, when any.
        duration_ms: step wall-clock duration.
        llm_calls: LLM invocations recorded during this step.
        crashed: true when the step failed with a non-coded error (engine-level crash).
    """

    step_no: int
    api: str
    actual_result: str
    request: dict[str, object] | None
    payload: dict[str, object] | None
    error_response: dict[str, object] | None
    err_code: str | None
    err_message: str | None
    duration_ms: int
    llm_calls: list[RecordedLlmCall] = field(default_factory=list)
    crashed: bool = False

    @classmethod
    def success(
        cls,
        step_no: int,
        api: str,
        request: dict[str, object],
        payload: dict[str, object],
        duration_ms: int,
        llm_calls: list[RecordedLlmCall],
    ) -> "StepOutcome":
        """Return a successful step outcome."""
        return cls(
            step_no=step_no,
            api=api,
            actual_result="success",
            request=request,
            payload=payload,
            error_response=None,
            err_code=None,
            err_message=None,
            duration_ms=duration_ms,
            llm_calls=llm_calls,
            crashed=False,
        )

    @classmethod
    def error(
        cls,
        step_no: int,
        api: str,
        request: dict[str, object],
        error_response: dict[str, object],
        err_code: str | None,
        err_message: str | None,
        crashed: bool,
        duration_ms: int,
        llm_calls: list[RecordedLlmCall],
    ) -> "StepOutcome":
        """Return a failed step outcome."""
        return cls(
            step_no=step_no,
            api=api,
            actual_result="error",
            request=request,
            payload=None,
            error_response=error_response,
            err_code=err_code,
            err_message=err_message,
            duration_ms=duration_ms,
            llm_calls=llm_calls,
            crashed=crashed,
        )

    @classmethod
    def skipped(cls, step_no: int, api: str, request: dict[str, object], duration_ms: int) -> "StepOutcome":
        """Return a skipped step outcome (a previous assertion already failed)."""
        return cls(
            step_no=step_no,
            api=api,
            actual_result="skipped",
            request=request,
            payload=None,
            error_response=None,
            err_code=None,
            err_message=None,
            duration_ms=duration_ms,
            llm_calls=[],
            crashed=False,
        )


@dataclass(slots=True)
class Interaction:
    """One transcript row: either an SDK API step or one of its underlying LLM calls."""

    step: int
    type: str
    result: str
    duration_ms: int
    request: dict[str, object]
    response: dict[str, object]
    input_token: int
    output_token: int


@dataclass(slots=True)
class CaseResult:
    """Execution transcript of one case."""

    scenario: str
    input_case: InputCase
    verdict: str
    fail_reason: str
    total_duration_ms: int
    total_input_token: int
    total_output_token: int
    interactions: list[Interaction] = field(default_factory=list)


class WorkflowEngine:
    """Runs one case end-to-end and produces its transcript; never raises for case-level failures."""

    def __init__(self, registry: ApiRegistry, recorder: RecordingMeteredLlmClient) -> None:
        self._registry = registry
        self._recorder = recorder

    def run(self, scenario_name: str, input_case: InputCase) -> CaseResult:
        """Run one case and return its transcript."""
        self._recorder.drain()

        prior_payloads: list[dict[str, object] | None] = []
        outcomes: list[StepOutcome] = []
        fail_reason = ""
        prior_assertion_failed = False
        crashed = False

        for index, step in enumerate(input_case.input):
            expected = input_case.expected[index]
            if prior_assertion_failed and expected.result != "error":
                outcome = StepOutcome.skipped(
                    index + 1, step.api, _soft_resolve(step, index + 1, prior_payloads), 0
                )
            else:
                outcome = self._execute_step(index + 1, step, prior_payloads)
                assertion = ExpectationComparator.compare(expected, outcome)
                if not assertion.passed:
                    if not fail_reason:
                        fail_reason = assertion.fail_message
                    prior_assertion_failed = True
                if outcome.crashed:
                    crashed = True
            prior_payloads.append(outcome.payload)
            outcomes.append(outcome)

        if crashed:
            verdict = "error"
        elif prior_assertion_failed:
            verdict = "failure"
        else:
            verdict = "success"

        total_duration_ms = 0
        total_input_token = 0
        total_output_token = 0
        interactions: list[Interaction] = []
        counter = 0
        for outcome in outcomes:
            counter += 1
            step_input_token = _token_sum(outcome.llm_calls, input_tokens=True)
            step_output_token = _token_sum(outcome.llm_calls, input_tokens=False)
            total_input_token += step_input_token
            total_output_token += step_output_token
            total_duration_ms += outcome.duration_ms
            interactions.append(
                Interaction(
                    step=counter,
                    type=outcome.api,
                    result=outcome.actual_result,
                    duration_ms=outcome.duration_ms,
                    request=outcome.request or {},
                    response=_display_response(outcome),
                    input_token=step_input_token,
                    output_token=step_output_token,
                )
            )
            for call in outcome.llm_calls:
                counter += 1
                interactions.append(
                    Interaction(
                        step=counter,
                        type="LLM",
                        result=call.result,
                        duration_ms=call.duration_ms,
                        request=call.request,
                        response=call.response,
                        input_token=call.input_token,
                        output_token=call.output_token,
                    )
                )

        return CaseResult(
            scenario=scenario_name,
            input_case=input_case,
            verdict=verdict,
            fail_reason=fail_reason,
            total_duration_ms=total_duration_ms,
            total_input_token=total_input_token,
            total_output_token=total_output_token,
            interactions=interactions,
        )

    def _execute_step(
        self,
        step_no: int,
        step: InputStep,
        prior_payloads: list[dict[str, object] | None],
    ) -> StepOutcome:
        started_at = time.perf_counter()
        try:
            request = FromStepResolver.resolve(step.args, step_no, prior_payloads)
        except Exception as error:
            duration_ms = _elapsed_ms(started_at)
            self._recorder.drain()
            return StepOutcome.error(
                step_no=step_no,
                api=step.api,
                request=step.args,
                error_response=to_map(error),
                err_code=None,
                err_message=str(error),
                crashed=True,
                duration_ms=duration_ms,
                llm_calls=[],
            )
        self._recorder.drain()
        try:
            handler = self._registry.handler(step.api)
            if handler is None:
                raise ValueError(f"api not registered: {step.api}")
            payload = handler(request)
            llm_calls = self._recorder.drain()
            return StepOutcome.success(step_no, step.api, request, payload, _elapsed_ms(started_at), llm_calls)
        except BaseException as error:
            llm_calls = self._recorder.drain()
            coded = isinstance(error, A2ATError)
            return StepOutcome.error(
                step_no=step_no,
                api=step.api,
                request=request,
                error_response=to_map(error),
                err_code=error.code_str if coded else None,
                err_message=str(error),
                crashed=not coded,
                duration_ms=_elapsed_ms(started_at),
                llm_calls=llm_calls,
            )


def _soft_resolve(
    step: InputStep,
    step_no: int,
    prior_payloads: list[dict[str, object] | None],
) -> dict[str, object]:
    """Resolve the arguments of a skipped step, falling back to the raw arguments on failure."""
    try:
        return FromStepResolver.resolve(step.args, step_no, prior_payloads)
    except Exception:
        return step.args


def _display_response(outcome: StepOutcome) -> dict[str, object]:
    if outcome.actual_result == "error":
        return outcome.error_response or {}
    if outcome.actual_result == "success":
        return outcome.payload or {}
    return {"reason": "Previous step assertion failed; this step was skipped"}


def _token_sum(calls: list[RecordedLlmCall], *, input_tokens: bool) -> int:
    return sum(call.input_token if input_tokens else call.output_token for call in calls)


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))
