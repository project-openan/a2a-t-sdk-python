from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
import logging
from pathlib import Path
from typing import Protocol

from .errors import PromptCacheError
from .models import CacheStatus, CachedPromptRecord


logger = logging.getLogger(__name__)


class PromptStore(Protocol):
    def write(self, *, record: CachedPromptRecord, content: str) -> None: ...

    def read(self, *, source_type: str, cache_key: str) -> tuple[CachedPromptRecord, str]: ...

    def resolve(
        self,
        *,
        source_type: str,
        cache_key: str,
        now: datetime,
        allow_stale_fallback: bool,
    ) -> tuple[CachedPromptRecord | None, str | None, CacheStatus]: ...


class ExpirationPolicy(Protocol):
    def is_expired(self, *, record: CachedPromptRecord, now: datetime) -> bool: ...


class ConflictResolutionPolicy(Protocol):
    def should_overwrite(self, *, existing_record: CachedPromptRecord, new_record: CachedPromptRecord) -> bool: ...


class TTLExpirationPolicy:
    """根据缓存记录的 `expires_at` 判断是否过期 / Determine whether a cached prompt record should be treated as expired."""

    def is_expired(self, *, record: CachedPromptRecord, now: datetime) -> bool:
        return record.expires_at < now


class OverwriteOnConflictPolicy:
    """总是允许新缓存记录覆盖旧记录 / Always allow a new cache record to overwrite an existing one."""

    def should_overwrite(self, *, existing_record: CachedPromptRecord, new_record: CachedPromptRecord) -> bool:
        return True


class LocalFilePromptStore:
    """在本地文件系统持久化远端 Prompt 内容与元数据 / Persist remote prompt content and metadata on the local filesystem."""

    def __init__(
        self,
        cache_root: Path | str,
        *,
        expiration_policy: ExpirationPolicy | None = None,
        conflict_resolution_policy: ConflictResolutionPolicy | None = None,
    ) -> None:
        self._cache_root = Path(cache_root)
        self._expiration_policy = expiration_policy or TTLExpirationPolicy()
        self._conflict_resolution_policy = conflict_resolution_policy or OverwriteOnConflictPolicy()

    def write(self, *, record: CachedPromptRecord, content: str) -> None:
        """将 Prompt 内容文件及其元数据写入缓存 / Write a prompt content file and its metadata into the cache."""

        logger.info(
            "Writing prompt entry name=%s version=%s language=%s",
            record.name,
            record.version,
            record.language,
        )
        cache_dir = self._cache_dir(record=record)
        content_path = cache_dir / self._content_filename(format_name=record.format)
        metadata_path = cache_dir / "metadata.json"
        existing_record = self._read_existing_record(metadata_path=metadata_path, cache_key=record.cache_key)

        if existing_record is not None and not self._conflict_resolution_policy.should_overwrite(
            existing_record=existing_record,
            new_record=record,
        ):
            logger.warning("Cache conflict cannot be resolved cache_key=%s", record.cache_key)
            raise PromptCacheError("Cache conflict cannot be resolved.", cache_key=record.cache_key)

        cache_dir.mkdir(parents=True, exist_ok=True)

        content_path.write_text(content, encoding="utf-8")
        metadata_path.write_text(self._serialize_record(record), encoding="utf-8")

    def read(self, *, source_type: str, cache_key: str) -> tuple[CachedPromptRecord, str]:
        """读取缓存的 Prompt 记录并校验必要文件存在 / Read a cached prompt entry and validate that required files exist."""

        logger.info("Reading cache entry cache_key=%s", cache_key)
        cache_dir = self._cache_dir_from_cache_key(cache_key=cache_key)
        metadata_path = cache_dir / "metadata.json"

        if not metadata_path.exists():
            logger.warning("Cache metadata file is missing cache_key=%s", cache_key)
            raise PromptCacheError("Cache metadata file is missing.", cache_key=cache_key)

        try:
            record_data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            logger.warning("Cache metadata file is corrupted cache_key=%s", cache_key)
            raise PromptCacheError("Cache metadata file is corrupted.", cache_key=cache_key) from error

        try:
            record = self._deserialize_record(record_data)
        except (KeyError, TypeError, ValueError) as error:
            logger.warning("Cache metadata payload is invalid cache_key=%s", cache_key)
            raise PromptCacheError("Cache metadata file is invalid.", cache_key=cache_key) from error

        content_path = cache_dir / self._content_filename(format_name=record.format)
        if not content_path.exists():
            logger.warning("Cache content file is missing cache_key=%s", cache_key)
            raise PromptCacheError("Cache content file is missing.", cache_key=cache_key)

        return record, content_path.read_text(encoding="utf-8")

    def get(
        self,
        *,
        source_type: str,
        cache_key: str,
        now: datetime,
        allow_stale_fallback: bool,
    ) -> tuple[CachedPromptRecord, str, CacheStatus]:
        """根据过期状态与策略返回命中或陈旧回退结果 / Return a cache hit or stale fallback depending on expiry and policy."""

        record, content, cache_status = self.resolve(
            source_type=source_type,
            cache_key=cache_key,
            now=now,
            allow_stale_fallback=allow_stale_fallback,
        )

        if cache_status == CacheStatus.MISS:
            raise PromptCacheError("Cache metadata file is missing.", cache_key=cache_key)
        if cache_status == CacheStatus.EXPIRED:
            logger.warning("Cache entry expired and stale fallback is disabled cache_key=%s", cache_key)
            raise PromptCacheError("Cache entry is expired.", cache_key=cache_key)

        assert record is not None
        assert content is not None
        return record, content, cache_status

    def resolve(
        self,
        *,
        source_type: str,
        cache_key: str,
        now: datetime,
        allow_stale_fallback: bool,
    ) -> tuple[CachedPromptRecord | None, str | None, CacheStatus]:
        """返回缓存命中、缺失或过期语义结果 / Return cache miss, hit, expired, or stale fallback semantics."""

        try:
            record, content = self.read(source_type=source_type, cache_key=cache_key)
        except PromptCacheError:
            logger.info("Cache entry is missing cache_key=%s", cache_key)
            return None, None, CacheStatus.MISS

        if not self._expiration_policy.is_expired(record=record, now=now):
            logger.info("Cache entry is fresh cache_key=%s", cache_key)
            return record, content, CacheStatus.HIT
        if allow_stale_fallback:
            logger.warning("Cache entry expired; using stale fallback cache_key=%s", cache_key)
            return record, content, CacheStatus.STALE_FALLBACK

        logger.info("Cache entry is expired cache_key=%s", cache_key)
        return record, content, CacheStatus.EXPIRED

    def _cache_dir(self, *, record: CachedPromptRecord) -> Path:
        return self._safe_identity_dir(name=record.name, version=record.version, language=record.language)

    def _cache_dir_from_cache_key(self, *, cache_key: str) -> Path:
        try:
            name, version, language, _ = cache_key.split("||")
        except ValueError as error:
            raise PromptCacheError("Cache key is invalid.", cache_key=cache_key) from error
        return self._safe_identity_dir(name=name, version=version, language=language)

    def _safe_identity_dir(self, *, name: str, version: str, language: str) -> Path:
        identity_parts = (
            ("name", name),
            ("version", version),
            ("language", language),
        )
        safe_parts = [
            self._validate_path_part(part, field_name=field_name) for field_name, part in identity_parts
        ]
        root = self._cache_root.resolve()
        target = root.joinpath(*safe_parts).resolve()
        if root != target and root not in target.parents:
            raise PromptCacheError(
                "Prompt identity path escapes local root.",
                cache_key=f"{name}||{version}||{language}",
            )
        return target

    def _validate_path_part(self, part: str, *, field_name: str) -> str:
        invalid_characters = set('<>:"/\\|?*')
        if not part or part in {".", ".."}:
            raise PromptCacheError("Prompt identity path part is invalid.", field=field_name)
        if Path(part).is_absolute() or any(character in invalid_characters for character in part):
            raise PromptCacheError("Prompt identity path part is invalid.", field=field_name)
        if any(ord(character) < 32 for character in part):
            raise PromptCacheError("Prompt identity path part is invalid.", field=field_name)
        return part

    def _content_filename(self, *, format_name: str) -> str:
        if format_name == "markdown":
            return "prompt.md"
        return f"prompt.{format_name.lstrip('.')}"

    def _serialize_record(self, record: CachedPromptRecord) -> str:
        if "\n" in record.source_locator or "\r" in record.source_locator:
            raise PromptCacheError("Prompt source locator is invalid.", cache_key=record.cache_key)
        if not record.content_hash.startswith("sha256:"):
            raise PromptCacheError("Prompt content hash is invalid.", cache_key=record.cache_key)

        payload = asdict(record)
        payload["fetched_at"] = record.fetched_at.isoformat()
        payload["expires_at"] = record.expires_at.isoformat()

        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _deserialize_record(self, payload: dict[str, object]) -> CachedPromptRecord:
        return CachedPromptRecord(
            cache_key=str(payload["cache_key"]),
            source_type=str(payload["source_type"]),
            name=str(payload["name"]),
            language=str(payload["language"]),
            version=str(payload["version"]),
            format=str(payload["format"]),
            fetched_at=datetime.fromisoformat(str(payload["fetched_at"])),
            expires_at=datetime.fromisoformat(str(payload["expires_at"])),
            source_locator=str(payload["source_locator"]),
            parser_name=str(payload["parser_name"]),
            content_hash=str(payload["content_hash"]),
        )

    def _read_existing_record(self, *, metadata_path: Path, cache_key: str) -> CachedPromptRecord | None:
        if not metadata_path.exists():
            return None

        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            return self._deserialize_record(payload)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            logger.warning("Existing cache metadata file is invalid cache_key=%s", cache_key)
            raise PromptCacheError("Cache metadata file is invalid.", cache_key=cache_key) from error


CacheStore = LocalFilePromptStore
