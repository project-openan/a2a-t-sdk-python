from __future__ import annotations

import json

from a2a_t.common.prompt_resources import PromptResourceLoader, PromptResourceNotFoundError
from a2a_t.negotiation.common.enums import NegotiationStatus, NegotiationType
from a2a_t.negotiation.common.models import NegotiationContext, StartNegotiationInput


class NegotiationPromptRenderer:
    def __init__(
        self,
        *,
        prompt_resource_loader: PromptResourceLoader | None = None,
        version: str = "0.0.1",
        language: str = "en-US",
    ) -> None:
        self._prompt_resource_loader = prompt_resource_loader
        self._version = version
        self._language = language

    def render_start(self, *, input: StartNegotiationInput, context: NegotiationContext) -> str:
        return self._render(
            negotiation_type=input.type,
            context=context,
            status=context.status,
            content_text=input.content_text,
            facts=input.facts,
        )

    def render_continue(
        self,
        *,
        negotiation_type: NegotiationType,
        context: NegotiationContext,
        status: NegotiationStatus,
        content_text: str,
        facts: dict[str, object],
    ) -> str:
        return self._render(
            negotiation_type=negotiation_type,
            context=context,
            status=status,
            content_text=content_text,
            facts=facts,
        )

    def _render(
        self,
        *,
        negotiation_type: NegotiationType,
        context: NegotiationContext,
        status: NegotiationStatus,
        content_text: str,
        facts: dict[str, object],
    ) -> str:
        payload = json.dumps(
            {
                "facts": facts,
                "contentText": content_text,
            },
            ensure_ascii=True,
        )
        natural_language_text = self._build_natural_language_text(
            negotiation_type=negotiation_type,
            context=context,
            status=status,
            content_text=content_text,
            facts=facts,
            facts_json=json.dumps(facts, ensure_ascii=True),
        )
        return (
            f"{natural_language_text}\n\n"
            f"```negotiation-json\n{payload}\n```"
        )

    def _build_natural_language_text(
        self,
        *,
        negotiation_type: NegotiationType,
        context: NegotiationContext,
        status: NegotiationStatus,
        content_text: str,
        facts: dict[str, object],
        facts_json: str,
    ) -> str:
        prompt_messages = self._load_prompt_messages(negotiation_type)
        if prompt_messages is None:
            return (
                f"Negotiation type: {negotiation_type.value}\n"
                f"Negotiation id: {context.negotiation_id}\n"
                f"Status: {status.value}\n"
                f"Content: {content_text}"
            )

        format_values = {
            "negotiation_type": negotiation_type.value,
            "negotiation_id": context.negotiation_id,
            "role": context.role.value,
            "round": context.round,
            "status": status.value,
            "content_text": content_text,
            "facts": facts,
            "facts_json": facts_json,
        }
        return "\n\n".join(
            part.format(**format_values)
            for part in (prompt_messages.system_prompt, prompt_messages.user_prompt)
            if part
        )

    def _load_prompt_messages(self, negotiation_type: NegotiationType):
        if self._prompt_resource_loader is None:
            return None
        analysis_action = f"{negotiation_type.value}_negotiation"
        try:
            return self._prompt_resource_loader.load(
                analysis_action=analysis_action,
                version=self._version,
                language=self._language,
            )
        except PromptResourceNotFoundError:
            if self._language == "en-US":
                raise
            return self._prompt_resource_loader.load(
                analysis_action=analysis_action,
                version=self._version,
                language="en-US",
            )
