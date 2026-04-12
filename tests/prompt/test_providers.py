from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.prompt.errors import PromptFetchError
from a2a_t.prompt.providers import AgentFetcher, AgentProvider, LocalFileFetcher, LocalFileProvider, UrlFetcher, UrlProvider
from tests.test_support import ManagedTempDirTestCase


class LocalFileProviderTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_root = self.make_temp_dir("providers")

    def test_fetch_reads_markdown_file(self) -> None:
        prompt_path = self.temp_root / "alarm.md"
        prompt_path.write_text("prompt body", encoding="utf-8")

        result = LocalFileProvider().fetch(str(prompt_path))

        self.assertEqual(result.content, "prompt body")
        self.assertEqual(result.source.source_type, "local_file")
        self.assertEqual(result.source.locator, str(prompt_path))

    def test_fetch_rejects_missing_file(self) -> None:
        missing_path = self.temp_root / "missing.md"

        with self.assertRaises(PromptFetchError):
            LocalFileProvider().fetch(str(missing_path))

    def test_fetch_uses_injected_fetcher(self) -> None:
        fetched_at = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

        class FakeFetcher:
            def __call__(self, locator: str) -> tuple[str, str | None, datetime]:
                self.locator = locator
                return "prompt body from injected fetcher", "text/markdown", fetched_at

        fetcher = FakeFetcher()

        result = LocalFileProvider(fetcher=fetcher).fetch("C:/prompts/alarm.md")

        self.assertEqual(fetcher.locator, "C:/prompts/alarm.md")
        self.assertEqual(result.content, "prompt body from injected fetcher")
        self.assertEqual(result.content_type, "text/markdown")
        self.assertEqual(result.fetched_at, fetched_at)


class UrlProviderTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_root = self.make_temp_dir("url_provider")

    def test_fetch_uses_injected_fetcher_and_returns_context(self) -> None:
        fetched_at = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

        class FakeFetcher:
            def __call__(self, url: str) -> tuple[str, str, str, datetime]:
                return ("prompt body", "text/markdown", "https://cdn.example.com/prompt.md", fetched_at)

        result = UrlProvider(fetcher=FakeFetcher()).fetch("https://example.com/prompt.md")

        self.assertEqual(result.content, "prompt body")
        self.assertEqual(result.content_type, "text/markdown")
        self.assertEqual(result.source.source_type, "url")
        self.assertEqual(result.source.locator, "https://example.com/prompt.md")
        self.assertEqual(result.fetched_at, fetched_at)

    def test_fetch_raises_prompt_fetch_error_for_http_failure(self) -> None:
        class FailingFetcher:
            def __call__(self, url: str) -> tuple[str, str, str, datetime]:
                raise PromptFetchError("HTTP 404", status_code=404)

        with self.assertRaises(PromptFetchError):
            UrlProvider(fetcher=FailingFetcher()).fetch("https://example.com/missing.md")

    def test_fetch_raises_prompt_fetch_error_for_timeout(self) -> None:
        class TimeoutFetcher:
            def __call__(self, url: str) -> tuple[str, str, str, datetime]:
                raise socket.timeout("timed out")

        with self.assertRaises(PromptFetchError):
            UrlProvider(fetcher=TimeoutFetcher()).fetch("https://example.com/timeout.md")

    def test_fetch_downloads_markdown_from_local_http_server(self) -> None:
        prompt_path = self.temp_root / "alarm.md"
        prompt_path.write_text("prompt body from http server", encoding="utf-8")

        handler = partial(SimpleHTTPRequestHandler, directory=str(self.temp_root))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(lambda: server_thread.join(timeout=1))
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        url = f"http://127.0.0.1:{server.server_port}/alarm.md"
        result = UrlProvider().fetch(url)

        self.assertEqual(result.content, "prompt body from http server")
        self.assertEqual(result.source.source_type, "url")
        self.assertEqual(result.source.locator, url)

    def test_fetcher_protocols_exist(self) -> None:
        self.assertTrue(hasattr(LocalFileFetcher, "_is_protocol"))
        self.assertTrue(hasattr(UrlFetcher, "_is_protocol"))
        self.assertTrue(hasattr(AgentFetcher, "_is_protocol"))


class AgentProviderTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_root = self.make_temp_dir("agent_provider")

    def test_fetch_uses_injected_fetcher_without_discovery_context(self) -> None:
        fetched_at = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

        class FakeFetcher:
            def __call__(self, locator: str) -> tuple[str, str | None, datetime]:
                self.locator = locator
                return "prompt body", "text/markdown", fetched_at

        fetcher = FakeFetcher()

        result = AgentProvider(fetcher=fetcher).fetch("https://example.com/prompts/alarm.md")

        self.assertEqual(fetcher.locator, "https://example.com/prompts/alarm.md")
        self.assertEqual(result.content, "prompt body")
        self.assertEqual(result.content_type, "text/markdown")
        self.assertEqual(result.source.source_type, "agent")
        self.assertEqual(result.source.locator, "https://example.com/prompts/alarm.md")
        self.assertEqual(result.fetched_at, fetched_at)

    def test_default_fetch_reads_prompt_from_direct_agent_url(self) -> None:
        prompt_path = self.temp_root / "alarm.md"
        prompt_path.write_text("prompt body from agent url", encoding="utf-8")

        handler = partial(SimpleHTTPRequestHandler, directory=str(self.temp_root))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(lambda: server_thread.join(timeout=1))
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        locator = f"http://127.0.0.1:{server.server_port}/alarm.md"
        result = AgentProvider().fetch(locator)

        self.assertEqual(result.content, "prompt body from agent url")
        self.assertEqual(result.source.source_type, "agent")
        self.assertEqual(result.source.locator, locator)

    def test_fetch_raises_prompt_fetch_error_for_direct_agent_timeout(self) -> None:
        class TimeoutFetcher:
            def __call__(self, locator: str) -> tuple[str, str | None, datetime]:
                raise socket.timeout("timed out")

        with self.assertRaises(PromptFetchError):
            AgentProvider(fetcher=TimeoutFetcher()).fetch("https://example.com/prompts/alarm.md")


if __name__ == "__main__":
    unittest.main()


