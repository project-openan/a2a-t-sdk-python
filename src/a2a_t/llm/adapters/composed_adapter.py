"""Composed adapter for builder/transport/parser provider stacks."""

from __future__ import annotations

from typing import Any

from a2a_t.llm.base import ChatMessage, LLMAdapter, LLMResponse
from a2a_t.llm.payload_builders import PayloadBuilder
from a2a_t.llm.response_parsers import ResponseParser
from a2a_t.llm.transports import TransportAdapter


class ComposedLLMAdapter(LLMAdapter):
    """Compose payload building, transport, and response parsing behind the LLMAdapter API."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: TransportAdapter,
        payload_builder: PayloadBuilder,
        response_parser: ResponseParser,
    ) -> None:
        super().__init__(config)
        self._transport = transport
        self._payload_builder = payload_builder
        self._response_parser = response_parser

    def complete(self, prompt: str, system_prompt: str | None = None, **kwargs: Any) -> LLMResponse:
        """Generate a completion through the composed provider path."""
        provider_kwargs = dict(kwargs)
        provider_kwargs.pop("history_window", None)
        payload = self._payload_builder.build_complete(
            model=self._model,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=provider_kwargs.get("temperature"),
            max_tokens=provider_kwargs.get("max_tokens"),
        )
        raw_response = self._transport.invoke(payload=payload)
        response = self._response_parser.parse_complete(response=raw_response, model=self._model)
        response.session_id = None
        return response

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        """Generate a structured response through the composed provider path."""
        payload = self._payload_builder.build_structured(
            model=self._model,
            messages=messages,
            json_schema=json_schema,
            temperature=kwargs.get("temperature"),
            max_tokens=kwargs.get("max_tokens"),
        )
        raw_response = self._transport.invoke(payload=payload)
        return self._response_parser.parse_structured(response=raw_response, model=self._model)

    @property
    def adapter_type(self) -> str:
        """Return the configured provider type for composed adapters."""
        return self._provider

    def _generate_from_messages(self, messages: list[ChatMessage], **kwargs: Any) -> LLMResponse:
        """Generate chat responses through the composed provider path."""
        payload = self._payload_builder.build_chat(
            model=self._model,
            messages=messages,
            temperature=kwargs.get("temperature"),
            max_tokens=kwargs.get("max_tokens"),
        )
        raw_response = self._transport.invoke(payload=payload)
        return self._response_parser.parse_chat(response=raw_response, model=self._model)
