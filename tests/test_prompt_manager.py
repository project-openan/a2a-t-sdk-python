from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.prompt.cache import LocalFilePromptStore
from a2a_t.prompt.config import PromptLoaderConfig
from a2a_t.prompt.errors import PromptCacheError, PromptFetchError, PromptMetadataError
from a2a_t.prompt.factory import build_default_prompt_catalog_registry, build_default_prompt_loader
from a2a_t.prompt.loader import PromptLoader
from a2a_t.prompt.models import CacheStatus, FetchResult, PromptSource
from a2a_t.prompt.parser import MarkdownPromptParser
from a2a_t.prompt.providers import LocalFileProvider
from tests.test_support import FakeRemoteProvider, ManagedTempDirTestCase, build_markdown


class InMemoryPromptStore:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str, str], tuple[object, str]] = {}
        self.resolve_calls = 0

    def write(self, *, record: object, content: str) -> None:
        cache_key = getattr(record, "cache_key")
        source_type = getattr(record, "source_type")
        self.records[(source_type, cache_key)] = (record, content)

    def read(self, *, source_type: str, cache_key: str) -> tuple[object, str]:
        try:
            return self.records[(source_type, cache_key)]
        except KeyError as error:
            raise PromptCacheError("Cache metadata file is missing.", cache_key=cache_key) from error

    def resolve(
        self,
        *,
        source_type: str,
        cache_key: str,
        now: datetime,
        allow_stale_fallback: bool,
    ) -> tuple[object | None, str | None, CacheStatus]:
        self.resolve_calls += 1
        try:
            record, content = self.read(source_type=source_type, cache_key=cache_key)
        except PromptCacheError:
            return None, None, CacheStatus.MISS

        if record.expires_at >= now:
            return record, content, CacheStatus.HIT
        if allow_stale_fallback:
            return record, content, CacheStatus.STALE_FALLBACK
        return record, content, CacheStatus.EXPIRED


class ResolveOnlyPromptStore:
    def __init__(self, *, record: object | None, content: str | None, cache_status: CacheStatus) -> None:
        self.record = record
        self.content = content
        self.cache_status = cache_status
        self.resolve_calls = 0
        self.writes: list[tuple[object, str]] = []

    def resolve(
        self,
        *,
        source_type: str,
        cache_key: str,
        now: datetime,
        allow_stale_fallback: bool,
    ) -> tuple[object | None, str | None, CacheStatus]:
        self.resolve_calls += 1
        return self.record, self.content, self.cache_status

    def write(self, *, record: object, content: str) -> None:
        self.writes.append((record, content))

    def read(self, *, source_type: str, cache_key: str) -> tuple[object, str]:
        raise AssertionError("PromptLoader should use resolve() instead of read().")


class RejectConflictPolicy:
    def should_overwrite(self, *, existing_record: object, new_record: object) -> bool:
        return False


class PromptLoaderTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_root = self.make_temp_dir("prompt_loader")

        self.cache_root = self.temp_root / "cache"
        self.cache_store = LocalFilePromptStore(self.cache_root)
        self.parser = MarkdownPromptParser()
        self.time_cursor = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

    def _now(self) -> datetime:
        return self.time_cursor

    def _manager(self, *, remote_provider: FakeRemoteProvider | None = None) -> PromptLoader:
        providers = {"local_file": LocalFileProvider()}
        if remote_provider is not None:
            providers["url"] = remote_provider

        return PromptLoader(
            config=PromptLoaderConfig(default_ttl=timedelta(hours=1), local_prompt_dir=str(self.cache_root)),
            parser=self.parser,
            cache_store=self.cache_store,
            providers=providers,
            now_provider=self._now,
        )

    def _expected_cache_key(
        self,
        *,
        source_type: str,
        locator: str,
        name: str,
        language: str,
        version: str,
        format: str = "markdown",
    ) -> str:
        raw_key = f"{source_type}|{locator}|{name}|{language}|{version}|{format}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def test_load_reads_local_file_directly(self) -> None:
        prompt_path = self.temp_root / "alarm.md"
        prompt_path.write_text(
            build_markdown(
                name="diagnosis",
                language="zh-CN",
                version="1.0.0",
                title="Alarm Diagnosis",
                description="Diagnose alarm events.",
                body="Prompt body",
            ),
            encoding="utf-8",
        )

        prompt = self._manager().load(
            source=PromptSource(source_type="local_file", locator=str(prompt_path)),
            expected_name="diagnosis",
            expected_language="zh-CN",
            expected_version="1.0.0",
        )

        self.assertEqual(prompt.body, "Prompt body\n")
        self.assertEqual(prompt.cache_status, CacheStatus.MISS)
        self.assertEqual(prompt.name, "diagnosis")

    def test_load_fetches_remote_and_then_hits_cache(self) -> None:
        locator = "https://example.com/alarm.md"
        source = PromptSource(source_type="url", locator=locator)
        fetched_at = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        remote_provider = FakeRemoteProvider(
            [
                FetchResult(
                    content=build_markdown(
                        name="diagnosis",
                        language="zh-CN",
                        version="1.0.0",
                        title="Alarm Diagnosis",
                        description="Diagnose alarm events.",
                        body="Prompt body",
                    ),
                    content_type="text/markdown",
                    source=PromptSource(source_type="url", locator=locator),
                    fetched_at=fetched_at,
                )
            ]
        )
        manager = self._manager(remote_provider=remote_provider)

        first = manager.load(
            source=source,
            expected_name="diagnosis",
            expected_language="zh-CN",
            expected_version="1.0.0",
        )
        second = manager.load(
            source=source,
            expected_name="diagnosis",
            expected_language="zh-CN",
            expected_version="1.0.0",
        )

        self.assertEqual(first.cache_status, CacheStatus.REFRESHED)
        self.assertEqual(second.cache_status, CacheStatus.HIT)
        self.assertEqual(remote_provider.calls, 1)

    def test_load_supports_custom_prompt_store(self) -> None:
        locator = "https://example.com/alarm.md"
        fetched_at = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        remote_provider = FakeRemoteProvider(
            [
                FetchResult(
                    content=build_markdown(
                        name="diagnosis",
                        language="zh-CN",
                        version="1.0.0",
                        title="Alarm Diagnosis",
                        description="Diagnose alarm events.",
                        body="Prompt body",
                    ),
                    content_type="text/markdown",
                    source=PromptSource(source_type="url", locator=locator),
                    fetched_at=fetched_at,
                )
            ]
        )
        store = InMemoryPromptStore()
        manager = PromptLoader(
            config=PromptLoaderConfig(default_ttl=timedelta(hours=1), local_prompt_dir=str(self.cache_root)),
            parser=self.parser,
            cache_store=store,
            providers={"local_file": LocalFileProvider(), "url": remote_provider},
            now_provider=self._now,
        )

        prompt = manager.load(
            source=PromptSource(source_type="url", locator=locator),
            expected_name="diagnosis",
            expected_language="zh-CN",
            expected_version="1.0.0",
        )

        self.assertEqual(prompt.cache_status, CacheStatus.REFRESHED)
        self.assertEqual(len(store.records), 1)
        self.assertEqual(store.resolve_calls, 1)

    def test_load_uses_prompt_store_resolve_for_cache_hit(self) -> None:
        locator = "https://example.com/alarm.md"
        cached_content = build_markdown(
            name="diagnosis",
            language="zh-CN",
            version="1.0.0",
            title="Alarm Diagnosis",
            description="Diagnose alarm events.",
            body="Prompt body from cache",
        )
        fetched_at = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        record = self._manager()._build_record(
            cache_key=self._expected_cache_key(
                source_type="url",
                locator=locator,
                name="diagnosis",
                language="zh-CN",
                version="1.0.0",
            ),
            prompt=self.parser.parse(
                content=cached_content,
                source=PromptSource(source_type="url", locator=locator),
                cache_status=CacheStatus.HIT,
            ),
            fetched_at=fetched_at,
        )
        store = ResolveOnlyPromptStore(record=record, content=cached_content, cache_status=CacheStatus.HIT)
        remote_provider = FakeRemoteProvider([])
        manager = PromptLoader(
            config=PromptLoaderConfig(default_ttl=timedelta(hours=1), local_prompt_dir=str(self.cache_root)),
            parser=self.parser,
            cache_store=store,
            providers={"local_file": LocalFileProvider(), "url": remote_provider},
            now_provider=self._now,
        )

        prompt = manager.load(
            source=PromptSource(source_type="url", locator=locator),
            expected_name="diagnosis",
            expected_language="zh-CN",
            expected_version="1.0.0",
        )

        self.assertEqual(prompt.body, "Prompt body from cache\n")
        self.assertEqual(prompt.cache_status, CacheStatus.HIT)
        self.assertEqual(store.resolve_calls, 1)
        self.assertEqual(remote_provider.calls, 0)

    def test_load_refresh_true_fetches_even_when_store_hits(self) -> None:
        locator = "https://example.com/alarm.md"
        cached_content = build_markdown(
            name="diagnosis",
            language="zh-CN",
            version="1.0.0",
            title="Alarm Diagnosis",
            description="Diagnose alarm events.",
            body="Prompt body from cache",
        )
        fetched_at = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        record = self._manager()._build_record(
            cache_key=self._expected_cache_key(
                source_type="url",
                locator=locator,
                name="diagnosis",
                language="zh-CN",
                version="1.0.0",
            ),
            prompt=self.parser.parse(
                content=cached_content,
                source=PromptSource(source_type="url", locator=locator),
                cache_status=CacheStatus.HIT,
            ),
            fetched_at=fetched_at,
        )
        store = ResolveOnlyPromptStore(record=record, content=cached_content, cache_status=CacheStatus.HIT)
        remote_provider = FakeRemoteProvider(
            [
                FetchResult(
                    content=build_markdown(
                        name="diagnosis",
                        language="zh-CN",
                        version="1.0.0",
                        title="Alarm Diagnosis",
                        description="Diagnose alarm events.",
                        body="Prompt body from remote",
                    ),
                    content_type="text/markdown",
                    source=PromptSource(source_type="url", locator=locator),
                    fetched_at=fetched_at + timedelta(minutes=5),
                )
            ]
        )
        manager = PromptLoader(
            config=PromptLoaderConfig(default_ttl=timedelta(hours=1), local_prompt_dir=str(self.cache_root)),
            parser=self.parser,
            cache_store=store,
            providers={"local_file": LocalFileProvider(), "url": remote_provider},
            now_provider=self._now,
        )

        prompt = manager.load(
            source=PromptSource(source_type="url", locator=locator),
            expected_name="diagnosis",
            expected_language="zh-CN",
            expected_version="1.0.0",
            refresh=True,
        )

        self.assertEqual(prompt.body, "Prompt body from remote\n")
        self.assertEqual(prompt.cache_status, CacheStatus.REFRESHED)
        self.assertEqual(store.resolve_calls, 1)
        self.assertEqual(remote_provider.calls, 1)
        self.assertEqual(len(store.writes), 1)

    def test_load_writes_prompt_under_parsed_identity_layout(self) -> None:
        locator = "https://example.com/alarm.md"
        source = PromptSource(source_type="url", locator=locator)
        fetched_at = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        remote_provider = FakeRemoteProvider(
            [
                FetchResult(
                    content=build_markdown(
                        name="diagnosis",
                        language="zh-CN",
                        version="1.0.0",
                        title="Alarm Diagnosis",
                        description="Diagnose alarm events.",
                        body="Prompt body",
                    ),
                    content_type="text/markdown",
                    source=PromptSource(source_type="url", locator=locator),
                    fetched_at=fetched_at,
                )
            ]
        )

        manager = self._manager(remote_provider=remote_provider)

        manager.load(
            source=source,
            expected_name="diagnosis",
            expected_language="zh-CN",
            expected_version="1.0.0",
        )

        cache_dir = self.cache_root / "diagnosis" / "1.0.0" / "zh-CN"

        self.assertTrue((cache_dir / "prompt.md").exists())
        self.assertTrue((cache_dir / "metadata.json").exists())
        metadata = json.loads((cache_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["source_locator"], "url://https://example.com/alarm.md")
        self.assertEqual(metadata["parser_name"], "markdown")
        self.assertTrue(metadata["content_hash"].startswith("sha256:"))

    def test_load_refreshes_expired_cache(self) -> None:
        locator = "https://example.com/alarm.md"
        source = PromptSource(source_type="url", locator=locator)
        remote_provider = FakeRemoteProvider(
            [
                FetchResult(
                    content=build_markdown(
                        name="diagnosis",
                        language="zh-CN",
                        version="1.0.0",
                        title="Alarm Diagnosis",
                        description="Diagnose alarm events.",
                        body="Prompt body v1",
                    ),
                    content_type="text/markdown",
                    source=PromptSource(source_type="url", locator=locator),
                    fetched_at=self.time_cursor,
                ),
                FetchResult(
                    content=build_markdown(
                        name="diagnosis",
                        language="zh-CN",
                        version="1.0.0",
                        title="Alarm Diagnosis",
                        description="Diagnose alarm events.",
                        body="Prompt body v2",
                    ),
                    content_type="text/markdown",
                    source=PromptSource(source_type="url", locator=locator),
                    fetched_at=self.time_cursor + timedelta(hours=2),
                ),
            ]
        )
        manager = self._manager(remote_provider=remote_provider)

        manager.load(
            source=source,
            expected_name="diagnosis",
            expected_language="zh-CN",
            expected_version="1.0.0",
        )
        self.time_cursor = self.time_cursor + timedelta(hours=2)
        refreshed = manager.load(
            source=source,
            expected_name="diagnosis",
            expected_language="zh-CN",
            expected_version="1.0.0",
        )

        self.assertEqual(refreshed.body, "Prompt body v2\n")
        self.assertEqual(refreshed.cache_status, CacheStatus.REFRESHED)
        self.assertEqual(remote_provider.calls, 2)

    def test_load_uses_stale_fallback_when_refresh_fails(self) -> None:
        locator = "https://example.com/alarm.md"
        source = PromptSource(source_type="url", locator=locator)
        remote_provider = FakeRemoteProvider(
            [
                FetchResult(
                    content=build_markdown(
                        name="diagnosis",
                        language="zh-CN",
                        version="1.0.0",
                        title="Alarm Diagnosis",
                        description="Diagnose alarm events.",
                        body="Prompt body v1",
                    ),
                    content_type="text/markdown",
                    source=PromptSource(source_type="url", locator=locator),
                    fetched_at=self.time_cursor,
                ),
                PromptFetchError("refresh failed", locator=locator),
            ]
        )
        manager = self._manager(remote_provider=remote_provider)

        manager.load(
            source=source,
            expected_name="diagnosis",
            expected_language="zh-CN",
            expected_version="1.0.0",
        )
        self.time_cursor = self.time_cursor + timedelta(hours=2)
        stale = manager.load(
            source=source,
            expected_name="diagnosis",
            expected_language="zh-CN",
            expected_version="1.0.0",
        )

        self.assertEqual(stale.body, "Prompt body v1\n")
        self.assertEqual(stale.cache_status, CacheStatus.STALE_FALLBACK)

    def test_load_rejects_mismatched_template_without_writing_cache(self) -> None:
        locator = "https://example.com/alarm.md"
        source = PromptSource(source_type="url", locator=locator)
        remote_provider = FakeRemoteProvider(
            [
                FetchResult(
                    content=build_markdown(
                        name="summary",
                        language="zh-CN",
                        version="1.0.0",
                        title="Alarm Summary",
                        description="Summarize alarm events.",
                        body="Prompt body",
                    ),
                    content_type="text/markdown",
                    source=PromptSource(source_type="url", locator=locator),
                    fetched_at=self.time_cursor,
                )
            ]
        )
        manager = self._manager(remote_provider=remote_provider)

        with self.assertRaises(PromptMetadataError):
            manager.load(
                source=source,
                expected_name="diagnosis",
                expected_language="zh-CN",
                expected_version="1.0.0",
            )

        prompts_dir = self.cache_root / "prompts"
        self.assertFalse(prompts_dir.exists() and any(prompts_dir.iterdir()))

    def test_load_rejects_mismatched_prompt_version(self) -> None:
        locator = "https://example.com/alarm.md"
        source = PromptSource(source_type="url", locator=locator)
        remote_provider = FakeRemoteProvider(
            [
                FetchResult(
                    content=build_markdown(
                        name="diagnosis",
                        language="zh-CN",
                        version="2.0.0",
                        title="Alarm Diagnosis",
                        description="Diagnose alarm events.",
                        body="Prompt body",
                    ),
                    content_type="text/markdown",
                    source=PromptSource(source_type="url", locator=locator),
                    fetched_at=self.time_cursor,
                )
            ]
        )
        manager = self._manager(remote_provider=remote_provider)

        with self.assertRaises(PromptMetadataError):
            manager.load(
                source=source,
                expected_name="diagnosis",
                expected_language="zh-CN",
                expected_version="1.0.0",
            )

    def test_build_default_prompt_catalog_registry_registers_local_catalog(self) -> None:
        prompt_root = self.temp_root / "prompts"
        prompt_dir = prompt_root / "diagnosis" / "1.0.0" / "zh-CN"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        (prompt_dir / "prompt.md").write_text(
            build_markdown(
                name="diagnosis",
                language="zh-CN",
                version="1.0.0",
                title="Alarm Diagnosis",
                description="Diagnose alarm events.",
                body="Prompt body",
            ),
            encoding="utf-8",
        )

        registry = build_default_prompt_catalog_registry(
            PromptLoaderConfig(default_ttl=timedelta(hours=1), local_prompt_dir=str(prompt_root))
        )

        self.assertIn("local", registry.list_catalogs())
        references = registry.get("local").list()
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].name, "diagnosis")

    def test_build_default_prompt_catalog_registry_allows_custom_catalog_override(self) -> None:
        class CustomCatalog:
            def list(self) -> list[object]:
                return []

        custom_catalog = CustomCatalog()
        registry = build_default_prompt_catalog_registry(
            PromptLoaderConfig(default_ttl=timedelta(hours=1), local_prompt_dir=str(self.temp_root / "prompts")),
            local_catalog=custom_catalog,
        )

        self.assertIs(registry.get("local"), custom_catalog)

    def test_build_default_prompt_loader_uses_custom_conflict_policy(self) -> None:
        loader = build_default_prompt_loader(
            PromptLoaderConfig(default_ttl=timedelta(hours=1), local_prompt_dir=str(self.cache_root)),
            conflict_resolution_policy=RejectConflictPolicy(),
            now_provider=self._now,
        )

        self.assertIsInstance(loader, PromptLoader)
        self.assertIsInstance(loader._cache_store, LocalFilePromptStore)
        self.assertIsInstance(loader._providers["local_file"], LocalFileProvider)
        self.assertIsInstance(loader._parser, MarkdownPromptParser)
        self.assertIs(loader._cache_store._conflict_resolution_policy.__class__, RejectConflictPolicy)


if __name__ == "__main__":
    unittest.main()

