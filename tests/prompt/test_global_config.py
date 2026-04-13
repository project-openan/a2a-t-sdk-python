from __future__ import annotations

import os
import sys
import types
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tests.test_support import ManagedTempDirTestCase


def _install_fake_dotenv() -> None:
    if "dotenv" in sys.modules:
        return

    dotenv_module = types.ModuleType("dotenv")

    def dotenv_values(path: str | Path) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            result[key] = value
        return result

    dotenv_module.dotenv_values = dotenv_values
    sys.modules["dotenv"] = dotenv_module


def _install_fake_a2a() -> None:
    if "a2a" in sys.modules:
        return

    a2a_module = types.ModuleType("a2a")
    a2a_types_module = types.ModuleType("a2a.types")

    class AgentCard:  # pragma: no cover
        pass

    a2a_types_module.AgentCard = AgentCard
    a2a_module.types = a2a_types_module
    sys.modules["a2a"] = a2a_module
    sys.modules["a2a.types"] = a2a_types_module


class A2ATConfigTest(ManagedTempDirTestCase):
    def _load_config_api(self) -> tuple[object, type[Exception]]:
        _install_fake_dotenv()
        _install_fake_a2a()
        from a2a_t.config import A2ATConfig, ConfigFileNotFoundError

        return A2ATConfig, ConfigFileNotFoundError

    def test_load_raises_when_dotenv_file_is_missing(self) -> None:
        missing_path = self.make_temp_dir("missing_config") / ".env"
        A2ATConfig, ConfigFileNotFoundError = self._load_config_api()

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
        A2ATConfig, _ = self._load_config_api()

        config = A2ATConfig.load(env_path)

        self.assertEqual(config.prompt.local_prompt_dir, "./runtime-prompts")
        self.assertEqual(config.prompt.allowed_extensions, [".md", ".json"])
        self.assertTrue(config.prompt_compliance.enabled)
        self.assertEqual(config.prompt_compliance.guardrail.provider, "google_model_armor")
        self.assertEqual(config.prompt_compliance.slot_schema.root_dir, "./runtime-slots")

    def test_load_reads_language_and_prompt_resource_version_from_dotenv_file(self) -> None:
        temp_root = self.make_temp_dir("global_prompt_generation_config")
        env_path = temp_root / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "A2AT_LANGUAGE=zh-CN",
                    "A2AT_PROMPT_RESOURCE_VERSION=0.0.2",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        A2ATConfig, _ = self._load_config_api()

        config = A2ATConfig.load(env_path)

        self.assertEqual(config.language, "zh-CN")
        self.assertEqual(config.prompt_resource_version, "0.0.2")

    def test_load_uses_defaults_for_language_and_prompt_resource_version(self) -> None:
        temp_root = self.make_temp_dir("global_prompt_generation_defaults")
        env_path = temp_root / ".env"
        env_path.write_text("", encoding="utf-8")
        A2ATConfig, _ = self._load_config_api()

        config = A2ATConfig.load(env_path)

        self.assertEqual(config.language, "en-US")
        self.assertEqual(config.prompt_resource_version, "0.0.1")

    def test_load_only_reads_dotenv_file_and_ignores_process_environment(self) -> None:
        temp_root = self.make_temp_dir("dotenv_only_config")
        env_path = temp_root / ".env"
        env_path.write_text("A2AT_PROMPT_LOCAL_DIR=./from-file\n", encoding="utf-8")
        A2ATConfig, _ = self._load_config_api()

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
