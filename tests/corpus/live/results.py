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

"""Per-case verdicts of the live corpus run (port of the Java ``LiveCaseResult`` and
``LiveRunSummary`` records).

The verdict is the unit the live suite feeds into :class:`~tests.corpus.live.transcript.LiveTranscript.Run`:
``PASS`` and ``FAIL`` are assertion verdicts (a ``FAIL`` carries the assertion diff), ``ERROR``
marks a case that could not be judged — an engine failure or an infrastructure failure that
survived all retries, re-raised so the test verdict matches the recorded one. ``SKIP`` is reserved
for a case the run did not execute; the phase-1 engine never records it.

The JSON projection (:func:`LiveCaseResult.to_json` / :func:`LiveLlmCall.to_json`) carries the
Java camelCase field names verbatim, so a Python transcript stays consumable by
``tools/live_transcript_export.py`` (the near-verbatim port of the Java export tool) and by
anything already reading Java transcripts.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from tests.corpus.llm_stub import LiveLlmCall

__all__ = ["LiveCaseResult", "LiveRunSummary", "Outcome"]


class Outcome(Enum):
    """Verdict of one live case."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    ERROR = "ERROR"


class LiveCaseResult:
    """The per-case verdict of one live corpus run: what the case asserted, what the pipeline
    extracted from the real model's output, and the LLM calls the case triggered.

    Unlike the rest of the corpus models this is a mutable accumulator the engine fills in as the
    run progresses, so it is a plain class with read-only properties rather than a frozen
    dataclass (the Java record counterpart is immutable, but every field is set at once by the
    engine's result assembly, which the constructor mirrors).
    """

    __slots__ = (
        "case_id",
        "outcome",
        "assertion_summary",
        "input_summary",
        "scenario_code",
        "params",
        "llm_calls",
        "duration_ms",
        "failure_diff",
    )

    def __init__(
        self,
        case_id: str,
        outcome: Outcome,
        assertion_summary: str,
        input_summary: str | None,
        scenario_code: str | None,
        params: dict[str, Any] | None,
        llm_calls: list[LiveLlmCall],
        duration_ms: int,
        failure_diff: str | None,
    ) -> None:
        """Assemble one verdict.

        Args:
            case_id: expanded case id, such as ``LIVE-GEN-01/zh-CN``.
            outcome: verdict of the case.
            assertion_summary: one-line summary of what the case asserted.
            input_summary: excerpt of the case's input — the natural-language text of a
                generation case or the prompt text of a validation case, truncated to the
                reporting length — or ``None`` when the case declares none.
            scenario_code: scenario code the pipeline actually recognized, or ``None``.
            params: parameters the pipeline actually extracted, or ``None`` when the case
                produced no parameter data. The filled parameter data legitimately carries
                ``None`` values (a schema slot the prompt misses), exactly what the
                ``paramsAbsent`` probes assert, so ``None``-valued entries stay.
            llm_calls: LLM calls the case triggered, recorded by the recording client.
            duration_ms: wall-clock duration of the case in milliseconds.
            failure_diff: assertion diff of a failed case, or ``None`` otherwise.

        Raises:
            TypeError: when ``case_id``, ``outcome``, ``assertion_summary`` or ``llm_calls`` is
                ``None`` (the Java ``Objects.requireNonNull`` counterpart).
        """
        for name, value in (
            ("case_id", case_id),
            ("outcome", outcome),
            ("assertion_summary", assertion_summary),
            ("llm_calls", llm_calls),
        ):
            if value is None:
                raise TypeError(f"LiveCaseResult requires a non-null '{name}'.")
        self.case_id = case_id
        self.outcome = outcome
        self.assertion_summary = assertion_summary
        self.input_summary = input_summary
        self.scenario_code = scenario_code
        self.params = None if params is None else dict(params)
        self.llm_calls = list(llm_calls)
        self.duration_ms = duration_ms
        self.failure_diff = failure_diff

    def to_json(self) -> dict[str, Any]:
        """Project this verdict onto the Java transcript JSON shape (camelCase keys)."""
        return {
            "caseId": self.case_id,
            "outcome": self.outcome.value,
            "assertionSummary": self.assertion_summary,
            "inputSummary": self.input_summary,
            "scenarioCode": self.scenario_code,
            "params": None if self.params is None else dict(self.params),
            "llmCalls": [call_to_json(call) for call in self.llm_calls],
            "durationMs": self.duration_ms,
            "failureDiff": self.failure_diff,
        }


class LiveRunSummary:
    """The aggregate of one live corpus run: the case verdict counts, the total LLM traffic and
    the schema-adherence statistic.

    ``schema_parse_failure_count`` is the M5 schema-adherence data point: the number of successful
    LLM responses whose content did not parse as a JSON object.
    """

    __slots__ = (
        "total_cases",
        "pass_count",
        "fail_count",
        "skip_count",
        "error_count",
        "total_llm_calls",
        "total_prompt_tokens",
        "total_completion_tokens",
        "schema_parse_failure_count",
    )

    def __init__(
        self,
        total_cases: int,
        pass_count: int,
        fail_count: int,
        skip_count: int,
        error_count: int,
        total_llm_calls: int,
        total_prompt_tokens: int,
        total_completion_tokens: int,
        schema_parse_failure_count: int,
    ) -> None:
        """Carry the aggregate of one run; every field is a plain count."""
        self.total_cases = total_cases
        self.pass_count = pass_count
        self.fail_count = fail_count
        self.skip_count = skip_count
        self.error_count = error_count
        self.total_llm_calls = total_llm_calls
        self.total_prompt_tokens = total_prompt_tokens
        self.total_completion_tokens = total_completion_tokens
        self.schema_parse_failure_count = schema_parse_failure_count

    def to_json(self) -> dict[str, Any]:
        """Project this aggregate onto the Java ``summary.json`` shape (camelCase keys)."""
        return {
            "totalCases": self.total_cases,
            "passCount": self.pass_count,
            "failCount": self.fail_count,
            "skipCount": self.skip_count,
            "errorCount": self.error_count,
            "totalLlmCalls": self.total_llm_calls,
            "totalPromptTokens": self.total_prompt_tokens,
            "totalCompletionTokens": self.total_completion_tokens,
            "schemaParseFailureCount": self.schema_parse_failure_count,
        }

    def __repr__(self) -> str:
        return (
            f"LiveRunSummary(total={self.total_cases}, pass={self.pass_count}, fail={self.fail_count}, "
            f"error={self.error_count}, skip={self.skip_count}, llmCalls={self.total_llm_calls}, "
            f"tokens={self.total_prompt_tokens}+{self.total_completion_tokens}, "
            f"schemaParseFailures={self.schema_parse_failure_count})"
        )


def call_to_json(call: LiveLlmCall) -> dict[str, Any]:
    """Project one recorded LLM call onto the Java transcript JSON shape (camelCase keys)."""
    return {
        "messages": [dict(message) for message in call.messages],
        "jsonSchema": None if call.json_schema is None else dict(call.json_schema),
        "temperature": call.temperature,
        "maxTokens": call.max_tokens,
        "content": call.content,
        "model": call.model,
        "usage": dict(call.usage),
        "durationMs": call.duration_ms,
        "error": call.error,
    }
