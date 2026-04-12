from __future__ import annotations

import hashlib
from datetime import datetime
import logging
from typing import Callable

from .cache import PromptStore
from .config import PromptLoaderConfig
from .errors import PromptMetadataError
from .models import CacheStatus, CachedPromptRecord, Prompt, PromptReference, PromptSource
from .parser import PromptParser
from .providers import PromptProvider


logger = logging.getLogger(__name__)


class PromptLoader:
    """加载 Prompt，协调 provider、解析、校验与缓存流程 / Load prompts by coordinating providers, parsing, validation, and caching."""

    def __init__(
        self,
        *,
        config: PromptLoaderConfig,
        parser: PromptParser,
        cache_store: PromptStore,
        providers: dict[str, PromptProvider],
        now_provider: Callable[[], datetime],
    ) -> None:
        self._config = config
        self._parser = parser
        self._cache_store = cache_store
        self._providers = providers
        self._now_provider = now_provider

    def load(
        self,
        *,
        reference: PromptReference | None = None,
        refresh: bool = False,
        **legacy_kwargs: object,
    ) -> Prompt:
        """从来源加载 Prompt 并校验其发布契约 / Load a prompt from a source and validate its publish contract."""

        source, expected_name, expected_language, expected_version = self._resolve_load_inputs(
            reference=reference,
            legacy_kwargs=legacy_kwargs,
        )

        logger.info("Loading prompt source_type=%s locator=%s", source.source_type, source.locator)
        provider = self._providers[source.source_type]
        if source.source_type == "local_file":
            fetch_result = provider.fetch(source.locator)
            prompt = self._parser.parse(
                content=fetch_result.content,
                source=fetch_result.source,
                cache_status=CacheStatus.MISS,
            )
            self._validate_prompt(
                prompt,
                expected_name=expected_name,
                expected_language=expected_language,
                expected_version=expected_version,
            )
            logger.info(
                "Loaded prompt source_type=local_file name=%s language=%s version=%s",
                prompt.name,
                prompt.language,
                prompt.version,
            )
            return prompt

        cached_prompt = None
        cached_record = None

        logger.info(
            "Resolving cache before remote fetch name=%s language=%s version=%s",
            expected_name,
            expected_language,
            expected_version,
        )
        cached_record, cached_content, cache_status = self._cache_store.resolve(
            source_type=source.source_type,
            name=expected_name,
            version=expected_version,
            language=expected_language,
            now=self._now_provider(),
            allow_stale_fallback=True,
        )

        if cached_record is not None and cached_content is not None:
            cached_prompt = self._parser.parse(
                content=cached_content,
                source=source,
                cache_status=cache_status,
            )
            self._validate_prompt(
                cached_prompt,
                expected_name=expected_name,
                expected_language=expected_language,
                expected_version=expected_version,
            )
            if cache_status == CacheStatus.HIT and not refresh:
                logger.info(
                    "Returning cached prompt name=%s language=%s version=%s",
                    cached_prompt.name,
                    cached_prompt.language,
                    cached_prompt.version,
                )
                return cached_prompt

        if cache_status == CacheStatus.MISS:
            logger.info(
                "Cache is unavailable name=%s language=%s version=%s",
                expected_name,
                expected_language,
                expected_version,
            )

        try:
            logger.info(
                "Fetching remote prompt name=%s language=%s version=%s source_type=%s locator=%s",
                expected_name,
                expected_language,
                expected_version,
                source.source_type,
                source.locator,
            )
            fetch_result = provider.fetch(source.locator)
            prompt = self._parser.parse(
                content=fetch_result.content,
                source=fetch_result.source,
                cache_status=CacheStatus.REFRESHED,
            )
            self._validate_prompt(
                prompt,
                expected_name=expected_name,
                expected_language=expected_language,
                expected_version=expected_version,
            )
            record = self._build_record(prompt=prompt, fetched_at=fetch_result.fetched_at)
            self._cache_store.write(record=record, content=fetch_result.content)
            logger.info(
                "Loaded and cached prompt name=%s language=%s version=%s",
                prompt.name,
                prompt.language,
                prompt.version,
            )
            return prompt
        except Exception:
            if cached_prompt is not None and cache_status == CacheStatus.STALE_FALLBACK:
                logger.warning(
                    "Remote refresh failed; using stale cache fallback name=%s language=%s version=%s",
                    expected_name,
                    expected_language,
                    expected_version,
                )
                cached_prompt.cache_status = CacheStatus.STALE_FALLBACK
                return cached_prompt
            logger.warning(
                "Remote prompt load failed without usable fallback name=%s language=%s version=%s",
                expected_name,
                expected_language,
                expected_version,
            )
            raise

    def _resolve_load_inputs(
        self,
        *,
        reference: PromptReference | None,
        legacy_kwargs: dict[str, object],
    ) -> tuple[PromptSource, str, str, str]:
        """解析 load() 的新旧输入形式 / Resolve the new and legacy input forms accepted by load()."""

        if reference is not None:
            return (
                reference.source,
                reference.name,
                reference.language,
                reference.version,
            )

        source = legacy_kwargs["source"]
        expected_name = legacy_kwargs["expected_name"]
        expected_language = legacy_kwargs["expected_language"]
        expected_version = legacy_kwargs["expected_version"]

        if not isinstance(source, PromptSource):
            raise TypeError("source must be a PromptSource.")
        if not isinstance(expected_name, str):
            raise TypeError("expected_name must be a string.")
        if not isinstance(expected_language, str):
            raise TypeError("expected_language must be a string.")
        if not isinstance(expected_version, str):
            raise TypeError("expected_version must be a string.")

        return source, expected_name, expected_language, expected_version

    def _validate_prompt(
        self,
        prompt: Prompt,
        *,
        expected_name: str,
        expected_language: str,
        expected_version: str,
    ) -> None:
        """强制校验解析结果与调用方期望身份一致 / Enforce that parsed metadata matches the caller's expected identity."""

        comparisons = (
            ("name", expected_name, prompt.name),
            ("language", expected_language, prompt.language),
            ("version", expected_version, prompt.version),
        )
        for field_name, expected_value, actual_value in comparisons:
            if actual_value != expected_value:
                logger.warning(
                    "Prompt metadata mismatch field=%s expected=%s actual=%s",
                    field_name,
                    expected_value,
                    actual_value,
                )
                raise PromptMetadataError(
                    f"Prompt metadata does not match expected {field_name}.",
                    **{f"expected_{field_name}": expected_value, f"actual_{field_name}": actual_value},
                )

    def _build_record(self, *, prompt: Prompt, fetched_at: datetime) -> CachedPromptRecord:
        """创建与缓存内容一起持久化的元数据记录 / Create the metadata record persisted alongside cached prompt content."""

        content_hash = hashlib.sha256(prompt.raw_content.encode("utf-8")).hexdigest()
        return CachedPromptRecord(
            source_type=prompt.source.source_type,
            name=prompt.name,
            language=prompt.language,
            version=prompt.version,
            format=prompt.format,
            fetched_at=fetched_at,
            expires_at=fetched_at + self._config.default_ttl,
            source_locator=f"{prompt.source.source_type}://{prompt.source.locator}",
            parser_name=prompt.format,
            content_hash=f"sha256:{content_hash}",
        )
