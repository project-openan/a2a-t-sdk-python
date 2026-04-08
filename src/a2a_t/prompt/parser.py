from __future__ import annotations

import logging
from typing import Protocol

from .errors import PromptMetadataError, PromptParseError
from .models import CacheStatus, Prompt, PromptSource


logger = logging.getLogger(__name__)


class PromptParser(Protocol):
    def parse(
        self,
        *,
        content: str,
        source: PromptSource,
        cache_status: CacheStatus,
    ) -> Prompt: ...


class MarkdownPromptParser:
    """解析 Markdown Prompt 并校验 front matter 元数据 / Parse Markdown prompts and validate their front matter metadata."""

    def parse(
        self,
        *,
        content: str,
        source: PromptSource,
        cache_status: CacheStatus,
    ) -> Prompt:
        """解析 Markdown Prompt 并返回标准化 Prompt 对象 / Parse a Markdown prompt and return the normalized prompt object."""

        logger.info("Parsing prompt locator=%s source_type=%s", source.locator, source.source_type)
        metadata, body = self._split_front_matter(content, locator=source.locator)

        name = self._require(metadata, "name")
        language = metadata.get("language") or "default"
        version = self._require(metadata, "version")
        title = self._require(metadata, "title")
        description = self._require(metadata, "description")

        return Prompt(
            name=name,
            language=language,
            version=version,
            title=title,
            description=description,
            format="markdown",
            body=body,
            raw_content=content,
            source=source,
            cache_status=cache_status,
        )

    def _split_front_matter(self, content: str, *, locator: str) -> tuple[dict[str, str], str]:
        if not content.startswith("---\n"):
            logger.warning("Prompt content is missing opening front matter fence locator=%s", locator)
            raise PromptParseError("Markdown prompt must start with front matter.")

        closing_index = content.find("\n---\n", 4)
        if closing_index == -1:
            logger.warning("Prompt front matter is missing closing fence locator=%s", locator)
            raise PromptParseError("Markdown front matter is not closed.")

        header = content[4:closing_index]
        body = content[closing_index + len("\n---\n") :]
        metadata = self._parse_metadata(header, locator=locator)

        return metadata, body

    def _parse_metadata(self, header: str, *, locator: str) -> dict[str, str]:
        metadata: dict[str, str] = {}

        for line in header.splitlines():
            if not line.strip():
                continue
            if ":" not in line:
                logger.warning("Prompt front matter contains invalid line locator=%s line=%s", locator, line)
                raise PromptParseError(f"Invalid front matter line: {line}")

            key, value = line.split(":", 1)
            normalized_key = key.strip()
            normalized_value = value.strip()

            if not normalized_key or not normalized_value:
                logger.warning("Prompt front matter contains empty key/value locator=%s line=%s", locator, line)
                raise PromptParseError(f"Invalid front matter line: {line}")

            metadata[normalized_key] = normalized_value

        return metadata

    def _require(self, metadata: dict[str, str], field_name: str) -> str:
        value = metadata.get(field_name)
        if not value:
            logger.warning("Prompt metadata is missing required field field=%s", field_name)
            raise PromptMetadataError(f"Missing required metadata field: {field_name}", field=field_name)

        return value
