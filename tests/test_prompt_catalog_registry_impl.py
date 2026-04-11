from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.prompt import DefaultPromptCatalogRegistry, PromptCatalogRegistryError
from a2a_t.prompt.models import PromptReference, PromptSource


class FakePromptCatalog:
    def __init__(self, references: list[PromptReference] | None = None) -> None:
        self._references = references or []

    def list(self) -> list[PromptReference]:
        return list(self._references)


class DefaultPromptCatalogRegistryTest(unittest.TestCase):
    def test_register_then_get_returns_catalog(self) -> None:
        registry = DefaultPromptCatalogRegistry()
        catalog = FakePromptCatalog()

        registry.register("local", catalog)

        self.assertIs(registry.get("local"), catalog)

    def test_list_catalogs_returns_registered_catalog_snapshot(self) -> None:
        registry = DefaultPromptCatalogRegistry()
        local_catalog = FakePromptCatalog()
        remote_catalog = FakePromptCatalog()

        registry.register("local", local_catalog)
        registry.register("remote", remote_catalog)
        catalogs = registry.list_catalogs()
        catalogs.clear()

        self.assertEqual(set(registry.list_catalogs()), {"local", "remote"})

    def test_register_overwrites_existing_catalog_name(self) -> None:
        registry = DefaultPromptCatalogRegistry()
        old_catalog = FakePromptCatalog()
        new_catalog = FakePromptCatalog()

        registry.register("local", old_catalog)
        registry.register("local", new_catalog)

        self.assertIs(registry.get("local"), new_catalog)

    def test_unregister_removes_catalog(self) -> None:
        registry = DefaultPromptCatalogRegistry()
        registry.register("local", FakePromptCatalog())

        registry.unregister("local")

        with self.assertRaises(PromptCatalogRegistryError) as context:
            registry.get("local")
        self.assertEqual(context.exception.context["catalog_name"], "local")

    def test_get_missing_catalog_raises_registry_error(self) -> None:
        registry = DefaultPromptCatalogRegistry()

        with self.assertRaises(PromptCatalogRegistryError) as context:
            registry.get("missing")

        self.assertEqual(context.exception.context["catalog_name"], "missing")

    def test_registered_catalog_can_list_references(self) -> None:
        reference = PromptReference(
            name="diagnosis",
            language="zh-CN",
            version="1.0.0",
            title="Diagnosis",
            description="Diagnose network issues.",
            source=PromptSource(source_type="local_file", locator="./prompts/diagnosis.md"),
        )
        registry = DefaultPromptCatalogRegistry()
        registry.register("local", FakePromptCatalog([reference]))

        self.assertEqual(registry.get("local").list(), [reference])


if __name__ == "__main__":
    unittest.main()
