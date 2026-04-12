from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from threading import Thread
import unittest

from tests.test_support import ManagedTempDirTestCase


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.prompt.config import PromptLoaderConfig
from a2a_t.prompt.errors import PromptSourceError
from a2a_t.prompt.models import CacheStatus, Prompt, PromptSource
from a2a_t.prompt.parser import MarkdownPromptParser, PromptParserRegistry


class PromptCatalogContractTest(unittest.TestCase):
    def test_prompt_catalog_module_exists_under_prompt_package(self) -> None:
        try:
            spec = importlib.util.find_spec("a2a_t.prompt.catalog")
        except ModuleNotFoundError:
            spec = None

        self.assertIsNotNone(spec)

    def test_prompt_catalog_protocol_exists(self) -> None:
        try:
            module = importlib.import_module("a2a_t.prompt.catalog")
        except ModuleNotFoundError:
            self.fail("a2a_t.prompt.catalog is missing")

        self.assertTrue(hasattr(module, "PromptCatalog"))
        self.assertTrue(hasattr(module.PromptCatalog, "_is_protocol"))

    def test_agent_prompt_catalog_constructor_uses_agentcard_type(self) -> None:
        module = importlib.import_module("a2a_t.prompt.catalog")

        signature = inspect.signature(module.AgentPromptCatalog.__init__)

        self.assertEqual(signature.parameters["agent_cards"].annotation, "list[AgentCard]")

    def test_agentcard_type_is_available_at_runtime_in_catalog_module(self) -> None:
        module = importlib.import_module("a2a_t.prompt.catalog")

        self.assertTrue(hasattr(module, "AgentCard"))


class PromptCatalogImplementationTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_root = self.make_temp_dir("prompt_catalog")

    def test_local_prompt_catalog_lists_markdown_files_from_directory(self) -> None:
        prompt_dir = self.temp_root / "local"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = prompt_dir / "diagnosis.md"
        prompt_path.write_text(
            "---\n"
            "name: diagnosis\n"
            "language: zh-CN\n"
            "version: 1.0.0\n"
            "title: Alarm Diagnosis\n"
            "description: Diagnose alarm events.\n"
            "---\n"
            "Prompt body\n",
            encoding="utf-8",
        )

        module = importlib.import_module("a2a_t.prompt.catalog")
        catalog = module.LocalPromptCatalog(prompt_dir=str(prompt_dir), parser=MarkdownPromptParser())

        references = catalog.list()

        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].name, "diagnosis")
        self.assertEqual(references[0].language, "zh-CN")
        self.assertEqual(references[0].version, "1.0.0")
        self.assertEqual(references[0].source.source_type, "local_file")
        self.assertEqual(references[0].source.locator, str(prompt_path))

    def test_local_prompt_catalog_scans_allowed_extensions_and_routes_parser_registry(self) -> None:
        class JsonPromptParser:
            def parse(
                self,
                *,
                content: str,
                source: PromptSource,
                cache_status: CacheStatus,
            ) -> Prompt:
                return Prompt(
                    name="json-diagnosis",
                    language="default",
                    version="1.0.0",
                    title="JSON Diagnosis",
                    description="Diagnose alarm events.",
                    format="json",
                    body=content,
                    raw_content=content,
                    source=source,
                    cache_status=cache_status,
                )

        prompt_dir = self.temp_root / "mixed"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = prompt_dir / "diagnosis.md"
        markdown_path.write_text(
            "---\n"
            "name: diagnosis\n"
            "language: zh-CN\n"
            "version: 1.0.0\n"
            "title: Alarm Diagnosis\n"
            "description: Diagnose alarm events.\n"
            "---\n"
            "Prompt body\n",
            encoding="utf-8",
        )
        json_path = prompt_dir / "diagnosis.json"
        json_path.write_text('{"prompt":"json"}', encoding="utf-8")

        registry = PromptParserRegistry()
        registry.register("markdown", MarkdownPromptParser(), [".md"])
        registry.register("json", JsonPromptParser(), [".json"])

        module = importlib.import_module("a2a_t.prompt.catalog")
        catalog = module.LocalPromptCatalog(
            prompt_dir=str(prompt_dir),
            parser_registry=registry,
            allowed_extensions=[".md", ".json"],
        )

        references = catalog.list()

        self.assertEqual(len(references), 2)
        self.assertEqual({reference.name for reference in references}, {"diagnosis", "json-diagnosis"})
        self.assertEqual({Path(reference.source.locator).suffix for reference in references}, {".md", ".json"})

    def test_local_prompt_catalog_can_use_prompt_loader_config_directly(self) -> None:
        prompt_dir = self.temp_root / "configured-local"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = prompt_dir / "diagnosis.md"
        prompt_path.write_text(
            "---\n"
            "name: diagnosis\n"
            "language: zh-CN\n"
            "version: 1.0.0\n"
            "title: Alarm Diagnosis\n"
            "description: Diagnose alarm events.\n"
            "---\n"
            "Prompt body\n",
            encoding="utf-8",
        )

        module = importlib.import_module("a2a_t.prompt.catalog")
        catalog = module.LocalPromptCatalog(
            config=PromptLoaderConfig(
                default_ttl=timedelta(hours=1),
                local_prompt_dir=str(prompt_dir),
                allowed_extensions=[".md"],
            )
        )

        references = catalog.list()

        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].source.locator, str(prompt_path))
    def test_url_prompt_catalog_reads_prompt_index_entries(self) -> None:
        index_dir = self.temp_root / "url"
        index_dir.mkdir(parents=True, exist_ok=True)
        index_path = index_dir / "index.json"
        index_path.write_text(
            "{\n"
            '  "prompts": [\n'
            "    {\n"
            '      "name": "diagnosis",\n'
            '      "language": "default",\n'
            '      "version": "1.0.0",\n'
            '      "title": "Alarm Diagnosis",\n'
            '      "description": "Diagnose alarm events.",\n'
            '      "url": "/prompts/diagnosis.md"\n'
            "    }\n"
            "  ]\n"
            "}\n",
            encoding="utf-8",
        )

        handler = partial(SimpleHTTPRequestHandler, directory=str(index_dir))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(lambda: server_thread.join(timeout=1))
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        module = importlib.import_module("a2a_t.prompt.catalog")
        catalog = module.UrlPromptCatalog(index_url=f"http://127.0.0.1:{server.server_port}/index.json")

        references = catalog.list()

        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].name, "diagnosis")
        self.assertEqual(
            references[0].source.locator,
            f"http://127.0.0.1:{server.server_port}/prompts/diagnosis.md",
        )
        self.assertEqual(references[0].source.source_type, "url")

    def test_url_prompt_catalog_requires_index_url(self) -> None:
        module = importlib.import_module("a2a_t.prompt.catalog")
        catalog = module.UrlPromptCatalog(index_url=None)

        with self.assertRaises(PromptSourceError):
            catalog.list()

    def test_agent_prompt_catalog_reads_index_url_from_configured_extension_and_param_key(self) -> None:
        @dataclass(slots=True)
        class AgentExtension:
            uri: str
            params: dict[str, str]

        @dataclass(slots=True)
        class AgentCard:
            name: str
            extensions: list[AgentExtension]

        agent_card = AgentCard(
            name="alarm-agent",
            extensions=[
                AgentExtension(
                    uri="a2a-t.prompts",
                    params={"promptIndexUrl": "https://example.com/catalog/index.json"},
                )
            ],
        )

        class FakeFetcher:
            def __call__(self, index_url: str) -> dict[str, object]:
                self.index_url = index_url
                return {
                    "prompts": [
                        {
                            "name": "diagnosis",
                            "language": "default",
                            "version": "1.0.0",
                            "title": "Alarm Diagnosis",
                            "description": "Diagnose alarm events.",
                            "url": "/prompts/default/alarm.md",
                        }
                    ]
                }

        fetcher = FakeFetcher()
        module = importlib.import_module("a2a_t.prompt.catalog")
        catalog = module.AgentPromptCatalog(
            agent_cards=[agent_card],
            default_prompt_extension_uri="a2a-t.prompts",
            default_prompt_index_url_param_key="promptIndexUrl",
            fetcher=fetcher,
        )

        references = catalog.list()

        self.assertEqual(len(references), 1)
        self.assertEqual(fetcher.index_url, "https://example.com/catalog/index.json")
        self.assertEqual(
            references[0].source.locator,
            "https://example.com/prompts/default/alarm.md",
        )
        self.assertEqual(references[0].source.source_type, "url")

    def test_agent_prompt_catalog_uses_name_based_overrides(self) -> None:
        @dataclass(slots=True)
        class AgentExtension:
            uri: str
            params: dict[str, str]

        @dataclass(slots=True)
        class AgentCard:
            name: str
            extensions: list[AgentExtension]

        agent_card = AgentCard(
            name="alarm-agent",
            extensions=[
                AgentExtension(
                    uri="custom.prompts",
                    params={"catalogUrl": "https://agents.example.com/custom-index.json"},
                )
            ],
        )

        class FakeFetcher:
            def __call__(self, index_url: str) -> dict[str, object]:
                self.index_url = index_url
                return {
                    "prompts": [
                        {
                            "name": "diagnosis",
                            "language": "zh-CN",
                            "version": "2.0.0",
                            "title": "Alarm Diagnosis",
                            "description": "Diagnose alarm events.",
                            "url": "/prompts/custom/diagnosis.md",
                        }
                    ]
                }

        fetcher = FakeFetcher()
        module = importlib.import_module("a2a_t.prompt.catalog")
        catalog = module.AgentPromptCatalog(
            agent_cards=[agent_card],
            default_prompt_extension_uri="a2a-t.prompts",
            prompt_extension_uri_overrides={"alarm-agent": "custom.prompts"},
            default_prompt_index_url_param_key="promptIndexUrl",
            prompt_index_url_param_key_overrides={"alarm-agent": "catalogUrl"},
            fetcher=fetcher,
        )

        references = catalog.list()

        self.assertEqual(len(references), 1)
        self.assertEqual(fetcher.index_url, "https://agents.example.com/custom-index.json")
        self.assertEqual(references[0].name, "diagnosis")
        self.assertEqual(references[0].language, "zh-CN")
        self.assertEqual(references[0].version, "2.0.0")

    def test_agent_prompt_catalog_can_use_prompt_loader_config_directly(self) -> None:
        @dataclass(slots=True)
        class AgentExtension:
            uri: str
            params: dict[str, str]

        @dataclass(slots=True)
        class AgentCard:
            name: str
            extensions: list[AgentExtension]

        agent_card = AgentCard(
            name="alarm-agent",
            extensions=[
                AgentExtension(
                    uri="custom.prompts",
                    params={"catalogUrl": "https://agents.example.com/custom-index.json"},
                )
            ],
        )

        class FakeFetcher:
            def __call__(self, index_url: str) -> dict[str, object]:
                self.index_url = index_url
                return {
                    "prompts": [
                        {
                            "name": "diagnosis",
                            "language": "zh-CN",
                            "version": "2.0.0",
                            "title": "Alarm Diagnosis",
                            "description": "Diagnose alarm events.",
                            "url": "/prompts/custom/diagnosis.md",
                        }
                    ]
                }

        fetcher = FakeFetcher()
        module = importlib.import_module("a2a_t.prompt.catalog")
        catalog = module.AgentPromptCatalog(
            config=PromptLoaderConfig(
                default_ttl=timedelta(hours=1),
                prompt_extension_uri_overrides={"alarm-agent": "custom.prompts"},
                prompt_index_url_param_key_overrides={"alarm-agent": "catalogUrl"},
            ),
            agent_cards=[agent_card],
            fetcher=fetcher,
        )

        references = catalog.list()

        self.assertEqual(len(references), 1)
        self.assertEqual(fetcher.index_url, "https://agents.example.com/custom-index.json")
        self.assertEqual(references[0].name, "diagnosis")


if __name__ == "__main__":
    unittest.main()
