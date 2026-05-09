"""OpenAI-compatible adapter built from composed provider components."""

from __future__ import annotations

from typing import Any

from a2a_t.llm.adapters.composed_adapter import ComposedLLMAdapter
from a2a_t.llm.payload_builders import OpenAICompatiblePayloadBuilder
from a2a_t.llm.response_parsers import OpenAICompatibleResponseParser
from a2a_t.llm.transports import OpenAICompatibleTransportAdapter


class OpenAICompatibleAdapter(ComposedLLMAdapter):
    """Adapter for providers exposing an OpenAI-compatible chat API."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(
            config,
            transport=OpenAICompatibleTransportAdapter(config),
            payload_builder=OpenAICompatiblePayloadBuilder(config),
            response_parser=OpenAICompatibleResponseParser(config),
        )
