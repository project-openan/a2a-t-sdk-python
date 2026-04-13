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
    def test_prompt_catalog_registry_module_exists_under_prompt_package(self) -> None:
        try:
            spec = importlib.util.find_spec("a2a_t.prompt.resources.catalog_registry")
        except ModuleNotFoundError:
            spec = None

        self.assertIsNotNone(spec)

    def test_prompt_catalog_registry_protocol_exists(self) -> None:
        try:
            module = importlib.import_module("a2a_t.prompt.resources.catalog_registry")
        except ModuleNotFoundError:
            self.fail("a2a_t.prompt.resources.catalog_registry is missing")

        self.assertTrue(hasattr(module, "PromptCatalogRegistry"))
        self.assertTrue(hasattr(module.PromptCatalogRegistry, "_is_protocol"))


if __name__ == "__main__":
    unittest.main()
