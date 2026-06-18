from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class LLMLegacyApiRemovedTest(unittest.TestCase):
    def test_legacy_adapter_modules_are_removed(self) -> None:
        removed_modules = [
            "a2a_t.llm.client",
            "a2a_t.llm.base",
            "a2a_t.llm.adapters",
            "a2a_t.llm.adapters.composed_adapter",
            "a2a_t.llm.adapters.openai_compatible",
            "a2a_t.llm.payload_builders",
            "a2a_t.llm.response_parsers",
            "a2a_t.llm.session_store",
            "a2a_t.llm.transports",
        ]

        for module_name in removed_modules:
            with self.subTest(module_name=module_name):
                sys.modules.pop(module_name, None)
                with self.assertRaises(ModuleNotFoundError):
                    importlib.import_module(module_name)

    def test_factory_no_longer_exposes_adapter_factory(self) -> None:
        from a2a_t.llm import factory

        self.assertFalse(hasattr(factory, "LLM" + "AdapterFactory"))

    def test_chat_session_models_are_removed(self) -> None:
        from a2a_t.llm import models

        self.assertFalse(hasattr(models, "Chat" + "Message"))
        self.assertFalse(hasattr(models, "Chat" + "Session"))
