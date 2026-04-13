from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Mapping

from .errors import PromptConfigError


@dataclass(slots=True)
class PromptLoaderConfig:
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
    def from_mapping(cls, values: Mapping[str, str]) -> "PromptLoaderConfig":
        raw_extensions = values.get("A2AT_PROMPT_ALLOWED_EXTENSIONS", ".md") or ".md"
        raw_ttl_seconds = values.get("A2AT_PROMPT_DEFAULT_TTL_SECONDS", "3600") or "3600"
        return cls(
            default_ttl=timedelta(seconds=cls._parse_ttl_seconds(raw_ttl_seconds)),
            local_prompt_dir=values.get("A2AT_PROMPT_LOCAL_DIR", "./prompts") or "./prompts",
            allowed_extensions=[item.strip() for item in raw_extensions.split(",") if item.strip()],
            default_prompt_extension_uri=values.get("A2AT_DEFAULT_PROMPT_EXTENSION_URI", "default-prompt")
            or "default-prompt",
            prompt_extension_uri_overrides=cls._parse_json_mapping(values, "A2AT_PROMPT_EXTENSION_URI_OVERRIDES"),
            default_prompt_index_url_param_key=(
                values.get("A2AT_DEFAULT_PROMPT_INDEX_URL_PARAM_KEY", "promptIndexUrl") or "promptIndexUrl"
            ),
            prompt_index_url_param_key_overrides=cls._parse_json_mapping(
                values,
                "A2AT_PROMPT_INDEX_URL_PARAM_KEY_OVERRIDES",
            ),
        )

    @staticmethod
    def _parse_ttl_seconds(raw_value: str) -> int:
        try:
            ttl_seconds = int(raw_value)
        except ValueError as exc:
            raise PromptConfigError(
                "Prompt default ttl seconds must be an integer.",
                field="A2AT_PROMPT_DEFAULT_TTL_SECONDS",
                value=raw_value,
            ) from exc
        if ttl_seconds <= 0:
            raise PromptConfigError(
                "Prompt default ttl seconds must be positive.",
                field="A2AT_PROMPT_DEFAULT_TTL_SECONDS",
                value=raw_value,
            )
        return ttl_seconds

    @staticmethod
    def _parse_json_mapping(values: Mapping[str, str], key: str) -> dict[str, str]:
        raw_value = values.get(key)
        if raw_value is None or not raw_value.strip():
            return {}

        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise PromptConfigError(
                f"Prompt env json mapping is invalid for {key}.",
                field=key,
                value=raw_value,
            ) from exc

        if not isinstance(parsed, dict) or not all(
            isinstance(item_key, str) and isinstance(item_value, str)
            for item_key, item_value in parsed.items()
        ):
            raise PromptConfigError(
                f"Prompt env json mapping must be a string map for {key}.",
                field=key,
                value=raw_value,
            )
        return parsed
