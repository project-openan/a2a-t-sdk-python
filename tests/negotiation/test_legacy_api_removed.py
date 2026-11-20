# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

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
