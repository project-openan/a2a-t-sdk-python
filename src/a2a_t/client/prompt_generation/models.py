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

"""Value types of the task prompt generation pipeline.

The result type is the value side of the port's result/exception dual track: the
scenario-recognition entry point reports its failures through the result object (an LLM step
failing is an expected outcome, not an exceptional one), while the template-directed entry points
raise catalog-coded exceptions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

__all__ = ["NormalizedInput", "PromptGenerationFailure", "PromptGenerationResult"]


@dataclass(slots=True)
class NormalizedInput:
    """Carry the normalized text and the input shape detected upstream.

    Attributes:
        input_kind: detected input shape, ``natural_language`` or ``json``.
        normalized_input: input text as consumed by the analysis steps; a mapping input is
            serialized to its JSON object form.
    """

    input_kind: str
    normalized_input: str


@dataclass(slots=True)
class PromptGenerationFailure:
    """Describe why prompt generation stopped before producing a valid result.

    Attributes:
        code: layered error code of the failure, for example ``scenario.not_matched`` or
            ``llm.response_invalid``; a plain string ready for JSON wire and logs.
        message: human-readable message rendered from the code's bilingual template.
        stage: pipeline stage where the failure occurred, for example ``input``, ``analysis``
            or ``rendering``; ``None`` when unknown.
    """

    code: str
    message: str
    stage: str | None

    def to_dict(self) -> dict[str, object]:
        """Serialize failure details into the public response shape.

        Returns:
            a plain mapping of ``code``, ``message`` and ``stage``, ready for JSON serialization.
        """
        return asdict(self)


@dataclass(slots=True)
class PromptGenerationResult:
    """Represent the final outcome of prompt generation.

    Exactly one of the two payload fields is set: a successful generation carries the rendered
    prompt text and no failure, a failed generation carries the structured failure and no prompt
    text.

    Attributes:
        success: whether the generation produced a prompt text.
        prompt_text: rendered task prompt; ``None`` on failure.
        failure: structured failure describing why generation stopped; ``None`` on success.
    """

    success: bool
    prompt_text: str | None
    failure: PromptGenerationFailure | None

    def to_dict(self) -> dict[str, object]:
        """Serialize the generation result into the public response shape.

        Returns:
            a plain mapping of ``success``, ``prompt_text`` and ``failure`` (itself serialized),
            ready for JSON serialization.
        """
        return {
            "success": self.success,
            "prompt_text": self.prompt_text,
            "failure": self.failure.to_dict() if self.failure is not None else None,
        }
