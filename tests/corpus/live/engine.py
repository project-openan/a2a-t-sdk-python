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

"""Executes one live corpus case against the real LLM endpoint (port of the Java
``LiveCaseEngine``; design document §4).

Everything except the model itself is production assembly: the engine wraps the real provider
client built from :class:`~tests.corpus.live.config.LiveLlmConfig` in a
:class:`~tests.corpus.llm_stub.RecordingLLMClient` and hands the wrapper to the
:class:`~tests.corpus.assemblers.TaskApiAssembler` — the real facade builders' assembly of the
closed loop — driven by the :mod:`~tests.corpus.live.env_writer` bridge, so the pipeline retry
limit comes from the bridge's ``A2AT_LLM_MAX_ATTEMPTS=3`` ([R6]/[R7], not a per-record value); the
offline engine-test seam keeps the scripted minimal env instead. A generation case runs the whole
mini closed loop its records assert against: the generated prompt is fed straight into
``validate_task_prompt_and_data_filling`` with the record's schema, so ``paramsContains`` /
``paramsAbsent`` judge the validate-step parameter keys and the two calls together make up the
``maxLlmCalls`` budget. ``scenarioCode`` compares the scenario segment of the template URI the
pipeline echoes — the from-text task API resolves the template from the caller's URI and reports
no separate recognition result.

Retry semantics (Q7): only infrastructure failures retry — LLM runtime errors and IO/timeout
exceptions, raw or wrapped by the pipeline's LLM-infrastructure error codes — at most
``A2AT_CORPUS_LIVE_INFRA_RETRIES`` times (default 2, the Java ``-Dcorpus.live.infraRetries``
property channel), each attempt on a fresh recording client; an assertion mismatch fails
immediately. Every case verdict is appended to the run's transcript before it surfaces: the
transcript keeps the recorded LLM calls of the failed attempts too, while ``maxLlmCalls`` bounds
only the final, judged attempt.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError
from a2a_t.core.metadata import MetadataContent
from a2a_t.core.template_uri import TemplateUri
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.provider import LLMClient
from tests.corpus.assemblers import TaskApiAssembler
from tests.corpus.live.config import LiveLlmConfig, create_llm_client
from tests.corpus.live.env_writer import env_file_for
from tests.corpus.live.results import LiveCaseResult, Outcome
from tests.corpus.live.transcript import LiveTranscriptRun
from tests.corpus.llm_stub import RecordingLLMClient
from tests.corpus.models import LiveCase, NegotiationApi, PromptSource

__all__ = ["INFRA_RETRIES_VARIABLE", "LiveCaseEngine"]

#: Environment variable overriding the infra-retry limit (the Python counterpart of the Java
#: ``-Dcorpus.live.infraRetries`` system property channel).
INFRA_RETRIES_VARIABLE: Final[str] = "A2AT_CORPUS_LIVE_INFRA_RETRIES"

#: Default of the infra-retry limit: at most two retries, three attempts in total.
DEFAULT_INFRA_RETRIES: Final[int] = 2

#: Fixed pipeline retry limit of the live env bridge ([R6]: the record does not declare it).
PIPELINE_MAX_ATTEMPTS: Final[int] = 3

#: Length an input or prompt excerpt is truncated to in verdicts and failure diffs.
MAX_REPORTED_TEXT_LENGTH: Final[int] = 400

#: The retryable pipeline error codes: raw infrastructure surfaced through the catalog.
_INFRA_ERROR_CODES: Final[frozenset[ErrorCatalog]] = frozenset(
    {
        ErrorCatalog.LLM_INVOCATION_FAILED,
        ErrorCatalog.LLM_RESPONSE_INVALID,
        ErrorCatalog.LLM_NOT_CONFIGURED,
    }
)


class LiveCaseEngine:
    """Executes one :class:`~tests.corpus.models.LiveCase` against the real endpoint and asserts
    its :class:`~tests.corpus.models.LiveExpectation` block."""

    def __init__(self, config: LiveLlmConfig, transcript: LiveTranscriptRun) -> None:
        """Create the engine for one suite run: the real provider client of the given
        configuration, the default infra-retry limit, the live ``.env`` bridge of the
        configuration and the run the verdicts are appended to.

        Args:
            config: resolved live test configuration.
            transcript: run handle the case verdicts are appended to.
        """
        self._llm_client = create_llm_client(config)
        self._infra_retries = _infra_retry_limit()
        self._env_file: Path | None = env_file_for(config)
        self._transcript = transcript

    @classmethod
    def for_testing(
        cls,
        llm_client: LLMClient,
        infra_retries: int,
        env_file: Path | None,
        transcript: LiveTranscriptRun,
    ) -> LiveCaseEngine:
        """Create the engine around an explicit LLM client, env file and infra-retry limit.

        The seam of the offline engine tests, which drive the assertion logic with a stub client
        and the scripted minimal env instead of a real endpoint and the live env bridge.

        Args:
            llm_client: LLM client every recording wrapper delegates to.
            infra_retries: number of infrastructure retries after the first attempt (negative
                values clamp to 0).
            env_file: the ``.env`` file the task assembly loads (the env-writer bridge), or
                ``None`` to fall back to the scripted minimal env with the fixed pipeline retry
                limit.
            transcript: run handle the case verdicts are appended to.
        """
        engine = cls.__new__(cls)
        engine._llm_client = llm_client
        engine._infra_retries = max(0, infra_retries)
        engine._env_file = env_file
        engine._transcript = transcript
        return engine

    def run(self, live_case: LiveCase) -> LiveCaseResult:
        """Run one live case, append its verdict to the transcript and return it.

        Args:
            live_case: expanded live corpus case.

        Returns:
            the verdict of the executed case.

        Raises:
            AssertionError: when a live expectation mismatches (the transcript carries the same
                diff).
            Exception: when the engine itself failed, or an infrastructure failure survived all
                retries.
        """
        start = time.perf_counter()
        attempts = self._infra_retries + 1
        recorded_calls = []
        for attempt in range(1, attempts + 1):
            recording = RecordingLLMClient(self._llm_client)
            assembler = (
                TaskApiAssembler(live_case.language, PIPELINE_MAX_ATTEMPTS, recording)
                if self._env_file is None
                else TaskApiAssembler.from_env_path(self._env_file, recording)
            )
            try:
                judged = _judge(assembler, recording, live_case)
                recorded_calls.extend(recording.snapshot())
                passed = judged.failure is None
                result = _as_result(
                    live_case,
                    judged,
                    Outcome.PASS if passed else Outcome.FAIL,
                    recorded_calls,
                    _duration_ms(start),
                    None if passed else str(judged.failure),
                )
                self._transcript.append_case(result)
                if not passed:
                    raise judged.failure
                return result
            except AssertionError:
                # An assertion mismatch is a verdict, not an outage: already appended above.
                raise
            except Exception as error:
                recorded_calls.extend(recording.snapshot())
                if _is_infrastructure_failure(error) and attempt < attempts:
                    continue
                # A surviving failure is recorded as ERROR and re-raised, so the transcript
                # verdict and the test verdict agree on the same red instead of the summary
                # counting a skip the build reports as an error.
                failure_diff = (
                    f"infrastructure failure after {attempts} attempts: {error}"
                    if _is_infrastructure_failure(error)
                    else f"{type(error).__name__}: {error}"
                )
                error_result = _as_result(
                    live_case,
                    None,
                    Outcome.ERROR,
                    recorded_calls,
                    _duration_ms(start),
                    failure_diff,
                )
                self._transcript.append_case(error_result)
                raise
        raise RuntimeError(f"{live_case.error_prefix()} the live engine left the retry loop without a verdict")


# --------------------------------------------------------------------- judging


@dataclass(slots=True)
class _Judged:
    """The judgement of one attempt: the pipeline output plus the first failed assertion."""

    judgement: _Judgement | None
    failure: AssertionError | None


@dataclass(slots=True)
class _Judgement:
    """What the pipeline produced for one case.

    ``message`` is the generated message of a generation case (``None`` for a pure validation case
    or an expected failure), ``params`` the filled parameter data of the validate step, and
    ``scenario_code`` the scenario code the pipeline reported.
    """

    message: MetadataContent | None
    params: FilledParamData | None
    scenario_code: str | None


def _judge(assembler: TaskApiAssembler, recording: RecordingLLMClient, live_case: LiveCase) -> _Judged:
    """Judge one attempt without throwing for an expectation mismatch.

    The returned :class:`_Judged` carries the pipeline output plus the first failed assertion, so
    the transcript can embed what the pipeline actually produced even for a failed case. Only
    infrastructure failures propagate — they are retried by :meth:`LiveCaseEngine.run`.
    """
    expect = live_case.live_expect
    judgement: _Judgement | None = None
    pipeline_failure: Exception | None = None
    try:
        judgement = _invoke(assembler, live_case)
    except Exception as error:  # noqa: BLE001 - classified below, never swallowed
        if _is_infrastructure_failure(error):
            raise
        pipeline_failure = error
    failure: AssertionError | None = None
    if expect.success:
        failure = (
            _fail(live_case, "$.expect.success", "success", f"failure ({pipeline_failure})")
            if pipeline_failure is not None
            else _assert_value_expectations(live_case, judgement)
        )
    elif pipeline_failure is None:
        params = "(no parameter data)" if judgement is None or judgement.params is None else str(judgement.params.data)
        failure = _fail(live_case, "$.expect.success", "failure", f"success ({params})")
    if failure is None and expect.max_llm_calls is not None and recording.call_count > expect.max_llm_calls:
        failure = _fail(
            live_case,
            "$.expect.maxLlmCalls",
            f"at most {expect.max_llm_calls} LLM calls",
            str(recording.call_count),
        )
    return _Judged(judgement, failure)


def _assert_value_expectations(live_case: LiveCase, judgement: _Judgement | None) -> AssertionError | None:
    """Assert the value-level live expectations; return the first mismatch instead of throwing so
    the caller keeps the pipeline output for the transcript."""
    expect = live_case.live_expect
    if judgement is None:
        judgement = _Judgement(None, None, None)
    if expect.scenario_code is not None and expect.scenario_code != judgement.scenario_code:
        return _fail(live_case, "$.expect.scenarioCode", expect.scenario_code, str(judgement.scenario_code))
    params = {} if judgement.params is None else judgement.params.data
    for key, expected in expect.params_contains.items():
        actual = params.get(key)
        if actual is None:
            spelling = "null" if key in params else "(missing)"
            return _fail(live_case, "$.expect.paramsContains", f"{key}={expected}", f"{key}={spelling}")
        if not _value_matches(expected, actual):
            return _fail(live_case, "$.expect.paramsContains", f"{key}={expected}", f"{key}={actual}")
    for slot in expect.params_absent:
        value = params.get(slot)
        if value is not None:
            return _fail(live_case, "$.expect.paramsAbsent", f"{slot} null or missing", f"{slot}={value}")
    for fragment in expect.prompt_text_contains:
        prompt_text = (
            ""
            if judgement.message is None or judgement.message.prompt_text is None
            else (judgement.message.prompt_text)
        )
        if _normalize(fragment) not in _normalize(prompt_text):
            return _fail(
                live_case,
                "$.expect.promptTextContains",
                f"a prompt text containing '{fragment}'",
                _quoted(_truncate(_normalize(prompt_text))),
            )
    return None


# --------------------------------------------------------------------- API dispatch


def _invoke(assembler: TaskApiAssembler, live_case: LiveCase) -> _Judgement:
    """Invoke the case's API on the real task assembly.

    The generation API runs the mini closed loop (generate, then validate the generated prompt
    against the record's schema); the validation API validates the record's prompt.
    """
    template_uri = _parse_template_uri(live_case)
    schema = _require_schema(live_case)
    if live_case.api is NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT:
        message = assembler.generate_task_prompt_from_text(_require_input_text(live_case), template_uri)
        params = assembler.validate_task_prompt_and_data_filling(message.prompt_text or "", schema, template_uri)
        return _Judgement(message, params, _scenario_of(message.template_uri))
    if live_case.api is NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING:
        params = assembler.validate_task_prompt_and_data_filling(_require_prompt_text(live_case), schema, template_uri)
        return _Judgement(None, params, _scenario_of(template_uri.uri))
    raise RuntimeError(
        f"{live_case.error_prefix()} the live engine covers the two phase-1 task APIs but got {live_case.api.json_name}"
    )


# --------------------------------------------------------------------- result assembly


def _as_result(
    live_case: LiveCase,
    judged: _Judged | None,
    outcome: Outcome,
    recorded_calls: list[Any],
    duration_ms: int,
    failure_diff: str | None,
) -> LiveCaseResult:
    """Assemble the verdict of one executed live case."""
    judgement = None if judged is None else judged.judgement
    return LiveCaseResult(
        case_id=live_case.id,
        outcome=outcome,
        assertion_summary=_assertion_summary(live_case, len(recorded_calls)),
        input_summary=_input_summary_of(live_case),
        scenario_code=None if judgement is None else judgement.scenario_code,
        params=None if judgement is None or judgement.params is None else dict(judgement.params.data),
        llm_calls=list(recorded_calls),
        duration_ms=duration_ms,
        failure_diff=failure_diff,
    )


def _input_summary_of(live_case: LiveCase) -> str | None:
    """Excerpt of what the case fed the pipeline (the transcript's input summary).

    The natural-language text of a generation case, or the prompt text a validation case
    validates, truncated to the reporting length.
    """
    if live_case.api is NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT:
        text = live_case.input_text
    elif isinstance(live_case.prompt, PromptSource.Text):
        text = live_case.prompt.text
    else:
        text = None
    return None if text is None else _truncate(_normalize(text))


def _assertion_summary(live_case: LiveCase, llm_calls: int) -> str:
    """One-line summary of what the case asserted, with the actual LLM call count."""
    expect = live_case.live_expect
    summary = f"expect.success={str(expect.success).lower()}"
    if expect.scenario_code is not None:
        summary += f" scenarioCode={expect.scenario_code}"
    if expect.params_contains:
        summary += f" paramsContains={list(expect.params_contains)}"
    if expect.params_absent:
        summary += f" paramsAbsent={expect.params_absent}"
    if expect.prompt_text_contains:
        summary += f" promptTextContains={len(expect.prompt_text_contains)}"
    if expect.max_llm_calls is not None:
        summary += f" maxLlmCalls<={expect.max_llm_calls}"
    return summary + f" llmCalls={llm_calls}"


# --------------------------------------------------------------------- classification


def _is_infrastructure_failure(error: BaseException) -> bool:
    """Classify one pipeline exception as an infrastructure failure (Q7).

    The LLM client's runtime errors and IO/timeout exceptions, raw or anywhere in the pipeline's
    wrapping cause chain, or an error carrying one of the retryable ``llm.*`` codes. Semantic
    rejections and rule violations are verdicts, not outages, and never retry.
    """
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (LLMRuntimeError, OSError)):
            return True
        if isinstance(current, A2ATError) and current.code in _INFRA_ERROR_CODES:
            return True
        current = current.__cause__ or current.__context__
    return False


def _infra_retry_limit() -> int:
    """Resolve the infra-retry limit from the environment, clamped to at least zero."""
    raw = os.environ.get(INFRA_RETRIES_VARIABLE)
    if raw is None or not raw.strip():
        return DEFAULT_INFRA_RETRIES
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return DEFAULT_INFRA_RETRIES


# --------------------------------------------------------------------- helpers


def _value_matches(expected: object, actual: object) -> bool:
    """Compare one expected slot value with the extracted one.

    Strings compare trimmed (model outputs may pad), and a number matches its JSON string form so
    a schema-declared number and its quoted extraction stay equivalent.
    """
    expected_number = _to_double_or_none(expected)
    actual_number = _to_double_or_none(actual)
    if (
        expected_number is not None
        and actual_number is not None
        and (isinstance(expected, (int, float)) or isinstance(actual, (int, float)))
    ):
        return expected_number == actual_number
    return str(expected).strip() == str(actual).strip()


def _to_double_or_none(value: object) -> float | None:
    """Read one value as a number, parsing a numeric string form."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _scenario_of(template_uri: str | None) -> str | None:
    """Return the scenario segment of a template URI — the last path segment of the Task-T layout
    (``Task-T/network-layer/private-line-complaint/v1`` names the scenario
    ``private-line-complaint``); ``None`` when the URI does not parse."""
    parsed = TemplateUri.parse(template_uri)
    return None if parsed is None else parsed.path_segments[-1]


def _parse_template_uri(live_case: LiveCase) -> TemplateUri:
    """Parse the case's template URI or fail with the case's error prefix."""
    raw = live_case.template_uri
    if raw is None:
        raise RuntimeError(f"{live_case.error_prefix()} templateUri: the live task APIs require a template URI")
    parsed = TemplateUri.parse(raw)
    if parsed is None:
        raise RuntimeError(f"{live_case.error_prefix()} templateUri: unparseable template URI {raw}")
    return parsed


def _require_input_text(live_case: LiveCase) -> str:
    """Read the case's natural-language input text or fail with the case's error prefix."""
    if live_case.input_text is None:
        raise RuntimeError(f"{live_case.error_prefix()} input.text: the task from-text API requires a text input")
    return live_case.input_text


def _require_prompt_text(live_case: LiveCase) -> str:
    """Read the case's inline prompt text or fail with the case's error prefix."""
    if isinstance(live_case.prompt, PromptSource.Text):
        return live_case.prompt.text
    raise RuntimeError(f"{live_case.error_prefix()} prompt.text: the live validate API requires an inline prompt text")


def _require_schema(live_case: LiveCase) -> dict[str, Any]:
    """Read the case's resolved schema or fail with the case's error prefix."""
    if live_case.schema is None:
        raise RuntimeError(f"{live_case.error_prefix()} schema: the live task APIs require a schema")
    return live_case.schema


def _duration_ms(start: float) -> int:
    """Wall-clock milliseconds since the given ``time.perf_counter`` start."""
    return int((time.perf_counter() - start) * 1000)


def _normalize(text: str | None) -> str:
    """CRLF→LF-normalize one text so a Windows checkout never yields a false mismatch."""
    return "" if text is None else text.replace("\r\n", "\n")


def _truncate(text: str) -> str:
    """Truncate one text to the reporting length."""
    return text if len(text) <= MAX_REPORTED_TEXT_LENGTH else text[:MAX_REPORTED_TEXT_LENGTH] + "..."


def _quoted(text: str | None) -> str:
    """Quote one text for a failure diff."""
    return "null" if text is None else f"<{text}>"


def _fail(live_case: LiveCase, json_path: str, expected: str, actual: str) -> AssertionError:
    """Build the assertion failure of one expectation mismatch."""
    return AssertionError(f"{live_case.error_prefix()} {json_path}: expected {expected} but was {actual}")
