from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.analysis.models import SlotExtractionResult
from a2a_t.prompt.resources.models import SlotDefinition, SlotSchema
from a2a_t.prompt.validation.models import SlotValidationError


class FakeSharedSlotExtractor:
    def __init__(self, result: SlotExtractionResult) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    def extract(self, **kwargs: object) -> SlotExtractionResult:
        self.calls.append(kwargs)
        return self._result


class PromptSlotExtractorRuntimeTest(unittest.TestCase):
    def test_extract_delegates_to_shared_slot_extractor(self) -> None:
        from a2a_t.server.prompt_compliance.extractor import PromptSlotExtractor

        shared_extractor = FakeSharedSlotExtractor(
            SlotExtractionResult(
                slots={"site": "Site A"},
                slot_errors=[
                    SlotValidationError(
                        slot_name="site",
                        code="invalid_value",
                        message="invalid",
                    )
                ],
            )
        )
        extractor = PromptSlotExtractor(slot_extractor=shared_extractor)

        result = extractor.extract(
            normalized_input="processed prompt text",
            scenario_code="energy_saving",
            version="0.0.1",
            language="en-US",
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
            user_prompt="Return slots.",
        )

        self.assertEqual(result.slots, {"site": "Site A"})
        self.assertEqual(shared_extractor.calls[0]["scenario_code"], "energy_saving")


if __name__ == "__main__":
    unittest.main()
