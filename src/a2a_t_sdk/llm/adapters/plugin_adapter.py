"""Plugin adapter for LLM integration."""

from __future__ import annotations

from typing import Any

from a2a_t_sdk.llm.base import LLMAdapter, LLMResponse


class PluginAdapter(LLMAdapter):
    """LLM adapter using plugin/extension mechanism."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._plugin_path = config.get("plugin_path", "")
        self._plugin_config = config.get("plugin_config", {})

    @property
    def adapter_type(self) -> str:
        return "plugin"

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        """Generate completion via plugin."""
        raise NotImplementedError("Plugin adapter requires backend integration")

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        """Generate chat completion via plugin."""
        raise NotImplementedError("Plugin adapter requires backend integration")
