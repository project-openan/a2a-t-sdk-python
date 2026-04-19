from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.analysis.models import SlotExtractionResult
from a2a_t.prompt.common.task_prompt_format import TaskPromptMetadata, format_task_prompt
from a2a_t.prompt.common.errors import PromptSourceError
from a2a_t.prompt.common.models import PromptReference
from a2a_t.common.prompt_resources.errors import PromptResourceNotFoundError, PromptResourceParseError
from a2a_t.common.prompt_resources.models import PromptMessages, SlotDefinition, SlotSchema
from a2a_t.prompt.validation.constants import INVALID_VALUE, MISSING_INPUT
from a2a_t.prompt.validation.errors import GuardrailExecutionError
from a2a_t.prompt.validation.models import GuardrailResult, SlotValidationError, SlotValidationResult
from a2a_t.server.prompt_compliance.constants import (
    GENERATION_STAGE,
    GUARDRAIL_REJECTED,
    PASSED_STAGE,
    PROMPT_RESOURCE_ACCESS_ERROR,
    PROMPT_RESOURCE_LOAD_ERROR,
    GUARDRAIL_STAGE,
    SLOT_VALIDATION_ERROR,
    SLOT_VALIDATION_STAGE,
    TEMPLATE_LOAD_ERROR,
)
from a2a_t.server.prompt_compliance.result import PromptComplianceResult


class FakeGuardrail:
    def __init__(self, result: GuardrailResult) -> None:
        self._result = result

    def check(self, prompt_text: str, context: dict[str, object] | None = None) -> GuardrailResult:
        return self._result


class RaisingGuardrail:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def check(self, prompt_text: str, context: dict[str, object] | None = None) -> GuardrailResult:
        raise self._error


class FakeTemplateLoader:
    def __init__(self, result: str | Exception) -> None:
        self._result = result
        self.last_reference: PromptReference | None = None

    def load(self, *, reference: PromptReference) -> str:
        self.last_reference = reference
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeSlotSchemaLoader:
    def __init__(self, result: SlotSchema | Exception) -> None:
        self._result = result
        self.last_reference: PromptReference | None = None

    def load(self, *, reference: PromptReference) -> SlotSchema:
        self.last_reference = reference
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
        self.last_reference: PromptReference | None = None

    def extract(self, **kwargs: object) -> SlotExtractionResult:
        self.last_reference = kwargs.get("reference")
        return self._result


class FakeValidator:
    def __init__(self, result: SlotValidationResult) -> None:
        self._result = result

    def validate(self, **kwargs: object) -> SlotValidationResult:
        return self._result


class PromptComplianceOrchestratorRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.processed_prompt = format_task_prompt(
            body="processed body",
            metadata=TaskPromptMetadata(
                scenario_code="energy_saving",
                language="en-US",
                version="0.0.1",
                description="Used for energy saving analysis.",
            ),
        )

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
        guardrail: FakeGuardrail | RaisingGuardrail | None = None,
        template_loader: FakeTemplateLoader | None = None,
        slot_schema_loader: FakeSlotSchemaLoader | None = None,
        prompt_resource_loader: FakePromptResourceLoader | None = None,
        extractor: FakeExtractor | None = None,
        validator: FakeValidator | None = None,
    ):
        from a2a_t.server.prompt_compliance.prompt_compliance_orchestrator import PromptComplianceOrchestrator

        return PromptComplianceOrchestrator(
            guardrail=guardrail or FakeGuardrail(GuardrailResult(passed=True, error_code=None, error_message=None)),
            template_loader=template_loader or FakeTemplateLoader("Site: {site}"),
            slot_schema_loader=slot_schema_loader or FakeSlotSchemaLoader(self._slot_schema()),
            prompt_resource_loader=prompt_resource_loader or FakePromptResourceLoader(
                PromptMessages(system_prompt="Extract slots.", user_prompt="Return slots.")
            ),
            extractor=extractor or FakeExtractor(SlotExtractionResult(slots={"site": "Site A"}, slot_errors=[])),
            validator=validator or FakeValidator(SlotValidationResult(passed=True, slot_errors=[])),
        )

    def test_check_returns_success_result(self) -> None:
        template_loader = FakeTemplateLoader("Site: {site}")
        slot_schema_loader = FakeSlotSchemaLoader(self._slot_schema())
        extractor = FakeExtractor(SlotExtractionResult(slots={"site": "Site A"}, slot_errors=[]))
        service = self._build_service(
            template_loader=template_loader,
            slot_schema_loader=slot_schema_loader,
            extractor=extractor,
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata={"task_id": "task-1"})

        self.assertEqual(template_loader.last_reference, PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"))
        self.assertEqual(slot_schema_loader.last_reference, PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"))
        self.assertEqual(extractor.last_reference, PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"))
        self.assertEqual(
            result,
            PromptComplianceResult(
                passed=True,
                stage=PASSED_STAGE,
                extracted_slots={"site": "Site A"},
                need_negotiation=False,
                negotiation_input=None,
            ),
        )

    def test_check_returns_slot_validation_error_with_aggregated_message(self) -> None:
        slot_errors = [
            SlotValidationError(
                slot_name="site",
                code="invalid_value",
                message="Site format is invalid.",
            )
        ]
        service = self._build_service(
            validator=FakeValidator(
                SlotValidationResult(
                    passed=False,
                    slot_errors=slot_errors,
                )
            )
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, SLOT_VALIDATION_STAGE)
        self.assertEqual(result.error_code, SLOT_VALIDATION_ERROR)
        self.assertEqual(result.error_message, "Site format is invalid.")
        self.assertEqual(result.extracted_slots, {"site": "Site A"})
        self.assertEqual(result.slot_errors, slot_errors)
        self.assertTrue(result.need_negotiation)
        self.assertEqual(
            result.negotiation_input,
            {
                "type": "information",
                "contentText": "Site format is invalid.",
                "facts": {
                    "missingFields": [],
                    "invalidFields": [
                        {
                            "name": "site",
                            "reason": "Site format is invalid.",
                        }
                    ],
                },
            },
        )

    def test_check_returns_information_negotiation_input_only_for_missing_and_invalid_fields(self) -> None:
        service = self._build_service(
            validator=FakeValidator(
                SlotValidationResult(
                    passed=False,
                    slot_errors=[
                        SlotValidationError(
                            slot_name="site",
                            code=MISSING_INPUT,
                            message="Required slot 'site' is missing.",
                        ),
                        SlotValidationError(
                            slot_name="analysis_target",
                            code=INVALID_VALUE,
                            message="analysis_target is invalid.",
                        ),
                    ],
                )
            )
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertEqual(result.passed, False)
        self.assertEqual(result.need_negotiation, True)
        self.assertEqual(result.error_message, "Required slot 'site' is missing.; analysis_target is invalid.")
        self.assertEqual(
            result.negotiation_input,
            {
                "type": "information",
                "contentText": "Required slot 'site' is missing.; analysis_target is invalid.",
                "facts": {
                    "missingFields": ["site"],
                    "invalidFields": [
                        {
                            "name": "analysis_target",
                            "reason": "analysis_target is invalid.",
                        }
                    ],
                },
            },
        )

    def test_check_returns_template_load_error_when_template_resource_is_missing(self) -> None:
        service = self._build_service(
            template_loader=FakeTemplateLoader(PromptResourceNotFoundError("missing template")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, GENERATION_STAGE)
        self.assertEqual(result.error_code, TEMPLATE_LOAD_ERROR)

    def test_check_returns_prompt_parse_error_when_front_matter_is_invalid(self) -> None:
        service = self._build_service()

        result = service.check(processed_prompt_text="invalid prompt", request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, "prompt_parse")
        self.assertEqual(result.error_code, "processed_prompt_parse_error")

    def test_check_returns_prompt_parse_error_when_language_is_missing(self) -> None:
        service = self._build_service()

        result = service.check(
            processed_prompt_text=(
                "---\n"
                "scenario_code: energy_saving\n"
                "version: 0.0.1\n"
                "description: Used for energy saving analysis.\n"
                "---\n\n"
                "processed body"
            ),
            request_metadata=None,
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, "prompt_parse")
        self.assertEqual(result.error_code, "processed_prompt_parse_error")

    def test_check_returns_guardrail_execution_error_when_guardrail_runtime_fails(self) -> None:
        service = self._build_service(
            guardrail=RaisingGuardrail(GuardrailExecutionError("guardrail timed out")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, "guardrail")
        self.assertEqual(result.error_code, "guardrail_execution_error")
        self.assertEqual(result.error_message, "guardrail timed out")

    def test_check_returns_guardrail_execution_error_when_guardrail_raises_unexpected_error(self) -> None:
        service = self._build_service(
            guardrail=RaisingGuardrail(RuntimeError("unexpected guardrail failure")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, "guardrail")
        self.assertEqual(result.error_code, "guardrail_execution_error")
        self.assertEqual(result.error_message, "unexpected guardrail failure")

    def test_check_returns_guardrail_rejected_when_guardrail_blocks_prompt(self) -> None:
        service = self._build_service(
            guardrail=FakeGuardrail(
                GuardrailResult(
                    passed=False,
                    error_code=None,
                    error_message=None,
                )
            ),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, GUARDRAIL_STAGE)
        self.assertEqual(result.error_code, GUARDRAIL_REJECTED)
        self.assertEqual(result.error_message, "Guardrail rejected the processed prompt.")

    def test_check_returns_generation_error_when_template_resource_is_invalid(self) -> None:
        service = self._build_service(
            template_loader=FakeTemplateLoader(PromptResourceParseError("template is invalid")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, GENERATION_STAGE)
        self.assertEqual(result.error_code, "prompt_resource_parse_error")
        self.assertEqual(result.error_message, "template is invalid")

    def test_check_returns_generation_error_when_slot_schema_resource_is_invalid(self) -> None:
        service = self._build_service(
            slot_schema_loader=FakeSlotSchemaLoader(PromptResourceParseError("slot schema is invalid")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, GENERATION_STAGE)
        self.assertEqual(result.error_code, "prompt_resource_parse_error")
        self.assertEqual(result.error_message, "slot schema is invalid")

    def test_check_returns_generation_error_when_slot_prompt_resources_are_missing(self) -> None:
        service = self._build_service(
            prompt_resource_loader=FakePromptResourceLoader(PromptResourceNotFoundError("missing slot extraction prompts")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, GENERATION_STAGE)
        self.assertEqual(result.error_code, PROMPT_RESOURCE_LOAD_ERROR)
        self.assertEqual(result.error_message, "missing slot extraction prompts")

    def test_check_returns_generation_error_when_slot_prompt_resource_access_fails(self) -> None:
        service = self._build_service(
            prompt_resource_loader=FakePromptResourceLoader(PromptSourceError("prompt resource path escapes local root")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, GENERATION_STAGE)
        self.assertEqual(result.error_code, PROMPT_RESOURCE_ACCESS_ERROR)
        self.assertEqual(result.error_message, "prompt resource path escapes local root")

    def test_check_returns_generation_error_when_resource_path_access_fails(self) -> None:
        service = self._build_service(
            template_loader=FakeTemplateLoader(PromptSourceError("resource path escapes local root")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertFalse(result.passed)
        self.assertEqual(result.stage, GENERATION_STAGE)
        self.assertEqual(result.error_code, "prompt_resource_access_error")
        self.assertEqual(result.error_message, "resource path escapes local root")


if __name__ == "__main__":
    unittest.main()

