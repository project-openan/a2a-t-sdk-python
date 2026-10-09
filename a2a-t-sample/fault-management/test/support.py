from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.client.prompt_generation.models import PromptGenerationResult
from a2a_t.server.prompt_compliance.models import PromptComplianceFailure, PromptComplianceResult


class FakePromptClient:
    def __init__(
        self,
        *,
        prompt_text: str = "fake prompt",
    ) -> None:
        self._prompt_text = prompt_text
        self.generate_calls: list[object] = []

    def generate_task_prompt(self, user_input: str | dict[str, object]) -> PromptGenerationResult:
        self.generate_calls.append(user_input)
        return PromptGenerationResult(success=True, prompt_text=self._prompt_text, failure=None)


class FakePromptServer:
    def __init__(
        self,
        *,
        check_success: bool = True,
    ) -> None:
        self._check_success = check_success
        self.check_calls: list[dict[str, object]] = []

    def check_task_prompt(self, *, processed_prompt_text: str) -> PromptComplianceResult:
        self.check_calls.append({"processed_prompt_text": processed_prompt_text})
        if self._check_success:
            return PromptComplianceResult(success=True, failure=None)
        return PromptComplianceResult(
            success=False,
            failure=PromptComplianceFailure(
                code="slot.rule_violation",
                message="Missing fields",
                stage="slot_validation",
            ),
        )


class FakeEventQueue:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def enqueue_event(self, event: object) -> None:
        self.events.append(event)
