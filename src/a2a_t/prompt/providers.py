from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib import request
from urllib.error import HTTPError, URLError

from .errors import PromptFetchError
from .models import FetchResult, PromptSource


logger = logging.getLogger(__name__)


class PromptProvider(Protocol):
    def fetch(self, locator: str) -> FetchResult: ...


class LocalFileFetcher(Protocol):
    def __call__(self, locator: str) -> tuple[str, str | None, datetime]: ...


class UrlFetcher(Protocol):
    def __call__(self, url: str) -> tuple[str, str | None, str, datetime]: ...


class AgentFetcher(Protocol):
    def __call__(self, locator: str) -> tuple[str, str | None, datetime]: ...


class LocalFileProvider:
    """直接从本地 Markdown 文件加载 Prompt 内容 / Load prompt content directly from a local Markdown file."""

    def __init__(self, *, fetcher: LocalFileFetcher | None = None) -> None:
        self._fetcher = fetcher or self._default_fetcher

    def fetch(self, locator: str) -> FetchResult:
        """从本地文件路径读取 Prompt 内容 / Read prompt content from a local file path."""

        logger.info("Loading prompt source_type=local_file locator=%s", locator)
        content, content_type, fetched_at = self._fetcher(locator)

        return FetchResult(
            content=content,
            content_type=content_type,
            source=PromptSource(
                source_type="local_file",
                locator=locator,
            ),
            fetched_at=fetched_at,
        )

    def _default_fetcher(self, locator: str) -> tuple[str, str | None, datetime]:
        path = Path(locator)
        if not path.exists() or not path.is_file():
            logger.warning("Local prompt file does not exist source_type=local_file locator=%s", locator)
            raise PromptFetchError("Local prompt file does not exist.", locator=locator)

        content = path.read_text(encoding="utf-8")
        return content, "text/markdown", datetime.now(timezone.utc)


class UrlProvider:
    """从直接 URL 加载 Prompt 内容 / Load prompt content from a direct URL."""

    def __init__(self, *, fetcher: UrlFetcher | None = None) -> None:
        self._fetcher = fetcher or self._default_fetcher

    def fetch(self, locator: str) -> FetchResult:
        """从 URL 获取 Prompt 内容并归一化请求错误 / Fetch prompt content from a URL and normalize request errors."""

        logger.info("Fetching prompt source_type=url locator=%s", locator)
        try:
            content, content_type, _resolved_uri, fetched_at = self._fetcher(locator)
        except PromptFetchError:
            logger.warning("URL fetch failed source_type=url locator=%s", locator)
            raise
        except (socket.timeout, TimeoutError) as error:
            logger.warning("URL fetch timed out source_type=url locator=%s", locator)
            raise PromptFetchError("URL fetch timed out.", locator=locator) from error
        except HTTPError as error:
            logger.warning("URL fetch returned HTTP error source_type=url locator=%s status_code=%s", locator, error.code)
            raise PromptFetchError("HTTP request failed.", locator=locator, status_code=error.code) from error
        except URLError as error:
            logger.warning("URL request failed source_type=url locator=%s reason=%s", locator, error)
            raise PromptFetchError("URL request failed.", locator=locator) from error

        return FetchResult(
            content=content,
            content_type=content_type,
            source=PromptSource(
                source_type="url",
                locator=locator,
            ),
            fetched_at=fetched_at,
        )

    def _default_fetcher(self, url: str) -> tuple[str, str | None, str, datetime]:
        with request.urlopen(url, timeout=10) as response:
            body = response.read().decode("utf-8")
            content_type = response.headers.get("Content-Type")
            resolved_uri = response.geturl()

        return body, content_type, resolved_uri, datetime.now(timezone.utc)


class AgentProvider:
    """从已知 Agent Prompt URL 获取 Prompt 内容 / Load prompt content from a known agent prompt URL."""

    def __init__(self, *, fetcher: AgentFetcher | None = None) -> None:
        self._fetcher = fetcher or self._default_fetcher

    def fetch(self, locator: str) -> FetchResult:
        """从已知 Agent URL 获取 Prompt 内容 / Fetch prompt content from a known agent URL."""

        logger.info("Fetching prompt source_type=agent locator=%s", locator)
        try:
            content, content_type, fetched_at = self._fetcher(locator)
        except PromptFetchError:
            logger.warning("Agent fetch failed source_type=agent locator=%s", locator)
            raise
        except (socket.timeout, TimeoutError) as error:
            logger.warning("Agent fetch timed out source_type=agent locator=%s", locator)
            raise PromptFetchError("Agent fetch timed out.", locator=locator) from error
        except HTTPError as error:
            logger.warning(
                "Agent fetch returned HTTP error source_type=agent locator=%s status_code=%s",
                locator,
                error.code,
            )
            raise PromptFetchError("HTTP request failed.", locator=locator, status_code=error.code) from error
        except URLError as error:
            logger.warning("Agent request failed source_type=agent locator=%s reason=%s", locator, error)
            raise PromptFetchError("Agent request failed.", locator=locator) from error

        return FetchResult(
            content=content,
            content_type=content_type,
            source=PromptSource(
                source_type="agent",
                locator=locator,
            ),
            fetched_at=fetched_at,
        )

    def _default_fetcher(self, locator: str) -> tuple[str, str | None, datetime]:
        content, content_type, _resolved_uri, fetched_at = UrlProvider()._default_fetcher(locator)
        return content, content_type, fetched_at
