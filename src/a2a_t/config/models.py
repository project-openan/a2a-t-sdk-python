"""Configuration data models for a2a_t."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from a2a_t.config.source import DotEnvConfigSource
from a2a_t.core.errors.input_limit import InputLimitConfig

logger = logging.getLogger(__name__)

#: Configuration key carrying the LLM retry attempt limit (Java ``A2ATConfigKeys.Llm.MAX_ATTEMPTS``).
LLM_MAX_ATTEMPTS_KEY: Final[str] = "A2AT_LLM_MAX_ATTEMPTS"

#: Default prompt resource source type since the 1.1.0 release flip (D10 step 2, Java
#: ``PromptRuntimeConfig.DEFAULT_SOURCE_TYPE`` = ``classpath``): out-of-the-box reads resolve to the
#: installed package resources. Users restoring the pre-1.1.0 behavior set
#: ``A2AT_PROMPT_SOURCE_TYPE=local_file`` (plus ``A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR`` for a custom
#: root).
DEFAULT_PROMPT_SOURCE_TYPE: Final[str] = "packaged"

#: Default maximum number of attempts of one retryable LLM step (Java ``LlmConfig.DEFAULT_MAX_ATTEMPTS``).
DEFAULT_LLM_MAX_ATTEMPTS: Final[int] = 3

#: Inclusive lower bound of the attempt limit; smaller configured values are clamped up to it.
LLM_MAX_ATTEMPTS_LOWER_BOUND: Final[int] = 1

#: Inclusive upper bound of the attempt limit; larger configured values are clamped down to it.
LLM_MAX_ATTEMPTS_UPPER_BOUND: Final[int] = 10


def _parse_float(raw_value: str | None, default: float) -> float:
    """Parse a float-like environment value with a fallback default."""
    if raw_value is None or not raw_value.strip():
        return default
    return float(raw_value)


def _default_prompt_resource_root_dir() -> str:
    """Return the packaged prompt resource root directory through the access layer (D8/D31).

    The import is deferred on purpose: the resource access package imports this module at import
    time, so the default-root resolution can only reach back into the access layer at call time.
    Non-filesystem layouts (zipapp) have no directory to point at; an empty string then makes
    ``local_file`` mode fail fast with a config error asking for an explicit local root, while
    ``packaged`` mode keeps working unchanged.
    """
    from a2a_t.common.prompt_resources.packaged_access import prompt_resources_root

    root = prompt_resources_root()
    return str(root.resolve()) if root is not None else ""


def _resolve_prompt_resource_root_dir(raw_value: str | None, *, base_dir: Path | None = None) -> str:
    """Resolve prompt resource roots relative to the config file when needed."""
    if raw_value is None or not raw_value.strip():
        return _default_prompt_resource_root_dir()

    candidate = Path(raw_value)
    if candidate.is_absolute():
        return str(candidate.resolve())

    resolved_base_dir = base_dir.resolve() if base_dir is not None else Path.cwd().resolve()
    return str((resolved_base_dir / candidate).resolve())


@dataclass(slots=True)
class PromptRuntimeConfig:
    """Prompt runtime configuration owned by the config package."""

    language: str = "en-US"
    source_type: str = DEFAULT_PROMPT_SOURCE_TYPE
    local_root_dir: str | None = field(default_factory=_default_prompt_resource_root_dir)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str], *, base_dir: Path | None = None) -> "PromptRuntimeConfig":
        """Build prompt runtime config from raw environment values."""
        return cls(
            language=values.get("A2AT_LANGUAGE", "en-US") or "en-US",
            source_type=values.get("A2AT_PROMPT_SOURCE_TYPE", DEFAULT_PROMPT_SOURCE_TYPE) or DEFAULT_PROMPT_SOURCE_TYPE,
            local_root_dir=_resolve_prompt_resource_root_dir(
                values.get("A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR"),
                base_dir=base_dir,
            ),
        )


@dataclass(slots=True)
class LlmRuntimeConfig:
    """Structured LLM runtime configuration resolved from unified SDK config.

    Port of the retry-relevant slice of the Java ``core/model/LlmConfig`` record (``maxAttempts``).
    The attempt limit drives the retry loop of every retryable LLM step: a step failing with one of
    the retryable codes is re-run up to ``max_attempts`` times and the exhaustion failure re-raises
    the original error code.

    Attributes:
        max_attempts: maximum number of attempts of one retryable LLM step, always within
            ``[LLM_MAX_ATTEMPTS_LOWER_BOUND, LLM_MAX_ATTEMPTS_UPPER_BOUND]``.
    """

    max_attempts: int = DEFAULT_LLM_MAX_ATTEMPTS

    def __post_init__(self) -> None:
        """Clamp the attempt limit into the allowed bounds, mirroring the Java parser warnings."""
        if self.max_attempts < LLM_MAX_ATTEMPTS_LOWER_BOUND:
            logger.warning(
                "LLM max attempts value is below the allowed minimum, clamped to bound. key=%s raw_value=%s "
                "clamped_value=%s",
                LLM_MAX_ATTEMPTS_KEY,
                self.max_attempts,
                LLM_MAX_ATTEMPTS_LOWER_BOUND,
            )
            self.max_attempts = LLM_MAX_ATTEMPTS_LOWER_BOUND
        elif self.max_attempts > LLM_MAX_ATTEMPTS_UPPER_BOUND:
            logger.warning(
                "LLM max attempts value is above the allowed maximum, clamped to bound. key=%s raw_value=%s "
                "clamped_value=%s",
                LLM_MAX_ATTEMPTS_KEY,
                self.max_attempts,
                LLM_MAX_ATTEMPTS_UPPER_BOUND,
            )
            self.max_attempts = LLM_MAX_ATTEMPTS_UPPER_BOUND

    @classmethod
    def from_mapping(cls, values: Mapping[str, str] | None) -> "LlmRuntimeConfig":
        """Build one LLM runtime config from raw ``.env`` values.

        A blank value keeps the default. A non-numeric value logs a warning and falls back to the
        default; an out-of-range value is clamped to the bound with a warning (Java
        ``LlmConfig.parseMaxAttempts`` parity).

        Args:
            values: raw config values keyed by config key.

        Returns:
            the resolved LLM runtime config.
        """
        raw_value = (values or {}).get(LLM_MAX_ATTEMPTS_KEY)
        if raw_value is None or not raw_value.strip():
            return cls(DEFAULT_LLM_MAX_ATTEMPTS)
        trimmed = raw_value.strip()
        try:
            return cls(int(trimmed))
        except ValueError:
            logger.warning(
                "LLM max attempts value is not a valid integer, falling back to default. key=%s raw_value=%s "
                "default_value=%s",
                LLM_MAX_ATTEMPTS_KEY,
                trimmed,
                DEFAULT_LLM_MAX_ATTEMPTS,
            )
            return cls(DEFAULT_LLM_MAX_ATTEMPTS)

    @classmethod
    def from_env(cls) -> "LlmRuntimeConfig":
        """Build one LLM runtime config from the process environment.

        Returns:
            the resolved LLM runtime config.
        """
        return cls.from_mapping(os.environ)


@dataclass
class A2ATConfig:
    """Global A2A-T configuration entry point."""

    prompt: PromptRuntimeConfig
    input_limits: InputLimitConfig = field(default_factory=InputLimitConfig)
    llm: LlmRuntimeConfig = field(default_factory=LlmRuntimeConfig)

    @classmethod
    def load(cls, env_path: Path) -> A2ATConfig:
        """Load the complete runtime configuration from a .env file."""
        values = DotEnvConfigSource.load(env_path)
        return cls(
            prompt=PromptRuntimeConfig.from_mapping(values, base_dir=env_path.parent),
            input_limits=InputLimitConfig.from_map(values),
            llm=LlmRuntimeConfig.from_mapping(values),
        )
