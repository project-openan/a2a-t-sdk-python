from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from a2a_t.config.env import EnvConfig
from .errors import PromptConfigError


@dataclass(slots=True)
class PromptLoaderConfig:
    """定义 Prompt Loader 的运行时配置 / Define runtime configuration for the prompt loader."""

    default_ttl: timedelta
    local_prompt_dir: str = "./prompts"
    allowed_extensions: list[str] = field(default_factory=lambda: [".md"])
    default_prompt_extension_uri: str | None = None
    prompt_extension_uri_overrides: dict[str, str] = field(default_factory=dict)
    default_prompt_index_url_param_key: str = "promptIndexUrl"
    prompt_index_url_param_key_overrides: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.local_prompt_dir:
            raise PromptConfigError("Prompt local prompt dir is required.", field="local_prompt_dir")
        if not self.allowed_extensions:
            raise PromptConfigError("Prompt allowed extensions are required.", field="allowed_extensions")

    @classmethod
    def from_env(cls, env: EnvConfig) -> "PromptLoaderConfig":
        raw_extensions = env.get("A2AT_PROMPT_ALLOWED_EXTENSIONS", ".md") or ".md"
        return cls(
            default_ttl=timedelta(hours=1),
            local_prompt_dir=env.get("A2AT_PROMPT_LOCAL_DIR", "./prompts") or "./prompts",
            allowed_extensions=[item.strip() for item in raw_extensions.split(",") if item.strip()],
        )
