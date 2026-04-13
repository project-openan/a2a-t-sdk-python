from __future__ import annotations

import sys
from pathlib import Path
from datetime import timedelta
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.config.errors import ConfigFileNotFoundError
from a2a_t.config.source import DotEnvConfigSource
from a2a_t.prompt.common.config import PromptLoaderConfig
from tests.test_support import ManagedTempDirTestCase


class PromptConfigTest(ManagedTempDirTestCase):
    def test_dotenv_source_reads_values_from_env_file(self) -> None:
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

        values = DotEnvConfigSource.load(env_path)

        self.assertEqual(values["A2AT_PROMPT_LOCAL_DIR"], "./prompts")
        self.assertEqual(values["A2AT_PROMPT_ALLOWED_EXTENSIONS"], ".md,.json,.yaml")

    def test_dotenv_source_raises_when_file_is_missing(self) -> None:
        missing_path = self.make_temp_dir("missing_prompt_env") / ".env"

        with self.assertRaises(ConfigFileNotFoundError):
            DotEnvConfigSource.load(missing_path)

    def test_dotenv_source_supports_quoted_values_in_env_file(self) -> None:
        temp_root = self.make_temp_dir("prompt_env_quoted")
        env_path = temp_root / ".env"
        env_path.write_text('A2AT_PROMPT_LOCAL_DIR="./quoted-prompts"\n', encoding="utf-8")

        values = DotEnvConfigSource.load(env_path)

        self.assertEqual(values["A2AT_PROMPT_LOCAL_DIR"], "./quoted-prompts")

    def test_dotenv_source_supports_export_prefix_in_env_file(self) -> None:
        temp_root = self.make_temp_dir("prompt_env_export")
        env_path = temp_root / ".env"
        env_path.write_text("export A2AT_PROMPT_LOCAL_DIR=./exported-prompts\n", encoding="utf-8")

        values = DotEnvConfigSource.load(env_path)

        self.assertEqual(values["A2AT_PROMPT_LOCAL_DIR"], "./exported-prompts")

    def test_prompt_loader_config_reads_local_dir_and_extensions_from_mapping(self) -> None:
        values = {
            "A2AT_PROMPT_LOCAL_DIR": "./runtime-prompts",
            "A2AT_PROMPT_ALLOWED_EXTENSIONS": ".md,.json,.yaml",
        }

        config = PromptLoaderConfig.from_mapping(values)

        self.assertEqual(config.local_prompt_dir, "./runtime-prompts")
        self.assertEqual(config.allowed_extensions, [".md", ".json", ".yaml"])

    def test_prompt_loader_config_from_mapping_does_not_require_cache_dir(self) -> None:
        values = {"A2AT_PROMPT_LOCAL_DIR": "./runtime-prompts"}

        config = PromptLoaderConfig.from_mapping(values)

        self.assertEqual(config.local_prompt_dir, "./runtime-prompts")

    def test_prompt_loader_config_reads_all_fields_from_mapping(self) -> None:
        values = {
            "A2AT_PROMPT_DEFAULT_TTL_SECONDS": "7200",
            "A2AT_PROMPT_LOCAL_DIR": "./runtime-prompts",
            "A2AT_PROMPT_ALLOWED_EXTENSIONS": ".md,.json,.yaml",
            "A2AT_DEFAULT_PROMPT_EXTENSION_URI": "prompt://default-extension",
            "A2AT_PROMPT_EXTENSION_URI_OVERRIDES": '{"agent":"prompt://agent-extension"}',
            "A2AT_DEFAULT_PROMPT_INDEX_URL_PARAM_KEY": "promptCatalogUrl",
            "A2AT_PROMPT_INDEX_URL_PARAM_KEY_OVERRIDES": '{"agent":"agentPromptCatalogUrl"}',
        }

        config = PromptLoaderConfig.from_mapping(values)

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

    def test_prompt_loader_config_from_mapping_uses_defaults_for_optional_fields(self) -> None:
        values = {"A2AT_PROMPT_LOCAL_DIR": "./runtime-prompts"}

        config = PromptLoaderConfig.from_mapping(values)

        self.assertEqual(config.default_ttl, timedelta(hours=1))
        self.assertEqual(config.default_prompt_extension_uri, "default-prompt")
        self.assertEqual(config.prompt_extension_uri_overrides, {})
        self.assertEqual(config.default_prompt_index_url_param_key, "promptIndexUrl")
        self.assertEqual(config.prompt_index_url_param_key_overrides, {})

    def test_prompt_loader_config_rejects_invalid_json_overrides_in_mapping(self) -> None:
        values = {
            "A2AT_PROMPT_LOCAL_DIR": "./runtime-prompts",
            "A2AT_PROMPT_EXTENSION_URI_OVERRIDES": "{invalid-json}",
        }

        with self.assertRaisesRegex(Exception, "A2AT_PROMPT_EXTENSION_URI_OVERRIDES"):
            PromptLoaderConfig.from_mapping(values)


if __name__ == "__main__":
    unittest.main()
