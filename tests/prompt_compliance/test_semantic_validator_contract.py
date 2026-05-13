from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class SemanticValidatorContractTest(unittest.TestCase):
    def test_semantic_validation_models_exist(self) -> None:
        from a2a_t.server.prompt_compliance.semantic_validator import SemanticValidationError, SemanticValidationResult

        error = SemanticValidationError(slot_name="site", code="semantic_mismatch", message="site is mismatched")
        result = SemanticValidationResult(passed=False, errors=[error])

        self.assertFalse(result.passed)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].slot_name, "site")
        self.assertEqual(result.errors[0].code, "semantic_mismatch")
        self.assertEqual(result.errors[0].message, "site is mismatched")


if __name__ == "__main__":
    unittest.main()

