from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..common.errors import PromptVersionComparisonError
from ..common.models import CacheStatus, CachedPromptRecord


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
    def is_expired(self, *, record: CachedPromptRecord, now: datetime) -> bool:
        return record.expires_at < now


class OverwriteOnConflictPolicy:
    def should_overwrite(self, *, existing_record: CachedPromptRecord, new_record: CachedPromptRecord) -> bool:
        return True


class OverwriteIfNewerVersionPolicy:
    OVERWRITE_REASON = "overwrite_on_newer_version"

    def should_overwrite(self, *, existing_record: CachedPromptRecord, new_record: CachedPromptRecord) -> bool:
        return self.compare_versions(new_record.version, existing_record.version) >= 0

    def compare_versions(self, left: str, right: str) -> int:
        left_parts = self._parse_version(left)
        right_parts = self._parse_version(right)
        width = max(len(left_parts), len(right_parts))
        left_parts.extend([0] * (width - len(left_parts)))
        right_parts.extend([0] * (width - len(right_parts)))

        for left_part, right_part in zip(left_parts, right_parts, strict=True):
            if left_part > right_part:
                return 1
            if left_part < right_part:
                return -1
        return 0

    def overwrite_reason(self) -> str:
        return self.OVERWRITE_REASON

    def _parse_version(self, version: str) -> list[int]:
        parts = version.split(".")
        if not parts or any(not part.isdigit() for part in parts):
            raise PromptVersionComparisonError("Prompt version is invalid.", version=version)
        return [int(part) for part in parts]
