from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.config.models import GuardrailProviderConfig, PromptComplianceConfig, PromptRuntimeConfig


class PackageSkeletonTest(unittest.TestCase):
    def test_a2at_config_is_exported_from_config_package(self) -> None:
        package = importlib.import_module("a2a_t.config")

        self.assertTrue(hasattr(package, "A2ATConfig"))
        self.assertTrue(hasattr(package, "PromptRuntimeConfig"))
        self.assertTrue(hasattr(package, "PromptComplianceConfig"))
        self.assertTrue(hasattr(package, "GuardrailProviderConfig"))
        self.assertTrue(hasattr(package, "ConfigFileNotFoundError"))

    def test_prompt_package_is_exported_from_a2a_t(self) -> None:
        package = importlib.import_module("a2a_t")

        self.assertTrue(hasattr(package, "prompt"))
        self.assertIn("prompt", getattr(package, "__all__", []))

    def test_prompt_package_is_importable_from_a2a_t(self) -> None:
        package = importlib.import_module("a2a_t.prompt")

        self.assertEqual(package.__name__, "a2a_t.prompt")

    def test_legacy_prompt_loader_exports_are_removed_from_prompt_package(self) -> None:
        package = importlib.import_module("a2a_t.prompt")

        self.assertFalse(hasattr(package, "PromptLoader"))
        self.assertFalse(hasattr(package, "AgentPromptCatalog"))
        self.assertFalse(hasattr(package, "AgentProvider"))
        self.assertFalse(hasattr(package, "LocalFilePromptStore"))
        self.assertFalse(hasattr(package, "UrlPromptCatalog"))
        self.assertFalse(hasattr(package, "UrlProvider"))

    def test_prompt_runtime_modules_exist_under_prompt_package(self) -> None:
        module_names = [
            "a2a_t.prompt.common.errors",
            "a2a_t.prompt.common.models",
            "a2a_t.prompt.resources.cache",
            "a2a_t.prompt.resources.catalog",
            "a2a_t.prompt.resources.providers",
        ]

        for module_name in module_names:
            module = importlib.import_module(module_name)
            self.assertEqual(module.__name__, module_name)

    def test_legacy_prompt_loader_module_is_not_importable(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("a2a_t.prompt.loader")

    def test_prompt_manager_package_is_not_exported_from_a2a_t(self) -> None:
        package = importlib.import_module("a2a_t")

        self.assertFalse(hasattr(package, "prompt_manager"))
        self.assertNotIn("prompt_manager", getattr(package, "__all__", []))

    def test_prompt_manager_package_is_not_importable(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("a2a_t.prompt_manager")

    def test_legacy_prompt_manager_package_is_not_importable(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("a2a_t_sdk.prompt_manager")

    def test_env_config_is_no_longer_exported_from_config_package(self) -> None:
        package = importlib.import_module("a2a_t.config")

        self.assertFalse(hasattr(package, "EnvConfig"))

    def test_module_configs_no_longer_expose_from_env_helpers(self) -> None:
        self.assertFalse(hasattr(PromptComplianceConfig, "from_env"))
        self.assertFalse(hasattr(PromptRuntimeConfig, "from_env"))
        self.assertFalse(hasattr(GuardrailProviderConfig, "from_env"))

    def test_legacy_prompt_loader_runtime_modules_are_not_importable(self) -> None:
        module_names = [
            "a2a_t.prompt.common.config",
            "a2a_t.prompt.resources.catalog_registry",
            "a2a_t.prompt.resources.parser",
        ]

        for module_name in module_names:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(module_name)

    def test_prompt_package_stops_exporting_legacy_catalog_parser_runtime(self) -> None:
        package = importlib.import_module("a2a_t.prompt")

        self.assertFalse(hasattr(package, "DefaultPromptCatalogRegistry"))
        self.assertFalse(hasattr(package, "LocalFileFetcher"))
        self.assertFalse(hasattr(package, "LocalFileProvider"))
        self.assertFalse(hasattr(package, "LocalPromptCatalog"))
        self.assertFalse(hasattr(package, "MarkdownPromptParser"))
        self.assertFalse(hasattr(package, "PromptCatalog"))
        self.assertFalse(hasattr(package, "PromptCatalogRegistry"))
        self.assertFalse(hasattr(package, "PromptLoaderConfig"))
        self.assertFalse(hasattr(package, "PromptParser"))
        self.assertFalse(hasattr(package, "PromptParserRegistry"))
        self.assertFalse(hasattr(package, "PromptProvider"))
        self.assertFalse(hasattr(package, "build_default_prompt_parser_registry"))


if __name__ == "__main__":
    unittest.main()
