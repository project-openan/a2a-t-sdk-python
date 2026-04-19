from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class FakePromptChecker:
    def __init__(self, result: object) -> None:
        self._result = result
        self.calls: list[str] = []

    def check(self, *, processed_prompt_text: str, request_metadata=None):
        self.calls.append(processed_prompt_text)
        return self._result


class NegotiationTypesTest(unittest.TestCase):
    def _prompt_compliance_result(self):
        from a2a_t.server.prompt_compliance.result import PromptComplianceResult

        return PromptComplianceResult(
            passed=False,
            stage="slot_validation",
            error_code="slot_validation_error",
            error_message="Need more information",
            need_negotiation=True,
            negotiation_input={
                "type": "information",
                "contentText": "Need more information",
                "facts": {
                    "missingFields": ["site"],
                    "invalidFields": [],
                },
            },
        )

    def _context(self):
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext

        return NegotiationContext(
            negotiation_type=NegotiationType.INFORMATION,
            negotiation_id="neg-1",
            role=NegotiationRole.SERVER,
            round=1,
            status=NegotiationStatus.IN_PROGRESS,
            extra={},
        )

    def test_information_type_on_client_side_returns_received_facts(self) -> None:
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer
        from a2a_t.negotiation.types.information import InformationNegotiationType

        negotiation_type = InformationNegotiationType(prompt_renderer=NegotiationPromptRenderer())

        result = negotiation_type.process_received_message(
            message="prompt",
            context=self._context(),
            record=None,
        )

        self.assertTrue(result.need_response)
        self.assertEqual(result.facts, {})
        self.assertEqual(result.message, "prompt")

    def test_information_type_on_server_side_uses_prompt_checker(self) -> None:
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer
        from a2a_t.negotiation.types.information import InformationNegotiationType

        checker = FakePromptChecker(
            self._prompt_compliance_result()
        )
        negotiation_type = InformationNegotiationType(
            prompt_renderer=NegotiationPromptRenderer(),
            prompt_checker=checker,
        )

        result = negotiation_type.process_received_message(
            message="latest full task prompt",
            context=self._context(),
            record=None,
        )

        self.assertEqual(checker.calls, ["latest full task prompt"])
        self.assertTrue(result.need_response)
        self.assertEqual(result.facts, {})
        self.assertEqual(result.message, "Need more information")

    def test_information_type_on_server_side_returns_completion_message_when_prompt_is_valid(self) -> None:
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer
        from a2a_t.negotiation.types.information import InformationNegotiationType
        from a2a_t.server.prompt_compliance.result import PromptComplianceResult

        checker = FakePromptChecker(
            PromptComplianceResult(
                passed=True,
                stage="done",
                error_code=None,
                error_message=None,
                need_negotiation=False,
                negotiation_input=None,
            )
        )
        negotiation_type = InformationNegotiationType(
            prompt_renderer=NegotiationPromptRenderer(),
            prompt_checker=checker,
        )

        result = negotiation_type.process_received_message(
            message="latest full task prompt",
            context=self._context(),
            record=None,
        )

        self.assertTrue(result.need_response)
        self.assertEqual(result.facts, {})
        self.assertEqual(result.message, "Task prompt is complete.")

    def test_information_type_on_server_side_returns_error_when_prompt_fails_without_negotiation(self) -> None:
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer
        from a2a_t.negotiation.types.information import InformationNegotiationType
        from a2a_t.server.prompt_compliance.result import PromptComplianceResult

        checker = FakePromptChecker(
            PromptComplianceResult(
                passed=False,
                stage="guardrail",
                error_code="guardrail_rejected",
                error_message="Guardrail rejected the task prompt.",
                need_negotiation=False,
                negotiation_input=None,
            )
        )
        negotiation_type = InformationNegotiationType(
            prompt_renderer=NegotiationPromptRenderer(),
            prompt_checker=checker,
        )

        result = negotiation_type.process_received_message(
            message="latest full task prompt",
            context=self._context(),
            record=None,
        )

        self.assertTrue(result.need_response)
        self.assertEqual(result.facts, {})
        self.assertEqual(result.message, "Guardrail rejected the task prompt.")

    def test_information_type_render_continue_returns_final_task_prompt_when_agreed(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationStatus
        from a2a_t.negotiation.common.models import NegotiationRecord
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer
        from a2a_t.negotiation.types.information import InformationNegotiationType

        negotiation_type = InformationNegotiationType(prompt_renderer=NegotiationPromptRenderer())
        result = negotiation_type.render_continue_prompt(
            record=NegotiationRecord(
                context=self._context(),
                last_message=None,
                last_receive_result=None,
                last_continue_result=None,
                last_task_prompt=None,
                created_at="t1",
                updated_at="t1",
            ),
            context=self._context(),
            status=NegotiationStatus.AGREED,
            content_text="final prompt",
        )

        self.assertEqual(result.final_task_prompt, "final prompt")
        self.assertEqual(result.prompt_text, "final prompt")

    def test_clarification_type_passthroughs_facts(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer
        from a2a_t.negotiation.types.clarification import ClarificationNegotiationType

        negotiation_type = ClarificationNegotiationType(prompt_renderer=NegotiationPromptRenderer())
        result = negotiation_type.process_received_message(
            message="clarify this",
            context=NegotiationContext(
                negotiation_type=NegotiationType.CLARIFICATION,
                negotiation_id="neg-2",
                role=NegotiationRole.CLIENT,
                round=1,
                status=NegotiationStatus.IN_PROGRESS,
                extra={},
            ),
            record=None,
        )

        self.assertEqual(result.facts, {})
        self.assertEqual(result.message, "clarify this")

    def test_clarification_type_on_terminal_message_does_not_require_response(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer
        from a2a_t.negotiation.types.clarification import ClarificationNegotiationType

        negotiation_type = ClarificationNegotiationType(prompt_renderer=NegotiationPromptRenderer())
        result = negotiation_type.process_received_message(
            message="clarify this",
            context=NegotiationContext(
                negotiation_type=NegotiationType.CLARIFICATION,
                negotiation_id="neg-3",
                role=NegotiationRole.CLIENT,
                round=2,
                status=NegotiationStatus.AGREED,
                extra={},
            ),
            record=None,
        )

        self.assertFalse(result.need_response)
        self.assertEqual(result.facts, {})
        self.assertEqual(result.message, "clarify this")
