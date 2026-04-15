from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.base import LLMResponse
from a2a_t.llm.errors import LLMConfigError
from tests.test_support import ManagedTempDirTestCase


class RecordingAdapter:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.chat_calls: list[dict[str, Any]] = []
        self.complete_calls: list[dict[str, Any]] = []
        self.structured_calls: list[dict[str, Any]] = []
        self.reset_calls: list[str] = []
        self.delete_calls: list[str] = []

    def chat(self, message: str, system_prompt: str | None = None, session_id: str | None = None, **kwargs: Any) -> LLMResponse:
        self.chat_calls.append(
            {
                "message": message,
                "system_prompt": system_prompt,
                "session_id": session_id,
                "kwargs": kwargs,
            }
        )
        return LLMResponse(content="chat-ok", model=str(self.config["model"]), usage={}, metadata={}, session_id="session-1")

    def complete(self, prompt: str, system_prompt: str | None = None, **kwargs: Any) -> LLMResponse:
        self.complete_calls.append({"prompt": prompt, "system_prompt": system_prompt, "kwargs": kwargs})
        return LLMResponse(content="complete-ok", model=str(self.config["model"]), usage={}, metadata={})

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        self.structured_calls.append({"messages": messages, "json_schema": json_schema, "kwargs": kwargs})
        return LLMResponse(content="{}", model=str(self.config["model"]), usage={}, metadata={})

    def reset_session(self, session_id: str) -> None:
        self.reset_calls.append(session_id)

    def delete_session(self, session_id: str) -> None:
        self.delete_calls.append(session_id)


class LLMClientTest(ManagedTempDirTestCase):
    def _write_env(self, body: str) -> Path:
        env_path = self.make_temp_dir("llm_client_env") / ".env"
        env_path.write_text(body, encoding="utf-8")
        return env_path

    def test_chat_loads_defaults_from_dotenv(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                    "A2AT_LLM_HISTORY_WINDOW=6",
                    "A2AT_LLM_TEMPERATURE=0.1",
                    "A2AT_LLM_MAX_TOKENS=128",
                    "A2AT_LLM_SESSION_MAX_TOTAL=40",
                    "A2AT_LLM_SESSION_MAX_PER_PROVIDER=20",
                ]
            )
            + "\n"
        )
        created: list[tuple[str, dict[str, Any], RecordingAdapter]] = []

        def factory_side_effect(adapter_type: str, config: dict[str, Any]) -> RecordingAdapter:
            adapter = RecordingAdapter(config)
            created.append((adapter_type, config, adapter))
            return adapter

        with patch("a2a_t.llm.client.LLMAdapterFactory.create", side_effect=factory_side_effect):
            from a2a_t.llm.client import LLMClient

            client = LLMClient(env_path=env_path)
            response = client.chat("hello", system_prompt="be concise")

        self.assertEqual(response.content, "chat-ok")
        self.assertEqual(created[0][0], "deepseek")
        self.assertEqual(created[0][1]["model"], "deepseek-chat")
        self.assertEqual(created[0][1]["api_key"], "sk-test")
        self.assertEqual(created[0][1]["history_window"], 6)
        self.assertEqual(created[0][2].chat_calls[0]["kwargs"]["temperature"], 0.1)
        self.assertEqual(created[0][2].chat_calls[0]["kwargs"]["max_tokens"], 128)
        self.assertEqual(created[0][2].chat_calls[0]["kwargs"]["history_window"], 6)
        self.assertEqual(client._defaults.session_max_total, 40)
        self.assertEqual(client._defaults.session_max_per_provider, 20)

    def test_complete_allows_method_level_overrides(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )
        created: list[tuple[str, dict[str, Any], RecordingAdapter]] = []

        def factory_side_effect(adapter_type: str, config: dict[str, Any]) -> RecordingAdapter:
            adapter = RecordingAdapter(config)
            created.append((adapter_type, config, adapter))
            return adapter

        with patch("a2a_t.llm.client.LLMAdapterFactory.create", side_effect=factory_side_effect):
            from a2a_t.llm.client import LLMClient

            client = LLMClient(env_path=env_path)
            response = client.complete(
                "say hi",
                provider="deepseek",
                model="deepseek-chat",
                temperature=0.2,
                max_tokens=64,
            )

        self.assertEqual(response.model, "deepseek-chat")
        self.assertEqual(created[0][0], "deepseek")
        self.assertEqual(created[0][1]["model"], "deepseek-chat")
        self.assertEqual(created[0][2].complete_calls[0]["kwargs"]["temperature"], 0.2)
        self.assertEqual(created[0][2].complete_calls[0]["kwargs"]["max_tokens"], 64)

    def test_structured_uses_shared_session_store_and_runtime_overrides(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                    "A2AT_LLM_API_KEY=sk-test",
                    "A2AT_LLM_HISTORY_WINDOW=3",
                ]
            )
            + "\n"
        )
        session_stores: list[object] = []

        def factory_side_effect(adapter_type: str, config: dict[str, Any]) -> RecordingAdapter:
            session_stores.append(config["session_store"])
            return RecordingAdapter(config)

        with patch("a2a_t.llm.client.LLMAdapterFactory.create", side_effect=factory_side_effect):
            from a2a_t.llm.client import LLMClient

            client = LLMClient(env_path=env_path)
            client.chat("first")
            client.structured(
                messages=[{"role": "user", "content": "extract"}],
                json_schema={"type": "object"},
                history_window=9,
            )

        self.assertEqual(session_stores[0].__class__.__name__, "ProviderScopedSessionStore")
        self.assertEqual(session_stores[1].__class__.__name__, "ProviderScopedSessionStore")
        self.assertIs(session_stores[0]._root_store, session_stores[1]._root_store)

    def test_chat_injects_provider_scoped_session_store(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                    "A2AT_LLM_API_KEY=sk-test",
                    "A2AT_LLM_SESSION_MAX_TOTAL=50",
                    "A2AT_LLM_SESSION_MAX_PER_PROVIDER=20",
                ]
            )
            + "\n"
        )
        session_stores: list[object] = []

        def factory_side_effect(adapter_type: str, config: dict[str, Any]) -> RecordingAdapter:
            session_stores.append(config["session_store"])
            return RecordingAdapter(config)

        with patch("a2a_t.llm.client.LLMAdapterFactory.create", side_effect=factory_side_effect):
            from a2a_t.llm.client import LLMClient

            client = LLMClient(env_path=env_path)
            client.chat("hello")

        self.assertEqual(session_stores[0].__class__.__name__, "ProviderScopedSessionStore")

    def test_session_store_defaults_apply_configured_limits(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                    "A2AT_LLM_API_KEY=sk-test",
                    "A2AT_LLM_SESSION_MAX_TOTAL=50",
                    "A2AT_LLM_SESSION_MAX_PER_PROVIDER=20",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        client = LLMClient(env_path=env_path)

        self.assertEqual(client._session_store._max_total, 50)
        self.assertEqual(client._session_store._max_per_provider, 20)

    def test_session_limit_config_is_loaded_from_dotenv(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                    "A2AT_LLM_API_KEY=sk-test",
                    "A2AT_LLM_SESSION_MAX_TOTAL=50",
                    "A2AT_LLM_SESSION_MAX_PER_PROVIDER=20",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        client = LLMClient(env_path=env_path)

        self.assertEqual(client._defaults.session_max_total, 50)
        self.assertEqual(client._defaults.session_max_per_provider, 20)

    def test_session_limit_config_defaults_to_recommended_values(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        client = LLMClient(env_path=env_path)

        self.assertEqual(client._defaults.session_max_total, 300)
        self.assertEqual(client._defaults.session_max_per_provider, 100)

    def test_history_window_override_rejects_above_maximum(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        client = LLMClient(env_path=env_path)

        with self.assertRaises(LLMConfigError):
            client.chat("hello", history_window=101)

    def test_session_limit_config_rejects_values_above_hard_maximums(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                    "A2AT_LLM_API_KEY=sk-test",
                    "A2AT_LLM_HISTORY_WINDOW=101",
                    "A2AT_LLM_SESSION_MAX_TOTAL=3001",
                    "A2AT_LLM_SESSION_MAX_PER_PROVIDER=1001",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        with self.assertRaises(LLMConfigError):
            LLMClient(env_path=env_path)

    def test_session_limit_config_rejects_total_smaller_than_per_provider(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                    "A2AT_LLM_API_KEY=sk-test",
                    "A2AT_LLM_SESSION_MAX_TOTAL=10",
                    "A2AT_LLM_SESSION_MAX_PER_PROVIDER=20",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        with self.assertRaises(LLMConfigError):
            LLMClient(env_path=env_path)

    def test_reset_and_delete_session_delegate_to_adapter(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )
        created_adapters: list[RecordingAdapter] = []

        def factory_side_effect(adapter_type: str, config: dict[str, Any]) -> RecordingAdapter:
            adapter = RecordingAdapter(config)
            created_adapters.append(adapter)
            return adapter

        with patch("a2a_t.llm.client.LLMAdapterFactory.create", side_effect=factory_side_effect):
            from a2a_t.llm.client import LLMClient

            client = LLMClient(env_path=env_path)
            client.reset_session("session-a")
            client.delete_session("session-a")

        self.assertEqual(created_adapters[0].reset_calls, ["session-a"])
        self.assertEqual(created_adapters[1].delete_calls, ["session-a"])

    def test_missing_provider_or_model_raises_config_error(self) -> None:
        env_path = self._write_env("A2AT_LLM_API_KEY=sk-test\n")

        from a2a_t.llm.client import LLMClient

        with self.assertRaises(LLMConfigError):
            LLMClient(env_path=env_path)

    def test_unknown_provider_raises_config_error(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=unknown",
                    "A2AT_LLM_MODEL=test-model",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        with self.assertRaises(LLMConfigError):
            LLMClient(env_path=env_path)

    def test_missing_env_file_raises_config_error(self) -> None:
        env_path = self.make_temp_dir("llm_client_missing_env") / "missing.env"

        from a2a_t.llm.client import LLMClient

        with self.assertRaises(LLMConfigError):
            LLMClient(env_path=env_path)

    def test_missing_api_key_raises_config_error_on_invocation(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        client = LLMClient(env_path=env_path)
        with self.assertRaises(LLMConfigError):
            client.complete("hello")

    def test_whitespace_only_api_key_is_treated_as_missing(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                    "A2AT_LLM_API_KEY=   ",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        client = LLMClient(env_path=env_path)
        with self.assertRaises(LLMConfigError):
            client.complete("hello")

    def test_runtime_api_key_override_whitespace_is_rejected(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=gpt-4.1",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        client = LLMClient(env_path=env_path)
        with self.assertRaises(LLMConfigError):
            client.complete("hello", api_key="   ")


if __name__ == "__main__":
    unittest.main()
