from __future__ import annotations

import sys
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class LLMPublicExportsTest(unittest.TestCase):
    def test_exports_new_provider_extension_surface(self) -> None:
        import a2a_t.llm as llm
        from a2a_t.llm.config_loader import LLMConfigLoader
        from a2a_t.llm.errors import LLMConfigError, LLMError, LLMRuntimeError
        from a2a_t.llm.factory import LLMClientFactory
        from a2a_t.llm.models import LLMClientConfig, LLMResponse
        from a2a_t.llm.provider import LLMClient

        self.assertIs(llm.LLMClient, LLMClient)
        self.assertIs(llm.LLMClientFactory, LLMClientFactory)
        self.assertIs(llm.LLMConfigLoader, LLMConfigLoader)
        self.assertIs(llm.LLMClientConfig, LLMClientConfig)
        self.assertIs(llm.LLMResponse, LLMResponse)
        self.assertIs(llm.LLMError, LLMError)
        self.assertIs(llm.LLMConfigError, LLMConfigError)
        self.assertIs(llm.LLMRuntimeError, LLMRuntimeError)

    def test_does_not_export_legacy_adapter_or_composed_components(self) -> None:
        import a2a_t.llm as llm

        adapter = "Adapter"
        payload = "Payload"
        response = "Response"
        transport = "Transport"
        openai_compatible = "OpenAICompatible"
        legacy_names = {
            "LLM" + adapter,
            "LLM" + adapter + "Factory",
            "ComposedLLM" + adapter,
            payload + "Builder",
            response + "Parser",
            transport + adapter,
            openai_compatible + adapter,
            openai_compatible + payload + "Builder",
            openai_compatible + response + "Parser",
            openai_compatible + transport + adapter,
        }

        self.assertTrue(legacy_names.isdisjoint(set(llm.__all__)))
        for name in legacy_names:
            with self.subTest(name=name):
                self.assertFalse(hasattr(llm, name))
