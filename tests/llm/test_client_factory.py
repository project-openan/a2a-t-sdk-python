from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.errors import LLMConfigError
from a2a_t.llm.models import LLMClientConfig, LLMResponse


class CustomClient:
    def __init__(self, config: LLMClientConfig, logger: Any | None = None) -> None:
        self.config = config
        self.logger = logger

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return LLMResponse(
            content="{}",
            model=self.config.model,
            usage={},
            metadata={
                "message_count": len(messages),
                "schema": json_schema,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )


def build_config(provider: str = "custom") -> LLMClientConfig:
    return LLMClientConfig(
        provider=provider,
        model="custom-model",
        api_key="sk-test",
        base_url=None,
        history_window=10,
        max_tokens=None,
        temperature=None,
        timeout_seconds=None,
        session_max_total=300,
        session_max_per_provider=100,
    )


class LLMClientFactoryTest(unittest.TestCase):
    def setUp(self) -> None:
        from a2a_t.llm.factory import LLMClientFactory

        self.original_clients = dict(LLMClientFactory._clients)
        self.original_client_defaults = dict(LLMClientFactory._client_defaults)

    def tearDown(self) -> None:
        from a2a_t.llm.factory import LLMClientFactory

        LLMClientFactory._clients = self.original_clients
        LLMClientFactory._client_defaults = self.original_client_defaults

    def test_register_and_create_custom_llm_client(self) -> None:
        from a2a_t.llm.factory import LLMClientFactory

        logger = object()
        config = build_config()

        LLMClientFactory.register("custom", CustomClient)
        client = LLMClientFactory.create("custom", config, logger=logger)

        self.assertIsInstance(client, CustomClient)
        self.assertIs(client.config, config)
        self.assertIs(client.logger, logger)

    def test_created_client_supports_structured_contract(self) -> None:
        from a2a_t.llm.factory import LLMClientFactory
        from a2a_t.llm.provider import LLMClient

        LLMClientFactory.register("custom", CustomClient)
        client = LLMClientFactory.create("custom", build_config())

        self.assertIsInstance(client, LLMClient)
        response = client.structured(
            messages=[{"role": "user", "content": "extract"}],
            json_schema={"type": "object"},
            temperature=0.2,
            max_tokens=32,
        )

        self.assertEqual(response.model, "custom-model")
        self.assertEqual(response.metadata["message_count"], 1)
        self.assertEqual(response.metadata["temperature"], 0.2)
        self.assertEqual(response.metadata["max_tokens"], 32)

    def test_register_rejects_duplicate_provider(self) -> None:
        from a2a_t.llm.factory import LLMClientFactory

        LLMClientFactory.register("custom", CustomClient)

        with self.assertRaises(LLMConfigError):
            LLMClientFactory.register("custom", CustomClient)

    def test_register_rejects_invalid_provider_name(self) -> None:
        from a2a_t.llm.factory import LLMClientFactory

        for provider in ["", "  ", "custom provider", "Custom"]:
            with self.subTest(provider=provider):
                with self.assertRaises(LLMConfigError):
                    LLMClientFactory.register(provider, CustomClient)

    def test_create_rejects_unknown_provider(self) -> None:
        from a2a_t.llm.factory import LLMClientFactory

        with self.assertRaises(LLMConfigError):
            LLMClientFactory.create("missing", build_config("missing"))

    def test_available_providers_includes_builtins_and_custom_clients(self) -> None:
        from a2a_t.llm.factory import LLMClientFactory

        LLMClientFactory.register("custom", CustomClient)

        self.assertIn("custom", LLMClientFactory.available_providers())
        self.assertIn("deepseek", LLMClientFactory.available_providers())
