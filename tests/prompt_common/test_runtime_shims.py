from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PromptCommonRuntimeShimTest(unittest.TestCase):
    def test_common_runtime_is_available_under_common_package(self) -> None:
        from a2a_t.prompt.common.config import PromptLoaderConfig as SharedPromptLoaderConfig
        from a2a_t.prompt.common.errors import PromptLoaderError as SharedPromptLoaderError
        from a2a_t.prompt.common.models import Prompt as SharedPrompt
        from a2a_t.prompt.config import PromptLoaderConfig as LegacyPromptLoaderConfig
        from a2a_t.prompt.errors import PromptLoaderError as LegacyPromptLoaderError
        from a2a_t.prompt.models import Prompt as LegacyPrompt

        self.assertIs(LegacyPromptLoaderConfig, SharedPromptLoaderConfig)
        self.assertIs(LegacyPromptLoaderError, SharedPromptLoaderError)
        self.assertIs(LegacyPrompt, SharedPrompt)


if __name__ == "__main__":
    unittest.main()
