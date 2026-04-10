"""gRPC adapter for LLM integration."""

from __future__ import annotations

from typing import Any

from a2a_t.llm.base import LLMAdapter, LLMResponse


class GrpcAdapter(LLMAdapter):
    """LLM adapter using gRPC protocol."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._host = config.get("host", "localhost")
        self._port = config.get("port", 50051)
        self._model = config.get("model", "default")

    @property
    def adapter_type(self) -> str:
        return "grpc"

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        """Generate completion via gRPC."""
        raise NotImplementedError("gRPC adapter requires backend integration")

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        """Generate chat completion via gRPC."""
        raise NotImplementedError("gRPC adapter requires backend integration")

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, Any], **kwargs: Any) -> LLMResponse:
        """Generate structured output via gRPC."""
        raise NotImplementedError("gRPC adapter requires backend integration")
