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

"""Scripted and recording LLM clients of the negotiation test corpus.

Port of the Java ``a2a-t-corpus`` ``ScriptedNegotiationLlmClient`` and ``RecordingLLMClient``: the
scripted client is the single scripted seam of the otherwise fully production-wired offline engine,
the recording client decorates the real provider client of the live family without changing its
behavior.

The scripted client consumes the resolved script steps of one corpus case strictly step by step: a
payload step answers one :class:`~a2a_t.llm.models.LLMResponse` carrying the payload text, a failure
step replays the marker's real failure form. Every call records the received messages and the call
count, so the ``llmCalls`` expectation stays the only calibration point — **fail-on-overconsumption
is on by default** (review risk 6 of the corpus design document): once the script is exhausted the
next call fails instead of silently repeating the last answer, because repeat-last semantics would
mask a wrong ``llmCalls`` expectation. The flag exists only as an explicit escape hatch for engine
self-tests.

The ``ASSERTION`` marker is the zero-call proof: any call raises :class:`AssertionError`, which is
how the engine proves that the from-data leg and the differential runs never touch the LLM.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.models import LLMResponse
from a2a_t.llm.provider import LLMClient
from tests.corpus.models import LlmFailMarker, LlmScriptStep

__all__ = [
    "LLM_ERROR_DETAIL",
    "NON_JSON_CONTENT",
    "RUNTIME_EXCEPTION_DETAIL",
    "LiveLlmCall",
    "RecordingLLMClient",
    "ScriptedNegotiationLlmClient",
]

#: Model name every scripted response carries (Java ``scripted-model``).
_SCRIPTED_MODEL: str = "scripted-model"

#: Usage summary every scripted response carries.
_SCRIPTED_USAGE: dict[str, int] = {"prompt_tokens": 1, "completion_tokens": 1}

#: Raw infrastructure detail of the ``runtime-exception`` marker; must never reach a user-visible
#: message (the ``noLlmLeakInUserMessage`` contract). Java exposes it as a class constant; the
#: module-level spelling is the import seam the stub tests and the leak contract use.
RUNTIME_EXCEPTION_DETAIL: str = "scripted-llm-transport-failure"

#: Raw infrastructure detail of the ``llm-error`` marker; same rule.
LLM_ERROR_DETAIL: str = "scripted-llm-error-response"

#: Unparseable payload content of the ``non-json`` marker.
NON_JSON_CONTENT: str = "<not a json object>"


class ScriptedNegotiationLlmClient:
    """Scripted LLM client of the negotiation test corpus.

    Attributes:
        RUNTIME_EXCEPTION_DETAIL: raw infrastructure detail of the ``runtime-exception`` marker;
            must never reach a user-visible message (the ``noLlmLeakInUserMessage`` contract).
        LLM_ERROR_DETAIL: raw infrastructure detail of the ``llm-error`` marker; same rule.
        NON_JSON_CONTENT: unparseable payload content of the ``non-json`` marker.
    """

    RUNTIME_EXCEPTION_DETAIL: str = RUNTIME_EXCEPTION_DETAIL
    LLM_ERROR_DETAIL: str = LLM_ERROR_DETAIL
    NON_JSON_CONTENT: str = NON_JSON_CONTENT

    def __init__(
        self,
        steps: list[LlmScriptStep] | tuple[LlmScriptStep, ...],
        fail_on_overconsumption: bool = True,
    ) -> None:
        """Create one scripted client.

        Args:
            steps: resolved script steps, consumed strictly step by step.
            fail_on_overconsumption: ``True`` fails once the script is exhausted; ``False`` repeats
                the last step (explicit escape hatch for engine self-tests only).

        Raises:
            ValueError: when ``steps`` is ``None``.
        """
        if steps is None:
            raise ValueError("steps")
        self._steps: tuple[LlmScriptStep, ...] = tuple(steps)
        self._fail_on_overconsumption = fail_on_overconsumption
        self._cursor = 0
        self._call_count = 0
        self._recorded_messages: list[list[dict[str, str]]] = []
        self._recorded_schemas: list[dict[str, Any] | None] = []
        self._leaked_failure_details: list[str] = []
        self._lock = threading.Lock()

    @classmethod
    def assertion_only(cls) -> ScriptedNegotiationLlmClient:
        """Create the zero-call proof client: any call raises :class:`AssertionError`."""
        return cls([LlmScriptStep.Fail(LlmFailMarker.ASSERTION)])

    # ------------------------------------------------------------------ LLMClient protocol

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse | None:
        """Answer one structured call from the script, or replay the step's failure form.

        Args:
            messages: messages of the request, one map per message.
            json_schema: JSON schema constraining the response.
            temperature: temperature override of the request (unused by the script).
            max_tokens: max-tokens override of the request (unused by the script).

        Returns:
            the scripted response of the current step, or ``None`` for the ``null-response``
            marker.

        Raises:
            RuntimeError: when the script is exhausted (default) — the ``llmCalls`` expectation is
                the only calibration point, so an exhausted script fails instead of repeating the
                last answer.
            RuntimeError: for the ``runtime-exception`` marker (raw infrastructure detail).
            LLMRuntimeError: for the ``llm-error`` marker (raw infrastructure detail).
            AssertionError: for the ``assertion`` marker — no LLM call was expected in this run.
        """
        with self._lock:
            self._call_count += 1
            self._recorded_messages.append(list(messages) if messages is not None else [])
            self._recorded_schemas.append(json_schema)
            if self._cursor >= len(self._steps):
                if self._fail_on_overconsumption:
                    raise RuntimeError(
                        f"The scripted LLM client consumed its whole script of {len(self._steps)} "
                        "step(s); an exhausted script fails instead of repeating the last answer "
                        "(the llmCalls expectation is the only calibration point)."
                    )
                self._cursor = len(self._steps) - 1
            step = self._steps[self._cursor]
            self._cursor += 1
            if isinstance(step, LlmScriptStep.Payload):
                return LLMResponse(
                    content=step.json,
                    model=_SCRIPTED_MODEL,
                    usage=dict(_SCRIPTED_USAGE),
                    metadata={},
                )
            return self._fail_as(step.marker)

    # ------------------------------------------------------------------ recording accessors

    @property
    def call_count(self) -> int:
        """Number of LLM calls made through this client, including the calls that failed."""
        return self._call_count

    @property
    def recorded_messages(self) -> list[list[dict[str, str]]]:
        """Messages of every call, one entry per call."""
        return [list(messages) for messages in self._recorded_messages]

    @property
    def last_messages(self) -> list[dict[str, str]]:
        """Messages of the most recent call, or an empty list before the first call."""
        return list(self._recorded_messages[-1]) if self._recorded_messages else []

    @property
    def last_schema(self) -> dict[str, Any] | None:
        """JSON schema of the most recent call, or ``None`` before the first call."""
        return self._recorded_schemas[-1] if self._recorded_schemas else None

    @property
    def leaked_failure_details(self) -> list[str]:
        """Raw infrastructure details of the scripted failures that were replayed.

        For the ``noLlmLeakInUserMessage`` contract: every detail listed here must never surface in
        a user-visible message.
        """
        return list(self._leaked_failure_details)

    # ------------------------------------------------------------------ failure replay

    def _fail_as(self, marker: LlmFailMarker) -> LLMResponse | None:
        """Replay the real failure form of one fail marker.

        Raises:
            RuntimeError: for the ``runtime-exception`` marker.
            LLMRuntimeError: for the ``llm-error`` marker.
            AssertionError: for the ``assertion`` marker.
        """
        if marker is LlmFailMarker.RUNTIME_EXCEPTION:
            self._leaked_failure_details.append(self.RUNTIME_EXCEPTION_DETAIL)
            raise RuntimeError(self.RUNTIME_EXCEPTION_DETAIL)
        if marker is LlmFailMarker.LLM_ERROR:
            self._leaked_failure_details.append(self.LLM_ERROR_DETAIL)
            raise LLMRuntimeError(self.LLM_ERROR_DETAIL)
        if marker is LlmFailMarker.NULL_RESPONSE:
            return None
        if marker is LlmFailMarker.BLANK_CONTENT:
            return LLMResponse(content="", model=_SCRIPTED_MODEL, usage=dict(_SCRIPTED_USAGE), metadata={})
        if marker is LlmFailMarker.NON_JSON:
            return LLMResponse(
                content=self.NON_JSON_CONTENT, model=_SCRIPTED_MODEL, usage=dict(_SCRIPTED_USAGE), metadata={}
            )
        # ASSERTION: the zero-call proof.
        raise AssertionError(
            "No LLM call was expected in this run (assertion-only client), but one happened with "
            f"messages: {self._recorded_messages[-1]}"
        )


@dataclass(frozen=True)
class LiveLlmCall:
    """One LLM call recorded by :class:`RecordingLLMClient`.

    A call that threw carries a non-``None`` :attr:`error` (the exception class name and message)
    and ``None`` response fields; a call that succeeded carries a ``None`` :attr:`error`. The
    record is the unit both of the ``maxLlmCalls`` upper bound and of the per-case ``llmCalls``
    section of the live transcript.

    Attributes:
        messages: messages of the request, one map per message.
        json_schema: JSON schema of the request, or ``None`` when the pipeline passed none.
        temperature: temperature of the request, or ``None`` when the request carries none.
        max_tokens: max-tokens override of the request, or ``None`` when the request carries none.
        content: raw response content, or ``None`` when the call failed or answered ``None``.
        model: resolved model name of the response, or ``None`` when the call failed or answered
            ``None``.
        usage: token usage summary of the response; empty when the call failed or answered
            ``None``.
        duration_ms: wall-clock duration of the call in milliseconds.
        error: exception class name and message when the call threw, or ``None`` on success.
    """

    messages: list[dict[str, str]] = field(default_factory=list)
    json_schema: dict[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    content: str | None = None
    model: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        """Defensively copy the request messages and the usage summary."""
        object.__setattr__(self, "messages", list(self.messages) if self.messages is not None else [])
        object.__setattr__(self, "usage", dict(self.usage) if self.usage is not None else {})

    @property
    def failed(self) -> bool:
        """Whether this call threw instead of answering."""
        return self.error is not None


class RecordingLLMClient:
    """Recording decorator around the real LLM client of the live corpus.

    Forwards every call to the delegate untouched — the wrapped client is the real provider
    instance, so the production behavior does not deviate — and records the request, the response
    (or the thrown exception) and the wall-clock duration of each call. The recorded calls are the
    single source of the live family's calibration points: the ``maxLlmCalls`` upper bound is
    checked against :attr:`call_count`, and the transcript embeds :meth:`snapshot` verbatim. A call
    that throws is recorded and then re-raised, so an infrastructure failure stays a failure while
    still showing up in the transcript.
    """

    def __init__(self, delegate: LLMClient) -> None:
        """Create one recording wrapper around the real delegate client.

        Args:
            delegate: the real provider client, such as the one built by the live harness.

        Raises:
            ValueError: when ``delegate`` is ``None``.
        """
        if delegate is None:
            raise ValueError("delegate")
        self._delegate = delegate
        self._calls: list[LiveLlmCall] = []
        self._lock = threading.Lock()

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Forward one structured call to the delegate and record its outcome.

        Args:
            messages: messages of the request, one map per message.
            json_schema: JSON schema constraining the response.
            temperature: temperature override of the request.
            max_tokens: max-tokens override of the request.

        Returns:
            the delegate's response.

        Raises:
            Exception: whatever the delegate raised, re-raised after being recorded.
        """
        start = time.perf_counter()
        response: LLMResponse | None = None
        error: str | None = None
        try:
            response = self._delegate.structured(
                messages=messages,
                json_schema=json_schema,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response
        except Exception as exception:  # noqa: BLE001 - re-raised below, recorded for the transcript
            error = f"{type(exception).__name__}: {exception}"
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            with self._lock:
                self._calls.append(
                    LiveLlmCall(
                        messages=list(messages) if messages is not None else [],
                        json_schema=json_schema,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        content=None if response is None else response.content,
                        model=None if response is None else response.model,
                        usage={} if response is None else _sanitized_usage(response.usage),
                        duration_ms=duration_ms,
                        error=error,
                    )
                )

    @property
    def call_count(self) -> int:
        """Number of LLM calls made through this client, including the calls that threw."""
        with self._lock:
            return len(self._calls)

    def snapshot(self) -> list[LiveLlmCall]:
        """Return the transcript-facing snapshot of every recorded call, in call order.

        Returns:
            immutable list of the recorded calls at snapshot time; the engine embeds this list into
            the live transcript's per-case ``llmCalls`` section.
        """
        with self._lock:
            return list(self._calls)


def _sanitized_usage(usage: dict[str, int] | None) -> dict[str, int]:
    """Copy the delegate's usage summary defensively, dropping ``None``-valued entries.

    A lenient OpenAI-compatible endpoint may answer a JSON ``null`` value (such as
    ``{"prompt_tokens": null}``), and passing that on would make the recording itself throw and
    mask the delegate outcome — so ``None``-valued entries are dropped instead.
    """
    if usage is None:
        return {}
    return {key: value for key, value in usage.items() if value is not None}
