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

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class SemanticValidatorContractTest(unittest.TestCase):
    def test_semantic_validation_models_exist(self) -> None:
        from a2a_t.server.prompt_compliance.models import SemanticValidationError, SemanticValidationResult

        error = SemanticValidationError(slot_name="site", code="semantic_mismatch", message="site is mismatched")
        result = SemanticValidationResult(passed=False, errors=[error])

        self.assertFalse(result.passed)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].slot_name, "site")
        self.assertEqual(result.errors[0].code, "semantic_mismatch")
        self.assertEqual(result.errors[0].message, "site is mismatched")


if __name__ == "__main__":
    unittest.main()
