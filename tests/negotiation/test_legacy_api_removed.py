from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

REMOVED_NEGOTIATION_MODULES = (
    "a2a_t.negotiation.common",
    "a2a_t.negotiation.types",
    "a2a_t.negotiation.store",
    "a2a_t.negotiation.runtime",
    "a2a_t.negotiation.handling",
    "a2a_t.negotiation.rendering",
    "a2a_t.client.negotiation",
    "a2a_t.server.negotiation",
)

REMOVED_FACADE_METHODS = (
    "start_negotiation",
    "receive_negotiation",
    "continue_negotiation",
)


class NegotiationLegacyApiRemovedTest(unittest.TestCase):
    def test_legacy_state_machine_modules_are_removed(self) -> None:
        for module_name in REMOVED_NEGOTIATION_MODULES:
            with self.subTest(module_name=module_name):
                sys.modules.pop(module_name, None)
                with self.assertRaises(ModuleNotFoundError):
                    importlib.import_module(module_name)

    def test_facades_no_longer_expose_the_legacy_methods(self) -> None:
        from a2a_t.client.a2at_client import A2ATClient
        from a2a_t.server.a2at_server import A2ATServer

        for facade in (A2ATClient, A2ATServer):
            for method in REMOVED_FACADE_METHODS:
                with self.subTest(facade=facade.__name__, method=method):
                    self.assertFalse(hasattr(facade, method))
