"""Per-run SDK ``.env`` construction shared by the client and server runtimes.

The A2A-T SDK reads its configuration from a file, never from process environment variables, so
both runtimes hand it a temporary copy of the sample ``.env`` instead of mutating the source file.
The builder lives in ``shared`` so neither runtime has to import the other: the server used to
delegate to :mod:`protocol.client_runtime`, and the client used to import the server assembly just
for its AgentCard constants (which built an unused ADK model at import time).

Offline mode pins the traced scripted provider; live mode keeps the SDK endpoint values of the
source file and fills any missing one from the ADK model configuration, so both SDK and ADK address
the same endpoint (design §5.1).

Both modes also write the sample's live fail-fast policy into the SDK ``.env``:
``A2AT_LLM_TIMEOUT_SECONDS`` and ``A2AT_LLM_MAX_ATTEMPTS`` come from the sample configuration and
always override the source values. A live server gate may perform several LLM calls before it emits
any event, while the client gives up after ``A2AT_SAMPLE_HTTP_TIMEOUT_SECONDS``; letting the SDK
retry a slow call for minutes would therefore surface as a transport timeout
(``A2AClientTimeoutError``) instead of a structured protocol message. The timeout is per LLM call
(120s by default, raised for slower endpoints); ``A2AT_LLM_MAX_ATTEMPTS`` is the SDK's
protocol-level retry budget, while ``TracedOpenAIClient`` disables the OpenAI client's own
transport retries so this budget stays predictable.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from dotenv import dotenv_values

from shared.config import MODEL_MODE_OFFLINE, SAMPLE_ROOT, SampleConfig, merge_sample_process_env
from shared.traced_llm import register_traced_providers, sdk_provider_name

#: Prefix of the temporary SDK ``.env`` directories.
DEFAULT_ENV_PREFIX: Final[str] = "a2at-ran-sdk-env-"

#: Sample-owned overlay of the SDK prompt resources. Only the slot schemas the sample must refine
#: live here; every other resource falls back to the packaged copy (``local_file`` overlay).
SAMPLE_PROMPT_RESOURCES: Final[Path] = SAMPLE_ROOT / "prompt_resources"


@dataclass(frozen=True)
class SdkEnvFile:
    """Per-run SDK ``.env`` built for one A2ATClient/A2ATServer.

    Attributes:
        path: Path of the generated ``.env`` file.
        directory: Temporary directory owning the file (removed by the client runtime).
    """

    path: Path
    directory: Path


def _env_values(env_path: Path | None) -> dict[str, str]:
    """Read the source ``.env`` values with the shell overlay (empty when the file is missing)."""
    if env_path is None or not env_path.exists():
        values: dict[str, str] = {}
    else:
        values = {
            str(key): str(value)
            for key, value in dotenv_values(env_path).items()
            if key is not None and value is not None
        }
    return merge_sample_process_env(values)


def _sdk_value(name_env: str, sdk_key: str, config_value: str, values: Mapping[str, str]) -> str:
    """Resolve one SDK endpoint value with the sample precedence.

    A shell ``A2AT_LLM_*`` export wins directly. Otherwise a shell ``ADK_MODEL_*`` export makes the
    resolved ADK configuration win over a stale source ``A2AT_LLM_*`` value, so exporting only the
    ADK model keys is enough to switch the whole client to one endpoint. Without any shell model
    key, a source ``A2AT_LLM_*`` value still allows the SDK and ADK to use different endpoints.
    """
    shell_sdk = os.environ.get(sdk_key, "").strip()
    if shell_sdk:
        return shell_sdk
    if os.environ.get(name_env, "").strip():
        return config_value
    return values.get(sdk_key) or config_value


def _sdk_model_name(adk_model_name: str) -> str:
    """Return the SDK model id matching one LiteLlm model id (drops the litellm provider prefix)."""
    return adk_model_name.removeprefix("openai/") if adk_model_name else ""


def prepare_sdk_env(
    config: SampleConfig,
    env_path: Path | None = None,
    *,
    prefix: str = DEFAULT_ENV_PREFIX,
) -> SdkEnvFile:
    """Build a per-run SDK ``.env`` without mutating the caller's file.

    ``A2AT_LANGUAGE`` always follows the process-level configuration. Offline mode pins the
    scripted provider; live mode keeps the SDK endpoint values of the source file and fills any
    missing one from the ADK model configuration, so both SDK and ADK address the same endpoint.

    The SDK prompt source is ``packaged`` unless ``config.prompt_overlay`` is set; an inherited
    ``local_file`` root is dropped so the packaged slot schema stays the shared baseline.

    Args:
        config: resolved sample configuration.
        env_path: source ``.env``; defaults to ``$CWD/.env``.
        prefix: prefix of the temporary directory (the caller owns its cleanup).

    Returns:
        The generated file and the temporary directory owning it.
    """
    # The generated file names a traced provider, so the matching registration is part of the
    # contract of this helper (a caller may build the facade right after it, as the tests do).
    register_traced_providers()
    source_path = env_path or Path.cwd() / ".env"
    values = _env_values(source_path)
    values["A2AT_LANGUAGE"] = config.language
    if config.model_mode == MODEL_MODE_OFFLINE:
        values.update(
            {
                "A2AT_LLM_PROVIDER": sdk_provider_name(offline=True),
                "A2AT_LLM_MODEL": "scripted",
                "A2AT_LLM_API_KEY": "not-needed",
                "A2AT_LLM_BASE_URL": values.get("A2AT_LLM_BASE_URL") or config.adk_model_api_base,
            }
        )
    else:
        values.update(
            {
                # The traced provider is a drop-in replacement of the SDK OpenAI provider; the Run
                # Explorer must see every structured call, so the source value never wins.
                "A2AT_LLM_PROVIDER": sdk_provider_name(offline=False),
                "A2AT_LLM_MODEL": _sdk_value(
                    "ADK_MODEL_NAME", "A2AT_LLM_MODEL", _sdk_model_name(config.adk_model_name), values
                ),
                "A2AT_LLM_API_KEY": _sdk_value(
                    "ADK_MODEL_API_KEY", "A2AT_LLM_API_KEY", config.adk_model_api_key, values
                ),
                "A2AT_LLM_BASE_URL": _sdk_value(
                    "ADK_MODEL_API_BASE", "A2AT_LLM_BASE_URL", config.adk_model_api_base, values
                ),
            }
        )
    values.update(
        {
            # The sample's fail-fast policy always wins over the source file: a slow or retrying
            # live LLM call must finish well inside the client's HTTP budget.
            "A2AT_LLM_TIMEOUT_SECONDS": f"{config.llm_timeout_seconds:g}",
            "A2AT_LLM_MAX_ATTEMPTS": str(config.llm_max_attempts),
        }
    )
    if config.prompt_overlay and SAMPLE_PROMPT_RESOURCES.is_dir():
        # The overlay refines the sample's own slot schema (placement rules); the SDK's packaged
        # prompts and every other resource still win, so the generic conversion contract is intact.
        values["A2AT_PROMPT_SOURCE_TYPE"] = "local_file"
        values["A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR"] = str(SAMPLE_PROMPT_RESOURCES)
    else:
        # Deterministic packaged baseline: do not inherit a stale local_file/root from the source
        # .env or the shell. A2AT_SAMPLE_PROMPT_OVERLAY=1 is the explicit opt-in.
        values["A2AT_PROMPT_SOURCE_TYPE"] = "packaged"
        values.pop("A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR", None)
    temp_dir = Path(tempfile.mkdtemp(prefix=prefix))
    target = temp_dir / ".env"
    target.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    return SdkEnvFile(path=target, directory=temp_dir)


__all__ = ["DEFAULT_ENV_PREFIX", "SdkEnvFile", "prepare_sdk_env"]
