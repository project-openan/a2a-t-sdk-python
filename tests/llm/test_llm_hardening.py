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

"""Tests of the LLM-layer hardening (D18): retry attempt limit key and reasoning-effort guard.

Covers the three additions of the port: the ``A2AT_LLM_MAX_ATTEMPTS`` configuration key resolved
by :class:`a2a_t.config.models.LlmRuntimeConfig` with the Java clamp semantics (default 3, bounds
``[1, 10]``, non-numeric fallback with a warning), the validated ``A2AT_LLM_REASONING_EFFORT``
value forwarded to the OpenAI-compatible provider, and the empty-response guard of the reasoning
models — a blank content raises a coded response-contract violation (which the boundaries resolve
to ``llm.response_invalid`` through :func:`a2a_t.llm.errors.is_response_contract_violation`)
instead of a silent empty string.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from a2a_t.config.models import (
    DEFAULT_LLM_MAX_ATTEMPTS,
    LLM_MAX_ATTEMPTS_KEY,
    LlmRuntimeConfig,
)
from a2a_t.llm.config_loader import LLMConfigLoader, coerce_reasoning_effort
from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError, is_response_contract_violation
from a2a_t.llm.models import LLMClientConfig
from a2a_t.llm.providers.openai import OpenAIClient


def build_client_config(reasoning_effort: str | None = None) -> LLMClientConfig:
    """Build one OpenAI client config carrying only the fields the provider requires."""
    return LLMClientConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-test",
        base_url="https://api.example.test/v1",
        history_window=10,
        max_tokens=None,
        temperature=None,
        timeout_seconds=None,
        session_max_total=300,
        session_max_per_provider=100,
        reasoning_effort=reasoning_effort,
    )


def scripted_response(content: Any) -> Any:
    """Build one provider response namespace carrying a single choice with the given content."""
    return SimpleNamespace(
        model="gpt-4o-mini",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


# --------------------------------------------------------------------------------------
# A2AT_LLM_MAX_ATTEMPTS: LlmRuntimeConfig parsing (valid / invalid / absent / clamped)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_value", "expected_attempts"),
    [
        ("5", 5),
        ("  7 ", 7),
        ("1", 1),
        ("10", 10),
        ("0", 1),
        ("-3", 1),
        ("11", 10),
        ("99", 10),
        ("abc", DEFAULT_LLM_MAX_ATTEMPTS),
        ("3.5", DEFAULT_LLM_MAX_ATTEMPTS),
        ("", DEFAULT_LLM_MAX_ATTEMPTS),
        ("   ", DEFAULT_LLM_MAX_ATTEMPTS),
    ],
)
def test_max_attempts_parsing(raw_value: str, expected_attempts: int) -> None:
    config = LlmRuntimeConfig.from_mapping({LLM_MAX_ATTEMPTS_KEY: raw_value})

    assert config.max_attempts == expected_attempts


def test_absent_max_attempts_keeps_the_java_default() -> None:
    assert LlmRuntimeConfig.from_mapping({}).max_attempts == DEFAULT_LLM_MAX_ATTEMPTS
    assert LlmRuntimeConfig.from_mapping(None).max_attempts == DEFAULT_LLM_MAX_ATTEMPTS
    assert LlmRuntimeConfig().max_attempts == DEFAULT_LLM_MAX_ATTEMPTS


def test_max_attempts_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LLM_MAX_ATTEMPTS_KEY, "6")

    assert LlmRuntimeConfig.from_env().max_attempts == 6

    monkeypatch.delenv(LLM_MAX_ATTEMPTS_KEY, raising=False)

    assert LlmRuntimeConfig.from_env().max_attempts == DEFAULT_LLM_MAX_ATTEMPTS


@pytest.mark.parametrize(
    ("raw_value", "expected_warning_part"),
    [
        ("99", "clamped_value=10"),
        ("0", "clamped_value=1"),
        ("abc", "default_value=3"),
    ],
)
def test_max_attempts_recoveries_log_a_warning(
    raw_value: str, expected_warning_part: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="a2a_t.config.models"):
        LlmRuntimeConfig.from_mapping({LLM_MAX_ATTEMPTS_KEY: raw_value})

    assert any(record.message.startswith("LLM max attempts") for record in caplog.records)
    assert any(expected_warning_part in record.message for record in caplog.records)


def test_max_attempts_clamping_of_direct_construction(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="a2a_t.config.models"):
        assert LlmRuntimeConfig(max_attempts=42).max_attempts == 10
        assert LlmRuntimeConfig(max_attempts=-1).max_attempts == 1

    assert len([record for record in caplog.records if "clamped" in record.message]) == 2


def test_unified_config_plumbs_the_attempt_limit(tmp_path: Path) -> None:
    from a2a_t.config.models import A2ATConfig

    env_path = tmp_path / ".env"
    env_path.write_text("A2AT_LLM_MAX_ATTEMPTS=5\n", encoding="utf-8")
    assert A2ATConfig.load(env_path).llm.max_attempts == 5

    default_env_path = tmp_path / "default.env"
    default_env_path.write_text("A2AT_LANGUAGE=zh-CN\n", encoding="utf-8")
    assert A2ATConfig.load(default_env_path).llm.max_attempts == DEFAULT_LLM_MAX_ATTEMPTS


# --------------------------------------------------------------------------------------
# A2AT_LLM_REASONING_EFFORT: validated provider configuration
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_value", "expected_effort"),
    [
        ("none", "none"),
        ("minimal", "minimal"),
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("xhigh", "xhigh"),
        ("HIGH", "high"),
        ("  Medium ", "medium"),
    ],
)
def test_reasoning_effort_normalizes_valid_values(raw_value: str, expected_effort: str) -> None:
    assert coerce_reasoning_effort(raw_value) == expected_effort


@pytest.mark.parametrize("raw_value", [None, "", "   "])
def test_reasoning_effort_stays_unset_when_blank(raw_value: str | None) -> None:
    assert coerce_reasoning_effort(raw_value) is None


@pytest.mark.parametrize("raw_value", ["ultra", "reasoning", "0", "nonee"])
def test_reasoning_effort_rejects_unknown_values(raw_value: str) -> None:
    with pytest.raises(LLMConfigError, match="Invalid reasoningEffort value"):
        coerce_reasoning_effort(raw_value)


def _write_env(tmp_path: Path, body: str) -> Path:
    env_path = tmp_path / ".env"
    env_path.write_text(body, encoding="utf-8")
    return env_path


def test_config_loader_reads_the_reasoning_effort(tmp_path: Path) -> None:
    env_path = _write_env(
        tmp_path,
        "A2AT_LLM_PROVIDER=openai\nA2AT_LLM_MODEL=gpt-4o-mini\nA2AT_LLM_API_KEY=sk-test\n"
        "A2AT_LLM_REASONING_EFFORT=high\n",
    )

    assert LLMConfigLoader.load(env_path).reasoning_effort == "high"


def test_config_loader_rejects_an_invalid_reasoning_effort(tmp_path: Path) -> None:
    env_path = _write_env(
        tmp_path,
        "A2AT_LLM_PROVIDER=openai\nA2AT_LLM_MODEL=gpt-4o-mini\nA2AT_LLM_API_KEY=sk-test\n"
        "A2AT_LLM_REASONING_EFFORT=ultra\n",
    )

    with pytest.raises(LLMConfigError, match="Invalid reasoningEffort value 'ultra'"):
        LLMConfigLoader.load(env_path)


def test_config_loader_keeps_the_reasoning_effort_unset_by_default(tmp_path: Path) -> None:
    env_path = _write_env(
        tmp_path,
        "A2AT_LLM_PROVIDER=openai\nA2AT_LLM_MODEL=gpt-4o-mini\nA2AT_LLM_API_KEY=sk-test\n",
    )

    assert LLMConfigLoader.load(env_path).reasoning_effort is None


# --------------------------------------------------------------------------------------
# OpenAI provider: reasoning-effort forwarding and the empty-response guard
# --------------------------------------------------------------------------------------


@patch("a2a_t.llm.providers.openai.OpenAI")
def test_reasoning_effort_is_forwarded_to_the_payload(openai_cls: Mock) -> None:
    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = scripted_response('{"device_type":"router"}')
    openai_cls.return_value = sdk_client
    client = OpenAIClient(build_client_config(reasoning_effort="low"))

    client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

    payload = sdk_client.chat.completions.create.call_args.kwargs
    assert payload["reasoning_effort"] == "low"


@patch("a2a_t.llm.providers.openai.OpenAI")
def test_reasoning_effort_is_omitted_when_unset(openai_cls: Mock) -> None:
    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = scripted_response('{"device_type":"router"}')
    openai_cls.return_value = sdk_client
    client = OpenAIClient(build_client_config())

    client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

    payload = sdk_client.chat.completions.create.call_args.kwargs
    assert "reasoning_effort" not in payload


@pytest.mark.parametrize("blank_content", ["", "   ", "\t\n"])
@patch("a2a_t.llm.providers.openai.OpenAI")
def test_empty_content_raises_the_reasoning_model_guard(openai_cls: Mock, blank_content: str) -> None:
    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = scripted_response(blank_content)
    openai_cls.return_value = sdk_client
    client = OpenAIClient(build_client_config(reasoning_effort="low"))

    with pytest.raises(LLMRuntimeError, match="returned empty content") as exc_info:
        client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

    assert "rate limit or model timeout" in str(exc_info.value)
    assert is_response_contract_violation(exc_info.value), "the guard must resolve to llm.response_invalid"


@patch("a2a_t.llm.providers.openai.OpenAI")
def test_missing_message_content_stays_a_response_contract_violation(openai_cls: Mock) -> None:
    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = scripted_response(None)
    openai_cls.return_value = sdk_client
    client = OpenAIClient(build_client_config(reasoning_effort="xhigh"))

    with pytest.raises(LLMRuntimeError, match="did not include message content") as exc_info:
        client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

    assert is_response_contract_violation(exc_info.value)


@patch("a2a_t.llm.providers.openai.OpenAI")
def test_non_empty_json_content_still_passes(openai_cls: Mock) -> None:
    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = scripted_response('{"device_type":"router"}')
    openai_cls.return_value = sdk_client
    client = OpenAIClient(build_client_config(reasoning_effort="medium"))

    response = client.structured(messages=[{"role": "user", "content": "extract"}], json_schema={"type": "object"})

    assert response.content == '{"device_type":"router"}'
