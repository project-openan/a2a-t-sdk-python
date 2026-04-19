from __future__ import annotations

from a2a_t.negotiation.common.constants import NEGOTIATION_CONTEXT_KEY, NEGOTIATION_TEXT_KEY, TASK_PROMPT_KEY
from a2a_t.negotiation.common.models import NegotiationContext


class NegotiationMapBuilder:
    def build(
        self,
        *,
        prompt_text: str,
        context: NegotiationContext,
        final_task_prompt: str | None = None,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            NEGOTIATION_TEXT_KEY: prompt_text,
            NEGOTIATION_CONTEXT_KEY: context.to_context_json(),
        }
        if final_task_prompt is not None:
            result[TASK_PROMPT_KEY] = final_task_prompt
        return result
