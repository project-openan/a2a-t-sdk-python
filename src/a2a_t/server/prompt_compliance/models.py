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

"""Value types of the task prompt compliance pipeline.

The result type is the value side of the port's result/exception dual track: the compliance check
reports a rejection through the result object (a prompt being rejected is an expected outcome of a
compliance check, not an exceptional one), while the validate-and-fill entry points raise
catalog-coded exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass

from a2a_t.core.errors.catalog import ErrorCatalog

__all__ = [
    "PromptComplianceFailure",
    "PromptComplianceResult",
    "SemanticValidationError",
    "SemanticValidationResult",
]


@dataclass(frozen=True)
class PromptComplianceFailure:
    """Structured description of why a compliance check rejected a prompt.

    Port of the Java ``PromptComplianceFailure`` record (D3): a machine-readable catalog code, a
    message rendered from the code's template, and the compliance stage where the failure
    occurred. The ``code`` is the external string carrier (D4), so a catalog member passed in is
    normalized to its plain string form.

    The ``get``/``__getitem__`` mapping access is a compatibility shim for the legacy negotiation
    demo packages, which still read ``failure.get("stage")``/``failure.get("message")``; it is
    removed together with those packages.

    Attributes:
        code: layered error code of the rejection, for example ``slot.not_provided`` or
            ``scenario.not_matched``; a plain string ready for JSON wire and logs.
        message: human-readable message rendered from the code's bilingual template.
        stage: compliance stage where the rejection occurred, for example ``input_gate``,
            ``prompt_parse`` or ``semantic_validation``.
    """

    code: str
    message: str
    stage: str

    def __post_init__(self) -> None:
        """Normalize a catalog member passed as the code to its plain string form (D4)."""
        # D4: normalize a catalog member passed in to its plain string form.
        if isinstance(self.code, ErrorCatalog):
            object.__setattr__(self, "code", self.code.value)

    def to_dict(self) -> dict[str, str]:
        """Serialize the failure into the public response shape.

        Returns:
            a plain mapping of ``code``, ``message`` and ``stage``, ready for JSON serialization.
        """
        return {"code": self.code, "message": self.message, "stage": self.stage}

    def __getitem__(self, key: str) -> str:
        """Read one field by its mapping key (``code``/``message``/``stage``).

        Args:
            key: field name to read.

        Returns:
            the value of the field.

        Raises:
            KeyError: when the key is not one of the three field names.
        """
        values = self.to_dict()
        if key in values:
            return values[key]
        raise KeyError(key)

    def get(self, key: str, default: str | None = None) -> str | None:
        """Read one field by its mapping key, returning the default when it is unknown.

        Args:
            key: field name to read.
            default: value returned when the key is not one of the three field names.

        Returns:
            the value of the field, or ``default`` when the key is unknown.
        """
        return self.to_dict().get(key, default)


@dataclass(frozen=True)
class PromptComplianceResult:
    """Unified compliance execution result.

    Exactly one of the two outcomes is set: a compliant prompt carries no failure, a rejected
    prompt carries the structured failure.

    Attributes:
        success: whether the prompt passed every compliance check.
        failure: structured failure describing the rejection; ``None`` when the prompt is
            compliant.
    """

    success: bool
    failure: PromptComplianceFailure | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize the compliance result into the public response shape.

        Returns:
            a plain mapping of ``success`` and ``failure`` (itself serialized), ready for JSON
            serialization.
        """
        return {
            "success": self.success,
            "failure": self.failure.to_dict() if self.failure is not None else None,
        }


@dataclass(frozen=True)
class SemanticValidationError:
    """Structured semantic error of one named slot (the pipeline-internal detail type).

    Attributes:
        slot_name: name of the slot the error belongs to.
        code: layered error code of the error; a plain string ready for JSON wire and logs.
        message: human-readable message rendered from the code's bilingual template.
    """

    slot_name: str
    code: str
    message: str


@dataclass(frozen=True)
class SemanticValidationResult:
    """Outcome of the semantic validation step of the compliance pipeline.

    Attributes:
        passed: whether every semantic constraint held.
        errors: structured semantic errors; empty when the validation passed.
    """

    passed: bool
    errors: list[SemanticValidationError]
