from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.base import ComposedLLMAdapter, PayloadBuilder, ResponseParser, TransportAdapter
from a2a_t.llm.models import ChatMessage, LLMResponse


class RecordingTransport(TransportAdapter):
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def invoke(self, *, payload: dict[str, Any]) -> Any:
        self.payloads.append(payload)
        return {"payload": payload, "call_index": len(self.payloads)}


class RecordingPayloadBuilder(PayloadBuilder):
    def __init__(self) -> None:
        self.complete_calls: list[dict[str, Any]] = []
        self.chat_calls: list[dict[str, Any]] = []
        self.structured_calls: list[dict[str, Any]] = []

    def build_complete(
        self,
        *,
        model: str,
        prompt: str,
        system_prompt: str | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        call = {
            "model": model,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        self.complete_calls.append(call)
        return {"kind": "complete", **call}

    def build_chat(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        call = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        self.chat_calls.append(call)
        return {
            "kind": "chat",
            "model": model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

    def build_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        call = {
            "model": model,
            "messages": messages,
            "json_schema": json_schema,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        self.structured_calls.append(call)
        return {"kind": "structured", **call}


class RecordingResponseParser(ResponseParser):
    def __init__(self) -> None:
        self.complete_calls: list[dict[str, Any]] = []
        self.chat_calls: list[dict[str, Any]] = []
        self.structured_calls: list[dict[str, Any]] = []

    def parse_complete(self, *, response: Any, model: str) -> LLMResponse:
        self.complete_calls.append({"response": response, "model": model})
        return LLMResponse(content="complete-ok", model=model, usage={}, metadata={"raw": response})

    def parse_chat(self, *, response: Any, model: str) -> LLMResponse:
        self.chat_calls.append({"response": response, "model": model})
        return LLMResponse(content="chat-ok", model=model, usage={}, metadata={"raw": response})

    def parse_structured(self, *, response: Any, model: str) -> LLMResponse:
        self.structured_calls.append({"response": response, "model": model})
        return LLMResponse(content="{}", model=model, usage={}, metadata={"raw": response})


class ComposedLLMAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = RecordingTransport()
        self.payload_builder = RecordingPayloadBuilder()
        self.response_parser = RecordingResponseParser()
        self.adapter = ComposedLLMAdapter(
            config={"model": "demo-model", "provider": "demo", "history_window": 2},
            transport=self.transport,
            payload_builder=self.payload_builder,
            response_parser=self.response_parser,
        )

    def test_complete_delegates_to_builder_transport_and_parser(self) -> None:
        response = self.adapter.complete(
            "say hi",
            system_prompt="be concise",
            temperature=0.3,
            max_tokens=12,
        )

        self.assertEqual(response.content, "complete-ok")
        self.assertEqual(len(self.payload_builder.complete_calls), 1)
        self.assertEqual(self.payload_builder.complete_calls[0]["prompt"], "say hi")
        self.assertEqual(self.transport.payloads[0]["kind"], "complete")
        self.assertEqual(self.response_parser.complete_calls[0]["model"], "demo-model")

    def test_structured_delegates_to_builder_transport_and_parser(self) -> None:
        response = self.adapter.structured(
            messages=[{"role": "user", "content": "extract router"}],
            json_schema={"type": "object"},
            temperature=0.2,
            max_tokens=9,
        )

        self.assertEqual(response.content, "{}")
        self.assertEqual(len(self.payload_builder.structured_calls), 1)
        self.assertEqual(self.payload_builder.structured_calls[0]["json_schema"], {"type": "object"})
        self.assertEqual(self.transport.payloads[0]["kind"], "structured")
        self.assertEqual(self.response_parser.structured_calls[0]["model"], "demo-model")

    def test_chat_uses_base_session_flow_and_composed_transport_path(self) -> None:
        first = self.adapter.chat("hello", system_prompt="be concise", temperature=0.1, max_tokens=7)
        second = self.adapter.chat("again", session_id=first.session_id, temperature=0.1, max_tokens=7)

        self.assertIsNotNone(first.session_id)
        self.assertEqual(second.session_id, first.session_id)
        self.assertEqual(len(self.payload_builder.chat_calls), 2)
        first_messages = self.payload_builder.chat_calls[0]["messages"]
        second_messages = self.payload_builder.chat_calls[1]["messages"]
        self.assertEqual([item.role for item in first_messages], ["system", "user"])
        self.assertEqual([item.role for item in second_messages], ["system", "user", "assistant", "user"])
        self.assertEqual(second_messages[1].content, "hello")
        self.assertEqual(second_messages[2].content, "chat-ok")
        self.assertEqual(self.transport.payloads[1]["kind"], "chat")
        self.assertEqual(self.response_parser.chat_calls[1]["model"], "demo-model")


if __name__ == "__main__":
    unittest.main()
