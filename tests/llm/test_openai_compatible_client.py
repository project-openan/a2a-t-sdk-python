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

# ruff: noqa: E402

from __future__ import annotations

import logging
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError
from a2a_t.llm.factory import LLMClientFactory
from a2a_t.llm.models import LLMClientConfig
from a2a_t.llm.provider import LLMClient


def build_config(provider: str = "openai", base_url: str | None = None) -> LLMClientConfig:
    return LLMClientConfig(
        provider=provider,
        model="gpt-4o-mini",
        api_key="sk-test",
        base_url=base_url,
        history_window=10,
        max_tokens=None,
        temperature=None,
        timeout_seconds=None,
        session_max_total=300,
        session_max_per_provider=100,
    )


class OpenAIClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_clients = dict(LLMClientFactory._clients)
        self.original_client_defaults = dict(LLMClientFactory._client_defaults)

    def tearDown(self) -> None:
        LLMClientFactory._clients = self.original_clients
        LLMClientFactory._client_defaults = self.original_client_defaults

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_factory_creates_default_openai_client(self, openai_cls: Mock) -> None:
        openai_cls.return_value = Mock()

        from a2a_t.llm.providers.openai import OpenAIClient

        client = LLMClientFactory.create(
            "openai", build_config(provider="openai", base_url="https://api.openai.com/v1")
        )

        self.assertIsInstance(client, OpenAIClient)
        self.assertIsInstance(client, LLMClient)
        self.assertEqual(client.provider, "openai")
        openai_cls.assert_not_called()

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_factory_allows_openai_client_without_base_url_until_invocation(self, openai_cls: Mock) -> None:
        client = LLMClientFactory.create("openai", build_config(provider="openai"))

        with self.assertRaisesRegex(LLMConfigError, "base_url"):
            client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

        openai_cls.assert_not_called()

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_structured_uses_json_schema_and_passes_messages_through(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"device_type":"router"}'))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2),
        )
        openai_cls.return_value = sdk_client

        from a2a_t.llm.providers.openai import OpenAIClient

        client = OpenAIClient(build_config(base_url="https://custom.example/v1"))
        json_schema = {
            "type": "object",
            "properties": {"device_type": {"type": "string"}},
            "required": ["device_type"],
        }

        messages = [{"role": "user", "content": "extract router"}]
        response = client.structured(
            messages=messages,
            json_schema=json_schema,
            temperature=0.2,
            max_tokens=9,
        )

        self.assertEqual(response.content, '{"device_type":"router"}')
        self.assertEqual(response.model, "gpt-4o-mini")
        self.assertEqual(response.usage["prompt_tokens"], 7)
        self.assertEqual(response.usage["completion_tokens"], 2)
        openai_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://custom.example/v1",
            timeout=None,
        )
        payload = sdk_client.chat.completions.create.call_args.kwargs
        self.assertEqual(payload["model"], "gpt-4o-mini")
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["max_tokens"], 9)
        self.assertEqual(
            payload["response_format"],
            {
                "type": "json_schema",
                "json_schema": {"name": "structured_output", "schema": json_schema},
            },
        )
        self.assertIs(payload["messages"], messages)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "extract router"}])

    def test_structured_uses_configured_default_temperature(self) -> None:
        from dataclasses import replace

        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        config = replace(build_config(base_url="https://api.example.test/v1"), temperature=0.0)
        client = OpenAIClient(config)
        client._client = sdk_client

        client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

        self.assertEqual(sdk_client.chat.completions.create.call_args.kwargs["temperature"], 0.0)

    def test_structured_call_temperature_overrides_configured_default(self) -> None:
        from dataclasses import replace

        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        config = replace(build_config(base_url="https://api.example.test/v1"), temperature=0.0)
        client = OpenAIClient(config)
        client._client = sdk_client

        client.structured(
            messages=[{"role": "user", "content": "extract"}],
            json_schema={"type": "object"},
            temperature=0.7,
        )

        self.assertEqual(sdk_client.chat.completions.create.call_args.kwargs["temperature"], 0.7)

    def test_structured_omits_temperature_when_not_configured(self) -> None:
        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))
        client._client = sdk_client

        client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

        self.assertNotIn("temperature", sdk_client.chat.completions.create.call_args.kwargs)

    @patch("a2a_t.llm.providers.openai.httpx")
    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_ssl_verification_disabled_uses_trust_all_http_client(self, openai_cls: Mock, httpx_module: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        openai_cls.return_value = sdk_client

        from dataclasses import replace

        from a2a_t.llm.providers.openai import OpenAIClient

        config = replace(build_config(base_url="https://self-signed.example/v1"), ssl_verify=False)
        client = OpenAIClient(config)

        client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

        httpx_module.Client.assert_called_once_with(verify=False)
        self.assertIs(openai_cls.call_args.kwargs["http_client"], httpx_module.Client.return_value)

    @patch("a2a_t.llm.providers.openai.httpx")
    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_ssl_verification_enabled_by_default_keeps_default_http_client(
        self, openai_cls: Mock, httpx_module: Mock
    ) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        openai_cls.return_value = sdk_client

        from a2a_t.llm.providers.openai import OpenAIClient

        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))

        client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

        httpx_module.Client.assert_not_called()
        self.assertNotIn("http_client", openai_cls.call_args.kwargs)

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_structured_rejects_non_json_object_response(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content='["not-object"]'))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        openai_cls.return_value = sdk_client

        from a2a_t.llm.providers.openai import OpenAIClient

        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))

        with self.assertRaises(LLMRuntimeError):
            client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_total_tokens_falls_back_to_prompt_plus_completion_when_absent(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2),
        )
        openai_cls.return_value = sdk_client

        from a2a_t.llm.providers.openai import OpenAIClient

        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))

        response = client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

        self.assertEqual(response.usage["prompt_tokens"], 7)
        self.assertEqual(response.usage["completion_tokens"], 2)
        self.assertEqual(response.usage["total_tokens"], 9)

    def test_structured_retries_a_timeout_when_configured(self) -> None:
        import httpx
        from openai import APITimeoutError

        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.side_effect = [
            APITimeoutError(request=httpx.Request("POST", "http://example.test/v1")),
            SimpleNamespace(
                model="gpt-4o-mini",
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
                usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2),
            ),
        ]
        client = OpenAIClient(replace(build_config(base_url="https://api.example.test/v1"), timeout_retries=1))
        client._client = sdk_client

        response = client.structured(
            messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"}
        )

        self.assertEqual(response.usage["prompt_tokens"], 7)
        self.assertEqual(sdk_client.chat.completions.create.call_count, 2)

    def test_structured_does_not_retry_a_timeout_by_default(self) -> None:
        import httpx
        from openai import APITimeoutError

        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.side_effect = APITimeoutError(
            request=httpx.Request("POST", "http://example.test/v1")
        )
        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))
        client._client = sdk_client

        with self.assertRaisesRegex(LLMRuntimeError, "timed out after 1 attempt"):
            client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

        self.assertEqual(sdk_client.chat.completions.create.call_count, 1)

    def test_structured_reports_exhausted_timeout_attempts(self) -> None:
        import httpx
        from openai import APITimeoutError

        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.side_effect = APITimeoutError(
            request=httpx.Request("POST", "http://example.test/v1")
        )
        client = OpenAIClient(replace(build_config(base_url="https://api.example.test/v1"), timeout_retries=2))
        client._client = sdk_client

        with self.assertRaisesRegex(LLMRuntimeError, "timed out after 3 attempt"):
            client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

        self.assertEqual(sdk_client.chat.completions.create.call_count, 3)

    def test_structured_does_not_retry_a_non_timeout_failure(self) -> None:
        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.side_effect = ValueError("boom")
        client = OpenAIClient(replace(build_config(base_url="https://api.example.test/v1"), timeout_retries=2))
        client._client = sdk_client

        with self.assertRaisesRegex(LLMRuntimeError, "invocation failed"):
            client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

        self.assertEqual(sdk_client.chat.completions.create.call_count, 1)

    def test_structured_forwards_the_configured_extra_body(self) -> None:
        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2),
        )
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        client = OpenAIClient(replace(build_config(base_url="https://api.example.test/v1"), extra_body=extra_body))
        client._client = sdk_client

        client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

        payload = sdk_client.chat.completions.create.call_args.kwargs
        self.assertEqual(payload["extra_body"], extra_body)
        self.assertIsNot(payload["extra_body"], extra_body)

    def test_structured_can_fall_back_to_json_object_response_format(self) -> None:
        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2),
        )
        client = OpenAIClient(
            replace(build_config(base_url="https://api.example.test/v1"), response_format="json_object")
        )
        client._client = sdk_client

        client.structured(
            messages=[{"role": "user", "content": "validate"}],
            json_schema={"type": "object", "properties": {"semantic_verdict": {"type": "boolean"}}},
        )

        payload = sdk_client.chat.completions.create.call_args.kwargs
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_structured_falls_back_to_the_configured_max_tokens(self) -> None:
        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        client = OpenAIClient(replace(build_config(base_url="https://custom.example/v1"), max_tokens=64))
        client._client = sdk_client

        client.structured(
            messages=[{"role": "user", "content": "extract router"}],
            json_schema={"type": "object"},
            max_tokens=None,
        )

        payload = sdk_client.chat.completions.create.call_args.kwargs
        self.assertEqual(payload["max_tokens"], 64)

    def test_structured_omits_max_tokens_when_the_config_disables_the_cap(self) -> None:
        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        client = OpenAIClient(build_config(base_url="https://custom.example/v1"))
        client._client = sdk_client

        client.structured(
            messages=[{"role": "user", "content": "extract router"}],
            json_schema={"type": "object"},
            max_tokens=None,
        )

        payload = sdk_client.chat.completions.create.call_args.kwargs
        self.assertNotIn("max_tokens", payload)

    def test_usage_is_empty_when_the_provider_does_not_report_it(self) -> None:
        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=None,
        )
        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))
        client._client = sdk_client

        response = client.structured(
            messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"}
        )

        self.assertEqual(response.usage, {})

    def test_usage_reads_the_openai_cached_tokens_field(self) -> None:
        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(
                prompt_tokens=793,
                completion_tokens=2,
                total_tokens=795,
                prompt_tokens_details=SimpleNamespace(cached_tokens=768),
            ),
        )
        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))
        client._client = sdk_client

        response = client.structured(
            messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"}
        )

        self.assertEqual(response.usage["cached"], 768)

    def test_usage_reads_the_deepseek_native_cache_field(self) -> None:
        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="deepseek-chat",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=1,
                total_tokens=11,
                prompt_cache_hit_tokens=8,
            ),
        )
        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))
        client._client = sdk_client

        response = client.structured(
            messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"}
        )

        self.assertEqual(response.usage["cached"], 8)

    def test_usage_omits_cached_when_the_provider_does_not_report_it(self) -> None:
        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=2, total_tokens=13),
        )
        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))
        client._client = sdk_client

        response = client.structured(
            messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"}
        )

        self.assertNotIn("cached", response.usage)

    def test_usage_preserves_a_reported_cache_miss(self) -> None:
        from a2a_t.llm.providers.openai import OpenAIClient

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=SimpleNamespace(
                prompt_tokens=11,
                completion_tokens=2,
                total_tokens=13,
                prompt_tokens_details={"cached_tokens": 0},
            ),
        )
        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))
        client._client = sdk_client

        response = client.structured(
            messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"}
        )

        self.assertEqual(response.usage["cached"], 0)


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


class OpenAIClientDetailLoggingTest(unittest.TestCase):
    def _make_response(self) -> Any:
        return SimpleNamespace(
            id="chatcmpl-123",
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"device_type":"router"}'))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2, total_tokens=9),
        )

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_summary_logs_recorded_at_debug_without_payload_when_switch_off(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = self._make_response()
        openai_cls.return_value = sdk_client

        from a2a_t.llm.providers.openai import OpenAIClient

        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))

        with self.assertLogs("a2a_t.llm.call", level="DEBUG") as captured:
            client.structured(
                messages=[{"role": "user", "content": "extract router"}],
                json_schema={"type": "object"},
                temperature=0.25,
                max_tokens=9,
            )

        output = "\n".join(captured.output)
        self.assertIn("llm_call event=request ", output)
        self.assertIn("llm_call event=response ", output)
        self.assertNotIn("event=request_body", output)
        self.assertNotIn("event=response_body", output)
        self.assertIn("messages=1", output)
        self.assertIn("temperature=0.25", output)
        self.assertIn("max_tokens=9", output)
        self.assertIn("elapsed_ms=", output)
        self.assertIn("prompt_tokens=7", output)
        self.assertIn("completion_tokens=2", output)
        self.assertIn("total_tokens=9", output)
        self.assertIn("response_id=chatcmpl-123", output)
        self.assertNotIn("sk-test", output)

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_payload_lines_recorded_without_truncation_when_switch_on(self, openai_cls: Mock) -> None:
        from dataclasses import replace

        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = self._make_response()
        openai_cls.return_value = sdk_client

        from a2a_t.llm.providers.openai import OpenAIClient

        config = replace(build_config(base_url="https://api.example.test/v1"), detail_log_enabled=True)
        client = OpenAIClient(config)

        with self.assertLogs("a2a_t.llm.call", level="DEBUG") as captured:
            client.structured(
                messages=[{"role": "user", "content": "extract the device type here please"}],
                json_schema={"type": "object", "properties": {"device_type": {"type": "string"}}},
            )

        output = "\n".join(captured.output)
        request_body_line = next(line for line in captured.output if "event=request_body" in line)
        response_body_line = next(line for line in captured.output if "event=response_body" in line)
        self.assertIn("extract the device type here please", request_body_line)
        self.assertIn('{"device_type":"router"}', response_body_line)
        self.assertIn("event=request ", output)
        self.assertIn("event=response ", output)

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_error_line_recorded_on_provider_failure(self, openai_cls: Mock) -> None:
        from dataclasses import replace

        sdk_client = Mock()
        sdk_client.chat.completions.create.side_effect = Exception("provider unavailable")
        openai_cls.return_value = sdk_client

        from a2a_t.llm.providers.openai import OpenAIClient

        config = replace(build_config(base_url="https://api.example.test/v1"), detail_log_enabled=True)
        client = OpenAIClient(config)

        with self.assertLogs("a2a_t.llm.call", level="DEBUG") as captured:
            with self.assertRaises(LLMRuntimeError):
                client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

        output = "\n".join(captured.output)
        self.assertIn("llm_call event=error ", output)
        self.assertIn("elapsed_ms=", output)
        self.assertIn("error_code=Exception", output)
        self.assertIn("error=provider unavailable", output)
        self.assertNotIn("prompt_tokens", output)
        self.assertNotIn("event=response ", output)
        self.assertNotIn("event=response_body", output)
        error_line = next(line for line in captured.output if "llm_call event=error " in line)
        self.assertNotIn("messages_json", error_line)
        self.assertNotIn("content=", error_line)

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_error_line_recorded_on_content_contract_violation(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-4o-mini",
            choices=[SimpleNamespace(message=SimpleNamespace(content='["not-object"]'))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        openai_cls.return_value = sdk_client

        from a2a_t.llm.providers.openai import OpenAIClient

        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))

        with self.assertLogs("a2a_t.llm.call", level="DEBUG") as captured:
            with self.assertRaises(LLMRuntimeError):
                client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

        output = "\n".join(captured.output)
        self.assertIn("llm_call event=error ", output)
        self.assertIn("elapsed_ms=", output)
        self.assertIn("error_code=LLMRuntimeError", output)
        self.assertNotIn("event=response ", output)

    @patch("a2a_t.llm.providers.openai.OpenAI")
    def test_no_call_logs_when_dedicated_logger_not_enabled_for_debug(self, openai_cls: Mock) -> None:
        sdk_client = Mock()
        sdk_client.chat.completions.create.return_value = self._make_response()
        openai_cls.return_value = sdk_client

        from a2a_t.llm.providers.openai import OpenAIClient

        client = OpenAIClient(build_config(base_url="https://api.example.test/v1"))
        call_logger = logging.getLogger("a2a_t.llm.call")
        handler = _ListHandler()
        call_logger.addHandler(handler)
        call_logger.setLevel(logging.INFO)
        try:
            client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})
        finally:
            call_logger.removeHandler(handler)
            call_logger.setLevel(logging.NOTSET)

        self.assertEqual(handler.messages, [])
