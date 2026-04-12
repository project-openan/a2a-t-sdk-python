from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.prompt.config import PromptLoaderConfig
from a2a_t.server.prompt_compliance.config import PromptComplianceConfig


class PackageSkeletonTest(unittest.TestCase):
    def test_a2at_config_is_exported_from_config_package(self) -> None:
        package = importlib.import_module("a2a_t.config")

        self.assertTrue(hasattr(package, "A2ATConfig"))
        self.assertTrue(hasattr(package, "ConfigFileNotFoundError"))

    def test_prompt_package_is_exported_from_a2a_t(self) -> None:
        package = importlib.import_module("a2a_t")

        self.assertTrue(hasattr(package, "prompt"))
        self.assertIn("prompt", getattr(package, "__all__", []))

    def test_prompt_package_is_importable_from_a2a_t(self) -> None:
        package = importlib.import_module("a2a_t.prompt")

        self.assertEqual(package.__name__, "a2a_t.prompt")

    def test_prompt_loader_is_exported_from_prompt_package(self) -> None:
        package = importlib.import_module("a2a_t.prompt")

        self.assertTrue(hasattr(package, "PromptLoader"))
        self.assertTrue(hasattr(package, "PromptLoaderConfig"))
        self.assertTrue(hasattr(package, "PromptCatalog"))
        self.assertTrue(hasattr(package, "PromptCatalogRegistry"))

    def test_prompt_runtime_modules_exist_under_prompt_package(self) -> None:
        module_names = [
            "a2a_t.prompt.cache",
            "a2a_t.prompt.config",
            "a2a_t.prompt.errors",
            "a2a_t.prompt.loader",
            "a2a_t.prompt.models",
            "a2a_t.prompt.parser",
            "a2a_t.prompt.providers",
        ]

        for module_name in module_names:
            module = importlib.import_module(module_name)
            self.assertEqual(module.__name__, module_name)

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
        self.assertFalse(hasattr(PromptLoaderConfig, "from_env"))
        self.assertFalse(hasattr(PromptComplianceConfig, "from_env"))


if __name__ == "__main__":
    unittest.main()
