from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.models import LLMResponse
from common.mock_llm import (
    _load_mock_responses,
    _resolve_language,
    get_mock_payload,
    get_mock_response,
    install_mock_llm,
    install_mock_llm_if_needed,
    is_mock_enabled,
    is_mock_needed,
)


def _write_env(content: str) -> Path:
    fd, path = tempfile.mkstemp(suffix=".env", text=True)
    os.write(fd, content.encode("utf-8"))
    os.close(fd)
    return Path(path)


class IsMockNeededTest(unittest.TestCase):
    def test_real_key_returns_false(self) -> None:
        env_path = _write_env("A2AT_LLM_API_KEY=sk-real-key-123\nA2AT_LANGUAGE=zh-CN\n")
        try:
            self.assertFalse(is_mock_needed(env_path=env_path))
        finally:
            os.unlink(str(env_path))

    def test_empty_key_returns_true(self) -> None:
        env_path = _write_env("A2AT_LLM_API_KEY=\nA2AT_LANGUAGE=zh-CN\n")
        try:
            self.assertTrue(is_mock_needed(env_path=env_path))
        finally:
            os.unlink(str(env_path))

    def test_missing_key_line_returns_true(self) -> None:
        env_path = _write_env("A2AT_LANGUAGE=zh-CN\n")
        try:
            self.assertTrue(is_mock_needed(env_path=env_path))
        finally:
            os.unlink(str(env_path))

    def test_nonexistent_env_returns_true(self) -> None:
        self.assertTrue(is_mock_needed(env_path=Path("/nonexistent/.env")))

    def test_placeholder_key_returns_false(self) -> None:
        env_path = _write_env("A2AT_LLM_API_KEY=sk-your-api-key-here\nA2AT_LANGUAGE=zh-CN\n")
        try:
            self.assertFalse(is_mock_needed(env_path=env_path))
        finally:
            os.unlink(str(env_path))


class ResolveLanguageTest(unittest.TestCase):
    def test_reads_zh_cn_from_env(self) -> None:
        env_path = _write_env("A2AT_LANGUAGE=zh-CN\n")
        try:
            self.assertEqual(_resolve_language(env_path=env_path), "zh-CN")
        finally:
            os.unlink(str(env_path))

    def test_reads_en_us_from_env(self) -> None:
        env_path = _write_env("A2AT_LANGUAGE=en-US\n")
        try:
            self.assertEqual(_resolve_language(env_path=env_path), "en-US")
        finally:
            os.unlink(str(env_path))

    def test_defaults_to_zh_cn_when_missing(self) -> None:
        env_path = _write_env("A2AT_LLM_API_KEY=sk-x\n")
        try:
            self.assertEqual(_resolve_language(env_path=env_path), "zh-CN")
        finally:
            os.unlink(str(env_path))


class LoadMockResponsesTest(unittest.TestCase):
    def test_loads_zh_cn_responses(self) -> None:
        responses = _load_mock_responses(env_path=_write_env("A2AT_LANGUAGE=zh-CN\n"))
        self.assertEqual(len(responses), 3)
        scenario = json.loads(responses[0])
        self.assertTrue(scenario["matched"])
        self.assertEqual(scenario["scenario_code"], "subscribe-incident")
        slots = json.loads(responses[1])
        self.assertIn("通知主题", slots["slots"])
        self.assertIn("订阅条件", slots["slots"])
        self.assertIn("上报通知数据格式", slots["slots"])
        semantic = json.loads(responses[2])
        self.assertTrue(semantic["passed"])

    def test_loads_en_us_responses(self) -> None:
        responses = _load_mock_responses(env_path=_write_env("A2AT_LANGUAGE=en-US\n"))
        self.assertEqual(len(responses), 3)
        slots = json.loads(responses[1])
        self.assertIn("notification_topic", slots["slots"])
        self.assertIn("subscribe_condition", slots["slots"])
        self.assertIn("notification_data_format", slots["slots"])

    def test_zh_and_en_have_different_slot_names(self) -> None:
        zh_slots = json.loads(_load_mock_responses(env_path=_write_env("A2AT_LANGUAGE=zh-CN\n"))[1])["slots"]
        en_slots = json.loads(_load_mock_responses(env_path=_write_env("A2AT_LANGUAGE=en-US\n"))[1])["slots"]
        self.assertNotEqual(set(zh_slots.keys()), set(en_slots.keys()))


class InstallMockLlmTest(unittest.TestCase):
    def test_install_sets_mock_enabled_flag(self) -> None:
        import common.mock_llm
        original = is_mock_enabled()
        try:
            env_path = _write_env("A2AT_LLM_API_KEY=\nA2AT_LANGUAGE=zh-CN\n")
            install_mock_llm_if_needed(env_path=env_path)
            self.assertTrue(is_mock_enabled())
        finally:
            common.mock_llm._mock_enabled = original
            os.unlink(str(env_path))

    def test_does_not_install_when_key_present(self) -> None:
        original = is_mock_enabled()
        try:
            env_path = _write_env("A2AT_LLM_API_KEY=sk-real\nA2AT_LANGUAGE=zh-CN\n")
            installed = install_mock_llm_if_needed(env_path=env_path)
            self.assertFalse(installed)
            self.assertEqual(is_mock_enabled(), original)
        finally:
            import common.mock_llm
            common.mock_llm._mock_enabled = original
            os.unlink(str(env_path))

    def test_install_injects_placeholder_key(self) -> None:
        from a2a_t.config.source import DotEnvConfigSource
        original_source_load = DotEnvConfigSource.load
        import common.mock_llm
        original_mock_enabled = is_mock_enabled()
        try:
            env_path = _write_env("A2AT_LLM_API_KEY=\nA2AT_LANGUAGE=zh-CN\n")
            install_mock_llm(env_path=env_path)
            values = DotEnvConfigSource.load(env_path)
            self.assertEqual(values["A2AT_LLM_API_KEY"], "mock-key-not-real")
        finally:
            DotEnvConfigSource.load = original_source_load
            common.mock_llm._mock_enabled = original_mock_enabled
            os.unlink(str(env_path))

    def test_get_mock_response_returns_sequenced_responses(self) -> None:
        import common.mock_llm
        original_mock_enabled = is_mock_enabled()
        original_responses = common.mock_llm._MOCK_RESPONSES
        original_index = common.mock_llm._call_index
        try:
            env_path = _write_env("A2AT_LLM_API_KEY=\nA2AT_LANGUAGE=zh-CN\n")
            install_mock_llm(env_path=env_path)

            r1 = get_mock_response()
            r2 = get_mock_response()
            r3 = get_mock_response()

            self.assertIsInstance(r1, LLMResponse)
            r1_data = json.loads(r1.content)
            r2_data = json.loads(r2.content)
            r3_data = json.loads(r3.content)

            self.assertTrue(r1_data["matched"])
            self.assertIn("slots", r2_data)
            self.assertTrue(r3_data["passed"])
        finally:
            common.mock_llm._mock_enabled = original_mock_enabled
            common.mock_llm._MOCK_RESPONSES = original_responses
            common.mock_llm._call_index = original_index
            os.unlink(str(env_path))

    def test_get_mock_payload_includes_messages_and_schema(self) -> None:
        messages = [{"role": "user", "content": "test"}]
        schema = {"type": "object"}
        payload = get_mock_payload(messages=messages, json_schema=schema, temperature=0.5, max_tokens=100)
        self.assertEqual(payload["model"], "mock-llm")
        self.assertEqual(payload["messages"], messages)
        self.assertEqual(payload["json_schema"], schema)
        self.assertEqual(payload["temperature"], 0.5)
        self.assertEqual(payload["max_tokens"], 100)

    def test_get_mock_payload_omits_none_optionals(self) -> None:
        payload = get_mock_payload(messages=[], json_schema={}, temperature=None, max_tokens=None)
        self.assertNotIn("temperature", payload)
        self.assertNotIn("max_tokens", payload)


class LlmLoggerIntegrationTest(unittest.TestCase):
    """Verify llm_logger routes to mock when enabled, real when not."""

    def test_logger_calls_mock_when_enabled(self) -> None:
        import common.mock_llm
        from a2a_t.llm.providers.openai import OpenAIClient

        original_structured = OpenAIClient.structured
        original_build_payload = OpenAIClient._build_structured_payload
        original_mock_enabled = is_mock_enabled()
        original_responses = common.mock_llm._MOCK_RESPONSES
        original_index = common.mock_llm._call_index
        logs: list[str] = []
        try:
            env_path = _write_env("A2AT_LLM_API_KEY=\nA2AT_LANGUAGE=zh-CN\n")
            install_mock_llm(env_path=env_path)
            from common.llm_logger import install_llm_logger, set_llm_log_sink
            set_llm_log_sink(logs.append)
            install_llm_logger(role="test")

            client = OpenAIClient.__new__(OpenAIClient)
            result = client.structured(messages=[{"role": "user", "content": "test"}], json_schema={})

            self.assertIsInstance(result, LLMResponse)
            self.assertEqual(result.model, "mock-llm")
            request_logs = [entry for entry in logs if "llm-request" in entry]
            response_logs = [entry for entry in logs if "llm-response" in entry]
            mock_marker_logs = [entry for entry in logs if "llm-mock" in entry]
            self.assertEqual(len(request_logs), 1)
            self.assertEqual(len(response_logs), 1)
            # A standalone log line must flag that this response is from mock LLM
            self.assertEqual(len(mock_marker_logs), 1)
            self.assertIn("using canned mock LLM response", mock_marker_logs[0])
        finally:
            OpenAIClient.structured = original_structured
            OpenAIClient._build_structured_payload = original_build_payload
            common.mock_llm._mock_enabled = original_mock_enabled
            common.mock_llm._MOCK_RESPONSES = original_responses
            common.mock_llm._call_index = original_index
            os.unlink(str(env_path))


if __name__ == "__main__":
    unittest.main()
