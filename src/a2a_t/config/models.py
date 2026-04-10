"""Configuration data models for a2a_t."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from a2a_t.server.prompt_compliance.models import (
    PromptComplianceConfig,
    PromptComplianceProviderConfig,
    SlotExtractionConfig,
    SlotSchemaConfig,
)


@dataclass
class ClientConfig:
    """Configuration for A2A client."""

    url: str = ""
    timeout: float = 30.0
    max_retries: int = 3
    pool_size: int = 10
    pool_max_size: int = 20


@dataclass
class ServerConfig:
    """Configuration for A2A server."""

    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = 4
    max_connections: int = 100
    rate_limit_requests: int = 100
    rate_limit_window: float = 60.0


@dataclass
class LLMConfig:
    """Configuration for LLM integration."""

    adapter_type: str = "http"
    base_url: str = ""
    api_key: str = ""
    model: str = "default"
    max_tokens: int = 2000


@dataclass
class PromptConfig:
    """Configuration for prompt management."""

    templates_dir: str = "./templates"
    cache_enabled: bool = True
    cache_ttl: float = 3600.0
    cache_max_size: int = 100
    remote_enabled: bool = False


@dataclass
class CompressionConfig:
    """Configuration for compression."""

    enabled: bool = True
    strategy: str = "keyword_extractor"
    max_ratio: float = 0.5


@dataclass
class SDKConfig:
    """Main SDK configuration."""

    client: ClientConfig = field(default_factory=ClientConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    compression: CompressionConfig = field(default_factory=CompressionConfig)
    prompt_compliance: PromptComplianceConfig = field(default_factory=PromptComplianceConfig)
    log_level: str = "INFO"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SDKConfig:
        """Create config from dictionary."""
        prompt_compliance_data = data.get("prompt_compliance", {})
        return cls(
            client=ClientConfig(**data.get("client", {})),
            server=ServerConfig(**data.get("server", {})),
            llm=LLMConfig(**data.get("llm", {})),
            prompt=PromptConfig(**data.get("prompt", {})),
            compression=CompressionConfig(**data.get("compression", {})),
            prompt_compliance=PromptComplianceConfig(
                enabled=prompt_compliance_data.get("enabled", False),
                guardrail=PromptComplianceProviderConfig(**prompt_compliance_data.get("guardrail", {})),
                slot_extraction=SlotExtractionConfig(**prompt_compliance_data.get("slot_extraction", {})),
                slot_schema=SlotSchemaConfig(**prompt_compliance_data.get("slot_schema", {})),
                providers=prompt_compliance_data.get("providers", {}),
            ),
            log_level=data.get("log_level", "INFO"),
        )
