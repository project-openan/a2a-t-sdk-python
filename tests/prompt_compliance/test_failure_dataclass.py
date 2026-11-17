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

"""Contract tests for the structured compliance failure (D3: frozen dataclass wire shape)."""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.server.prompt_compliance.models import PromptComplianceFailure, PromptComplianceResult


def test_failure_is_frozen_and_exposes_the_wire_fields() -> None:
    failure = PromptComplianceFailure(code="slot.rule_violation", message="m", stage="slot_validation")

    with pytest.raises(FrozenInstanceError):
        failure.code = "template.not_found"  # type: ignore[misc]

    assert failure.code == "slot.rule_violation"
    assert failure.message == "m"
    assert failure.stage == "slot_validation"


def test_failure_normalizes_a_catalog_member_to_its_plain_string_code() -> None:
    failure = PromptComplianceFailure(
        code=ErrorCatalog.SLOT_NOT_PROVIDED,
        message="m",
        stage="slot_validation",
    )

    assert failure.code == "slot.not_provided"


@pytest.mark.parametrize("key", ["code", "message", "stage"])
def test_failure_supports_mapping_access_for_legacy_consumers(key: str) -> None:
    failure = PromptComplianceFailure(code="slot.rule_violation", message="m", stage="slot_validation")

    assert failure[key] == getattr(failure, key)
    assert failure.get(key) == getattr(failure, key)
    assert failure.get(key, "fallback") == getattr(failure, key)


def test_failure_mapping_access_rejects_unknown_keys() -> None:
    failure = PromptComplianceFailure(code="slot.rule_violation", message="m", stage="slot_validation")

    with pytest.raises(KeyError):
        _ = failure["unknown"]
    assert failure.get("unknown") is None
    assert failure.get("unknown", "fallback") == "fallback"


@pytest.mark.parametrize(
    "failure",
    [
        PromptComplianceFailure(code="slot.rule_violation", message="m", stage="slot_validation"),
        PromptComplianceFailure(code=ErrorCatalog.TEMPLATE_NOT_FOUND, message="m", stage="preparation"),
    ],
    ids=["string-code", "catalog-member-code"],
)
def test_failure_serializes_into_the_public_response_shape(failure: PromptComplianceFailure) -> None:
    assert failure.to_dict() == {"code": failure.code, "message": failure.message, "stage": failure.stage}


def test_compliance_result_serializes_into_the_public_response_shape() -> None:
    success = PromptComplianceResult(success=True)
    failure = PromptComplianceResult(
        success=False,
        failure=PromptComplianceFailure(code="slot.rule_violation", message="m", stage="slot_validation"),
    )

    assert success.to_dict() == {"success": True, "failure": None}
    assert failure.to_dict() == {
        "success": False,
        "failure": {"code": "slot.rule_violation", "message": "m", "stage": "slot_validation"},
    }
    assert success == PromptComplianceResult(success=True, failure=None)
