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

from a2a_t.llm.models import ChatMessage, ChatSession, LLMResponse
from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError
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


class FakeLogger:
    def __init__(self) -> None:
        self.debug_messages: list[str] = []
        self.info_messages: list[str] = []

    def debug(self, message: str, *args: object) -> None:
        self.debug_messages.append(message % args if args else message)

    def info(self, message: str, *args: object) -> None:
        self.info_messages.append(message % args if args else message)


class LLMClientTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        from a2a_t.llm import client as llm_client_module

        llm_client_module._reset_default_session_store_for_tests()

    def tearDown(self) -> None:
        from a2a_t.llm import client as llm_client_module

        llm_client_module._reset_default_session_store_for_tests()
        super().tearDown()

    def _write_env(self, body: str) -> Path:
        env_path = self.make_temp_dir("llm_client_env") / ".env"
        env_path.write_text(body, encoding="utf-8")
        return env_path

    def test_only_deepseek_provider_is_available(self) -> None:
        from a2a_t.llm.factory import LLMAdapterFactory

        self.assertEqual(LLMAdapterFactory.available_types(), ["deepseek"])

    def test_default_session_store_is_shared_across_clients(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                    "A2AT_LLM_SESSION_MAX_TOTAL=50",
                    "A2AT_LLM_SESSION_MAX_PER_PROVIDER=20",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        first = LLMClient(env_path=env_path)
        second = LLMClient(env_path=env_path)

        self.assertIs(first._session_store, second._session_store)

    def test_default_session_store_rejects_capacity_config_conflicts(self) -> None:
        env_dir = self.make_temp_dir("llm_client_conflict_envs")
        first_env = env_dir / "first.env"
        second_env = env_dir / "second.env"
        first_env.write_text(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                    "A2AT_LLM_SESSION_MAX_TOTAL=50",
                    "A2AT_LLM_SESSION_MAX_PER_PROVIDER=20",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        second_env.write_text(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                    "A2AT_LLM_SESSION_MAX_TOTAL=60",
                    "A2AT_LLM_SESSION_MAX_PER_PROVIDER=20",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        from a2a_t.llm.client import LLMClient

        LLMClient(env_path=first_env)

        with self.assertRaises(LLMConfigError):
            LLMClient(env_path=second_env)

    def test_init_rejects_public_session_store_injection(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient
        from a2a_t.llm.session_store import InMemorySessionStore

        with self.assertRaises(TypeError):
            LLMClient(env_path=env_path, session_store=InMemorySessionStore(max_total=1, max_per_provider=1))

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
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
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

    def test_structured_logs_request_and_response_when_logger_is_provided(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
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

            logger = FakeLogger()
            client = LLMClient(env_path=env_path, logger=logger)
            client.structured(
                messages=[{"role": "user", "content": "extract fields"}],
                json_schema={"type": "object"},
            )

        self.assertTrue(any("llm_request method=structured" in item for item in logger.debug_messages))
        self.assertTrue(any("extract fields" in item for item in logger.debug_messages))
        self.assertTrue(any("llm_response method=structured" in item for item in logger.debug_messages))

    def test_chat_accepts_explicit_runtime_overrides(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-default",
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
            client.chat(
                "hello",
                api_key="sk-override",
                base_url="https://example.test/v1",
                timeout_seconds=12,
            )

        self.assertEqual(created[0][1]["api_key"], "sk-override")
        self.assertEqual(created[0][1]["base_url"], "https://example.test/v1")
        self.assertEqual(created[0][1]["timeout_seconds"], 12)

    def test_structured_uses_shared_session_store_and_runtime_overrides(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
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
                max_tokens=9,
            )

        self.assertEqual(session_stores[0].__class__.__name__, "ProviderScopedSessionStore")
        self.assertEqual(session_stores[1].__class__.__name__, "ProviderScopedSessionStore")
        self.assertIs(session_stores[0]._root_store, session_stores[1]._root_store)

    def test_chat_injects_provider_scoped_session_store(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
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
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
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
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
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
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
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
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        client = LLMClient(env_path=env_path)

        with self.assertRaises(LLMConfigError):
            client.chat("hello", history_window=101)

    def test_complete_rejects_history_window_argument(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        client = LLMClient(env_path=env_path)

        with self.assertRaises(TypeError):
            client.complete("hello", history_window=2)

    def test_structured_rejects_history_window_argument(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        client = LLMClient(env_path=env_path)

        with self.assertRaises(TypeError):
            client.structured(
                messages=[{"role": "user", "content": "extract"}],
                json_schema={"type": "object"},
                history_window=2,
            )

    def test_chat_rejects_unknown_runtime_keyword(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        client = LLMClient(env_path=env_path)

        with self.assertRaises(TypeError):
            client.chat("hello", unsupported=True)

    def test_session_limit_config_rejects_values_above_hard_maximums(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
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
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
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

    def test_reset_session_uses_root_store_without_creating_adapter(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )
        with patch("a2a_t.llm.client.LLMAdapterFactory.create") as create_mock:
            from a2a_t.llm.client import LLMClient

            client = LLMClient(env_path=env_path)
            client._session_store.save(
                ChatSession(
                    session_id="deepseek-1",
                    provider="deepseek",
                    system_prompt="keep",
                    messages=[ChatMessage(role="user", content="hello")],
                )
            )

            client.reset_session("deepseek-1")

        create_mock.assert_not_called()
        reset_session = client._session_store.get("deepseek-1")
        self.assertEqual(reset_session.messages, [])
        self.assertIsNone(reset_session.system_prompt)

    def test_delete_session_is_idempotent_without_creating_adapter(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )

        with patch("a2a_t.llm.client.LLMAdapterFactory.create") as create_mock:
            from a2a_t.llm.client import LLMClient

            client = LLMClient(env_path=env_path)
            client.delete_session("deepseek-missing")

        create_mock.assert_not_called()

    def test_reset_session_rejects_unknown_session_id_without_provider_args(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.client import LLMClient

        client = LLMClient(env_path=env_path)

        with self.assertRaises(LLMRuntimeError):
            client.reset_session("deepseek-missing")

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
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
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
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
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
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
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
