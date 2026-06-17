from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.errors import LLMConfigError
from tests.test_support import ManagedTempDirTestCase


class LLMConfigLoaderTest(ManagedTempDirTestCase):
    def _write_env(self, body: str) -> Path:
        env_path = self.make_temp_dir("llm_config_loader_env") / ".env"
        env_path.write_text(body, encoding="utf-8")
        return env_path

    def test_load_returns_llm_client_config_from_dotenv(self) -> None:
        env_path = self._write_env(
            "\n".join(
                [
                    "A2AT_LLM_PROVIDER=deepseek",
                    "A2AT_LLM_MODEL=deepseek-chat",
                    "A2AT_LLM_API_KEY=sk-test",
                    "A2AT_LLM_BASE_URL=https://example.test/v1",
                    "A2AT_LLM_HISTORY_WINDOW=6",
                    "A2AT_LLM_TEMPERATURE=0.1",
                    "A2AT_LLM_MAX_TOKENS=128",
                    "A2AT_LLM_TIMEOUT_SECONDS=30.5",
                    "A2AT_LLM_SESSION_MAX_TOTAL=40",
                    "A2AT_LLM_SESSION_MAX_PER_PROVIDER=20",
                ]
            )
            + "\n"
        )

        from a2a_t.llm.config_loader import LLMConfigLoader

        config = LLMConfigLoader.load(env_path)

        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.model, "deepseek-chat")
        self.assertEqual(config.api_key, "sk-test")
        self.assertEqual(config.base_url, "https://example.test/v1")
        self.assertEqual(config.history_window, 6)
        self.assertEqual(config.temperature, 0.1)
        self.assertEqual(config.max_tokens, 128)
        self.assertEqual(config.timeout_seconds, 30.5)
        self.assertEqual(config.session_max_total, 40)
        self.assertEqual(config.session_max_per_provider, 20)

    def test_load_uses_existing_defaults_for_optional_values(self) -> None:
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

        from a2a_t.llm.config_loader import LLMConfigLoader

        config = LLMConfigLoader.load(env_path)

        self.assertEqual(config.history_window, 10)
        self.assertEqual(config.session_max_total, 300)
        self.assertEqual(config.session_max_per_provider, 100)
        self.assertIsNone(config.base_url)
        self.assertIsNone(config.max_tokens)
        self.assertIsNone(config.temperature)
        self.assertIsNone(config.timeout_seconds)

    def test_load_rejects_missing_env_file(self) -> None:
        env_path = self.make_temp_dir("llm_config_loader_missing_env") / "missing.env"

        from a2a_t.llm.config_loader import LLMConfigLoader

        with self.assertRaises(LLMConfigError):
            LLMConfigLoader.load(env_path)

    def test_load_rejects_missing_provider_model_or_api_key(self) -> None:
        cases = [
            "A2AT_LLM_MODEL=deepseek-chat\nA2AT_LLM_API_KEY=sk-test\n",
            "A2AT_LLM_PROVIDER=deepseek\nA2AT_LLM_API_KEY=sk-test\n",
            "A2AT_LLM_PROVIDER=deepseek\nA2AT_LLM_MODEL=deepseek-chat\n",
            "A2AT_LLM_PROVIDER=deepseek\nA2AT_LLM_MODEL=deepseek-chat\nA2AT_LLM_API_KEY=   \n",
        ]

        from a2a_t.llm.config_loader import LLMConfigLoader

        for index, body in enumerate(cases):
            with self.subTest(index=index):
                env_path = self._write_env(body)
                with self.assertRaises(LLMConfigError):
                    LLMConfigLoader.load(env_path)

    def test_load_rejects_invalid_numeric_values(self) -> None:
        cases = [
            ("A2AT_LLM_HISTORY_WINDOW=abc", "A2AT_LLM_HISTORY_WINDOW"),
            ("A2AT_LLM_MAX_TOKENS=abc", "A2AT_LLM_MAX_TOKENS"),
            ("A2AT_LLM_TEMPERATURE=abc", "A2AT_LLM_TEMPERATURE"),
            ("A2AT_LLM_TIMEOUT_SECONDS=abc", "A2AT_LLM_TIMEOUT_SECONDS"),
            ("A2AT_LLM_SESSION_MAX_TOTAL=abc", "A2AT_LLM_SESSION_MAX_TOTAL"),
            ("A2AT_LLM_SESSION_MAX_PER_PROVIDER=abc", "A2AT_LLM_SESSION_MAX_PER_PROVIDER"),
        ]

        from a2a_t.llm.config_loader import LLMConfigLoader

        for line, key in cases:
            with self.subTest(key=key):
                env_path = self._write_env(
                    "\n".join(
                        [
                            "A2AT_LLM_PROVIDER=deepseek",
                            "A2AT_LLM_MODEL=deepseek-chat",
                            "A2AT_LLM_API_KEY=sk-test",
                            line,
                        ]
                    )
                    + "\n"
                )
                with self.assertRaisesRegex(LLMConfigError, key):
                    LLMConfigLoader.load(env_path)

    def test_load_preserves_session_limit_validation(self) -> None:
        cases = [
            ("A2AT_LLM_HISTORY_WINDOW=101", "A2AT_LLM_HISTORY_WINDOW"),
            ("A2AT_LLM_SESSION_MAX_TOTAL=3001", "A2AT_LLM_SESSION_MAX_TOTAL"),
            ("A2AT_LLM_SESSION_MAX_PER_PROVIDER=1001", "A2AT_LLM_SESSION_MAX_PER_PROVIDER"),
            ("A2AT_LLM_SESSION_MAX_TOTAL=10\nA2AT_LLM_SESSION_MAX_PER_PROVIDER=20", "SESSION_MAX_TOTAL"),
        ]

        from a2a_t.llm.config_loader import LLMConfigLoader

        for lines, expected_message in cases:
            with self.subTest(lines=lines):
                env_path = self._write_env(
                    "\n".join(
                        [
                            "A2AT_LLM_PROVIDER=deepseek",
                            "A2AT_LLM_MODEL=deepseek-chat",
                            "A2AT_LLM_API_KEY=sk-test",
                            lines,
                        ]
                    )
                    + "\n"
                )
                with self.assertRaisesRegex(LLMConfigError, expected_message):
                    LLMConfigLoader.load(env_path)
