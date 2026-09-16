"""Data models for LLM integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    """Response from an LLM adapter."""

    content: str
    model: str
    usage: dict[str, int]
    metadata: dict[str, Any]
    session_id: str | None = None


@dataclass(frozen=True)
class LLMClientConfig:
    """Resolved default configuration for the shared LLM client.

    Attributes:
        reasoning_effort: optional reasoning effort for reasoning models, one of
            ``none``/``minimal``/``low``/``medium``/``high``/``xhigh`` (already normalized to lower
            case); ``None`` leaves the provider parameter unset. Java ``LLMClientConfig.reasoningEffort``.
        ssl_verify: whether to verify the TLS certificate chain and hostname of the LLM endpoint; ``False``
            disables both certificate-chain and hostname verification for HTTPS gateways whose
            certificate is not in the system trust store. Java ``LLMClientConfig.sslVerify``.
        detail_log_enabled: whether to print the full LLM request and response payloads (no truncation).
            Summary logs (timestamp, token usage, elapsed time) are recorded at DEBUG level on the
            dedicated logger ``a2a_t.llm.call`` independently of this flag. Java
            ``LLMClientConfig.detailLogEnabled``.
    """

    provider: str
    model: str
    api_key: str
    base_url: str | None
    history_window: int
    max_tokens: int | None
    temperature: float | None
    timeout_seconds: float | None
    session_max_total: int
    session_max_per_provider: int
    reasoning_effort: str | None = None
    ssl_verify: bool = True
    detail_log_enabled: bool = False
