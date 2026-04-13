from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.analysis.models import SlotExtractionResult
from a2a_t.prompt.resources.errors import PromptResourceNotFoundError
from a2a_t.prompt.resources.models import PromptMessages, SlotDefinition, SlotSchema
from a2a_t.prompt.validation.models import GuardrailResult, SlotValidationError, SlotValidationResult
from a2a_t.server.prompt_compliance.models import PromptComplianceResult, PromptIdentity


PROCESSED_PROMPT = """---
scenario_code: energy_saving
language: en-US
version: 0.0.1
description: Used for energy saving analysis.
---
processed body"""


class FakeGuardrail:
    def __init__(self, result: GuardrailResult) -> None:
        self._result = result

    def check(self, prompt_text: str, context: dict[str, object] | None = None) -> GuardrailResult:
        return self._result


class FakeParser:
    def __init__(self, identity: PromptIdentity) -> None:
        self._identity = identity

    def parse(self, processed_prompt_text: str) -> PromptIdentity:
        return self._identity


class FakeTemplateLoader:
    def __init__(self, result: str | Exception) -> None:
        self._result = result

    def load(self, *, scenario_code: str, version: str, language: str) -> str:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeSlotSchemaLoader:
    def __init__(self, result: SlotSchema | Exception) -> None:
        self._result = result

    def load(self, *, scenario_code: str, version: str, language: str) -> SlotSchema:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakePromptResourceLoader:
    def __init__(self, result: PromptMessages | Exception) -> None:
        self._result = result

    def load(self, *, analysis_action: str, version: str, language: str) -> PromptMessages:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeExtractor:
    def __init__(self, result: SlotExtractionResult) -> None:
        self._result = result

    def extract(self, **kwargs: object) -> SlotExtractionResult:
        return self._result


class FakeValidator:
    def __init__(self, result: SlotValidationResult) -> None:
        self._result = result

    def validate(self, **kwargs: object) -> SlotValidationResult:
        return self._result


class PromptComplianceServiceRuntimeTest(unittest.TestCase):
    def _slot_schema(self) -> SlotSchema:
        return SlotSchema(
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
        )

    def _build_service(
        self,
        *,
        template_loader: FakeTemplateLoader | None = None,
        slot_schema_loader: FakeSlotSchemaLoader | None = None,
        prompt_resource_loader: FakePromptResourceLoader | None = None,
        extractor: FakeExtractor | None = None,
        validator: FakeValidator | None = None,
    ):
        from a2a_t.server.prompt_compliance.service import PromptComplianceService

        return PromptComplianceService(
            guardrail=FakeGuardrail(GuardrailResult(passed=True, error_code=None, error_message=None)),
            parser=FakeParser(PromptIdentity(scenario_code="energy_saving", language="en-US", version="0.0.1")),
            template_loader=template_loader or FakeTemplateLoader("Site: {site}"),
            slot_schema_loader=slot_schema_loader or FakeSlotSchemaLoader(self._slot_schema()),
            prompt_resource_loader=prompt_resource_loader or FakePromptResourceLoader(
                PromptMessages(system_prompt="Extract slots.", user_prompt="Return slots.")
            ),
            extractor=extractor or FakeExtractor(SlotExtractionResult(slots={"site": "Site A"}, slot_errors=[])),
            validator=validator or FakeValidator(SlotValidationResult(passed=True, slot_errors=[])),
        )

    def test_check_returns_success_result(self) -> None:
        service = self._build_service()

        result = service.check(processed_prompt_text=PROCESSED_PROMPT, request_metadata={"task_id": "task-1"})

        self.assertEqual(
            result,
            PromptComplianceResult(
                passed=True,
                stage="passed",
                extracted_slots={"site": "Site A"},
            ),
        )

    def test_check_returns_slot_validation_error_with_aggregated_message(self) -> None:
        service = self._build_service(
            validator=FakeValidator(
                SlotValidationResult(
                    passed=False,
                    slot_errors=[
                        SlotValidationError(
                            slot_name="site",
                            code="invalid_value",
                            message="Site format is invalid.",
                        )
                    ],
                )
            )
        )

        result = service.check(processed_prompt_text=PROCESSED_PROMPT, request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, "slot_validation")
        self.assertEqual(result.error_code, "slot_validation_error")
        self.assertEqual(result.error_message, "Site format is invalid.")
        self.assertEqual(result.extracted_slots, {"site": "Site A"})

    def test_check_returns_template_load_error_when_template_resource_is_missing(self) -> None:
        service = self._build_service(
            template_loader=FakeTemplateLoader(PromptResourceNotFoundError("missing template")),
        )

        result = service.check(processed_prompt_text=PROCESSED_PROMPT, request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, "generation")
        self.assertEqual(result.error_code, "template_load_error")


if __name__ == "__main__":
    unittest.main()
