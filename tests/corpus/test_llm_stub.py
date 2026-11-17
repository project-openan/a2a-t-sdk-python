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

"""Direct unit tests of the corpus LLM stubs.

Port of the Java ``ScriptedNegotiationLlmClientTest`` and ``RecordingLLMClientTest``: the scripted
client's strict step-by-step consumption, the six failure markers, the call and message recording
and the fail-on-overconsumption default that forbids repeat-last semantics; the recording client's
transparent forwarding, its full request/response/duration recording and its
record-and-rethrow behavior on infrastructure failures.

The ``llmCalls`` counting seam these tests pin is the calibration point of the whole corpus: the
retry expectations and the differential zero-call proof both assert the exact call count, so a
count that is off by one must be visible here at the client level first.
"""

from __future__ import annotations

from typing import Any

import pytest

from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.models import LLMResponse
from tests.corpus.llm_stub import (
    LLM_ERROR_DETAIL,
    NON_JSON_CONTENT,
    RUNTIME_EXCEPTION_DETAIL,
    LiveLlmCall,
    RecordingLLMClient,
    ScriptedNegotiationLlmClient,
)
from tests.corpus.models import LlmFailMarker, LlmScriptStep

#: Messages of one scripted request (Java ``MESSAGES``).
MESSAGES: list[dict[str, str]] = [
    {"role": "system", "content": "system prompt"},
    {"role": "user", "content": "user prompt"},
]

#: Schema of one scripted request.
SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def call(client: ScriptedNegotiationLlmClient | RecordingLLMClient) -> Any:
    """Make one structured call with the shared request fixture."""
    return client.structured(messages=MESSAGES, json_schema=dict(SCHEMA))


# --------------------------------------------------------------------------- scripted client


class TestScriptedNegotiationLlmClient:
    """The scripted client: one answer per step, every call counted."""

    def test_consumes_payload_steps_strictly_in_order(self) -> None:
        client = ScriptedNegotiationLlmClient([LlmScriptStep.Payload('{"a":1}'), LlmScriptStep.Payload('{"a":2}')])

        first = call(client)
        second = call(client)

        assert first.content == '{"a":1}'
        assert second.content == '{"a":2}'
        assert client.call_count == 2
        assert len(client.recorded_messages) == 2
        assert client.last_messages == MESSAGES
        assert first.model == "scripted-model"

    def test_records_the_schema_of_each_call(self) -> None:
        client = ScriptedNegotiationLlmClient([LlmScriptStep.Payload("{}")])

        assert client.last_schema is None
        call(client)

        assert client.last_schema == SCHEMA

    @pytest.mark.parametrize(
        ("marker", "detail"),
        [
            (LlmFailMarker.RUNTIME_EXCEPTION, RUNTIME_EXCEPTION_DETAIL),
            (LlmFailMarker.LLM_ERROR, LLM_ERROR_DETAIL),
        ],
        ids=["runtime-exception", "llm-error"],
    )
    def test_infrastructure_markers_throw_their_real_failure_form(self, marker: LlmFailMarker, detail: str) -> None:
        expected_type = RuntimeError if marker is LlmFailMarker.RUNTIME_EXCEPTION else LLMRuntimeError
        client = ScriptedNegotiationLlmClient([LlmScriptStep.Fail(marker)])

        with pytest.raises(expected_type, match=detail):
            call(client)

        assert client.call_count == 1, "the failed call still counts as a call"
        assert client.leaked_failure_details == [detail]

    def test_null_response_marker_answers_none(self) -> None:
        client = ScriptedNegotiationLlmClient([LlmScriptStep.Fail(LlmFailMarker.NULL_RESPONSE)])

        assert call(client) is None
        assert client.call_count == 1

    def test_blank_content_marker_answers_a_blank_payload(self) -> None:
        client = ScriptedNegotiationLlmClient([LlmScriptStep.Fail(LlmFailMarker.BLANK_CONTENT)])

        response = call(client)

        assert response is not None
        assert response.content.strip() == ""
        assert client.call_count == 1

    def test_non_json_marker_answers_an_unparseable_payload(self) -> None:
        client = ScriptedNegotiationLlmClient([LlmScriptStep.Fail(LlmFailMarker.NON_JSON)])

        response = call(client)

        assert response.content == NON_JSON_CONTENT
        assert client.call_count == 1

    def test_assertion_marker_fails_on_any_call(self) -> None:
        client = ScriptedNegotiationLlmClient.assertion_only()

        with pytest.raises(AssertionError, match="No LLM call was expected"):
            call(client)

        assert client.call_count == 1

    def test_exhausted_script_fails_instead_of_repeating_the_last_answer(self) -> None:
        client = ScriptedNegotiationLlmClient([LlmScriptStep.Payload('{"a":1}')])
        call(client)

        with pytest.raises(RuntimeError, match="consumed its whole script"):
            call(client)

        assert client.call_count == 2, "the rejected call still counts as a call"

    def test_overconsumption_can_be_disabled_for_engine_self_tests(self) -> None:
        client = ScriptedNegotiationLlmClient([LlmScriptStep.Payload('{"a":1}')], False)
        call(client)

        repeated = call(client)

        assert repeated.content == '{"a":1}'
        assert client.call_count == 2

    def test_the_recorded_messages_are_copies_not_aliases(self) -> None:
        client = ScriptedNegotiationLlmClient([LlmScriptStep.Payload("{}")])
        call(client)

        client.recorded_messages[0].append({"role": "user", "content": "mutated"})
        client.last_messages.append({"role": "user", "content": "mutated"})

        assert client.last_messages == MESSAGES

    def test_none_steps_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="steps"):
            ScriptedNegotiationLlmClient(None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- recording client


class StubDelegate:
    """Hand-written fake of the real delegate: answers its canned responses one by one, ``None`` once exhausted."""

    def __init__(self, *responses: LLMResponse | None) -> None:
        self._responses = list(responses)
        self._cursor = 0

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse | None:
        if self._cursor < len(self._responses):
            response = self._responses[self._cursor]
            self._cursor += 1
            return response
        return None


class FailingDelegate:
    """Hand-written fake of the real delegate that always throws the given infrastructure failure."""

    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        raise self._failure


class TestRecordingLLMClient:
    """The recording decorator: forwards untouched, records everything, rethrows failures."""

    def test_forwards_to_the_delegate_and_records_request_and_response(self) -> None:
        client = RecordingLLMClient(
            StubDelegate(
                LLMResponse(
                    content='{"a":1}',
                    model="qwen3-27b",
                    usage={"prompt_tokens": 11, "completion_tokens": 7},
                    metadata={},
                )
            )
        )

        response = client.structured(messages=MESSAGES, json_schema=SCHEMA, temperature=0.0, max_tokens=512)

        assert response.content == '{"a":1}'
        assert client.call_count == 1
        recorded = client.snapshot()[0]
        assert recorded.messages == MESSAGES
        assert recorded.json_schema == SCHEMA
        assert recorded.temperature == 0.0
        assert recorded.max_tokens == 512
        assert recorded.content == '{"a":1}'
        assert recorded.model == "qwen3-27b"
        assert recorded.usage == {"prompt_tokens": 11, "completion_tokens": 7}
        assert recorded.error is None
        assert recorded.duration_ms >= 0
        assert not recorded.failed

    def test_none_schema_and_overrides_pass_through_as_none(self) -> None:
        client = RecordingLLMClient(StubDelegate(LLMResponse(content="{}", model="m", usage={}, metadata={})))

        client.structured(messages=MESSAGES, json_schema=None, temperature=None, max_tokens=None)

        recorded = client.snapshot()[0]
        assert recorded.json_schema is None
        assert recorded.temperature is None
        assert recorded.max_tokens is None
        assert recorded.usage == {}, "a None usage records as an empty map"

    def test_a_none_valued_usage_entry_is_dropped_instead_of_breaking_the_recording(self) -> None:
        client = RecordingLLMClient(
            StubDelegate(
                LLMResponse(content="{}", model="m", usage={"prompt_tokens": None, "completion_tokens": 5}, metadata={})
            )
        )

        client.structured(messages=MESSAGES, json_schema=SCHEMA)

        assert client.snapshot()[0].usage == {"completion_tokens": 5}

    def test_a_none_delegate_answer_records_none_response_fields(self) -> None:
        client = RecordingLLMClient(StubDelegate(None))

        assert call(client) is None

        recorded = client.snapshot()[0]
        assert client.call_count == 1, "the answered-None call counts as a call"
        assert recorded.content is None
        assert recorded.model is None
        assert recorded.error is None

    def test_an_infrastructure_failure_is_recorded_and_rethrown(self) -> None:
        failure = LLMRuntimeError("connection reset")
        client = RecordingLLMClient(FailingDelegate(failure))

        with pytest.raises(LLMRuntimeError, match="connection reset") as thrown:
            call(client)

        assert thrown.value is failure, "the wrapper must rethrow the original exception, not wrap it"
        assert client.call_count == 1, "the failed call counts as a call"
        recorded = client.snapshot()[0]
        assert recorded.failed
        assert "LLMRuntimeError" in (recorded.error or "") and "connection reset" in (recorded.error or "")
        assert recorded.content is None
        assert client.snapshot() == [recorded], "the snapshot carries the failed call for the transcript"

    def test_calls_record_in_order_and_the_snapshot_is_a_copy(self) -> None:
        client = RecordingLLMClient(
            StubDelegate(
                LLMResponse(content='{"a":1}', model="m", usage={}, metadata={}),
                LLMResponse(content='{"a":2}', model="m", usage={}, metadata={}),
            )
        )

        call(client)
        call(client)

        assert client.call_count == 2
        snapshot = client.snapshot()
        assert [one.content for one in snapshot] == ['{"a":1}', '{"a":2}']
        snapshot.append(LiveLlmCall())
        assert client.call_count == 2, "mutating the snapshot must not touch the recorder"

    def test_none_delegate_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="delegate"):
            RecordingLLMClient(None)  # type: ignore[arg-type]
