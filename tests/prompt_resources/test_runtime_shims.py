from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PromptResourceRuntimeShimTest(unittest.TestCase):
    def test_cache_runtime_is_available_under_resources_package(self) -> None:
        from a2a_t.prompt.cache import LocalFilePromptStore as LegacyLocalFilePromptStore
        from a2a_t.prompt.cache import TTLExpirationPolicy as LegacyTTLExpirationPolicy
        from a2a_t.prompt.resources.cache import LocalFilePromptStore
        from a2a_t.prompt.resources.cache import TTLExpirationPolicy

        self.assertIs(LegacyLocalFilePromptStore, LocalFilePromptStore)
        self.assertIs(LegacyTTLExpirationPolicy, TTLExpirationPolicy)

    def test_catalog_runtime_is_available_under_resources_package(self) -> None:
        from a2a_t.prompt.catalog import AgentPromptCatalog as LegacyAgentPromptCatalog
        from a2a_t.prompt.catalog import LocalPromptCatalog as LegacyLocalPromptCatalog
        from a2a_t.prompt.catalog import UrlPromptCatalog as LegacyUrlPromptCatalog
        from a2a_t.prompt.resources.catalog import AgentPromptCatalog
        from a2a_t.prompt.resources.catalog import LocalPromptCatalog
        from a2a_t.prompt.resources.catalog import UrlPromptCatalog

        self.assertIs(LegacyLocalPromptCatalog, LocalPromptCatalog)
        self.assertIs(LegacyUrlPromptCatalog, UrlPromptCatalog)
        self.assertIs(LegacyAgentPromptCatalog, AgentPromptCatalog)

    def test_catalog_registry_runtime_is_available_under_resources_package(self) -> None:
        from a2a_t.prompt.catalog_registry import DefaultPromptCatalogRegistry as LegacyDefaultPromptCatalogRegistry
        from a2a_t.prompt.resources.catalog_registry import DefaultPromptCatalogRegistry

        self.assertIs(LegacyDefaultPromptCatalogRegistry, DefaultPromptCatalogRegistry)

    def test_provider_runtime_is_available_under_resources_package(self) -> None:
        from a2a_t.prompt.providers import AgentProvider as LegacyAgentProvider
        from a2a_t.prompt.providers import LocalFileProvider as LegacyLocalFileProvider
        from a2a_t.prompt.providers import UrlProvider as LegacyUrlProvider
        from a2a_t.prompt.resources.providers import AgentProvider
        from a2a_t.prompt.resources.providers import LocalFileProvider
        from a2a_t.prompt.resources.providers import UrlProvider

        self.assertIs(LegacyLocalFileProvider, LocalFileProvider)
        self.assertIs(LegacyUrlProvider, UrlProvider)
        self.assertIs(LegacyAgentProvider, AgentProvider)

    def test_parser_runtime_is_available_under_resources_package(self) -> None:
        from a2a_t.prompt.parser import MarkdownPromptParser as LegacyMarkdownPromptParser
        from a2a_t.prompt.parser import PromptParserRegistry as LegacyPromptParserRegistry
        from a2a_t.prompt.resources.parser import MarkdownPromptParser
        from a2a_t.prompt.resources.parser import PromptParserRegistry

        self.assertIs(LegacyMarkdownPromptParser, MarkdownPromptParser)
        self.assertIs(LegacyPromptParserRegistry, PromptParserRegistry)

    def test_obsolete_prompt_factory_module_is_not_importable(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            __import__("a2a_t.prompt.factory")


if __name__ == "__main__":
    unittest.main()
