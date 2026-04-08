from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta


@dataclass(slots=True)
class PromptLoaderConfig:
    """定义 Prompt Loader 的运行时配置 / Define runtime configuration for the prompt loader."""

    default_ttl: timedelta
    cache_dir: str | None = None
    allow_stale_fallback: bool = True
    default_prompt_extension_uri: str | None = None
    prompt_extension_uri_overrides: dict[str, str] = field(default_factory=dict)
    default_prompt_index_url_param_key: str = "promptIndexUrl"
    prompt_index_url_param_key_overrides: dict[str, str] = field(default_factory=dict)
