from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import timedelta
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.config.env import EnvConfig
from a2a_t.prompt.config import PromptLoaderConfig
from tests.test_support import ManagedTempDirTestCase


class PromptEnvConfigTest(ManagedTempDirTestCase):
    def test_project_contains_env_example_for_prompt_runtime(self) -> None:
        env_example_path = PROJECT_ROOT / "package_data" / "env.example"

        self.assertTrue(env_example_path.exists())
        content = env_example_path.read_text(encoding="utf-8")
        self.assertIn("A2AT_PROMPT_DEFAULT_TTL_SECONDS=", content)
        self.assertIn("A2AT_PROMPT_LOCAL_DIR=", content)
        self.assertIn("A2AT_PROMPT_ALLOWED_EXTENSIONS=", content)
        self.assertIn("A2AT_DEFAULT_PROMPT_EXTENSION_URI=", content)
        self.assertIn("A2AT_DEFAULT_PROMPT_EXTENSION_URI=default-prompt", content)
        self.assertIn("A2AT_PROMPT_EXTENSION_URI_OVERRIDES=", content)
        self.assertIn("A2AT_DEFAULT_PROMPT_INDEX_URL_PARAM_KEY=", content)
        self.assertIn("A2AT_PROMPT_INDEX_URL_PARAM_KEY_OVERRIDES=", content)
        self.assertIn('A2AT_PROMPT_EXTENSION_URI_OVERRIDES={"agent":"prompt://agent-extension"}', content)
        self.assertIn(
            'A2AT_PROMPT_INDEX_URL_PARAM_KEY_OVERRIDES={"agent":"agentPromptCatalogUrl"}',
            content,
        )
        self.assertNotIn("A2AT_PROMPT_CACHE_DIR=", content)

    def test_project_contains_dotenv_file_for_prompt_runtime(self) -> None:
        env_path = PROJECT_ROOT / "package_data" / ".env"

        self.assertTrue(env_path.exists())
        content = env_path.read_text(encoding="utf-8")
        self.assertIn("A2AT_PROMPT_DEFAULT_TTL_SECONDS=", content)
        self.assertIn("A2AT_PROMPT_LOCAL_DIR=", content)
        self.assertIn("A2AT_PROMPT_ALLOWED_EXTENSIONS=", content)
        self.assertIn("A2AT_DEFAULT_PROMPT_EXTENSION_URI=", content)
        self.assertIn("A2AT_DEFAULT_PROMPT_EXTENSION_URI=default-prompt", content)
        self.assertIn("A2AT_PROMPT_EXTENSION_URI_OVERRIDES=", content)
        self.assertIn("A2AT_DEFAULT_PROMPT_INDEX_URL_PARAM_KEY=", content)
        self.assertIn("A2AT_PROMPT_INDEX_URL_PARAM_KEY_OVERRIDES=", content)
        self.assertIn('A2AT_PROMPT_EXTENSION_URI_OVERRIDES={"agent":"prompt://agent-extension"}', content)
        self.assertIn(
            'A2AT_PROMPT_INDEX_URL_PARAM_KEY_OVERRIDES={"agent":"agentPromptCatalogUrl"}',
            content,
        )

    def test_project_contains_default_prompt_directory_placeholder(self) -> None:
        prompt_dir = PROJECT_ROOT / "package_data" / "prompts"
        placeholder = prompt_dir / ".gitkeep"

        self.assertTrue(prompt_dir.is_dir())
        self.assertTrue(placeholder.exists())

    def test_project_root_no_longer_contains_prompt_runtime_resources(self) -> None:
        self.assertFalse((PROJECT_ROOT / ".env").exists())
        self.assertFalse((PROJECT_ROOT / "env.example").exists())
        self.assertFalse((PROJECT_ROOT / "prompts").exists())

    def test_env_config_reads_values_from_env_file(self) -> None:
        temp_root = self.make_temp_dir("prompt_env")
        env_path = temp_root / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "A2AT_PROMPT_LOCAL_DIR=./prompts",
                    "A2AT_PROMPT_ALLOWED_EXTENSIONS=.md,.json,.yaml",
                ]
            ),
            encoding="utf-8",
        )

        env = EnvConfig.load(env_path=env_path)

        self.assertEqual(env.get("A2AT_PROMPT_LOCAL_DIR"), "./prompts")
        self.assertEqual(env.get("A2AT_PROMPT_ALLOWED_EXTENSIONS"), ".md,.json,.yaml")

    def test_env_config_prefers_process_environment_over_file_value(self) -> None:
        temp_root = self.make_temp_dir("prompt_env_override")
        env_path = temp_root / ".env"
        env_path.write_text("A2AT_PROMPT_LOCAL_DIR=./from-file\n", encoding="utf-8")

        original = os.environ.get("A2AT_PROMPT_LOCAL_DIR")
        os.environ["A2AT_PROMPT_LOCAL_DIR"] = "./from-env"
        try:
            env = EnvConfig.load(env_path=env_path)
        finally:
            if original is None:
                os.environ.pop("A2AT_PROMPT_LOCAL_DIR", None)
            else:
                os.environ["A2AT_PROMPT_LOCAL_DIR"] = original

        self.assertEqual(env.get("A2AT_PROMPT_LOCAL_DIR"), "./from-env")

    def test_env_config_supports_quoted_values_in_env_file(self) -> None:
        temp_root = self.make_temp_dir("prompt_env_quoted")
        env_path = temp_root / ".env"
        env_path.write_text('A2AT_PROMPT_LOCAL_DIR="./quoted-prompts"\n', encoding="utf-8")

        env = EnvConfig.load(env_path=env_path)

        self.assertEqual(env.get("A2AT_PROMPT_LOCAL_DIR"), "./quoted-prompts")

    def test_env_config_supports_export_prefix_in_env_file(self) -> None:
        temp_root = self.make_temp_dir("prompt_env_export")
        env_path = temp_root / ".env"
        env_path.write_text("export A2AT_PROMPT_LOCAL_DIR=./exported-prompts\n", encoding="utf-8")

        env = EnvConfig.load(env_path=env_path)

        self.assertEqual(env.get("A2AT_PROMPT_LOCAL_DIR"), "./exported-prompts")

    def test_prompt_loader_config_reads_local_dir_and_extensions_from_env(self) -> None:
        env = EnvConfig(
            values={
                "A2AT_PROMPT_LOCAL_DIR": "./runtime-prompts",
                "A2AT_PROMPT_ALLOWED_EXTENSIONS": ".md,.json,.yaml",
            }
        )

        config = PromptLoaderConfig.from_env(env)

        self.assertEqual(config.local_prompt_dir, "./runtime-prompts")
        self.assertEqual(config.allowed_extensions, [".md", ".json", ".yaml"])

    def test_prompt_loader_config_from_env_does_not_require_cache_dir(self) -> None:
        env = EnvConfig(values={"A2AT_PROMPT_LOCAL_DIR": "./runtime-prompts"})

        config = PromptLoaderConfig.from_env(env)

        self.assertEqual(config.local_prompt_dir, "./runtime-prompts")

    def test_prompt_loader_config_reads_all_fields_from_env(self) -> None:
        env = EnvConfig(
            values={
                "A2AT_PROMPT_DEFAULT_TTL_SECONDS": "7200",
                "A2AT_PROMPT_LOCAL_DIR": "./runtime-prompts",
                "A2AT_PROMPT_ALLOWED_EXTENSIONS": ".md,.json,.yaml",
                "A2AT_DEFAULT_PROMPT_EXTENSION_URI": "prompt://default-extension",
                "A2AT_PROMPT_EXTENSION_URI_OVERRIDES": '{"agent":"prompt://agent-extension"}',
                "A2AT_DEFAULT_PROMPT_INDEX_URL_PARAM_KEY": "promptCatalogUrl",
                "A2AT_PROMPT_INDEX_URL_PARAM_KEY_OVERRIDES": '{"agent":"agentPromptCatalogUrl"}',
            }
        )

        config = PromptLoaderConfig.from_env(env)

        self.assertEqual(config.default_ttl, timedelta(seconds=7200))
        self.assertEqual(config.local_prompt_dir, "./runtime-prompts")
        self.assertEqual(config.allowed_extensions, [".md", ".json", ".yaml"])
        self.assertEqual(config.default_prompt_extension_uri, "prompt://default-extension")
        self.assertEqual(config.prompt_extension_uri_overrides, {"agent": "prompt://agent-extension"})
        self.assertEqual(config.default_prompt_index_url_param_key, "promptCatalogUrl")
        self.assertEqual(
            config.prompt_index_url_param_key_overrides,
            {"agent": "agentPromptCatalogUrl"},
        )

    def test_prompt_loader_config_from_env_uses_defaults_for_optional_fields(self) -> None:
        env = EnvConfig(values={"A2AT_PROMPT_LOCAL_DIR": "./runtime-prompts"})

        config = PromptLoaderConfig.from_env(env)

        self.assertEqual(config.default_ttl, timedelta(hours=1))
        self.assertEqual(config.default_prompt_extension_uri, "default-prompt")
        self.assertEqual(config.prompt_extension_uri_overrides, {})
        self.assertEqual(config.default_prompt_index_url_param_key, "promptIndexUrl")
        self.assertEqual(config.prompt_index_url_param_key_overrides, {})

    def test_prompt_loader_config_rejects_invalid_json_overrides_in_env(self) -> None:
        env = EnvConfig(
            values={
                "A2AT_PROMPT_LOCAL_DIR": "./runtime-prompts",
                "A2AT_PROMPT_EXTENSION_URI_OVERRIDES": "{invalid-json}",
            }
        )

        with self.assertRaisesRegex(Exception, "A2AT_PROMPT_EXTENSION_URI_OVERRIDES"):
            PromptLoaderConfig.from_env(env)


if __name__ == "__main__":
    unittest.main()
