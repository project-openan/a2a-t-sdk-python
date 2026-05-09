"""LLM integration layer for a2a_t."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from a2a_t.llm.adapters.composed_adapter import ComposedLLMAdapter
from a2a_t.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from a2a_t.llm.base import ChatMessage, ChatSession, LLMAdapter, LLMResponse
from a2a_t.llm.client import LLMClient
from a2a_t.llm.errors import LLMConfigError, LLMError, LLMRuntimeError
from a2a_t.llm.factory import LLMAdapterFactory
from a2a_t.llm.payload_builders import OpenAICompatiblePayloadBuilder, PayloadBuilder
from a2a_t.llm.response_parsers import OpenAICompatibleResponseParser, ResponseParser
from a2a_t.llm.session_store import InMemorySessionStore, SessionStore
from a2a_t.llm.transports import OpenAICompatibleTransportAdapter, TransportAdapter

__all__ = [
    "ChatMessage",
    "ChatSession",
    "ComposedLLMAdapter",
    "InMemorySessionStore",
    "LLMAdapter",
    "LLMAdapterFactory",
    "LLMClient",
    "LLMConfigError",
    "LLMError",
    "LLMResponse",
    "LLMRuntimeError",
    "OpenAICompatibleAdapter",
    "OpenAICompatiblePayloadBuilder",
    "OpenAICompatibleResponseParser",
    "OpenAICompatibleTransportAdapter",
    "PayloadBuilder",
    "ResponseParser",
    "SessionStore",
    "TransportAdapter",
]


def __getattr__(name: str) -> Any:
    if name == "adapters":
        return import_module("a2a_t.llm.adapters")
    if name in {
        "ChatMessage",
        "ChatSession",
        "ComposedLLMAdapter",
        "OpenAICompatibleAdapter",
        "OpenAICompatiblePayloadBuilder",
        "OpenAICompatibleResponseParser",
        "OpenAICompatibleTransportAdapter",
        "PayloadBuilder",
        "ResponseParser",
        "TransportAdapter",
    }:
        if name in {"OpenAICompatibleAdapter"}:
            from a2a_t.llm.adapters import openai_compatible as llm_openai_compatible

            try:
                return getattr(llm_openai_compatible, name)
            except AttributeError as exc:
                raise AttributeError(
                    f"module 'a2a_t.llm' has no attribute '{name}' in this phase"
                ) from exc
        if name in {"OpenAICompatiblePayloadBuilder", "OpenAICompatibleResponseParser"}:
            if name == "OpenAICompatiblePayloadBuilder":
                from a2a_t.llm import payload_builders as llm_payload_builders

                try:
                    return getattr(llm_payload_builders, name)
                except AttributeError as exc:
                    raise AttributeError(
                        f"module 'a2a_t.llm' has no attribute '{name}' in this phase"
                    ) from exc
            from a2a_t.llm import response_parsers as llm_response_parsers

            try:
                return getattr(llm_response_parsers, name)
            except AttributeError as exc:
                raise AttributeError(
                    f"module 'a2a_t.llm' has no attribute '{name}' in this phase"
                ) from exc
        if name in {"OpenAICompatibleTransportAdapter"}:
            from a2a_t.llm import transports as llm_transports

            try:
                return getattr(llm_transports, name)
            except AttributeError as exc:
                raise AttributeError(
                    f"module 'a2a_t.llm' has no attribute '{name}' in this phase"
                ) from exc
        if name in {"PayloadBuilder"}:
            from a2a_t.llm import payload_builders as llm_payload_builders

            try:
                return getattr(llm_payload_builders, name)
            except AttributeError as exc:
                raise AttributeError(
                    f"module 'a2a_t.llm' has no attribute '{name}' in this phase"
                ) from exc
        if name in {"ResponseParser"}:
            from a2a_t.llm import response_parsers as llm_response_parsers

            try:
                return getattr(llm_response_parsers, name)
            except AttributeError as exc:
                raise AttributeError(
                    f"module 'a2a_t.llm' has no attribute '{name}' in this phase"
                ) from exc
        if name in {"TransportAdapter"}:
            from a2a_t.llm import transports as llm_transports

            try:
                return getattr(llm_transports, name)
            except AttributeError as exc:
                raise AttributeError(
                    f"module 'a2a_t.llm' has no attribute '{name}' in this phase"
                ) from exc
        if name in {"ComposedLLMAdapter"}:
            from a2a_t.llm.adapters import composed_adapter as llm_composed_adapter

            try:
                return getattr(llm_composed_adapter, name)
            except AttributeError as exc:
                raise AttributeError(
                    f"module 'a2a_t.llm' has no attribute '{name}' in this phase"
                ) from exc
        from a2a_t.llm import base as llm_base

        try:
            return getattr(llm_base, name)
        except AttributeError as exc:
            raise AttributeError(
                f"module 'a2a_t.llm' has no attribute '{name}' in this phase"
            ) from exc
    raise AttributeError(f"module 'a2a_t.llm' has no attribute '{name}'")
