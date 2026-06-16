from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.base import (
    ComposedLLMAdapter,
    LLMAdapter,
    PayloadBuilder,
    ResponseParser,
    TransportAdapter,
)
from a2a_t.llm.errors import LLMConfigError
from a2a_t.llm.factory import LLMAdapterFactory
from a2a_t.llm.models import LLMResponse


class DirectAdapter(LLMAdapter):
    @property
    def adapter_type(self) -> str:
        return "direct"

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        return LLMResponse(content="{}", model="direct-model", usage={}, metadata={})

    def _generate_from_messages(self, messages, **kwargs: Any) -> LLMResponse:
        return LLMResponse(content="direct", model="direct-model", usage={}, metadata={})


class StubTransport(TransportAdapter):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def invoke(self, *, payload: dict[str, Any]) -> Any:
        return {"payload": payload, "provider": self.config["provider"]}


class StubPayloadBuilder(PayloadBuilder):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def build_complete(
        self,
        *,
        model: str,
        prompt: str,
        system_prompt: str | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        return {"kind": "complete", "provider": self.config["provider"], "prompt": prompt}

    def build_chat(self, *, model: str, messages, temperature: float | None, max_tokens: int | None) -> dict[str, Any]:
        return {"kind": "chat", "provider": self.config["provider"], "size": len(messages)}

    def build_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        return {"kind": "structured", "provider": self.config["provider"], "schema": json_schema}


class StubResponseParser(ResponseParser):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def parse_complete(self, *, response: Any, model: str) -> LLMResponse:
        return LLMResponse(content="complete", model=model, usage={}, metadata={"raw": response})

    def parse_chat(self, *, response: Any, model: str) -> LLMResponse:
        return LLMResponse(content="chat", model=model, usage={}, metadata={"raw": response})

    def parse_structured(self, *, response: Any, model: str) -> LLMResponse:
        return LLMResponse(content="{}", model=model, usage={}, metadata={"raw": response})


class LLMAdapterFactoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_adapters = dict(LLMAdapterFactory._adapters)
        self.original_adapter_imports = dict(LLMAdapterFactory._adapter_imports)
        composed = getattr(LLMAdapterFactory, "_composed_adapters", None)
        self.original_composed_adapters = dict(composed or {})

    def tearDown(self) -> None:
        LLMAdapterFactory._adapters = self.original_adapters
        LLMAdapterFactory._adapter_imports = self.original_adapter_imports
        if hasattr(LLMAdapterFactory, "_composed_adapters"):
            LLMAdapterFactory._composed_adapters = self.original_composed_adapters

    def test_register_keeps_direct_adapter_path_working(self) -> None:
        LLMAdapterFactory.register("direct-test", DirectAdapter)

        adapter = LLMAdapterFactory.create("direct-test", {"model": "direct-model", "provider": "direct-test"})

        self.assertIsInstance(adapter, DirectAdapter)

    def test_register_composed_creates_composed_llm_adapter(self) -> None:
        LLMAdapterFactory.register_composed(
            "composed-test",
            transport_factory=StubTransport,
            payload_builder_factory=StubPayloadBuilder,
            response_parser_factory=StubResponseParser,
        )

        adapter = LLMAdapterFactory.create("composed-test", {"model": "demo-model", "provider": "composed-test"})

        self.assertIsInstance(adapter, ComposedLLMAdapter)
        response = adapter.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})
        self.assertEqual(response.metadata["raw"]["provider"], "composed-test")

    def test_register_composed_rejects_incomplete_definition(self) -> None:
        with self.assertRaises(LLMConfigError):
            LLMAdapterFactory.register_composed(
                "broken-composed",
                transport_factory=StubTransport,
                payload_builder_factory=None,
                response_parser_factory=StubResponseParser,
            )

    def test_register_rejects_type_already_claimed_by_composed_definition(self) -> None:
        LLMAdapterFactory.register_composed(
            "shared-type",
            transport_factory=StubTransport,
            payload_builder_factory=StubPayloadBuilder,
            response_parser_factory=StubResponseParser,
        )

        with self.assertRaises(LLMConfigError):
            LLMAdapterFactory.register("shared-type", DirectAdapter)

    def test_register_composed_rejects_type_already_claimed_by_direct_adapter(self) -> None:
        LLMAdapterFactory.register("shared-type", DirectAdapter)

        with self.assertRaises(LLMConfigError):
            LLMAdapterFactory.register_composed(
                "shared-type",
                transport_factory=StubTransport,
                payload_builder_factory=StubPayloadBuilder,
                response_parser_factory=StubResponseParser,
            )


if __name__ == "__main__":
    unittest.main()
