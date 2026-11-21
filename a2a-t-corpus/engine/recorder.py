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

"""Recording, metered LLM client (port of the Java ``RecordingMeteredLlmClient``).

Decorator wrapping the real LLM client as the single test seam of the corpus: it records every
invocation with the complete request (messages, schema, parameters, model), the complete response
(content, model, usage), the latency and the token counters, then delegates transparently. The
production pipeline is otherwise assembled unchanged.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any

from engine.errors_serializer import to_map

from a2a_t.llm.models import LLMResponse
from a2a_t.llm.provider import LLMClient

__all__ = ["RecordedLlmCall", "RecordingMeteredLlmClient"]


@dataclass(slots=True)
class RecordedLlmCall:
    """One captured LLM invocation."""

    request: dict[str, object]
    response: dict[str, object]
    result: str
    duration_ms: int
    input_token: int
    output_token: int


class RecordingMeteredLlmClient:
    """Records every ``structured`` call, then delegates to the wrapped client."""

    def __init__(
        self,
        delegate: LLMClient,
        model_name: str,
        default_temperature: float | None = None,
    ) -> None:
        self._delegate = delegate
        self._model_name = model_name
        self._default_temperature = default_temperature
        self._calls: list[RecordedLlmCall] = []

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Record and delegate one structured invocation."""
        started_at = time.perf_counter()
        effective_temperature = temperature if temperature is not None else self._default_temperature
        request: dict[str, object] = {
            "messages": [dict(message) for message in messages],
            "jsonSchema": copy.deepcopy(json_schema) if json_schema is not None else {},
        }
        if effective_temperature is not None:
            request["temperature"] = effective_temperature
        if max_tokens is not None:
            request["maxTokens"] = max_tokens
        request["model"] = self._model_name
        try:
            response = self._delegate.structured(
                messages=messages,
                json_schema=json_schema,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            self._record(
                RecordedLlmCall(
                    request=request,
                    response={
                        "content": response.content,
                        "model": response.model,
                        "usage": dict(response.usage) if response.usage is not None else {},
                    },
                    result="success",
                    duration_ms=_elapsed_ms(started_at),
                    input_token=_tokens(response.usage, "prompt_tokens"),
                    output_token=_tokens(response.usage, "completion_tokens"),
                )
            )
            return response
        except BaseException as error:
            self._record(
                RecordedLlmCall(
                    request=request,
                    response=to_map(error),
                    result="error",
                    duration_ms=_elapsed_ms(started_at),
                    input_token=0,
                    output_token=0,
                )
            )
            raise

    def drain(self) -> list[RecordedLlmCall]:
        """Return the calls recorded since the previous drain and forget them."""
        snapshot = list(self._calls)
        self._calls.clear()
        return snapshot

    def _record(self, call: RecordedLlmCall) -> None:
        self._calls.append(call)


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _tokens(usage: dict[str, int] | None, key: str) -> int:
    if usage is None:
        return 0
    return int(usage.get(key, 0) or 0)
