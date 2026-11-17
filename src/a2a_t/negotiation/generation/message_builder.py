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

"""Assembly of the LLM message list of one negotiation LLM step (port of Java
``NegotiationMessageBuilder``).

The system prompt of the addressed category is used verbatim. The user prompt is loaded from the
same category and every literal bracket token it contains, such as ``[phase]`` or ``[input]``, is
replaced by the value supplied for that token name; a supplied ``None`` value is replaced with an
empty string and bracket tokens without a supplied value are left unchanged.

The Java builder holds a ``NegotiationPromptResourceLoader`` (its own private classpath loader);
this port consumes the common resource access layer instead (D31): prompts are a package-fixed
category of :class:`~a2a_t.common.prompt_resources.resource_access.PromptResourceAccess`, and the
``access`` parameter — ``None`` meaning the packaged default, the equivalent of the Java no-arg
constructor — is the only seam. The Python convention replaces the Java builder constructor with
keyword arguments (port plan 2.3).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from a2a_t.common.prompt_resources.resource_access import (
    PackagedPromptResourceAccess,
    PromptResourceAccess,
)

__all__ = [
    "TOKEN_INPUT",
    "TOKEN_NEGOTIATION_TYPE",
    "TOKEN_PHASE",
    "TOKEN_SCHEMA",
    "TOKEN_TEMPLATE_URI",
    "build_messages",
]

#: Token name receiving the negotiation phase of the step, such as ``propose`` or ``accept``.
TOKEN_PHASE: Final[str] = "phase"

#: Token name receiving the free-text input of the step.
TOKEN_INPUT: Final[str] = "input"

#: Token name receiving the template URI declared by the caller.
TOKEN_TEMPLATE_URI: Final[str] = "template_uri"

#: Token name receiving the negotiation type declared by the caller.
TOKEN_NEGOTIATION_TYPE: Final[str] = "negotiation_type"

#: Token name receiving the JSON Schema of the step.
TOKEN_SCHEMA: Final[str] = "schema"

_SYSTEM_FILE_NAME: Final[str] = "system.md"

_USER_FILE_NAME: Final[str] = "user.md"

_DEFAULT_ACCESS: PackagedPromptResourceAccess | None = None


def build_messages(
    prompt_category: str,
    language: str,
    tokens: Mapping[str, str | None] | None = None,
    access: PromptResourceAccess | None = None,
) -> list[dict[str, str]]:
    """Build the system and user messages of one LLM step.

    Args:
        prompt_category: prompt category directory such as ``information_negotiation``.
        language: locale identifier such as ``zh-CN`` or ``en-US``.
        tokens: token values keyed by token name such as ``phase`` or ``input``; a ``None`` value is
            replaced with an empty string, and bracket tokens without a supplied value are left
            unchanged.
        access: resource access object resolving the prompt tree; ``None`` uses the packaged access
            (the Java no-arg constructor's default classpath loader).

    Returns:
        the ordered message list with one system message followed by one user message, each
        carrying ``role`` and ``content`` keys in that order.

    Raises:
        ValueError: when the prompt category or the language is not a non-blank simple path
            segment.
        A2ATError: carrying ``infra.resource_read_failed`` when the prompt resource is missing from
            the package or cannot be read (the common layer's replacement of the Java
            ``ResourceNotFoundException``).
    """
    resolved = _default_access() if access is None else access
    system_prompt = resolved.load_prompt(prompt_category, language, _SYSTEM_FILE_NAME)
    user_prompt = _replace_tokens(resolved.load_prompt(prompt_category, language, _USER_FILE_NAME), tokens)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _replace_tokens(user_prompt: str, tokens: Mapping[str, str | None] | None) -> str:
    """Replace every literal bracket token of the user prompt with its supplied value."""
    result = user_prompt
    if tokens is not None:
        for name, value in tokens.items():
            result = result.replace(f"[{name}]", "" if value is None else value)
    return result


def _default_access() -> PackagedPromptResourceAccess:
    """Return the module-level packaged access used when no access object is injected."""
    global _DEFAULT_ACCESS
    if _DEFAULT_ACCESS is None:
        _DEFAULT_ACCESS = PackagedPromptResourceAccess()
    return _DEFAULT_ACCESS
