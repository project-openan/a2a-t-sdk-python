from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PromptResourceRuntimeModuleTest(unittest.TestCase):
    def test_resource_runtime_modules_are_importable(self) -> None:
        module_names = [
            "a2a_t.prompt.resources",
            "a2a_t.prompt.resources.cache",
            "a2a_t.prompt.resources.catalog",
            "a2a_t.prompt.resources.providers",
            "a2a_t.prompt.resources.registry",
            "a2a_t.prompt.resources.source",
        ]

        for module_name in module_names:
            module = importlib.import_module(module_name)
            self.assertEqual(module.__name__, module_name)

    def test_legacy_resource_shim_modules_are_not_importable(self) -> None:
        module_names = [
            "a2a_t.prompt.cache",
            "a2a_t.prompt.catalog",
            "a2a_t.prompt.catalog_registry",
            "a2a_t.prompt.parser",
            "a2a_t.prompt.providers",
        ]

        for module_name in module_names:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)

    def test_resources_package_no_longer_exports_legacy_remote_runtime(self) -> None:
        module = importlib.import_module("a2a_t.prompt.resources")

        self.assertFalse(hasattr(module, "AgentPromptCatalog"))
        self.assertFalse(hasattr(module, "DefaultPromptCatalogRegistry"))
        self.assertFalse(hasattr(module, "AgentProvider"))
        self.assertFalse(hasattr(module, "LocalFileFetcher"))
        self.assertFalse(hasattr(module, "LocalFileProvider"))
        self.assertFalse(hasattr(module, "LocalFilePromptStore"))
        self.assertFalse(hasattr(module, "LocalPromptCatalog"))
        self.assertFalse(hasattr(module, "MarkdownPromptParser"))
        self.assertFalse(hasattr(module, "PromptCatalog"))
        self.assertFalse(hasattr(module, "PromptCatalogRegistry"))
        self.assertFalse(hasattr(module, "PromptProvider"))
        self.assertFalse(hasattr(module, "PromptParser"))
        self.assertFalse(hasattr(module, "PromptParserRegistry"))
        self.assertFalse(hasattr(module, "UrlPromptCatalog"))
        self.assertFalse(hasattr(module, "UrlProvider"))

    def test_legacy_catalog_and_parser_modules_are_not_importable(self) -> None:
        module_names = [
            "a2a_t.prompt.resources.catalog_registry",
            "a2a_t.prompt.resources.parser",
        ]

        for module_name in module_names:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()
