from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.models import CacheStatus, Prompt, PromptReference, PromptSource
from a2a_t.server.prompt_compliance.errors import ProcessedPromptParseError, PromptOriginResolveError
from a2a_t.server.prompt_compliance.models import PromptIdentity
from a2a_t.server.prompt_compliance.origin_resolver import PromptOriginResolver
from a2a_t.server.prompt_compliance.parser import ProcessedPromptParser


class FakePromptCatalog:
    def __init__(self, references: list[PromptReference]) -> None:
        self._references = references

    def list(self) -> list[PromptReference]:
        return list(self._references)


class FakePromptCatalogRegistry:
    def __init__(self, catalogs: dict[str, FakePromptCatalog]) -> None:
        self._catalogs = catalogs

    def list_catalogs(self) -> dict[str, FakePromptCatalog]:
        return dict(self._catalogs)


class FakePromptLoader:
    def __init__(self, prompt: Prompt) -> None:
        self.prompt = prompt
        self.loaded_reference: PromptReference | None = None

    def load(self, *, reference: PromptReference, refresh: bool = False) -> Prompt:
        self.loaded_reference = reference
        return self.prompt


class ProcessedPromptParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = ProcessedPromptParser()

    def test_parse_identity_from_processed_prompt_front_matter(self) -> None:
        identity = self.parser.parse(
            "---\n"
            "name: diagnosis\n"
            "language: zh-CN\n"
            "version: 1.0.0\n"
            "---\n"
            "processed prompt body\n"
        )

        self.assertEqual(
            identity,
            PromptIdentity(name="diagnosis", language="zh-CN", version="1.0.0"),
        )

    def test_parse_identity_falls_back_language_to_default(self) -> None:
        identity = self.parser.parse(
            "---\n"
            "name: diagnosis\n"
            "version: 1.0.0\n"
            "---\n"
            "processed prompt body\n"
        )

        self.assertEqual(identity.language, "default")

    def test_parse_identity_rejects_missing_name(self) -> None:
        with self.assertRaises(ProcessedPromptParseError):
            self.parser.parse(
                "---\n"
                "language: zh-CN\n"
                "version: 1.0.0\n"
                "---\n"
                "processed prompt body\n"
            )

    def test_parse_identity_rejects_invalid_front_matter_line(self) -> None:
        with self.assertRaises(ProcessedPromptParseError):
            self.parser.parse(
                "---\n"
                "name=diagnosis\n"
                "version: 1.0.0\n"
                "---\n"
                "processed prompt body\n"
            )


class PromptOriginResolverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = PromptReference(
            name="diagnosis",
            language="zh-CN",
            version="1.0.0",
            title="Diagnosis Prompt",
            description="Resolve diagnosis requests.",
            source=PromptSource(source_type="local_file", locator="./prompts/diagnosis.md"),
        )
        self.prompt = Prompt(
            name="diagnosis",
            language="zh-CN",
            version="1.0.0",
            title="Diagnosis Prompt",
            description="Resolve diagnosis requests.",
            format="markdown",
            body="prompt body",
            raw_content="raw prompt",
            source=self.reference.source,
            cache_status=CacheStatus.MISS,
        )

    def test_resolve_loads_prompt_from_matching_reference(self) -> None:
        registry = FakePromptCatalogRegistry({"default": FakePromptCatalog([self.reference])})
        loader = FakePromptLoader(self.prompt)
        resolver = PromptOriginResolver(catalog_registry=registry, prompt_loader=loader)

        resolved = resolver.resolve(PromptIdentity(name="diagnosis", language="zh-CN", version="1.0.0"))

        self.assertIs(resolved, self.prompt)
        self.assertEqual(loader.loaded_reference, self.reference)

    def test_resolve_raises_when_identity_is_missing_from_catalogs(self) -> None:
        registry = FakePromptCatalogRegistry({"default": FakePromptCatalog([])})
        loader = FakePromptLoader(self.prompt)
        resolver = PromptOriginResolver(catalog_registry=registry, prompt_loader=loader)

        with self.assertRaises(PromptOriginResolveError):
            resolver.resolve(PromptIdentity(name="diagnosis", language="zh-CN", version="1.0.0"))


if __name__ == "__main__":
    unittest.main()
