from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.prompt.cache import (
    ConflictResolutionPolicy,
    ExpirationPolicy,
    LocalFilePromptStore,
    OverwriteOnConflictPolicy,
    PromptStore,
    TTLExpirationPolicy,
)
from a2a_t.prompt.errors import PromptCacheError
from a2a_t.prompt.models import CacheStatus, CachedPromptRecord
from tests.test_support import ManagedTempDirTestCase


class NeverExpirePolicy:
    def is_expired(self, *, record: CachedPromptRecord, now: datetime) -> bool:
        return False


class RejectOverwritePolicy:
    def should_overwrite(self, *, existing_record: CachedPromptRecord, new_record: CachedPromptRecord) -> bool:
        return False


class LocalFilePromptStoreTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.cache_root = self.make_temp_dir("cache_store")
        self.store = LocalFilePromptStore(self.cache_root)
        self.now = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

    def test_prompt_store_protocol_exists(self) -> None:
        self.assertTrue(hasattr(PromptStore, "_is_protocol"))

    def test_expiration_policy_protocol_exists(self) -> None:
        self.assertTrue(hasattr(ExpirationPolicy, "_is_protocol"))

    def test_conflict_resolution_policy_protocol_exists(self) -> None:
        self.assertTrue(hasattr(ConflictResolutionPolicy, "_is_protocol"))

    def test_default_policy_types_exist(self) -> None:
        self.assertIsInstance(TTLExpirationPolicy(), TTLExpirationPolicy)
        self.assertIsInstance(OverwriteOnConflictPolicy(), OverwriteOnConflictPolicy)

    def _record(self, *, expires_at: datetime) -> CachedPromptRecord:
        return CachedPromptRecord(
            cache_key="url__alarm-diagnosis__zh-CN__1.0.0__markdown",
            source_type="url",
            name="diagnosis",
            language="zh-CN",
            version="1.0.0",
            format="markdown",
            fetched_at=self.now,
            expires_at=expires_at,
            checksum="abc123",
        )

    def test_write_and_read_round_trip(self) -> None:
        record = self._record(expires_at=self.now + timedelta(hours=1))

        self.store.write(record=record, content="Prompt body")

        cached_record, cached_content = self.store.read(
            source_type=record.source_type,
            cache_key=record.cache_key,
        )

        self.assertNotIn("namespace", CachedPromptRecord.__dataclass_fields__)
        self.assertEqual(cached_record.source_type, "url")
        self.assertEqual(cached_record.name, "diagnosis")
        self.assertEqual(cached_content, "Prompt body")

    def test_get_returns_hit_for_unexpired_cache(self) -> None:
        record = self._record(expires_at=self.now + timedelta(hours=1))
        self.store.write(record=record, content="Prompt body")

        cached_record, cached_content, cache_status = self.store.get(
            source_type=record.source_type,
            cache_key=record.cache_key,
            now=self.now,
            allow_stale_fallback=False,
        )

        self.assertEqual(cached_record.cache_key, record.cache_key)
        self.assertEqual(cached_content, "Prompt body")
        self.assertEqual(cache_status, CacheStatus.HIT)

    def test_resolve_returns_miss_when_cache_is_missing(self) -> None:
        cached_record, cached_content, cache_status = self.store.resolve(
            source_type="url",
            cache_key="missing-cache-key",
            now=self.now,
            allow_stale_fallback=False,
        )

        self.assertIsNone(cached_record)
        self.assertIsNone(cached_content)
        self.assertEqual(cache_status, CacheStatus.MISS)

    def test_get_returns_stale_fallback_for_expired_cache_when_allowed(self) -> None:
        record = self._record(expires_at=self.now - timedelta(minutes=1))
        self.store.write(record=record, content="Prompt body")

        cached_record, _, cache_status = self.store.get(
            source_type=record.source_type,
            cache_key=record.cache_key,
            now=self.now,
            allow_stale_fallback=True,
        )

        self.assertEqual(cached_record.expires_at, record.expires_at)
        self.assertEqual(cache_status, CacheStatus.STALE_FALLBACK)

    def test_resolve_returns_expired_when_stale_fallback_disabled(self) -> None:
        record = self._record(expires_at=self.now - timedelta(minutes=1))
        self.store.write(record=record, content="Prompt body")

        cached_record, cached_content, cache_status = self.store.resolve(
            source_type=record.source_type,
            cache_key=record.cache_key,
            now=self.now,
            allow_stale_fallback=False,
        )

        self.assertEqual(cached_record.cache_key, record.cache_key)
        self.assertEqual(cached_content, "Prompt body")
        self.assertEqual(cache_status, CacheStatus.EXPIRED)

    def test_get_rejects_expired_cache_when_stale_fallback_disabled(self) -> None:
        record = self._record(expires_at=self.now - timedelta(minutes=1))
        self.store.write(record=record, content="Prompt body")

        with self.assertRaises(PromptCacheError):
            self.store.get(
                source_type=record.source_type,
                cache_key=record.cache_key,
                now=self.now,
                allow_stale_fallback=False,
            )

    def test_get_uses_custom_expiration_policy(self) -> None:
        store = LocalFilePromptStore(self.cache_root, expiration_policy=NeverExpirePolicy())
        record = self._record(expires_at=self.now - timedelta(days=1))
        store.write(record=record, content="Prompt body")

        _, _, cache_status = store.get(
            source_type=record.source_type,
            cache_key=record.cache_key,
            now=self.now,
            allow_stale_fallback=False,
        )

        self.assertEqual(cache_status, CacheStatus.HIT)

    def test_write_rejects_conflict_when_policy_disallows_overwrite(self) -> None:
        store = LocalFilePromptStore(self.cache_root, conflict_resolution_policy=RejectOverwritePolicy())
        old_record = self._record(expires_at=self.now + timedelta(hours=1))
        new_record = self._record(expires_at=self.now + timedelta(hours=2))
        store.write(record=old_record, content="Prompt body v1")

        with self.assertRaises(PromptCacheError):
            store.write(record=new_record, content="Prompt body v2")

    def test_read_rejects_corrupted_metadata_file(self) -> None:
        record = self._record(expires_at=self.now + timedelta(hours=1))
        self.store.write(record=record, content="Prompt body")
        metadata_path = self.cache_root / "prompts" / record.source_type / record.cache_key / "metadata.json"
        metadata_path.write_text("{not-json", encoding="utf-8")

        with self.assertRaises(PromptCacheError):
            self.store.read(
                source_type=record.source_type,
                cache_key=record.cache_key,
            )

    def test_read_rejects_missing_content_file(self) -> None:
        record = self._record(expires_at=self.now + timedelta(hours=1))
        self.store.write(record=record, content="Prompt body")
        content_path = self.cache_root / "prompts" / record.source_type / record.cache_key / "content.md"
        content_path.unlink()

        with self.assertRaises(PromptCacheError):
            self.store.read(
                source_type=record.source_type,
                cache_key=record.cache_key,
            )

    def test_write_uses_source_type_directory_layout(self) -> None:
        record = self._record(expires_at=self.now + timedelta(hours=1))

        self.store.write(record=record, content="Prompt body")

        cache_dir = self.cache_root / "prompts" / "url" / record.cache_key
        self.assertTrue((cache_dir / "content.md").exists())
        self.assertTrue((cache_dir / "metadata.json").exists())


if __name__ == "__main__":
    unittest.main()
