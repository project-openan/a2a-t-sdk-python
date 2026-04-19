from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.llm.base import LLMResponse
from a2a_t.common.prompt_resources.models import ScenarioDefinition


class FakeLLMClient:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[dict[str, object]] = []

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, object], **kwargs: object) -> LLMResponse:
        self.calls.append({"messages": messages, "json_schema": json_schema, "kwargs": kwargs})
        return LLMResponse(content=self._response_text, model="fake-model", usage={}, metadata={})


class ScenarioRecognizerTest(unittest.TestCase):
    def test_recognize_calls_structured_with_system_and_user_messages_and_fixed_schema(self) -> None:
        llm_client = FakeLLMClient('{"matched": true, "scenario_code": "energy_saving", "error_message": null}')

        from a2a_t.prompt.analysis.scenario_recognizer import ScenarioRecognizer

        recognizer = ScenarioRecognizer(llm_client=llm_client)
        result = recognizer.recognize(
            normalized_input="Please analyze site A energy usage.",
            scenarios=[
                ScenarioDefinition(
                    scenario_code="energy_saving",
                    scenario_name="Energy Saving",
                    description="Energy saving analysis tasks.",
                    example="Analyze site power usage and suggest optimization.",
                )
            ],
            language="en-US",
            system_prompt="Identify the best matching scenario.",
            user_prompt="Choose from the provided scenario list.",
        )

        self.assertTrue(result.matched)
        self.assertEqual(result.scenario_code, "energy_saving")
        self.assertIsNone(result.error_message)
        self.assertEqual(len(llm_client.calls), 1)
        self.assertEqual(len(llm_client.calls[0]["messages"]), 2)
        self.assertEqual(llm_client.calls[0]["messages"][0]["role"], "system")
        self.assertEqual(llm_client.calls[0]["messages"][1]["role"], "user")
        self.assertEqual(
            llm_client.calls[0]["json_schema"]["required"],
            ["matched", "scenario_code", "error_message"],
        )
        self.assertIn("Identify the best matching scenario.", llm_client.calls[0]["messages"][0]["content"])
        self.assertIn("Choose from the provided scenario list.", llm_client.calls[0]["messages"][1]["content"])
        self.assertIn("energy_saving", llm_client.calls[0]["messages"][1]["content"])
        self.assertIn("Please analyze site A energy usage.", llm_client.calls[0]["messages"][1]["content"])

    def test_recognize_rejects_semantically_invalid_payload(self) -> None:
        llm_client = FakeLLMClient('{"matched": true, "scenario_code": null, "error_message": null}')

        from a2a_t.prompt.analysis.errors import ScenarioRecognitionError
        from a2a_t.prompt.analysis.scenario_recognizer import ScenarioRecognizer

        recognizer = ScenarioRecognizer(llm_client=llm_client)

        with self.assertRaises(ScenarioRecognitionError):
            recognizer.recognize(
                normalized_input="Analyze site A energy usage.",
                scenarios=[
                    ScenarioDefinition(
                        scenario_code="energy_saving",
                        scenario_name="Energy Saving",
                        description="Energy saving analysis tasks.",
                        example="Analyze site power usage and suggest optimization.",
                    )
                ],
                language="en-US",
                system_prompt="Identify the best matching scenario.",
                user_prompt="Choose from the provided scenario list.",
            )

    def test_recognize_rejects_payload_when_unmatched_response_contains_scenario_code(self) -> None:
        llm_client = FakeLLMClient('{"matched": false, "scenario_code": "energy_saving", "error_message": "No match."}')

        from a2a_t.prompt.analysis.errors import ScenarioRecognitionError
        from a2a_t.prompt.analysis.scenario_recognizer import ScenarioRecognizer

        recognizer = ScenarioRecognizer(llm_client=llm_client)

        with self.assertRaises(ScenarioRecognitionError):
            recognizer.recognize(
                normalized_input="Analyze site A energy usage.",
                scenarios=[
                    ScenarioDefinition(
                        scenario_code="energy_saving",
                        scenario_name="Energy Saving",
                        description="Energy saving analysis tasks.",
                        example="Analyze site power usage and suggest optimization.",
                    )
                ],
                language="en-US",
                system_prompt="Identify the best matching scenario.",
                user_prompt="Choose from the provided scenario list.",
            )

    def test_recognize_rejects_non_object_json_payload(self) -> None:
        llm_client = FakeLLMClient('["energy_saving"]')

        from a2a_t.prompt.analysis.errors import ScenarioRecognitionError
        from a2a_t.prompt.analysis.scenario_recognizer import ScenarioRecognizer

        recognizer = ScenarioRecognizer(llm_client=llm_client)

        with self.assertRaises(ScenarioRecognitionError):
            recognizer.recognize(
                normalized_input="Analyze site A energy usage.",
                scenarios=[
                    ScenarioDefinition(
                        scenario_code="energy_saving",
                        scenario_name="Energy Saving",
                        description="Energy saving analysis tasks.",
                        example="Analyze site power usage and suggest optimization.",
                    )
                ],
                language="en-US",
                system_prompt="Identify the best matching scenario.",
                user_prompt="Choose from the provided scenario list.",
            )


if __name__ == "__main__":
    unittest.main()

