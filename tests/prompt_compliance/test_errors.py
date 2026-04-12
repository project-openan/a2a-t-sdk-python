from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


import a2a_t.server.prompt_compliance as prompt_compliance_module
from a2a_t.server.prompt_compliance import errors as error_module


class PromptComplianceErrorContractTest(unittest.TestCase):
    def test_public_api_does_not_export_removed_guardrail_rejected_error(self) -> None:
        self.assertFalse(hasattr(prompt_compliance_module, "GuardrailRejectedError"))
        self.assertFalse(hasattr(error_module, "GuardrailRejectedError"))


if __name__ == "__main__":
    unittest.main()
