from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PromptCatalogRegistryContractTest(unittest.TestCase):
    def test_prompt_catalog_registry_module_is_not_importable(self) -> None:
        try:
            spec = importlib.util.find_spec("a2a_t.prompt.resources.catalog_registry")
        except ModuleNotFoundError:
            spec = None

        self.assertIsNone(spec)

    def test_prompt_package_stops_exporting_catalog_registry_runtime(self) -> None:
        module = importlib.import_module("a2a_t.prompt")

        self.assertFalse(hasattr(module, "PromptCatalogRegistry"))
        self.assertFalse(hasattr(module, "DefaultPromptCatalogRegistry"))


if __name__ == "__main__":
    unittest.main()
