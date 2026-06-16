from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.analysis.models import (
    ScenarioResolutionFailure,
    ScenarioResolutionResult,
    SlotExtractionResult,
)
from a2a_t.prompt.common.errors import PromptSourceError
from a2a_t.prompt.common.models import PromptReference
from a2a_t.common.prompt_resources.errors import PromptResourceNotFoundError, PromptResourceParseError
from a2a_t.common.prompt_resources.models import PromptMessages, ScenarioDefinition, SlotDefinition, SlotSchema
from a2a_t.prompt.validation.constants import INVALID_VALUE, MISSING_INPUT
from a2a_t.prompt.validation.models import SlotValidationError, SlotValidationResult
from a2a_t.server.prompt_compliance.constants import (
    PROMPT_RESOURCE_ACCESS_ERROR,
    PROMPT_RESOURCE_LOAD_ERROR,
    SLOT_VALIDATION_ERROR,
    SLOT_VALIDATION_STAGE,
    TEMPLATE_LOAD_ERROR,
)
from a2a_t.server.prompt_compliance.models import PromptComplianceResult
from a2a_t.server.prompt_compliance.models import SemanticValidationError, SemanticValidationResult


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


class FakeSlotJsonSchemaLoader:
    def __init__(self, result: dict[str, object] | Exception) -> None:
        self._result = result
        self.last_reference: PromptReference | None = None

    def load(self, *, reference: PromptReference) -> dict[str, object]:
        self.last_reference = reference
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakePromptResourceLoader:
    def __init__(self, result: PromptMessages | Exception) -> None:
        self._result = result

    def load(self, *, analysis_action: str, language: str) -> PromptMessages:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeScenarioResolver:
    def __init__(self, result: ScenarioResolutionResult) -> None:
        self._result = result
        self.calls: list[str] = []

    def resolve(self, normalized_input: str) -> ScenarioResolutionResult:
        self.calls.append(normalized_input)
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


class FakeSemanticValidator:
    def __init__(self, passed: bool, message: str = "semantic validation failed") -> None:
        self._passed = passed
        self._message = message
        self.calls: int = 0
        self.last_kwargs: dict[str, object] | None = None

    def validate(self, **kwargs: object) -> SemanticValidationResult:
        self.calls += 1
        self.last_kwargs = dict(kwargs)
        if self._passed:
            return SemanticValidationResult(passed=True, errors=[])
        return SemanticValidationResult(
            passed=False,
            errors=[
                SemanticValidationError(
                    slot_name="site",
                    code=INVALID_VALUE,
                    message=self._message,
                )
            ],
        )


class PromptComplianceOrchestratorRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.processed_prompt = "processed body"
        self.scenario_resolution = ScenarioResolutionResult(
            success=True,
            reference=PromptReference(
                scenario_code="energy_saving",
                language="en-US",
            ),
            scenario=ScenarioDefinition(
                scenario_code="energy_saving",
                scenario_name="Energy Saving",
                description="Used for energy saving analysis.",
                example="Analyze site power usage and suggest optimization.",
            ),
        )

    def _slot_schema(self) -> SlotSchema:
        return SlotSchema(
            scenario_code="energy_saving",
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
        slot_json_schema_loader: FakeSlotJsonSchemaLoader | None = None,
        prompt_resource_loader: FakePromptResourceLoader | None = None,
        scenario_resolver: FakeScenarioResolver | None = None,
        extractor: FakeExtractor | None = None,
        validator: FakeValidator | None = None,
        semantic_validator: FakeSemanticValidator | None = None,
    ):
        from a2a_t.server.prompt_compliance.prompt_compliance_orchestrator import PromptComplianceOrchestrator

        return PromptComplianceOrchestrator(
            scenario_resolver=scenario_resolver or FakeScenarioResolver(self.scenario_resolution),
            template_loader=template_loader or FakeTemplateLoader("Site: {site}"),
            slot_schema_loader=slot_schema_loader or FakeSlotSchemaLoader(self._slot_schema()),
            slot_json_schema_loader=slot_json_schema_loader or FakeSlotJsonSchemaLoader(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {"site": {"type": "string", "minLength": 1}},
                    "required": ["site"],
                    "additionalProperties": False,
                }
            ),
            prompt_resource_loader=prompt_resource_loader or FakePromptResourceLoader(
                PromptMessages(system_prompt="Extract slots.", user_prompt="Return slots.")
            ),
            extractor=extractor or FakeExtractor(SlotExtractionResult(slots={"site": "Site A"}, slot_errors=[])),
            validator=validator or FakeValidator(SlotValidationResult(passed=True, slot_errors=[])),
            semantic_validator=semantic_validator or FakeSemanticValidator(passed=True),
        )

    def test_check_returns_success_result(self) -> None:
        template_loader = FakeTemplateLoader("Site: {site}")
        slot_schema_loader = FakeSlotSchemaLoader(self._slot_schema())
        slot_json_schema_loader = FakeSlotJsonSchemaLoader(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"site": {"type": "string", "minLength": 1}},
                "required": ["site"],
                "additionalProperties": False,
            }
        )
        extractor = FakeExtractor(SlotExtractionResult(slots={"site": "Site A"}, slot_errors=[]))
        service = self._build_service(
            template_loader=template_loader,
            slot_schema_loader=slot_schema_loader,
            slot_json_schema_loader=slot_json_schema_loader,
            extractor=extractor,
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata={"task_id": "task-1"})

        self.assertEqual(template_loader.last_reference, PromptReference(scenario_code="energy_saving", language="en-US"))
        self.assertEqual(slot_schema_loader.last_reference, PromptReference(scenario_code="energy_saving", language="en-US"))
        self.assertEqual(slot_json_schema_loader.last_reference, PromptReference(scenario_code="energy_saving", language="en-US"))
        self.assertEqual(extractor.last_reference, PromptReference(scenario_code="energy_saving", language="en-US"))
        self.assertEqual(
            result,
            PromptComplianceResult(
                success=True,
            ),
        )

    def test_check_returns_slot_validation_error_with_failure_payload(self) -> None:
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

        self.assertEqual(
            result,
            PromptComplianceResult(
                success=False,
                failure={
                    "code": SLOT_VALIDATION_ERROR,
                    "message": "Site format is invalid.",
                    "stage": SLOT_VALIDATION_STAGE,
                },
            ),
        )

    def test_check_returns_slot_validation_error_for_negotiable_slot_failures(self) -> None:
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

        self.assertEqual(
            result,
            PromptComplianceResult(
                success=False,
                failure={
                    "code": SLOT_VALIDATION_ERROR,
                    "message": "Required slot 'site' is missing.; analysis_target is invalid.",
                    "stage": SLOT_VALIDATION_STAGE,
                },
            ),
        )

    def test_check_skips_semantic_validation_when_schema_fails(self) -> None:
        semantic_validator = FakeSemanticValidator(passed=True)
        service = self._build_service(
            validator=FakeValidator(
                SlotValidationResult(
                    passed=False,
                    slot_errors=[
                        SlotValidationError(
                            slot_name="site",
                            code=MISSING_INPUT,
                            message="Required slot 'site' is missing.",
                        )
                    ],
                )
            ),
            semantic_validator=semantic_validator,
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertEqual(semantic_validator.calls, 0)
        self.assertEqual(result.success, False)
        assert result.failure is not None
        self.assertEqual(result.failure["code"], SLOT_VALIDATION_ERROR)
        self.assertEqual(result.failure["stage"], SLOT_VALIDATION_STAGE)

    def test_check_returns_slot_validation_error_when_semantic_validation_fails(self) -> None:
        semantic_validator = FakeSemanticValidator(passed=False, message="semantic mismatch for site")
        service = self._build_service(semantic_validator=semantic_validator)

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertEqual(semantic_validator.calls, 1)
        self.assertEqual(
            semantic_validator.last_kwargs,
            {
                "language": "en-US",
                "slot_json_schema": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {"site": {"type": "string", "minLength": 1}},
                    "required": ["site"],
                    "additionalProperties": False,
                },
                "extracted_slots": {"site": "Site A"},
            },
        )
        self.assertEqual(
            result,
            PromptComplianceResult(
                success=False,
                failure={
                    "code": SLOT_VALIDATION_ERROR,
                    "message": "semantic mismatch for site",
                    "stage": SLOT_VALIDATION_STAGE,
                },
            ),
        )

    def test_check_returns_success_when_schema_and_semantic_validation_pass(self) -> None:
        semantic_validator = FakeSemanticValidator(passed=True)
        service = self._build_service(semantic_validator=semantic_validator)

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertEqual(semantic_validator.calls, 1)
        self.assertEqual(result, PromptComplianceResult(success=True))

    def test_check_returns_template_load_error_when_template_resource_is_missing(self) -> None:
        service = self._build_service(
            template_loader=FakeTemplateLoader(PromptResourceNotFoundError("missing template")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertEqual(
            result,
            PromptComplianceResult(
                success=False,
                failure={
                    "code": TEMPLATE_LOAD_ERROR,
                    "message": "missing template",
                    "stage": "preparation",
                },
            ),
        )

    def test_check_returns_prompt_parse_error_when_scenario_resolution_fails(self) -> None:
        service = self._build_service(
            scenario_resolver=FakeScenarioResolver(
                ScenarioResolutionResult(
                    success=False,
                    failure=ScenarioResolutionFailure(
                        code="processed_prompt_parse_error",
                        message="No matching scenario.",
                        stage="prompt_parse",
                    ),
                )
            ),
        )

        result = service.check(processed_prompt_text="natural language prompt", request_metadata=None)

        self.assertEqual(
            result,
            PromptComplianceResult(
                success=False,
                failure={
                    "code": "processed_prompt_parse_error",
                    "message": "No matching scenario.",
                    "stage": "prompt_parse",
                },
            ),
        )

    def test_check_returns_preparation_error_when_scenario_resources_cannot_be_resolved(self) -> None:
        service = self._build_service(
            scenario_resolver=FakeScenarioResolver(
                ScenarioResolutionResult(
                    success=False,
                    failure=ScenarioResolutionFailure(
                        code="prompt_resource_load_error",
                        message="Scenario recognition prompt resources are missing.",
                        stage="preparation",
                    ),
                )
            ),
        )

        result = service.check(processed_prompt_text="natural language prompt", request_metadata=None)

        self.assertEqual(
            result,
            PromptComplianceResult(
                success=False,
                failure={
                    "code": "prompt_resource_load_error",
                    "message": "Scenario recognition prompt resources are missing.",
                    "stage": "preparation",
                },
            ),
        )

    def test_check_returns_preparation_error_when_template_resource_is_invalid(self) -> None:
        service = self._build_service(
            template_loader=FakeTemplateLoader(PromptResourceParseError("template is invalid")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertEqual(
            result,
            PromptComplianceResult(
                success=False,
                failure={
                    "code": "prompt_resource_parse_error",
                    "message": "template is invalid",
                    "stage": "preparation",
                },
            ),
        )

    def test_check_returns_preparation_error_when_slot_schema_resource_is_invalid(self) -> None:
        service = self._build_service(
            slot_schema_loader=FakeSlotSchemaLoader(PromptResourceParseError("slot schema is invalid")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertEqual(
            result,
            PromptComplianceResult(
                success=False,
                failure={
                    "code": "prompt_resource_parse_error",
                    "message": "slot schema is invalid",
                    "stage": "preparation",
                },
            ),
        )

    def test_check_returns_preparation_error_when_slot_prompt_resources_are_missing(self) -> None:
        service = self._build_service(
            prompt_resource_loader=FakePromptResourceLoader(PromptResourceNotFoundError("missing slot extraction prompts")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertEqual(
            result,
            PromptComplianceResult(
                success=False,
                failure={
                    "code": PROMPT_RESOURCE_LOAD_ERROR,
                    "message": "missing slot extraction prompts",
                    "stage": "preparation",
                },
            ),
        )

    def test_check_returns_preparation_error_when_slot_prompt_resource_access_fails(self) -> None:
        service = self._build_service(
            prompt_resource_loader=FakePromptResourceLoader(PromptSourceError("prompt resource path escapes local root")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertEqual(
            result,
            PromptComplianceResult(
                success=False,
                failure={
                    "code": PROMPT_RESOURCE_ACCESS_ERROR,
                    "message": "prompt resource path escapes local root",
                    "stage": "preparation",
                },
            ),
        )

    def test_check_returns_preparation_error_when_resource_path_access_fails(self) -> None:
        service = self._build_service(
            template_loader=FakeTemplateLoader(PromptSourceError("resource path escapes local root")),
        )

        result = service.check(processed_prompt_text=self.processed_prompt, request_metadata=None)

        self.assertEqual(
            result,
            PromptComplianceResult(
                success=False,
                failure={
                    "code": "prompt_resource_access_error",
                    "message": "resource path escapes local root",
                    "stage": "preparation",
                },
            ),
        )


if __name__ == "__main__":
    unittest.main()

