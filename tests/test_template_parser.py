from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.prompt.errors import PromptMetadataError, PromptParseError
from a2a_t.prompt.models import CacheStatus, PromptSource
from a2a_t.prompt.parser import MarkdownPromptParser, PromptParser


class MarkdownPromptParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = MarkdownPromptParser()
        self.source = PromptSource(source_type="local_file", locator="./prompts/alarm.md")

    def test_prompt_parser_is_protocol(self) -> None:
        self.assertTrue(issubclass(PromptParser, Protocol))

    def test_parse_markdown_prompt_with_publish_contract_front_matter(self) -> None:
        prompt = self.parser.parse(
            content=(
                "---\n"
                "name: diagnosis\n"
                "language: zh-CN\n"
                "version: 1.0.0\n"
                "title: Alarm Diagnosis\n"
                "description: Diagnose alarm events.\n"
                "---\n"
                "Prompt body line 1.\n"
                "Prompt body line 2.\n"
            ),
            source=self.source,
            cache_status=CacheStatus.MISS,
        )

        self.assertEqual(prompt.name, "diagnosis")
        self.assertEqual(prompt.language, "zh-CN")
        self.assertEqual(prompt.version, "1.0.0")
        self.assertEqual(prompt.title, "Alarm Diagnosis")
        self.assertEqual(prompt.description, "Diagnose alarm events.")
        self.assertEqual(prompt.body, "Prompt body line 1.\nPrompt body line 2.\n")
        self.assertEqual(prompt.format, "markdown")
        self.assertEqual(prompt.source, self.source)

    def test_parse_falls_back_language_to_default(self) -> None:
        prompt = self.parser.parse(
            content=(
                "---\n"
                "name: diagnosis\n"
                "version: 1.0.0\n"
                "title: Alarm Diagnosis\n"
                "description: Diagnose alarm events.\n"
                "---\n"
                "Prompt body\n"
            ),
            source=self.source,
            cache_status=CacheStatus.HIT,
        )

        self.assertEqual(prompt.language, "default")
        self.assertEqual(prompt.cache_status, CacheStatus.HIT)

    def test_parse_rejects_missing_name(self) -> None:
        with self.assertRaises(PromptMetadataError):
            self.parser.parse(
                content=(
                    "---\n"
                    "language: zh-CN\n"
                    "version: 1.0.0\n"
                    "title: Alarm Diagnosis\n"
                    "description: Diagnose alarm events.\n"
                    "---\n"
                    "Prompt body\n"
                ),
                source=self.source,
                cache_status=CacheStatus.MISS,
            )

    def test_parse_rejects_missing_version(self) -> None:
        with self.assertRaises(PromptMetadataError):
            self.parser.parse(
                content=(
                    "---\n"
                    "name: diagnosis\n"
                    "language: zh-CN\n"
                    "title: Alarm Diagnosis\n"
                    "description: Diagnose alarm events.\n"
                    "---\n"
                    "Prompt body\n"
                ),
                source=self.source,
                cache_status=CacheStatus.MISS,
            )

    def test_parse_rejects_missing_title(self) -> None:
        with self.assertRaises(PromptMetadataError):
            self.parser.parse(
                content=(
                    "---\n"
                    "name: diagnosis\n"
                    "language: zh-CN\n"
                    "version: 1.0.0\n"
                    "description: Diagnose alarm events.\n"
                    "---\n"
                    "Prompt body\n"
                ),
                source=self.source,
                cache_status=CacheStatus.MISS,
            )

    def test_parse_rejects_missing_description(self) -> None:
        with self.assertRaises(PromptMetadataError):
            self.parser.parse(
                content=(
                    "---\n"
                    "name: diagnosis\n"
                    "language: zh-CN\n"
                    "version: 1.0.0\n"
                    "title: Alarm Diagnosis\n"
                    "---\n"
                    "Prompt body\n"
                ),
                source=self.source,
                cache_status=CacheStatus.MISS,
            )

    def test_parse_rejects_invalid_front_matter(self) -> None:
        with self.assertRaises(PromptParseError):
            self.parser.parse(
                content=(
                    "---\n"
                    "name=diagnosis\n"
                    "name: diagnosis\n"
                    "language: zh-CN\n"
                    "version: 1.0.0\n"
                    "title: Alarm Diagnosis\n"
                    "description: Diagnose alarm events.\n"
                    "---\n"
                    "Prompt body\n"
                ),
                source=self.source,
                cache_status=CacheStatus.MISS,
            )


if __name__ == "__main__":
    unittest.main()
