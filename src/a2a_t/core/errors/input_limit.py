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

"""Input limit configuration resolved from unified SDK config.

Port of the Java ``core/model/InputLimitConfig`` record (1.1.0). The limits guard every facade entry
point that accepts a free-text string and forwards it to an LLM step, so oversized inputs fail fast
before any LLM call instead of overflowing the LLM context.

Length is measured with ``len()`` (Unicode code points, D5): this differs from the Java
``String.length()`` UTF-16 code unit count only for supplementary-plane characters, and the limit
exists to prevent abuse rather than to bill precisely.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError

__all__ = ["DEFAULT_MAX_TEXT_CHARS", "INPUT_TEXT_MAX_CHARS_KEY", "InputLimitConfig"]

logger = logging.getLogger(__name__)

#: Default maximum length in characters accepted for free-text inputs.
DEFAULT_MAX_TEXT_CHARS: Final[int] = 16384

#: Configuration key carrying the maximum length (Java ``A2ATConfigKeys.Input.MAX_TEXT_CHARS``).
INPUT_TEXT_MAX_CHARS_KEY: Final[str] = "A2AT_INPUT_TEXT_MAX_CHARS"


@dataclass(frozen=True)
class InputLimitConfig:
    """Input limit configuration resolved from unified SDK config.

    One instance is resolved once from the ``.env`` values and shared by every pipeline stage that
    gates a free-text input. Length is measured with ``len()`` (Unicode code points); an oversized
    input fails fast and is never truncated.

    Attributes:
        max_text_chars: maximum number of characters accepted for one free-text input.
    """

    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS

    def __post_init__(self) -> None:
        """Validate the configured maximum.

        Raises:
            ValueError: when the maximum is not positive.
        """
        if self.max_text_chars <= 0:
            raise ValueError(f"max_text_chars must be positive: {self.max_text_chars}")

    @classmethod
    def from_map(cls, values: Mapping[str, str] | None) -> InputLimitConfig:
        """Build one input limit config from raw ``.env`` values.

        A blank value keeps the default. A non-numeric or non-positive value logs a warning and
        falls back to the default, mirroring the Java ``fromMap`` catch of ``IllegalArgumentException``
        which covers both the parse and the compact-constructor rejection.

        Args:
            values: raw config values keyed by config key

        Returns:
            the resolved input limit config
        """
        raw_value = (values or {}).get(INPUT_TEXT_MAX_CHARS_KEY)
        if raw_value is None or not raw_value.strip():
            return cls(DEFAULT_MAX_TEXT_CHARS)
        trimmed = raw_value.strip()
        try:
            return cls(int(trimmed))
        except ValueError:
            logger.warning(
                "Input max text chars value is not a valid positive integer, falling back to default."
                " key=%s raw_value=%s default_value=%s",
                INPUT_TEXT_MAX_CHARS_KEY,
                trimmed,
                DEFAULT_MAX_TEXT_CHARS,
            )
            return cls(DEFAULT_MAX_TEXT_CHARS)

    @classmethod
    def from_env(cls) -> InputLimitConfig:
        """Build one input limit config from the process environment.

        Returns:
            the resolved input limit config
        """
        return cls.from_map(os.environ)

    def is_too_long(self, text: str | None) -> bool:
        """Report whether the given free-text input exceeds the limit.

        ``None`` is never too long, mirroring the Java null-tolerant static helper.

        Args:
            text: free-text input to measure; ``None`` is never too long.

        Returns:
            ``True`` when the input length in characters exceeds ``max_text_chars``.
        """
        return text is not None and len(text) > self.max_text_chars

    def violation_message(self, text: str | None) -> str:
        """Build the violation message for an oversized free-text input.

        Args:
            text: the oversized input; ``None`` counts as length 0.

        Returns:
            the human-readable message stating the actual length, the configured maximum and the
            configuration key that controls it.
        """
        actual_length = 0 if text is None else len(text)
        return (
            f"input text length {actual_length} exceeds the configured maximum of "
            f"{self.max_text_chars} characters ({INPUT_TEXT_MAX_CHARS_KEY})"
        )

    def too_long_facts(self, text: str | None) -> dict[str, str]:
        """Build the fact values of an input-length violation for the ``input.text_too_long`` code.

        Args:
            text: the oversized input; ``None`` counts as length 0.

        Returns:
            the ``actual_length`` and ``max_chars`` fact values keyed by the fact parameter names
            declared on the code.
        """
        return {
            "actual_length": str(0 if text is None else len(text)),
            "max_chars": str(self.max_text_chars),
        }

    def check(self, text: str | None, language: str | None = None) -> None:
        """Fail fast when the given free-text input exceeds the limit; never truncates.

        Module-specific pipelines that need their own exception type at this boundary should call
        :meth:`is_too_long` and raise it themselves (Java raise sites wrap this code in
        ``PromptGenerationException`` / ``NegotiationGenerationException``).

        Args:
            text: free-text input; ``None`` is never too long
            language: message language for the rendered violation message

        Raises:
            A2ATBusinessError: with the ``input.text_too_long`` code and the ``actual_length``
                (``len()`` code points) and ``max_chars`` facts when the input is too long
        """
        if not self.is_too_long(text):
            return
        raise A2ATBusinessError(
            ErrorCatalog.INPUT_TEXT_TOO_LONG,
            self.too_long_facts(text),
            language=language,
        )
