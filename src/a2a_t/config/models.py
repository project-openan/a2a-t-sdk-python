"""Configuration data models for a2a_t."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from a2a_t.config.source import DotEnvConfigSource


def _parse_bool(raw_value: str | None, default: bool) -> bool:
    if raw_value is None or not raw_value.strip():
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_float(raw_value: str | None, default: float) -> float:
    if raw_value is None or not raw_value.strip():
        return default
    return float(raw_value)


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
    history_window: int = 10
    session_store_type: str = "memory"


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


@dataclass(slots=True)
class PromptRuntimeConfig:
    """Prompt runtime configuration owned by the config package."""

    language: str = "en-US"
    prompt_resource_version: str = "0.0.1"
    source_type: str = "local_file"
    local_root_dir: str = "./package_data/prompt_resources"

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "PromptRuntimeConfig":
        return cls(
            language=values.get("A2AT_LANGUAGE", "en-US") or "en-US",
            prompt_resource_version=values.get("A2AT_PROMPT_RESOURCE_VERSION", "0.0.1") or "0.0.1",
            source_type=values.get("A2AT_PROMPT_SOURCE_TYPE", "local_file") or "local_file",
            local_root_dir=values.get(
                "A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR",
                "./package_data/prompt_resources",
            )
            or "./package_data/prompt_resources",
        )


@dataclass(slots=True)
class GuardrailProviderConfig:
    """Provider configuration for safety guardrail adapters."""

    provider: str = "noop"
    timeout: float = 10.0
    policy_id: str = ""
    endpoint: str = ""
    region: str = ""
    credentials_ref: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PromptComplianceConfig:
    """Top-level configuration for prompt compliance."""

    enabled: bool = False
    guardrail: GuardrailProviderConfig = field(default_factory=GuardrailProviderConfig)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "PromptComplianceConfig":
        return cls(
            enabled=_parse_bool(values.get("A2AT_PROMPT_COMPLIANCE_ENABLED"), False),
            guardrail=GuardrailProviderConfig(
                provider=values.get("A2AT_PROMPT_COMPLIANCE_GUARDRAIL_PROVIDER", "noop") or "noop",
                timeout=_parse_float(
                    values.get("A2AT_PROMPT_COMPLIANCE_GUARDRAIL_TIMEOUT_SECONDS"),
                    10.0,
                ),
                policy_id=values.get("A2AT_PROMPT_COMPLIANCE_GUARDRAIL_POLICY_ID", "") or "",
                endpoint=values.get("A2AT_PROMPT_COMPLIANCE_GUARDRAIL_ENDPOINT", "") or "",
                region=values.get("A2AT_PROMPT_COMPLIANCE_GUARDRAIL_REGION", "") or "",
                credentials_ref=values.get("A2AT_PROMPT_COMPLIANCE_GUARDRAIL_CREDENTIALS_REF", "") or "",
            ),
        )


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
    def from_dict(cls, data: dict[str, Any]) -> "SDKConfig":
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
                guardrail=GuardrailProviderConfig(**prompt_compliance_data.get("guardrail", {})),
                providers=prompt_compliance_data.get("providers", {}),
            ),
            log_level=data.get("log_level", "INFO"),
        )


@dataclass
class A2ATConfig:
    """全局 A2A-T 配置入口 / Global A2A-T configuration entry point."""

    prompt: PromptRuntimeConfig
    prompt_compliance: PromptComplianceConfig

    @classmethod
    def load(cls, env_path: Path) -> A2ATConfig:
        values = DotEnvConfigSource.load(env_path)
        return cls(
            prompt=PromptRuntimeConfig.from_mapping(values),
            prompt_compliance=PromptComplianceConfig.from_mapping(values),
        )
