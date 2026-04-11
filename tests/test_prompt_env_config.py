from __future__ import annotations

import os
import sys
from pathlib import Path
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
        env_example_path = PROJECT_ROOT / "env.example"

        self.assertTrue(env_example_path.exists())
        content = env_example_path.read_text(encoding="utf-8")
        self.assertIn("A2AT_PROMPT_LOCAL_DIR=", content)
        self.assertIn("A2AT_PROMPT_ALLOWED_EXTENSIONS=", content)
        self.assertNotIn("A2AT_PROMPT_CACHE_DIR=", content)

    def test_project_contains_dotenv_file_for_prompt_runtime(self) -> None:
        env_path = PROJECT_ROOT / ".env"

        self.assertTrue(env_path.exists())
        content = env_path.read_text(encoding="utf-8")
        self.assertIn("A2AT_PROMPT_LOCAL_DIR=", content)
        self.assertIn("A2AT_PROMPT_ALLOWED_EXTENSIONS=", content)

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


if __name__ == "__main__":
    unittest.main()
