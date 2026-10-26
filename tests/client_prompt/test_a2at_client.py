from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.client.prompt_generation.models import PromptGenerationResult
from a2a_t.llm.models import LLMClientConfig
from tests.support import TEST_ENV_PATH, ManagedTempDirTestCase


def build_llm_config() -> LLMClientConfig:
    return LLMClientConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-test",
        base_url=None,
        history_window=10,
        max_tokens=None,
        temperature=None,
        timeout_seconds=None,
        session_max_total=300,
        session_max_per_provider=100,
    )


class FakePromptGenerationOrchestrator:
    def __init__(self, result: object) -> None:
        self._result = result
        self.calls: list[object] = []

    def generate(self, user_input: object) -> object:
        self.calls.append(user_input)
        return self._result


class FakePromptGenerationBuilder:
    def __init__(self, orchestrator: FakePromptGenerationOrchestrator) -> None:
        self._orchestrator = orchestrator
        self.calls: list[dict[str, object]] = []

    def build(self, **kwargs: object) -> FakePromptGenerationOrchestrator:
        self.calls.append(dict(kwargs))
        return self._orchestrator


class A2ATClientTest(unittest.TestCase):
    def test_constructor_signature_remains_stable(self) -> None:
        from a2a_t.client.a2at_client import A2ATClient

        signature = inspect.signature(A2ATClient.__init__)

        self.assertEqual(list(signature.parameters), ["self", "env_path", "logger"])
        self.assertEqual(signature.parameters["env_path"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(signature.parameters["logger"].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_a2at_client_delegates_prompt_generation_with_configured_llm(self) -> None:
        from a2a_t.client.a2at_client import A2ATClient

        prompt_result = PromptGenerationResult(
            success=True,
            prompt_text="prompt",
            failure=None,
        )
        prompt_orchestrator = FakePromptGenerationOrchestrator(prompt_result)
        prompt_builder = FakePromptGenerationBuilder(prompt_orchestrator)
        llm_config = build_llm_config()
        llm_client = object()

        with (
            patch("a2a_t.client.a2at_client._default_env_path", return_value=TEST_ENV_PATH),
            patch("a2a_t.client.a2at_client.LLMConfigLoader.load", return_value=llm_config) as load_llm_config,
            patch("a2a_t.client.a2at_client.LLMClientFactory.create", return_value=llm_client) as create_llm_client,
            patch("a2a_t.client.a2at_client.PromptGenerationOrchestratorBuilder", return_value=prompt_builder),
        ):
            client = A2ATClient()

            result = client.generate_task_prompt("Analyze Site A.")

            self.assertIs(result, prompt_result)

        load_llm_config.assert_called_once_with(TEST_ENV_PATH)
        create_llm_client.assert_called_once_with(llm_config.provider, llm_config, logger=None)
        # The builder receives the observability-wrapped client (spec 2.2 A2ATLLMClientDecorator)
        # whose inner is the factory product.
        built_llm_client = prompt_builder.calls[0]["llm_client"]
        self.assertIs(built_llm_client._inner, llm_client)
        self.assertEqual(prompt_orchestrator.calls, ["Analyze Site A."])


class A2ATClientPromptResourceTimingTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.make_temp_dir("a2at_client_prompt_timing")

    def _write_resource_file(self, relative_path: str, content: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_env(self) -> Path:
        env_path = self.root / "client.env"
        env_path.write_text(
            "\n".join(
                [
                    "A2AT_LANGUAGE=en-US",
                    "A2AT_PROMPT_SOURCE_TYPE=local_file",
                    f"A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR={self.root}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return env_path

    def test_generate_task_prompt_still_fails_at_call_time_when_packaged_prompts_are_missing(self) -> None:
        from a2a_t.client.a2at_client import A2ATClient
        from a2a_t.common.prompt_resources.packaged_access import PackagedResourceReader
        from a2a_t.core.errors.exceptions import A2ATError

        self._write_resource_file(
            "scenarios/en-US/scenarios.json",
            '{"scenarios":[{"scenario_code":"ran-energy-saving","scenario_name":"Energy Saving","description":"Used for energy saving analysis.","example":"Analyze site power usage and suggest optimization."}]}',
        )
        env_path = self._write_env()

        original_read_text = PackagedResourceReader.read_text

        def missing_prompts_read_text(self: object, key: object) -> str:
            if key.relative_path().startswith("prompt_resources/prompts/"):
                raise A2ATError(f"Failed to read resource '{key.relative_path()}'.")
            return original_read_text(self, key)

        with (
            patch("a2a_t.client.a2at_client.LLMConfigLoader.load", return_value=build_llm_config()),
            patch("a2a_t.client.a2at_client.LLMClientFactory.create", return_value=object()),
            patch.object(PackagedResourceReader, "read_text", missing_prompts_read_text),
        ):
            client = A2ATClient(env_path=env_path)

            result = client.generate_task_prompt("Analyze Site A.")

        self.assertFalse(result.success)
        self.assertIsNotNone(result.failure)
        self.assertEqual(result.failure.code, "template.load_failed")
        self.assertEqual(result.failure.stage, "preparation")


if __name__ == "__main__":
    unittest.main()
