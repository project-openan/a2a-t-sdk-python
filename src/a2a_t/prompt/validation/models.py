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

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class SlotValidationError:
    """Describe one slot-level validation problem.

    ``facts`` carries the structured values reported by the extraction step (e.g. ``slot_label``,
    ``actual``) so the caller can render the catalog message; ``None`` when the error carries no
    facts.
    """

    slot_name: str
    code: str
    message: str
    facts: dict[str, str] | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize the slot validation error into the public response shape."""
        return asdict(self)


@dataclass(slots=True)
class SlotValidationResult:
    """Represent the combined outcome of slot validation."""

    passed: bool
    slot_errors: list[SlotValidationError]
