from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.prompt.models import CacheStatus, Prompt, PromptSource
from a2a_t.prompt.parser import MarkdownPromptParser, PromptParseError, PromptParserRegistry, build_default_prompt_parser_registry


class FakeJsonPromptParser:
    def parse(
        self,
        *,
        content: str,
        source: PromptSource,
        cache_status: CacheStatus,
    ) -> Prompt:
        return Prompt(
            name="json-diagnosis",
            language="default",
            version="1.0.0",
            title="JSON Diagnosis",
            description="Diagnose from json prompt.",
            format="json",
            body=content,
            raw_content=content,
            source=source,
            cache_status=cache_status,
        )


class PromptParserRegistryTest(unittest.TestCase):
    def test_default_prompt_parser_registry_registers_markdown_parser(self) -> None:
        registry = build_default_prompt_parser_registry()

        parser = registry.get_by_extension(".md")

        self.assertIsInstance(parser, MarkdownPromptParser)
        self.assertIn(".md", registry.list_supported_extensions())

    def test_prompt_parser_registry_routes_custom_extension(self) -> None:
        registry = PromptParserRegistry()
        parser = FakeJsonPromptParser()
        registry.register("json", parser, [".json"])

        self.assertIs(registry.get_by_extension(".json"), parser)

    def test_prompt_parser_registry_rejects_unknown_extension(self) -> None:
        registry = PromptParserRegistry()

        with self.assertRaises(PromptParseError):
            registry.get_by_extension(".yaml")


if __name__ == "__main__":
    unittest.main()
