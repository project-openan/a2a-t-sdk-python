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

import sys
import unittest
from pathlib import Path

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
        from a2a_t.llm.providers.openai import OpenAIClient

        self.assertIs(llm.LLMClient, LLMClient)
        self.assertIs(llm.LLMClientFactory, LLMClientFactory)
        self.assertIs(llm.LLMConfigLoader, LLMConfigLoader)
        self.assertIs(llm.LLMClientConfig, LLMClientConfig)
        self.assertIs(llm.LLMResponse, LLMResponse)
        self.assertIs(llm.LLMError, LLMError)
        self.assertIs(llm.LLMConfigError, LLMConfigError)
        self.assertIs(llm.LLMRuntimeError, LLMRuntimeError)
        self.assertIs(llm.OpenAIClient, OpenAIClient)

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
