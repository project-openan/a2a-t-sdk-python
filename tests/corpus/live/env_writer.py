"""The live ``.env`` bridge of the live corpus family (port of the Java ``LiveLlmEnvWriter``).

The SDK config only accepts a caller-provided ``.env`` file, so the live harness resolves
:meth:`~tests.corpus.live.config.LiveLlmConfig` and materializes it exactly once per distinct
configuration into a temporary directory, following the
:class:`~tests.corpus.assemblers.TaskApiAssembler` env-file precedent.

Entry-by-entry rationale: the prompt resources come from the packaged tree with an empty local
override root ([R2]); the LLM entries carry the real test-endpoint values with an explicit
temperature, timeout and retry limit ([R7] — an unset temperature would let the server-side
default decide, which is unstable); language and state store mirror the minimal facade env. The
language is fixed to ``zh-CN`` because live phase 1 covers zh-CN only (the loader enforces it).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from threading import Lock
from typing import Final

from tests.corpus.live.config import LiveLlmConfig

__all__ = ["LANGUAGE", "MAX_ATTEMPTS", "env_file_for"]

#: Fixed retry limit of the live runs ([R6]: not per-record, it comes from the env bridge).
MAX_ATTEMPTS: Final[str] = "3"

#: Language of the live ``.env`` bridge: live phase 1 covers zh-CN only.
LANGUAGE: Final[str] = "zh-CN"

#: Written env-file cache, keyed by the distinct-configuration cache key (Java ``ENV_FILES`` parity).
_ENV_FILES: dict[str, Path] = {}

_ENV_FILES_LOCK = Lock()


def env_file_for(config: LiveLlmConfig) -> Path:
    """Write (once per distinct configuration) the live ``.env`` file the live harness hands to
    the SDK config loading, mirroring the facade-test minimal-env precedent.

    Args:
        config: resolved live test configuration.

    Returns:
        path of the written ``.env`` file.
    """
    key = _cache_key(config)
    with _ENV_FILES_LOCK:
        cached = _ENV_FILES.get(key)
        if cached is not None:
            return cached
        env_file = Path(tempfile.mkdtemp(prefix="a2at-corpus-live-env")) / "live.env"
        env_file.write_text(
            "\n".join(
                (
                    f"A2AT_LANGUAGE={LANGUAGE}",
                    "A2AT_PROMPT_SOURCE_TYPE=packaged",
                    "A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR=",
                    "A2AT_LLM_PROVIDER=openai",
                    f"A2AT_LLM_MODEL={config.model}",
                    f"A2AT_LLM_API_KEY={config.api_key}",
                    f"A2AT_LLM_BASE_URL={config.base_url}",
                    f"A2AT_LLM_TEMPERATURE={config.temperature}",
                    f"A2AT_LLM_TIMEOUT_SECONDS={config.timeout_seconds}",
                    f"A2AT_LLM_MAX_ATTEMPTS={MAX_ATTEMPTS}",
                    "",
                )
            ),
            encoding="utf-8",
            newline="\n",
        )
        _ENV_FILES[key] = env_file
        return env_file


def _cache_key(config: LiveLlmConfig) -> str:
    """Distinct-configuration key: every value the bridge writes (Java parity)."""
    return "\n".join((config.base_url, config.api_key, config.model, config.temperature, config.timeout_seconds))
