from __future__ import annotations

import sys
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class LLMModulePublicExportsTest(unittest.TestCase):
    def test_ability_modules_co_locate_interfaces_and_default_implementations(self) -> None:
        from a2a_t.llm.adapters.composed_adapter import ComposedLLMAdapter
        from a2a_t.llm.payload_builders import (
            OpenAICompatiblePayloadBuilder,
            PayloadBuilder,
        )
        from a2a_t.llm.response_parsers import (
            OpenAICompatibleResponseParser,
            ResponseParser,
        )
        from a2a_t.llm.transports import OpenAICompatibleTransportAdapter, TransportAdapter

        self.assertIsNotNone(ComposedLLMAdapter)
        self.assertIsNotNone(PayloadBuilder)
        self.assertIsNotNone(OpenAICompatiblePayloadBuilder)
        self.assertIsNotNone(ResponseParser)
        self.assertIsNotNone(OpenAICompatibleResponseParser)
        self.assertIsNotNone(OpenAICompatibleTransportAdapter)
        self.assertIsNotNone(TransportAdapter)

    def test_llm_module_exports_composed_adapter_primitives(self) -> None:
        from a2a_t.llm import (
            ComposedLLMAdapter,
            OpenAICompatiblePayloadBuilder,
            OpenAICompatibleResponseParser,
            OpenAICompatibleTransportAdapter,
            PayloadBuilder,
            ResponseParser,
            TransportAdapter,
        )

        self.assertIsNotNone(ComposedLLMAdapter)
        self.assertIsNotNone(OpenAICompatiblePayloadBuilder)
        self.assertIsNotNone(OpenAICompatibleResponseParser)
        self.assertIsNotNone(OpenAICompatibleTransportAdapter)
        self.assertIsNotNone(PayloadBuilder)
        self.assertIsNotNone(ResponseParser)
        self.assertIsNotNone(TransportAdapter)

    def test_adapters_module_exports_openai_compatible_components(self) -> None:
        from a2a_t.llm.adapters import (
            OpenAICompatibleAdapter,
            openai_compatible,
        )

        self.assertIsNotNone(OpenAICompatibleAdapter)
        self.assertIsNotNone(openai_compatible)

    def test_legacy_provider_specific_exports_are_removed(self) -> None:
        import a2a_t.llm.adapters as adapters

        self.assertFalse(hasattr(adapters, "DeepSeekAdapter"))
        self.assertFalse(hasattr(adapters, "DeepSeekPayloadBuilder"))
        self.assertFalse(hasattr(adapters, "DeepSeekResponseParser"))
        self.assertFalse(hasattr(adapters, "DeepSeekTransportAdapter"))
        self.assertFalse(hasattr(adapters, "deepseek"))
        self.assertFalse(hasattr(adapters, "deepseek_adapter"))

    def test_legacy_default_openai_compatible_exports_are_removed(self) -> None:
        import a2a_t.llm as llm
        import a2a_t.llm.payload_builders as payload_builders
        import a2a_t.llm.response_parsers as response_parsers

        self.assertFalse(hasattr(llm, "DefaultOpenAICompatiblePayloadBuilder"))
        self.assertFalse(hasattr(llm, "DefaultOpenAICompatibleResponseParser"))
        self.assertFalse(hasattr(payload_builders, "DefaultOpenAICompatiblePayloadBuilder"))
        self.assertFalse(hasattr(response_parsers, "DefaultOpenAICompatibleResponseParser"))


if __name__ == "__main__":
    unittest.main()
