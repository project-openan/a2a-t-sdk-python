"""Unified runtime configuration of the accuracy verification corpus (port of the Java ``CorpusEnvConfig``).

The configuration keys reuse the project-root ``env.example`` naming (``A2AT_LANGUAGE`` and the
``A2AT_LLM_*`` family), so the corpus ``env.example`` is a tuned subset of the root template and
one set of names serves both production and testing. One shared configuration serves every -T
extension suite. ``A2AT_LANGUAGE`` selects both the prompt-resource language and the language-
specific case-file variant (``input_case_from_*_en-US.json``); it defaults to ``zh-CN`` so the
committed Chinese corpus keeps working unchanged.

Resolution order: process environment variables > the ``.env`` file next to this module
(Python has no ``-D`` system-property channel; the environment is the override channel). Blank
values count as unset. This module must run against a real LLM; a missing or invalid configuration
therefore fails fast with an actionable error instead of producing silently wrong results. The
configuration loads lazily on the first workflow execution, so CI runs that deselect the live
suites never touch it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

from dotenv import dotenv_values
from engine.constants import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

__all__ = ["CorpusEnvConfig", "resolve_language"]

#: Environment-file keys (also accepted as process environment variables); names mirror the
#: project-root ``env.example``.
ENV_LANGUAGE: Final[str] = "A2AT_LANGUAGE"
ENV_PROVIDER: Final[str] = "A2AT_LLM_PROVIDER"
ENV_BASE_URL: Final[str] = "A2AT_LLM_BASE_URL"
ENV_API_KEY: Final[str] = "A2AT_LLM_API_KEY"
ENV_MODEL: Final[str] = "A2AT_LLM_MODEL"
ENV_TEMPERATURE: Final[str] = "A2AT_LLM_TEMPERATURE"
ENV_TIMEOUT_SECONDS: Final[str] = "A2AT_LLM_TIMEOUT_SECONDS"
ENV_MAX_ATTEMPTS: Final[str] = "A2AT_LLM_MAX_ATTEMPTS"
ENV_MAX_TOKENS: Final[str] = "A2AT_LLM_MAX_TOKENS"

#: The only provider the corpus speaks (every -T extension shares the OpenAI-compatible protocol).
DEFAULT_PROVIDER: Final[str] = "openai"

#: Default of the retryable LLM step attempt limit (Java ``DEFAULT_MAX_ATTEMPTS``).
DEFAULT_MAX_ATTEMPTS: Final[int] = 3


def _env_file() -> Path:
    """Return the corpus ``.env`` path regardless of whether the file exists."""
    return Path(__file__).resolve().parent.parent / ".env"


def _language_problem(language: str) -> str | None:
    """Return the validation problem of one prompt language, or ``None`` when it is supported."""
    if language in SUPPORTED_LANGUAGES:
        return None
    supported = ", ".join(sorted(SUPPORTED_LANGUAGES))
    return f"- {ENV_LANGUAGE} must be one of: {supported} (currently \"{language}\")"


def _resolve_language(env_file_values: dict[str, str | None]) -> str:
    """Resolve the prompt language from the environment over the ``.env`` file."""
    return _resolve(ENV_LANGUAGE, env_file_values) or DEFAULT_LANGUAGE


def resolve_language(env_path: Path | None = None) -> str:
    """Resolve only the corpus prompt language, without validating the LLM configuration.

    The corpus selects its language-specific case files at pytest collection time, before the
    lazy :class:`CorpusEnvConfig` runtime loading. This helper therefore touches only
    ``A2AT_LANGUAGE``; it keeps the CI collection path free of the fail-fast LLM checks.

    Args:
        env_path: optional ``.env`` path; defaults to the corpus ``.env`` file.

    Returns:
        the configured language, or ``zh-CN`` when unset.

    Raises:
        RuntimeError: when the configured language is not supported by the bundled resources.
    """
    values = dotenv_values(env_path or _env_file())
    language = _resolve_language(values)
    problem = _language_problem(language)
    if problem:
        source = env_path or _env_file()
        raise RuntimeError(
            f"a2a-t-corpus has an invalid prompt-language configuration in {source}:\n"
            f"{problem}\nSet {ENV_LANGUAGE} in the environment or the corpus .env file."
        )
    return language


@lru_cache(maxsize=1)
def _create() -> CorpusEnvConfig:
    """Resolve and validate the configuration exactly once per process."""
    env_file_values = dotenv_values(_env_file())

    language = _resolve_language(env_file_values)
    provider = _resolve(ENV_PROVIDER, env_file_values)
    if not provider:
        provider = DEFAULT_PROVIDER
    base_url = _resolve(ENV_BASE_URL, env_file_values)
    api_key = _resolve(ENV_API_KEY, env_file_values)
    model = _resolve(ENV_MODEL, env_file_values)

    problems: list[str] = []
    language_problem = _language_problem(language)
    if language_problem:
        problems.append(language_problem)
    if provider != DEFAULT_PROVIDER:
        problems.append(f"- {ENV_PROVIDER} must be \"{DEFAULT_PROVIDER}\" (currently \"{provider}\")")
    if not base_url:
        problems.append(f"- {ENV_BASE_URL} is not configured")
    if not api_key:
        problems.append(f"- {ENV_API_KEY} is not configured")
    if not model:
        problems.append(f"- {ENV_MODEL} is not configured")
    if problems:
        raise RuntimeError(
            "a2a-t-corpus requires a real LLM configuration; invalid or missing values:\n"
            + "\n".join(problems)
            + f"\nCopy a2a-t-corpus/env.example to {_env_file()} and fill in the required"
            + " entries (or override them with environment variables)."
            + "\nCI pipelines should exclude this module with -m 'not live'."
        )

    return CorpusEnvConfig(
        language=language,
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=_resolve_optional_float(ENV_TEMPERATURE, env_file_values),
        timeout_seconds=_resolve_optional_float(ENV_TIMEOUT_SECONDS, env_file_values),
        max_attempts=_resolve_max_attempts(env_file_values),
        max_tokens=_resolve_optional_int(ENV_MAX_TOKENS, env_file_values),
    )


def _resolve(key: str, env_file_values: dict[str, str | None]) -> str:
    """Resolve one variable: environment > ``.env`` file; blank values count as unset."""
    from_environment = os.environ.get(key)
    if from_environment is not None and from_environment.strip():
        return from_environment.strip()
    from_file = env_file_values.get(key)
    if from_file is not None and str(from_file).strip():
        return str(from_file).strip()
    return ""


def _resolve_optional_float(key: str, env_file_values: dict[str, str | None]) -> float | None:
    raw = _resolve(key, env_file_values)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"{key} must be numeric: '{raw}'") from None


def _resolve_optional_int(key: str, env_file_values: dict[str, str | None]) -> int | None:
    raw = _resolve(key, env_file_values)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{key} must be an integer: '{raw}'") from None


def _resolve_max_attempts(env_file_values: dict[str, str | None]) -> int:
    raw = _resolve(ENV_MAX_ATTEMPTS, env_file_values)
    if not raw:
        return DEFAULT_MAX_ATTEMPTS
    try:
        parsed = int(raw)
    except ValueError:
        raise RuntimeError(
            f"{ENV_MAX_ATTEMPTS} must be an integer between 1 and 10: '{raw}'"
        ) from None
    if parsed < 1 or parsed > 10:
        raise RuntimeError(f"{ENV_MAX_ATTEMPTS} must be between 1 and 10: {parsed}")
    return parsed


@dataclass(frozen=True, slots=True)
class CorpusEnvConfig:
    """Resolved corpus test runtime configuration.

    Attributes:
        language: prompt-resource and case-file language, ``zh-CN`` or ``en-US``.
        provider: LLM provider, always ``openai``.
        base_url: OpenAI-compatible endpoint base URL.
        api_key: endpoint API key.
        model: model name.
        temperature: optional sampling temperature.
        timeout_seconds: optional request timeout in seconds.
        max_attempts: attempt limit of retryable LLM steps, always within 1-10.
        max_tokens: optional maximum number of completion tokens.
    """

    language: str
    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float | None
    timeout_seconds: float | None
    max_attempts: int
    max_tokens: int | None

    @classmethod
    def load(cls) -> CorpusEnvConfig:
        """Return the process-wide singleton configuration, resolving it on first use."""
        return _create()
