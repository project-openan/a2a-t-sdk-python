from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.models import CacheStatus, Prompt, PromptSource
from a2a_t.server.prompt_compliance.errors import (
    GuardrailRejectedError,
    PromptOriginResolveError,
    SlotSchemaLoadError,
    SlotValidationError,
)
from a2a_t.server.prompt_compliance.models import (
    GuardrailDecision,
    GuardrailResult,
    PromptComplianceResult,
    PromptIdentity,
    SlotExtractionResult,
)
from a2a_t.server.prompt_compliance.service import PromptComplianceService
from a2a_t.server.prompt_handler import PromptHandler


PROCESSED_PROMPT = """---
name: network diagnosis
language: zh-CN
version: 1.0.0
---
processed prompt body"""


class FakeGuardrail:
    def __init__(self, result: GuardrailResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    def check(self, prompt_text: str, context: dict[str, object] | None = None) -> GuardrailResult:
        self.calls.append((prompt_text, context))
        return self.result


class FakeParser:
    def __init__(self, identity: PromptIdentity) -> None:
        self.identity = identity

    def parse(self, processed_prompt_text: str) -> PromptIdentity:
        return self.identity


class FakeOriginResolver:
    def __init__(self, prompt: Prompt | Exception) -> None:
        self.prompt = prompt

    def resolve(self, identity: PromptIdentity) -> Prompt:
        if isinstance(self.prompt, Exception):
            raise self.prompt
        return self.prompt


class FakeExtractor:
    def __init__(self, result: SlotExtractionResult) -> None:
        self.result = result

    def extract(self, *, original_prompt: Prompt, processed_prompt_text: str) -> SlotExtractionResult:
        return self.result


class FakeSlotSchemaResolver:
    def __init__(self, slot_schema: object | Exception) -> None:
        self.slot_schema = slot_schema

    def load(self, identity: PromptIdentity) -> object:
        if isinstance(self.slot_schema, Exception):
            raise self.slot_schema
        return self.slot_schema


class FakeValidator:
    def __init__(self, valid: bool, errors: list[str] | None = None) -> None:
        self.valid = valid
        self.errors = errors or []

    def validate(self, *, extracted_slots: dict[str, object], slot_schema: object) -> object:
        return type("ValidationResult", (), {"valid": self.valid, "errors": self.errors})()


class PromptComplianceServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = PromptIdentity(name="network diagnosis", language="zh-CN", version="1.0.0")
        self.original_prompt = Prompt(
            name="network diagnosis",
            language="zh-CN",
            version="1.0.0",
            title="Diagnosis Prompt",
            description="Extract slots for diagnosis tasks.",
            format="markdown",
            body="original prompt body",
            raw_content="raw original prompt",
            source=PromptSource(source_type="local_file", locator="./prompts/diagnosis.md"),
            cache_status=CacheStatus.MISS,
        )

    def _build_service(
        self,
        *,
        guardrail: FakeGuardrail | None = None,
        origin_resolver: FakeOriginResolver | None = None,
        slot_schema_resolver: FakeSlotSchemaResolver | None = None,
        validator: FakeValidator | None = None,
    ) -> PromptComplianceService:
        return PromptComplianceService(
            guardrail=guardrail or FakeGuardrail(GuardrailResult(passed=True)),
            parser=FakeParser(self.identity),
            origin_resolver=origin_resolver or FakeOriginResolver(self.original_prompt),
            extractor=FakeExtractor(
                SlotExtractionResult(slots={"device_type": "router"}, notes=["ok"], confidence=0.9)
            ),
            slot_config_resolver=slot_schema_resolver or FakeSlotSchemaResolver(object()),
            validator=validator or FakeValidator(True),
            slot_not_found_policy="strict",
        )

    def test_service_returns_guardrail_rejection_result(self) -> None:
        service = self._build_service(
            guardrail=FakeGuardrail(
                GuardrailResult(passed=False, category="prompt_injection", reason="blocked by policy")
            )
        )

        result = service.check(processed_prompt_text=PROCESSED_PROMPT, request_metadata={"request_id": "req-1"})

        self.assertEqual(
            result,
            PromptComplianceResult(
                passed=False,
                stage="guardrail",
                error_code="guardrail_rejected",
                error_message="blocked by policy",
            ),
        )

    def test_service_rejects_block_mask_and_review_guardrail_decisions(self) -> None:
        for decision in [GuardrailDecision.BLOCK, GuardrailDecision.MASK, GuardrailDecision.REVIEW]:
            with self.subTest(decision=decision):
                service = self._build_service(
                    guardrail=FakeGuardrail(
                        GuardrailResult(
                            passed=True,
                            decision=decision,
                            provider="google_model_armor",
                            reason=f"{decision.value} by policy",
                        )
                    )
                )

                result = service.check(processed_prompt_text=PROCESSED_PROMPT, request_metadata={"request_id": "req-1"})

                self.assertEqual(
                    result,
                    PromptComplianceResult(
                        passed=False,
                        stage="guardrail",
                        error_code="guardrail_rejected",
                        error_message=f"{decision.value} by policy",
                    ),
                )

    def test_service_returns_guardrail_rejected_error_result(self) -> None:
        class RejectingGuardrail:
            def check(self, prompt_text: str, context: dict[str, object] | None = None) -> object:
                raise GuardrailRejectedError("blocked by policy", category="prompt_injection")

        service = self._build_service(guardrail=RejectingGuardrail())

        result = service.check(processed_prompt_text=PROCESSED_PROMPT, request_metadata={"request_id": "req-1"})

        self.assertEqual(result.stage, "guardrail")
        self.assertEqual(result.error_code, "guardrail_rejected")
        self.assertEqual(result.error_message, "blocked by policy")

    def test_service_returns_origin_resolve_failure_result(self) -> None:
        service = self._build_service(
            origin_resolver=FakeOriginResolver(
                PromptOriginResolveError("Original prompt could not be resolved from prompt identity.")
            )
        )

        result = service.check(processed_prompt_text=PROCESSED_PROMPT, request_metadata=None)

        self.assertEqual(
            result,
            PromptComplianceResult(
                passed=False,
                stage="origin_resolve",
                error_code="prompt_origin_resolve_error",
                error_message="Original prompt could not be resolved from prompt identity.",
            ),
        )

    def test_service_skips_validation_when_slot_config_missing_and_policy_is_skip(self) -> None:
        service = PromptComplianceService(
            guardrail=FakeGuardrail(GuardrailResult(passed=True)),
            parser=FakeParser(self.identity),
            origin_resolver=FakeOriginResolver(self.original_prompt),
            extractor=FakeExtractor(
                SlotExtractionResult(slots={"device_type": "router"}, notes=["ok"], confidence=0.9)
            ),
            slot_config_resolver=FakeSlotSchemaResolver(SlotSchemaLoadError("missing")),
            validator=FakeValidator(True),
            slot_not_found_policy="skip",
        )

        result = service.check(processed_prompt_text=PROCESSED_PROMPT, request_metadata=None)

        self.assertEqual(
            result,
            PromptComplianceResult(
                passed=True,
                stage="skipped_slot_validation",
                extracted_slots={"device_type": "router"},
                notes=["ok"],
                confidence=0.9,
            ),
        )

    def test_service_returns_validation_error_when_slot_config_missing_and_policy_is_strict(self) -> None:
        service = PromptComplianceService(
            guardrail=FakeGuardrail(GuardrailResult(passed=True)),
            parser=FakeParser(self.identity),
            origin_resolver=FakeOriginResolver(self.original_prompt),
            extractor=FakeExtractor(
                SlotExtractionResult(slots={"device_type": "router"}, notes=["ok"], confidence=0.9)
            ),
            slot_config_resolver=FakeSlotSchemaResolver(SlotSchemaLoadError("missing")),
            validator=FakeValidator(True),
            slot_not_found_policy="strict",
        )

        result = service.check(processed_prompt_text=PROCESSED_PROMPT, request_metadata=None)

        self.assertEqual(result.stage, "slot_schema")
        self.assertEqual(result.error_code, "slot_schema_load_error")
        self.assertFalse(result.passed)

    def test_service_returns_slot_validation_error_result(self) -> None:
        service = self._build_service(validator=FakeValidator(False, ["location is required"]))

        result = service.check(processed_prompt_text=PROCESSED_PROMPT, request_metadata=None)

        self.assertEqual(
            result,
            PromptComplianceResult(
                passed=False,
                stage="slot_validation",
                error_code="slot_validation_error",
                error_message="location is required",
            ),
        )

    def test_service_returns_success_result(self) -> None:
        service = self._build_service()

        result = service.check(processed_prompt_text=PROCESSED_PROMPT, request_metadata={"request_id": "req-1"})

        self.assertEqual(
            result,
            PromptComplianceResult(
                passed=True,
                stage="passed",
                extracted_slots={"device_type": "router"},
                notes=["ok"],
                confidence=0.9,
            ),
        )


class PromptComplianceErrorModelTest(unittest.TestCase):
    def test_slot_validation_error_keeps_machine_readable_context(self) -> None:
        error = SlotValidationError("slot validation failed", errors=["location is required"], slot_name="location")

        self.assertEqual(str(error), "slot validation failed")
        self.assertEqual(error.context["errors"], ["location is required"])
        self.assertEqual(error.context["slot_name"], "location")

class PromptHandlerTest(unittest.TestCase):
    def test_process_runs_prompt_compliance_and_returns_passed_payload(self) -> None:
        class FakeComplianceService:
            def check(self, *, processed_prompt_text: str, request_metadata: dict[str, object] | None) -> PromptComplianceResult:
                return PromptComplianceResult(
                    passed=True,
                    stage="passed",
                    extracted_slots={"device_type": "router"},
                    notes=["ok"],
                    confidence=0.9,
                )

        handler = PromptHandler(validator=FakeComplianceService())

        result = handler.process("task-1", {"processed_prompt_text": PROCESSED_PROMPT})

        self.assertEqual(result["passed"], True)
        self.assertEqual(result["extracted_slots"], {"device_type": "router"})


if __name__ == "__main__":
    unittest.main()
