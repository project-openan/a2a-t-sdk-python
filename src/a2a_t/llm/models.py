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

"""Data models for LLM integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    """Response from an LLM adapter."""

    content: str
    model: str
    usage: dict[str, int]
    metadata: dict[str, Any]
    session_id: str | None = None


@dataclass(frozen=True)
class LLMClientConfig:
    """Resolved default configuration for the shared LLM client.

    Attributes:
        max_tokens: default completion cap of a structured call whose call site passes
            ``max_tokens=None``; ``None`` means "no cap" (the provider decides). The config loader
            fills ``DEFAULT_MAX_TOKENS`` when ``A2AT_LLM_MAX_TOKENS`` is unset and keeps ``None``
            only for the explicit ``none``/``null`` value. Java ``LLMClientConfig.maxTokens``.
        reasoning_effort: optional reasoning effort for reasoning models, one of
            ``none``/``minimal``/``low``/``medium``/``high``/``xhigh`` (already normalized to lower
            case); ``None`` leaves the provider parameter unset. Java ``LLMClientConfig.reasoningEffort``.
        ssl_verify: whether to verify the TLS certificate chain and hostname of the LLM endpoint; ``False``
            disables both certificate-chain and hostname verification for HTTPS gateways whose
            certificate is not in the system trust store. Java ``LLMClientConfig.sslVerify``.
        detail_log_enabled: whether to print the full LLM request and response payloads (no truncation).
            Summary logs (timestamp, token usage, elapsed time) are recorded at DEBUG level on the
            dedicated logger ``a2a_t.llm.call`` independently of this flag. Java
            ``LLMClientConfig.detailLogEnabled``.
        extra_body: optional provider-specific request fields forwarded verbatim as the OpenAI SDK
            ``extra_body`` argument. ``None`` sends nothing.
        response_format: ``json_schema`` (default) asks the gateway to enforce the caller's output
            schema; ``json_object`` falls back to portable JSON mode for gateways that do not accept
            schema enforcement.
        timeout_retries: how many extra attempts one structured call makes after a transport timeout;
            ``0`` keeps the original single-attempt behavior.
    """

    provider: str
    model: str
    api_key: str
    base_url: str | None
    history_window: int
    max_tokens: int | None
    temperature: float | None
    timeout_seconds: float | None
    session_max_total: int
    session_max_per_provider: int
    reasoning_effort: str | None = None
    ssl_verify: bool = True
    detail_log_enabled: bool = False
    extra_body: dict[str, Any] | None = None
    response_format: str = "json_schema"
    timeout_retries: int = 0
