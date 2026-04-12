from __future__ import annotations

import os
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.config import A2ATConfig, ConfigFileNotFoundError
from tests.test_support import ManagedTempDirTestCase


class A2ATConfigTest(ManagedTempDirTestCase):
    def test_load_raises_when_dotenv_file_is_missing(self) -> None:
        missing_path = self.make_temp_dir("missing_config") / ".env"

        with self.assertRaises(ConfigFileNotFoundError):
            A2ATConfig.load(missing_path)

    def test_load_reads_prompt_and_prompt_compliance_config_from_dotenv_file(self) -> None:
        temp_root = self.make_temp_dir("global_config")
        env_path = temp_root / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "A2AT_PROMPT_DEFAULT_TTL_SECONDS=7200",
                    "A2AT_PROMPT_LOCAL_DIR=./runtime-prompts",
                    "A2AT_PROMPT_ALLOWED_EXTENSIONS=.md,.json",
                    "A2AT_PROMPT_COMPLIANCE_ENABLED=true",
                    "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_PROVIDER=google_model_armor",
                    "A2AT_PROMPT_COMPLIANCE_SLOT_LOCAL_DIR=./runtime-slots",
                ]
            ),
            encoding="utf-8",
        )

        config = A2ATConfig.load(env_path)

        self.assertEqual(config.prompt.local_prompt_dir, "./runtime-prompts")
        self.assertEqual(config.prompt.allowed_extensions, [".md", ".json"])
        self.assertTrue(config.prompt_compliance.enabled)
        self.assertEqual(config.prompt_compliance.guardrail.provider, "google_model_armor")
        self.assertEqual(config.prompt_compliance.slot_schema.root_dir, "./runtime-slots")

    def test_load_only_reads_dotenv_file_and_ignores_process_environment(self) -> None:
        temp_root = self.make_temp_dir("dotenv_only_config")
        env_path = temp_root / ".env"
        env_path.write_text("A2AT_PROMPT_LOCAL_DIR=./from-file\n", encoding="utf-8")

        original = os.environ.get("A2AT_PROMPT_LOCAL_DIR")
        os.environ["A2AT_PROMPT_LOCAL_DIR"] = "./from-process"
        try:
            config = A2ATConfig.load(env_path)
        finally:
            if original is None:
                os.environ.pop("A2AT_PROMPT_LOCAL_DIR", None)
            else:
                os.environ["A2AT_PROMPT_LOCAL_DIR"] = original

        self.assertEqual(config.prompt.local_prompt_dir, "./from-file")


if __name__ == "__main__":
    unittest.main()
