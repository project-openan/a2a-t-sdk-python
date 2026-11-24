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

"""Cross-step reference resolution (port of the Java ``FromStepResolver``).

Resolves ``{"$fromStep": N, "$field": "a.b"}`` references inside step arguments before the step
runs: ``$fromStep`` is the 1-based index of an earlier step whose serialized response payload is
navigated with the dot-separated ``$field`` path. Literal values always take precedence; a
reference may appear at any nesting depth of the arguments map.
"""

from __future__ import annotations

from typing import Any

__all__ = ["FromStepResolver"]


class FromStepResolver:
    """Replaces every ``$fromStep`` reference of one step's arguments."""

    @classmethod
    def resolve(
        cls,
        args: dict[str, object],
        step_number: int,
        prior_payloads: list[dict[str, object] | None],
    ) -> dict[str, object]:
        """Return a deep copy of the arguments with all references replaced."""
        return cls._resolve_value(args, step_number, prior_payloads)  # type: ignore[return-value]

    @classmethod
    def _resolve_value(
        cls,
        value: Any,
        step_number: int,
        prior_payloads: list[dict[str, object] | None],
    ) -> Any:
        if isinstance(value, dict):
            if "$fromStep" in value or "$field" in value:
                return cls._resolve_reference(value, step_number, prior_payloads)
            return {
                str(key): cls._resolve_value(entry, step_number, prior_payloads)
                for key, entry in value.items()
            }
        if isinstance(value, list):
            return [cls._resolve_value(entry, step_number, prior_payloads) for entry in value]
        return value

    @classmethod
    def _resolve_reference(
        cls,
        reference: dict[str, Any],
        step_number: int,
        prior_payloads: list[dict[str, object] | None],
    ) -> Any:
        if set(reference) != {"$fromStep", "$field"}:
            raise ValueError(
                f"step{step_number} $fromStep reference must contain exactly the two keys "
                f"$fromStep and $field: {reference}"
            )
        from_step = reference.get("$fromStep")
        field = reference.get("$field")
        invalid_positive_integer = (
            not isinstance(from_step, int) or isinstance(from_step, bool) or from_step <= 0
        )
        if invalid_positive_integer or not isinstance(field, str) or not field.strip():
            raise ValueError(
                f"step{step_number} invalid $fromStep reference ($fromStep must be a positive "
                f"integer, $field a non-blank string): {reference}"
            )
        if from_step < 1 or from_step >= step_number or from_step - 1 >= len(prior_payloads):
            raise ValueError(
                f"step{step_number} $fromStep={from_step} must reference an earlier, "
                f"already-executed step (1..{step_number - 1})"
            )
        payload = prior_payloads[from_step - 1]
        if payload is None:
            raise ValueError(
                f"step{step_number} $fromStep={from_step} references a step without a success "
                "payload (it failed or was skipped)"
            )
        return cls._navigate(payload, field, from_step, step_number)

    @staticmethod
    def _navigate(payload: dict[str, Any], path: str, from_step: int, step_number: int) -> Any:
        current: Any = payload
        for segment in path.split("."):
            if not isinstance(current, dict) or segment not in current:
                raise ValueError(
                    f"step{step_number} reference $fromStep={from_step} $field={path} failed to "
                    f"resolve: field {segment} does not exist in the referenced step payload"
                )
            current = current[segment]
        if current is None:
            raise ValueError(
                f"step{step_number} reference $fromStep={from_step} $field={path} resolved to "
                "null, which cannot be used as a step argument"
            )
        return current
