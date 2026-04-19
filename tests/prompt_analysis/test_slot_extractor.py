from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.llm.base import LLMResponse
from a2a_t.prompt.common.models import PromptReference
from a2a_t.common.prompt_resources.models import SlotDefinition, SlotSchema
from a2a_t.prompt.validation.models import SlotValidationError


class FakeLLMClient:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[dict[str, object]] = []

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, object], **kwargs: object) -> LLMResponse:
        self.calls.append({"messages": messages, "json_schema": json_schema, "kwargs": kwargs})
        return LLMResponse(content=self._response_text, model="fake-model", usage={}, metadata={})


class SlotExtractorTest(unittest.TestCase):
    def test_extract_calls_structured_with_dynamic_schema_and_system_user_messages(self) -> None:
        llm_client = FakeLLMClient(
            (
                '{"slots": {"site": "Site A", "additional_notes": null}, '
                '"slot_errors": [{"slot_name": "additional_notes", "code": "missing_input", "message": "optional"}]}'
            )
        )

        from a2a_t.prompt.analysis.slot_extractor import SlotExtractor

        slot_schema = SlotSchema(
            scenario_code="energy_saving",
            version="0.0.1",
            slots=[
                SlotDefinition(
                    name="site",
                    required=True,
                    description="Site name",
                    example="Site A",
                    value_constraint="Must be a concrete site name.",
                    type="string",
                    allowed_values=None,
                    range=None,
                    pattern=None,
                ),
                SlotDefinition(
                    name="additional_notes",
                    required=False,
                    description="Additional notes",
                    example="Focus on power system",
                    value_constraint="Free-form notes.",
                    type="string",
                    allowed_values=None,
                    range=None,
                    pattern=None,
                ),
            ],
        )
        extractor = SlotExtractor(llm_client=llm_client)

        result = extractor.extract(
            normalized_input="Analyze Site A and focus on power system.",
            reference=PromptReference(scenario_code="energy_saving", version="0.0.1", language="en-US"),
            template_text="Site: {site}\nNotes: {additional_notes}",
            slot_schema=slot_schema,
            system_prompt="Extract slots.",
            user_prompt="Return slots and slot errors.",
        )

        self.assertEqual(
            result.slots,
            {
                "site": "Site A",
                "additional_notes": None,
            },
        )
        self.assertEqual(
            result.slot_errors,
            [
                SlotValidationError(
                    slot_name="additional_notes",
                    code="missing_input",
                    message="optional",
                )
            ],
        )
        self.assertEqual(len(llm_client.calls), 1)
        self.assertEqual(len(llm_client.calls[0]["messages"]), 2)
        self.assertEqual(llm_client.calls[0]["messages"][0]["role"], "system")
        self.assertEqual(llm_client.calls[0]["messages"][1]["role"], "user")
        self.assertIn("Extract slots.", llm_client.calls[0]["messages"][0]["content"])
        self.assertIn("Return slots and slot errors.", llm_client.calls[0]["messages"][1]["content"])
        self.assertIn("Analyze Site A and focus on power system.", llm_client.calls[0]["messages"][1]["content"])
        self.assertEqual(
            llm_client.calls[0]["json_schema"]["properties"]["slots"]["required"],
            ["site", "additional_notes"],
        )
        self.assertEqual(
            llm_client.calls[0]["json_schema"]["properties"]["slot_errors"]["items"]["properties"]["code"]["enum"],
            ["missing_input", "invalid_value"],
        )
        self.assertEqual(
            llm_client.calls[0]["json_schema"]["properties"]["slot_errors"]["items"]["properties"]["slot_name"]["enum"],
            ["site", "additional_notes"],
        )

    def test_extract_rejects_invalid_slot_error_code(self) -> None:
        llm_client = FakeLLMClient(
            (
                '{"slots": {"site": "Site A"}, '
                '"slot_errors": [{"slot_name": "site", "code": "bad_code", "message": "bad"}]}'
            )
        )

        from a2a_t.prompt.analysis.errors import SlotExtractionError
        from a2a_t.prompt.analysis.slot_extractor import SlotExtractor

        extractor = SlotExtractor(llm_client=llm_client)

        with self.assertRaises(SlotExtractionError):
            extractor.extract(
                normalized_input="Analyze Site A.",
                reference=PromptReference(scenario_code="energy_saving", version="0.0.1", language="en-US"),
                template_text="Site: {site}",
                slot_schema=SlotSchema(
                    scenario_code="energy_saving",
                    version="0.0.1",
                    slots=[
                        SlotDefinition(
                            name="site",
                            required=True,
                            description="Site name",
                            example="Site A",
                            value_constraint="Must be a concrete site name.",
                            type="string",
                            allowed_values=None,
                            range=None,
                            pattern=None,
                        )
                    ],
                ),
                system_prompt="Extract slots.",
                user_prompt="Return slots and slot errors.",
            )

    def test_extract_ignores_unknown_slot_key_in_slots_payload(self) -> None:
        llm_client = FakeLLMClient(
            (
                '{"slots": {"site": "Site A", "unexpected_slot": "bad"}, '
                '"slot_errors": []}'
            )
        )

        from a2a_t.prompt.analysis.slot_extractor import SlotExtractor

        extractor = SlotExtractor(llm_client=llm_client)

        result = extractor.extract(
            normalized_input="Analyze Site A.",
            reference=PromptReference(scenario_code="energy_saving", version="0.0.1", language="en-US"),
            template_text="Site: {site}",
            slot_schema=SlotSchema(
                scenario_code="energy_saving",
                version="0.0.1",
                slots=[
                    SlotDefinition(
                        name="site",
                        required=True,
                        description="Site name",
                        example="Site A",
                        value_constraint="Must be a concrete site name.",
                        type="string",
                        allowed_values=None,
                        range=None,
                        pattern=None,
                    )
                ],
            ),
            system_prompt="Extract slots.",
            user_prompt="Return slots and slot errors.",
        )

        self.assertEqual(
            result.slots,
            {
                "site": "Site A",
            },
        )
        self.assertEqual(result.slot_errors, [])


if __name__ == "__main__":
    unittest.main()

