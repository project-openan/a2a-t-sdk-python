from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


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

    def test_root_package_only_keeps_minimal_domain_entrypoints(self) -> None:
        package = importlib.import_module("a2a_t")

        self.assertEqual(
            package.__all__,
            ["__version__", "client", "server", "config", "llm", "common", "prompt"],
        )
        self.assertTrue(hasattr(package, "client"))
        self.assertTrue(hasattr(package, "server"))
        self.assertTrue(hasattr(package, "config"))
        self.assertTrue(hasattr(package, "llm"))
        self.assertTrue(hasattr(package, "common"))

        for removed_name in [
            "errors",
            "connection_pool",
            "logging",
            "utils",
            "models",
            "loader",
            "cache",
            "prompt_errors",
            "base",
            "chain",
            "compression_errors",
            "llm_base",
            "factory",
            "config_models",
        ]:
            self.assertNotIn(removed_name, package.__all__)
            with self.assertRaises(AttributeError):
                getattr(package, removed_name)

    def test_prompt_package_is_importable_from_a2a_t(self) -> None:
        package = importlib.import_module("a2a_t.prompt")

        self.assertEqual(package.__name__, "a2a_t.prompt")

    def test_prompt_package_only_exports_minimal_task_prompt_api(self) -> None:
        package = importlib.import_module("a2a_t.prompt")

        self.assertEqual(
            package.__all__,
            [
                "PromptReference",
                "TaskPromptMetadata",
                "TaskPromptFormatError",
                "format_task_prompt",
                "parse_task_prompt_metadata",
                "TaskPromptRenderer",
                "TaskPromptRenderError",
            ],
        )

        for kept_name in package.__all__:
            self.assertTrue(hasattr(package, kept_name))

        for removed_name in [
            "CacheStatus",
            "ConflictResolutionPolicy",
            "CachedPromptRecord",
            "ExpirationPolicy",
            "FetchResult",
            "OverwriteIfNewerVersionPolicy",
            "OverwriteOnConflictPolicy",
            "Prompt",
            "PromptAssetReference",
            "PromptCacheError",
            "PromptCatalogRegistryError",
            "PromptConfigError",
            "PromptConflictError",
            "PromptFetchError",
            "PromptLoaderError",
            "PromptMetadataError",
            "PromptParseError",
            "PromptSource",
            "PromptSourceError",
            "PromptStore",
            "PromptVersionComparisonError",
            "TTLExpirationPolicy",
        ]:
            self.assertNotIn(removed_name, package.__all__)
            with self.assertRaises(AttributeError):
                getattr(package, removed_name)


if __name__ == "__main__":
    unittest.main()
