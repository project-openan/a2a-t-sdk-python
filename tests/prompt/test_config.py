from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.config.errors import ConfigFileNotFoundError
from a2a_t.config.source import DotEnvConfigSource
from tests.test_support import ManagedTempDirTestCase


class PromptConfigTest(ManagedTempDirTestCase):
    def test_dotenv_source_requires_python_dotenv_dependency(self) -> None:
        original_import = __import__

        def guarded_import(name: str, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
            if name == "dotenv":
                raise ModuleNotFoundError("No module named 'dotenv'")
            return original_import(name, globals, locals, fromlist, level)

        sys.modules.pop("a2a_t.config.source", None)
        try:
            with mock.patch("builtins.__import__", side_effect=guarded_import):
                with self.assertRaises(ModuleNotFoundError):
                    __import__("a2a_t.config.source")
        finally:
            sys.modules.pop("a2a_t.config.source", None)
            __import__("a2a_t.config.source")

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

if __name__ == "__main__":
    unittest.main()
