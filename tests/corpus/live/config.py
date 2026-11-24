# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""The dedicated test-LLM configuration of the live corpus family (port of the Java
``LiveLlmConfig``; live design document §1.1).

Resolved once per process and fully decoupled from the production ``A2AT_LLM_*`` variables: the
live harness talks to a real OpenAI-compatible endpoint while the offline corpus stays
deterministic. The three required variables — ``A2AT_TEST_LLM_BASE_URL``, ``A2AT_TEST_LLM_API_KEY``
and ``A2AT_TEST_LLM_MODEL`` (the same names as Java, D24) — must all resolve; any one missing
means the live family is not configured and every live test skips through
:func:`assume_configured`. A variable that is set but blank counts as absent. The two optional
variables fall back to their documented defaults.

The values stay raw strings because their only destinations are string-keyed: the ``.env`` bridge
of :mod:`tests.corpus.live.env_writer` and the LLM client configuration. Numeric validation is
delegated to the production coercers of :mod:`a2a_t.llm.config_loader` — the gate probe reuses
exactly that validation semantics, so :meth:`LiveLlmConfig.validation_error` reports what the
production config layer would reject.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final

import pytest

from a2a_t.llm.config_loader import (
    coerce_optional_float,
    coerce_optional_int,
)
from a2a_t.llm.errors import LLMConfigError
from a2a_t.llm.factory import LLMClientFactory
from a2a_t.llm.models import LLMClientConfig
from a2a_t.llm.provider import LLMClient

__all__ = ["LiveLlmConfig", "assume_configured"]

#: Required test variable: OpenAI-compatible endpoint base URL.
BASE_URL_VARIABLE: Final[str] = "A2AT_TEST_LLM_BASE_URL"

#: Required test variable: endpoint API key.
API_KEY_VARIABLE: Final[str] = "A2AT_TEST_LLM_API_KEY"

#: Required test variable: model name.
MODEL_VARIABLE: Final[str] = "A2AT_TEST_LLM_MODEL"

#: Optional test variable: sampling temperature (default 0, stable output).
TEMPERATURE_VARIABLE: Final[str] = "A2AT_TEST_LLM_TEMPERATURE"

#: Optional test variable: request timeout in seconds (default 60).
TIMEOUT_SECONDS_VARIABLE: Final[str] = "A2AT_TEST_LLM_TIMEOUT_SECONDS"

#: Default of the optional temperature, mirroring the live design document §1.1.
DEFAULT_TEMPERATURE: Final[str] = "0"

#: Default of the optional timeout, mirroring the SDK default the live design document §1.1 keeps.
DEFAULT_TIMEOUT_SECONDS: Final[str] = "60"

#: The live family speaks the OpenAI-compatible protocol only (live design document §1.2).
PROVIDER: Final[str] = "openai"

#: Production defaults of the client-config entries the live bridge does not override (the same
#: defaults ``LLMConfigLoader`` applies when the ``.env`` omits them).
_HISTORY_WINDOW_DEFAULT: Final[int] = 10
_SESSION_MAX_TOTAL_DEFAULT: Final[int] = 300
_SESSION_MAX_PER_PROVIDER_DEFAULT: Final[int] = 100


class LiveLlmConfig:
    """The resolved live test-LLM configuration.

    The Java original resolves each variable with the lowercase-dotted system property
    (``-Da2at.test.llm.*``) taking precedence over the environment variable; the Python port
    resolves the environment alone — the system-property channel is the JUnit command-line
    counterpart, and pytest's native ``-k``/env-variable workflow needs no second channel.
    """

    __slots__ = ("base_url", "api_key", "model", "temperature", "timeout_seconds")

    def __init__(self, base_url: str, api_key: str, model: str, temperature: str, timeout_seconds: str) -> None:
        """Carry one resolved configuration.

        Raises:
            TypeError: when any argument is ``None`` (the Java ``Objects.requireNonNull`` counterpart).
        """
        for name, value in (
            ("base_url", base_url),
            ("api_key", api_key),
            ("model", model),
            ("temperature", temperature),
            ("timeout_seconds", timeout_seconds),
        ):
            if value is None:
                raise TypeError(f"LiveLlmConfig requires a non-null '{name}'.")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_current_process(cls, environ: Mapping[str, str] | None = None) -> LiveLlmConfig | None:
        """Resolve the live test configuration from the process environment.

        Args:
            environ: environment mapping to resolve from (defaults to :data:`os.environ`; the
                injection seam of the hermetic config tests).

        Returns:
            the resolved configuration, or ``None`` when the family is not configured (any
            required variable missing or blank).
        """
        variables = os.environ if environ is None else environ
        base_url = _resolve(variables, BASE_URL_VARIABLE)
        api_key = _resolve(variables, API_KEY_VARIABLE)
        model = _resolve(variables, MODEL_VARIABLE)
        if base_url is None or api_key is None or model is None:
            return None
        return cls(
            base_url,
            api_key,
            model,
            _resolve_or_default(variables, TEMPERATURE_VARIABLE, DEFAULT_TEMPERATURE),
            _resolve_or_default(variables, TIMEOUT_SECONDS_VARIABLE, DEFAULT_TIMEOUT_SECONDS),
        )

    def to_llm_client_config(self) -> LLMClientConfig:
        """Build the unified LLM client configuration through the production parsing semantics.

        The raw string values flow through the same coercers the ``.env`` loader uses, so every
        parse-error semantics of the production path applies (a non-numeric temperature or
        timeout is a configuration failure naming the offending variable).

        Raises:
            LLMConfigError: when an optional value fails the production parsing.
        """
        return LLMClientConfig(
            provider=PROVIDER,
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            history_window=_HISTORY_WINDOW_DEFAULT,
            max_tokens=coerce_optional_int(None, "A2AT_LLM_MAX_TOKENS"),
            temperature=coerce_optional_float(self.temperature, "A2AT_LLM_TEMPERATURE"),
            timeout_seconds=coerce_optional_float(self.timeout_seconds, "A2AT_LLM_TIMEOUT_SECONDS"),
            session_max_total=_SESSION_MAX_TOTAL_DEFAULT,
            session_max_per_provider=_SESSION_MAX_PER_PROVIDER_DEFAULT,
        )

    def validation_error(self) -> str | None:
        """Probe this configuration with the production validation semantics.

        Returns:
            the validation error message, or ``None`` when the configuration is legal.
        """
        try:
            self.to_llm_client_config()
            return None
        except LLMConfigError as error:
            return str(error)

    def __repr__(self) -> str:
        return (
            f"LiveLlmConfig(base_url={self.base_url!r}, model={self.model!r}, "
            f"temperature={self.temperature!r}, timeout_seconds={self.timeout_seconds!r})"
        )


def assume_configured() -> LiveLlmConfig:
    """The skip gate of every live test (live design document §1.1).

    An unconfigured environment skips the whole family through :func:`pytest.skip`, mirroring the
    JUnit assumption precedent; a configured but invalid environment is a configuration failure,
    not a skip, so the broken setup cannot hide behind a green build.

    Returns:
        the resolved live test configuration.

    Raises:
        pytest.skip.Exception: when any required variable is missing (the family is opt-in).
        RuntimeError: when the resolved configuration fails the production validation.
    """
    config = LiveLlmConfig.from_current_process()
    if config is None:
        pytest.skip(_configuration_hint())
    error = config.validation_error()
    if error is not None:
        raise RuntimeError(f"live LLM configuration is invalid: {error} ({_configuration_hint()})")
    return config


def create_llm_client(config: LiveLlmConfig) -> LLMClient:
    """Create the real provider client of the given configuration.

    ``LLMClientFactory`` instantiates the production :class:`~a2a_t.llm.providers.openai.OpenAIClient`
    for the ``openai`` provider, the exact client the facades would build from the same
    configuration. Configuration problems surface as a readable configuration failure instead of
    the raw :class:`~a2a_t.llm.errors.LLMConfigError`.

    Args:
        config: resolved live test configuration.

    Returns:
        the real OpenAI-compatible LLM client.

    Raises:
        RuntimeError: when the configuration fails the production validation.
    """
    try:
        return LLMClientFactory.create(PROVIDER, config.to_llm_client_config())
    except LLMConfigError as error:
        raise RuntimeError(f"live LLM configuration is invalid: {error} (fix the A2AT_TEST_LLM_* variables)") from error


def _resolve(variables: Mapping[str, str], name: str) -> str | None:
    """Resolve one required variable: absent or blank counts as unset, values trim."""
    value = variables.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _resolve_or_default(variables: Mapping[str, str], name: str, default: str) -> str:
    """Resolve one optional variable, falling back to its documented default."""
    value = _resolve(variables, name)
    return default if value is None else value


def _configuration_hint() -> str:
    """List what to set for users hitting the skip.

    Variables that resolve already are omitted, so a partial setup names exactly the missing
    pieces.
    """
    hint = "live LLM validation is disabled; set "
    missing = [
        name for name in (BASE_URL_VARIABLE, API_KEY_VARIABLE, MODEL_VARIABLE) if _resolve(os.environ, name) is None
    ]
    if not missing:
        missing = [BASE_URL_VARIABLE, API_KEY_VARIABLE, MODEL_VARIABLE]
    hint += ", ".join(missing) + " "
    hint += (
        f"to enable it (optional: {TEMPERATURE_VARIABLE}, default {DEFAULT_TEMPERATURE}; "
        f"{TIMEOUT_SECONDS_VARIABLE}, default {DEFAULT_TIMEOUT_SECONDS})"
    )
    return hint
