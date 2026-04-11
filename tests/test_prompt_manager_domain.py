from __future__ import annotations

import importlib
import inspect
import sys
from datetime import timedelta
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.prompt.config import PromptLoaderConfig
from a2a_t.prompt.cache import (
    ConflictResolutionPolicy,
    ExpirationPolicy,
    LocalFilePromptStore,
    OverwriteOnConflictPolicy,
    PromptStore,
    TTLExpirationPolicy,
)
from a2a_t.prompt.errors import (
    PromptCacheError,
    PromptConfigError,
    PromptConflictError,
    PromptFetchError,
    PromptLoaderError,
    PromptMetadataError,
    PromptParseError,
    PromptSourceError,
    PromptVersionComparisonError,
)
from a2a_t.prompt.loader import PromptLoader
from a2a_t.prompt.models import CacheStatus, FetchResult, Prompt, PromptSource
from a2a_t.prompt.parser import MarkdownPromptParser, PromptParser
from a2a_t.prompt.providers import AgentFetcher, LocalFileFetcher, PromptProvider, UrlFetcher


class PromptLoaderDomainTest(unittest.TestCase):
    def test_prompt_source_only_keeps_minimal_fields(self) -> None:
        self.assertEqual(tuple(PromptSource.__dataclass_fields__.keys()), ("source_type", "locator"))

    def test_prompt_source_keeps_declared_fields(self) -> None:
        source = PromptSource(source_type="url", locator="https://example.com/prompts/alarm.md")

        self.assertEqual(source.source_type, "url")
        self.assertEqual(source.locator, "https://example.com/prompts/alarm.md")

    def test_prompt_keeps_publish_contract_fields(self) -> None:
        source = PromptSource(source_type="local_file", locator="./prompts/alarm.md")
        prompt = Prompt(
            name="diagnosis",
            language="default",
            version="1.0.0",
            title="Alarm Diagnosis",
            description="Diagnose alarm events.",
            format="markdown",
            body="Prompt body",
            raw_content="---\nname: diagnosis\n---",
            source=source,
            cache_status=CacheStatus.MISS,
        )

        self.assertNotIn("namespace", Prompt.__dataclass_fields__)
        self.assertEqual(prompt.name, "diagnosis")
        self.assertEqual(prompt.language, "default")
        self.assertEqual(prompt.cache_status, CacheStatus.MISS)

    def test_prompt_reference_exists_with_listing_and_loading_fields(self) -> None:
        models = importlib.import_module("a2a_t.prompt.models")

        self.assertTrue(hasattr(models, "PromptReference"))
        reference = models.PromptReference(
            name="diagnosis",
            language="default",
            version="1.0.0",
            title="Alarm Diagnosis",
            description="Diagnose alarm events.",
            source=PromptSource(source_type="url", locator="https://example.com/prompts/alarm.md"),
        )

        self.assertEqual(reference.name, "diagnosis")
        self.assertEqual(reference.source.source_type, "url")

    def test_fetch_result_carries_source_and_timestamp(self) -> None:
        source = PromptSource(source_type="agent", locator="127.0.0.1:9000")
        fetched_at = object()

        result = FetchResult(
            content="content",
            content_type="text/markdown",
            source=source,
            fetched_at=fetched_at,
        )

        self.assertEqual(result.content_type, "text/markdown")
        self.assertIs(result.source, source)
        self.assertIs(result.fetched_at, fetched_at)

    def test_prompt_loader_config_keeps_only_active_runtime_fields(self) -> None:
        config = PromptLoaderConfig(default_ttl=timedelta(hours=6))

        self.assertEqual(config.default_ttl, timedelta(hours=6))
        self.assertEqual(config.local_prompt_dir, "./prompts")
        self.assertEqual(config.allowed_extensions, [".md"])
        self.assertNotIn("cache_dir", PromptLoaderConfig.__dataclass_fields__)
        self.assertNotIn("allow_stale_fallback", PromptLoaderConfig.__dataclass_fields__)
        self.assertIsNone(config.default_prompt_extension_uri)
        self.assertEqual(config.prompt_extension_uri_overrides, {})
        self.assertEqual(config.default_prompt_index_url_param_key, "promptIndexUrl")
        self.assertEqual(config.prompt_index_url_param_key_overrides, {})

    def test_prompt_loader_config_rejects_empty_local_prompt_dir(self) -> None:
        with self.assertRaises(PromptConfigError):
            PromptLoaderConfig(default_ttl=timedelta(hours=1), local_prompt_dir="")

    def test_prompt_fetch_error_is_prompt_loader_error(self) -> None:
        error = PromptFetchError("fetch failed", locator="127.0.0.1:9000")

        self.assertIsInstance(error, PromptLoaderError)
        self.assertEqual(error.context["locator"], "127.0.0.1:9000")

    def test_prompt_fetch_error_keeps_short_sentence_message(self) -> None:
        error = PromptFetchError("URL fetch timed out.", locator="https://example.com")

        self.assertEqual(str(error), "URL fetch timed out.")

    def test_prompt_loader_docstring_uses_short_summary_style(self) -> None:
        self.assertIn(
            "Load prompts by coordinating providers, parsing, validation, and caching.",
            PromptLoader.__doc__ or "",
        )

    def test_prompt_loader_load_accepts_prompt_reference(self) -> None:
        signature = inspect.signature(PromptLoader.load)

        self.assertIn("reference", signature.parameters)
        self.assertNotIn("source", signature.parameters)
        self.assertNotIn("expected_name", signature.parameters)

    def test_prompt_parser_protocol_exists(self) -> None:
        self.assertTrue(hasattr(PromptParser, "_is_protocol"))

    def test_prompt_provider_protocol_exists(self) -> None:
        self.assertTrue(hasattr(PromptProvider, "_is_protocol"))

    def test_provider_fetcher_protocols_exist(self) -> None:
        self.assertTrue(hasattr(LocalFileFetcher, "_is_protocol"))
        self.assertTrue(hasattr(UrlFetcher, "_is_protocol"))
        self.assertTrue(hasattr(AgentFetcher, "_is_protocol"))

    def test_prompt_store_protocol_exists(self) -> None:
        self.assertTrue(hasattr(PromptStore, "_is_protocol"))

    def test_cache_policy_protocols_exist(self) -> None:
        self.assertTrue(hasattr(ExpirationPolicy, "_is_protocol"))
        self.assertTrue(hasattr(ConflictResolutionPolicy, "_is_protocol"))

    def test_local_file_prompt_store_docstring_uses_short_summary_style(self) -> None:
        self.assertIn(
            "Persist remote prompt content and metadata on the local filesystem.",
            LocalFilePromptStore.__doc__ or "",
        )

    def test_default_store_policy_docstrings_use_short_summary_style(self) -> None:
        self.assertIn(
            "Determine whether a cached prompt record should be treated as expired.",
            TTLExpirationPolicy.__doc__ or "",
        )
        self.assertIn(
            "Always allow a new cache record to overwrite an existing one.",
            OverwriteOnConflictPolicy.__doc__ or "",
        )

    def test_markdown_prompt_parser_docstring_uses_short_summary_style(self) -> None:
        self.assertIn(
            "Parse Markdown prompts and validate their front matter metadata.",
            MarkdownPromptParser.__doc__ or "",
        )

    def test_prompt_loader_error_docstring_uses_short_summary_style(self) -> None:
        self.assertIn(
            "Base class for all prompt loader errors.",
            PromptLoaderError.__doc__ or "",
        )

    def test_prompt_loader_error_context_preserves_machine_readable_fields(self) -> None:
        error = PromptMetadataError(
            "Prompt metadata does not match expected language.",
            expected_language="zh-CN",
            actual_language="default",
        )

        self.assertEqual(
            error.context,
            {"expected_language": "zh-CN", "actual_language": "default"},
        )

    def test_prompt_error_subclasses_define_short_summary_docstrings(self) -> None:
        self.assertIn("Raised when prompt runtime configuration is invalid.", PromptConfigError.__doc__ or "")
        self.assertIn("Raised when a prompt source is invalid or unsupported.", PromptSourceError.__doc__ or "")
        self.assertIn("Raised when prompt content cannot be fetched from a source.", PromptFetchError.__doc__ or "")
        self.assertIn("Raised when prompt content cannot be parsed as a template.", PromptParseError.__doc__ or "")
        self.assertIn("Raised when required prompt metadata is missing or mismatched.", PromptMetadataError.__doc__ or "")
        self.assertIn("Raised when cached prompt content cannot be read or written.", PromptCacheError.__doc__ or "")
        self.assertIn("Raised when prompt identity conflicts cannot be resolved.", PromptConflictError.__doc__ or "")
        self.assertIn("Raised when prompt version comparison fails.", PromptVersionComparisonError.__doc__ or "")

    def test_prompt_conflict_error_is_prompt_loader_error(self) -> None:
        error = PromptConflictError("Prompt conflict cannot be resolved.", cache_key="diagnosis||1.0.0||zh-CN||markdown")

        self.assertIsInstance(error, PromptLoaderError)
        self.assertEqual(error.context["cache_key"], "diagnosis||1.0.0||zh-CN||markdown")

    def test_prompt_version_comparison_error_is_prompt_loader_error(self) -> None:
        error = PromptVersionComparisonError("Prompt version is invalid.", version="1.0.beta")

        self.assertIsInstance(error, PromptLoaderError)
        self.assertEqual(error.context["version"], "1.0.beta")

    def test_prompt_loader_config_docstring_uses_bilingual_style(self) -> None:
        self.assertIn(
            "Define runtime configuration for the prompt loader.",
            PromptLoaderConfig.__doc__ or "",
        )

    def test_prompt_docstring_uses_bilingual_style(self) -> None:
        self.assertIn(
            "Represent a parsed prompt.",
            Prompt.__doc__ or "",
        )

    def test_readme_matches_namespace_removed_contract(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertNotIn("expected_namespace", readme)
        self.assertNotIn("`namespace`", readme)
        self.assertNotIn("`prompts/<namespace>/<source_type>/<cache_key>/`", readme)
        self.assertIn("`name + language + version`", readme)
        self.assertIn("`prompts/<source_type>/<cache_key>/`", readme)
        self.assertIn("`local_file` 没有缓存过期的概念", readme)
        self.assertIn("`ExpirationPolicy` 负责判断缓存是否过期", readme)
        self.assertIn("`ConflictResolutionPolicy` 负责决定缓存冲突时是否覆盖", readme)

if __name__ == "__main__":
    unittest.main()

