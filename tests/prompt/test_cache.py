from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.prompt.resources.cache import (
    ConflictResolutionPolicy,
    ExpirationPolicy,
    LocalFilePromptStore,
    OverwriteIfNewerVersionPolicy,
    OverwriteOnConflictPolicy,
    PromptStore,
    TTLExpirationPolicy,
)
from a2a_t.prompt.common.errors import PromptCacheError, PromptConflictError, PromptVersionComparisonError
from a2a_t.prompt.common.models import CacheStatus, CachedPromptRecord
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
        self.assertIsInstance(OverwriteIfNewerVersionPolicy(), OverwriteIfNewerVersionPolicy)

    def _record(self, *, expires_at: datetime) -> CachedPromptRecord:
        return CachedPromptRecord(
            source_type="url",
            name="diagnosis",
            language="zh-CN",
            version="1.0.0",
            format="markdown",
            fetched_at=self.now,
            expires_at=expires_at,
            source_locator="url://https://example.com/alarm.md",
            parser_name="markdown",
            content_hash="sha256:abc123",
        )

    def test_write_and_read_round_trip(self) -> None:
        record = self._record(expires_at=self.now + timedelta(hours=1))

        self.store.write(record=record, content="Prompt body")

        cached_record, cached_content = self.store.read(
            source_type=record.source_type,
            name=record.name,
            version=record.version,
            language=record.language,
        )

        self.assertNotIn("namespace", CachedPromptRecord.__dataclass_fields__)
        self.assertEqual(cached_record.source_type, "url")
        self.assertEqual(cached_record.name, "diagnosis")
        self.assertEqual(cached_record.source_locator, "url://https://example.com/alarm.md")
        self.assertEqual(cached_record.content_hash, "sha256:abc123")
        self.assertEqual(cached_content, "Prompt body")

    def test_get_returns_hit_for_unexpired_cache(self) -> None:
        record = self._record(expires_at=self.now + timedelta(hours=1))
        self.store.write(record=record, content="Prompt body")

        cached_record, cached_content, cache_status = self.store.get(
            source_type=record.source_type,
            name=record.name,
            version=record.version,
            language=record.language,
            now=self.now,
            allow_stale_fallback=False,
        )

        self.assertEqual(cached_record.name, record.name)
        self.assertEqual(cached_content, "Prompt body")
        self.assertEqual(cache_status, CacheStatus.HIT)

    def test_resolve_returns_miss_when_cache_is_missing(self) -> None:
        cached_record, cached_content, cache_status = self.store.resolve(
            source_type="url",
            name="missing",
            version="1.0.0",
            language="zh-CN",
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
            name=record.name,
            version=record.version,
            language=record.language,
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
            name=record.name,
            version=record.version,
            language=record.language,
            now=self.now,
            allow_stale_fallback=False,
        )

        self.assertEqual(cached_record.name, record.name)
        self.assertEqual(cached_content, "Prompt body")
        self.assertEqual(cache_status, CacheStatus.EXPIRED)

    def test_get_rejects_expired_cache_when_stale_fallback_disabled(self) -> None:
        record = self._record(expires_at=self.now - timedelta(minutes=1))
        self.store.write(record=record, content="Prompt body")

        with self.assertRaises(PromptCacheError):
            self.store.get(
                source_type=record.source_type,
                name=record.name,
                version=record.version,
                language=record.language,
                now=self.now,
                allow_stale_fallback=False,
            )

    def test_get_uses_custom_expiration_policy(self) -> None:
        store = LocalFilePromptStore(self.cache_root, expiration_policy=NeverExpirePolicy())
        record = self._record(expires_at=self.now - timedelta(days=1))
        store.write(record=record, content="Prompt body")

        _, _, cache_status = store.get(
            source_type=record.source_type,
            name=record.name,
            version=record.version,
            language=record.language,
            now=self.now,
            allow_stale_fallback=False,
        )

        self.assertEqual(cache_status, CacheStatus.HIT)

    def test_write_rejects_conflict_when_policy_disallows_overwrite(self) -> None:
        store = LocalFilePromptStore(self.cache_root, conflict_resolution_policy=RejectOverwritePolicy())
        old_record = self._record(expires_at=self.now + timedelta(hours=1))
        new_record = self._record(expires_at=self.now + timedelta(hours=2))
        store.write(record=old_record, content="Prompt body v1")

        with self.assertRaises(PromptConflictError):
            store.write(record=new_record, content="Prompt body v2")

    def test_read_rejects_corrupted_metadata_file(self) -> None:
        record = self._record(expires_at=self.now + timedelta(hours=1))
        self.store.write(record=record, content="Prompt body")
        metadata_path = self.cache_root / "diagnosis" / "1.0.0" / "zh-CN" / "metadata.json"
        metadata_path.write_text("{not-json", encoding="utf-8")

        with self.assertRaises(PromptCacheError):
            self.store.read(
                source_type=record.source_type,
                name=record.name,
                version=record.version,
                language=record.language,
            )

    def test_read_rejects_missing_content_file(self) -> None:
        record = self._record(expires_at=self.now + timedelta(hours=1))
        self.store.write(record=record, content="Prompt body")
        content_path = self.cache_root / "diagnosis" / "1.0.0" / "zh-CN" / "prompt.md"
        content_path.unlink()

        with self.assertRaises(PromptCacheError):
            self.store.read(
                source_type=record.source_type,
                name=record.name,
                version=record.version,
                language=record.language,
            )

    def test_write_uses_prompt_identity_directory_layout(self) -> None:
        record = self._record(expires_at=self.now + timedelta(hours=1))

        self.store.write(record=record, content="Prompt body")

        cache_dir = self.cache_root / "diagnosis" / "1.0.0" / "zh-CN"
        self.assertTrue((cache_dir / "prompt.md").exists())
        self.assertTrue((cache_dir / "metadata.json").exists())

    def test_write_metadata_uses_fixed_contract(self) -> None:
        record = self._record(expires_at=self.now + timedelta(hours=1))

        self.store.write(record=record, content="Prompt body")

        metadata_path = self.cache_root / "diagnosis" / "1.0.0" / "zh-CN" / "metadata.json"
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(
            set(payload),
            {
                "name",
                "version",
                "language",
                "format",
                "source_type",
                "source_locator",
                "parser_name",
                "content_hash",
                "fetched_at",
                "expires_at",
            },
        )
        self.assertEqual(payload["source_locator"], "url://https://example.com/alarm.md")
        self.assertNotIn("\n", payload["source_locator"])
        self.assertTrue(payload["content_hash"].startswith("sha256:"))
        self.assertNotIn("overwrite_reason", payload)
        self.assertNotIn("previous_content_hash", payload)

    def test_write_rejects_unsafe_identity_path_part(self) -> None:
        unsafe_record = self._record(expires_at=self.now + timedelta(hours=1))
        unsafe_record.name = ".."

        with self.assertRaises(PromptCacheError):
            self.store.write(record=unsafe_record, content="Prompt body")

    def test_write_rejects_absolute_identity_path_part(self) -> None:
        unsafe_record = self._record(expires_at=self.now + timedelta(hours=1))
        unsafe_record.name = str(Path("C:/unsafe"))

        with self.assertRaises(PromptCacheError):
            self.store.write(record=unsafe_record, content="Prompt body")

    def test_write_rejects_reserved_identity_path_character(self) -> None:
        unsafe_record = self._record(expires_at=self.now + timedelta(hours=1))
        unsafe_record.name = "bad:name"

        with self.assertRaises(PromptCacheError):
            self.store.write(record=unsafe_record, content="Prompt body")

    def test_newer_version_overwrites_older_version_and_removes_old_directory(self) -> None:
        store = LocalFilePromptStore(self.cache_root, conflict_resolution_policy=OverwriteIfNewerVersionPolicy())
        old_record = self._record(expires_at=self.now + timedelta(hours=1))
        new_record = self._record(expires_at=self.now + timedelta(hours=2))
        new_record.version = "1.10.0"

        store.write(record=old_record, content="Prompt body v1")
        store.write(record=new_record, content="Prompt body v2")

        self.assertFalse((self.cache_root / "diagnosis" / "1.0.0").exists())
        new_dir = self.cache_root / "diagnosis" / "1.10.0" / "zh-CN"
        payload = json.loads((new_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["overwrite_reason"], "overwrite_on_newer_version")
        self.assertEqual(payload["previous_content_hash"], old_record.content_hash)
        self.assertEqual((new_dir / "prompt.md").read_text(encoding="utf-8"), "Prompt body v2")

    def test_equal_version_overwrites_same_directory_and_records_previous_hash(self) -> None:
        store = LocalFilePromptStore(self.cache_root, conflict_resolution_policy=OverwriteIfNewerVersionPolicy())
        old_record = self._record(expires_at=self.now + timedelta(hours=1))
        new_record = self._record(expires_at=self.now + timedelta(hours=2))
        new_record.content_hash = "sha256:def456"

        store.write(record=old_record, content="Prompt body v1")
        store.write(record=new_record, content="Prompt body v2")

        prompt_path = self.cache_root / "diagnosis" / "1.0.0" / "zh-CN" / "prompt.md"
        payload = json.loads(prompt_path.with_name("metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(prompt_path.read_text(encoding="utf-8"), "Prompt body v2")
        self.assertEqual(payload["overwrite_reason"], "overwrite_on_newer_version")
        self.assertEqual(payload["previous_content_hash"], old_record.content_hash)

    def test_older_version_is_rejected_when_newer_version_exists(self) -> None:
        store = LocalFilePromptStore(self.cache_root, conflict_resolution_policy=OverwriteIfNewerVersionPolicy())
        newer_record = self._record(expires_at=self.now + timedelta(hours=1))
        newer_record.version = "1.10.0"
        older_record = self._record(expires_at=self.now + timedelta(hours=2))
        older_record.version = "1.2.9"

        store.write(record=newer_record, content="Prompt body v2")

        with self.assertRaises(PromptConflictError):
            store.write(record=older_record, content="Prompt body v1")

    def test_non_numeric_dotted_version_is_rejected(self) -> None:
        store = LocalFilePromptStore(self.cache_root, conflict_resolution_policy=OverwriteIfNewerVersionPolicy())
        invalid_record = self._record(expires_at=self.now + timedelta(hours=1))
        invalid_record.version = "1.0.beta"

        with self.assertRaises(PromptVersionComparisonError):
            store.write(record=invalid_record, content="Prompt body")


if __name__ == "__main__":
    unittest.main()
