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

"""Renders error messages from the bundled ``prompt_resources/errors/{language}/errors.json`` templates.

Port of Java ``a2a-t-core`` ``net.openan.a2at.sdk.core.exception.ErrorMessages`` (1.1.0). The language
follows the ``A2AT_LANGUAGE`` configuration resolved by the caller (default ``en-US``). A template
missing in the requested language falls back to ``en-US``; a template missing in both languages
renders as the bare code string. Placeholders are ``{name}`` tokens; a placeholder without a matching
fact value is kept literal, matching the template style of the A2A-T prompt resources.

Rendering is never-throw: a missing or unreadable resource logs a warning and degrades to the bare
code, and a loaded language is cached at module level while a missing one is re-read on every call.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Mapping
from importlib.resources import files as resource_files
from importlib.resources.abc import Traversable
from re import Match
from typing import Final

from a2a_t.core.errors.catalog import ErrorCatalog

__all__ = ["DEFAULT_LANGUAGE", "ErrorMessages", "render", "template"]

logger = logging.getLogger(__name__)

#: Language used when no language is configured or a template is missing in the requested language.
DEFAULT_LANGUAGE: Final[str] = "en-US"

_PACKAGE: Final[str] = "a2a_t"
_RESOURCE_ROOT: Final[str] = "prompt_resources"

_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"\{([a-zA-Z][a-zA-Z0-9_]*)\}")
_NO_VALUE: Final[object] = object()

# Module-level cache of loaded code-to-template maps, keyed by language (Java: ConcurrentHashMap).
# A language whose resource is missing or broken is never cached: the next call re-reads it, so a
# resource added later in the same process is picked up (plan 7.2, "missing 不缓存").
_templates_by_language: dict[str, dict[str, str]] = {}


class _FactValues(Mapping[str, str]):
    """Mapping view of the fact values consumed by ``str.format_map`` substitution.

    A fact key without a usable value (absent or ``None``) resolves to the literal ``{key}``
    placeholder, matching the Java renderer which keeps unmatched placeholders literal. Non-string
    values (for example the integer ``actual_length``) are ``str()``-ized.
    """

    def __init__(self, facts: Mapping[str, object]) -> None:
        self._facts = facts

    def __getitem__(self, key: str) -> str:
        if not isinstance(key, str):
            # Positional fields (for example "{}") are not part of the Java placeholder style;
            # raising routes the whole template through the exact Java substitution below.
            raise KeyError(key)
        value = self._facts.get(key, _NO_VALUE)
        if value is _NO_VALUE or value is None:
            return "{" + key + "}"
        if isinstance(value, str):
            return value
        return str(value)

    def __iter__(self) -> Iterator[str]:
        return iter(self._facts)

    def __len__(self) -> int:
        return len(self._facts)


def _resource_file(language: str) -> Traversable:
    """Returns the traversable resource of one language's error message catalog."""
    return resource_files(_PACKAGE) / _RESOURCE_ROOT / "errors" / language / "errors.json"


def _load_templates(language: str) -> dict[str, str] | None:
    """Loads one language's code-to-template map.

    Returns:
        the loaded template map, or ``None`` when the resource is missing, unreadable or malformed
        (never raises: the caller degrades to the fallback chain)
    """
    resource = _resource_file(language)
    try:
        if not resource.is_file():
            logger.warning(
                "Error message resource '%s/errors/%s/errors.json' not found in package '%s'.",
                _RESOURCE_ROOT,
                language,
                _PACKAGE,
            )
            return None
        data = json.loads(resource.read_text(encoding="utf-8"))
    except Exception as error:  # never-throw boundary, mirrors Java catch (IOException | RuntimeException)
        logger.warning(
            "Cannot load error message resource '%s/errors/%s/errors.json': %s",
            _RESOURCE_ROOT,
            language,
            error,
        )
        return None
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in data.items()
    ):
        logger.warning(
            "Error message resource '%s/errors/%s/errors.json' is not a flat code-to-template object.",
            _RESOURCE_ROOT,
            language,
        )
        return None
    return data


def _templates(language: str) -> dict[str, str]:
    """Returns the cached template map of one language; missing resources are re-read every call."""
    cached = _templates_by_language.get(language)
    if cached is None:
        loaded = _load_templates(language)
        if loaded is None:
            return {}
        _templates_by_language[language] = loaded
        cached = loaded
    return cached


def _normalize_language(language: str | None) -> str:
    """Normalizes one language tag; ``None`` or blank falls back to the default language."""
    if language is None:
        return DEFAULT_LANGUAGE
    trimmed = language.strip()
    return trimmed or DEFAULT_LANGUAGE


def _resolve_template(code: str, language: str | None) -> str | None:
    """Resolves the template of one code, falling back to the default language."""
    normalized = _normalize_language(language)
    resolved = _templates(normalized).get(code)
    if resolved is None and normalized != DEFAULT_LANGUAGE:
        resolved = _templates(DEFAULT_LANGUAGE).get(code)
    return resolved


def _fact_replacement(match: Match[str], facts: Mapping[str, object]) -> str:
    """Returns the substitution of one placeholder match; unmatched placeholders stay literal."""
    value = facts.get(match.group(1), _NO_VALUE)
    if value is _NO_VALUE or value is None:
        return match.group(0)
    if isinstance(value, str):
        return value
    return str(value)


def _render_template(template_text: str, facts: Mapping[str, object] | None) -> str:
    """Renders one template with its fact values; without facts the template is returned unchanged."""
    if not facts:
        return template_text
    try:
        return template_text.format_map(_FactValues(facts))
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        # The bundled templates use plain {name} placeholders only. A template carrying something
        # str.format cannot express without raising (positional fields, attribute paths) falls back
        # to the exact Java substitution, so rendering stays never-throw for every template shape.
        return _PLACEHOLDER.sub(lambda match: _fact_replacement(match, facts), template_text)


def _code_str(code: ErrorCatalog | str) -> str:
    """Normalizes one code to its plain string form (D4: catalog members expose ``value``)."""
    if isinstance(code, ErrorCatalog):
        return str(code.value)
    return str(code)


def render(code: ErrorCatalog | str, facts: Mapping[str, object] | None = None, language: str | None = None) -> str:
    """Render the message of one error code in one language.

    Args:
        code: layered error code or catalog entry, for example ``content.param_missing``
        facts: fact values keyed by fact parameter name; non-string values are ``str()``-ized and
            keys without a value keep their placeholder literal
        language: message language, for example ``zh-CN``; ``None`` or blank falls back to ``en-US``

    Returns:
        the rendered message; the bare code when no template exists in either language
    """
    code_str = _code_str(code)
    resolved = _resolve_template(code_str, language)
    if resolved is None:
        logger.warning(
            "No error message template found for code '%s' in language '%s' or '%s'.",
            code_str,
            language,
            DEFAULT_LANGUAGE,
        )
        return code_str
    return _render_template(resolved, facts)


def template(code: ErrorCatalog | str, language: str | None = None) -> str | None:
    """Return the message template of one error code in one language, without rendering it.

    Args:
        code: layered error code or catalog entry
        language: message language; ``None`` or blank falls back to ``en-US``

    Returns:
        the template text, or ``None`` when no template exists in the requested language or ``en-US``
    """
    return _resolve_template(_code_str(code), language)


class ErrorMessages:
    """Java-parity facade over the module-level rendering functions; never instantiate."""

    #: Language used when no language is configured or a template is missing in the requested language.
    DEFAULT_LANGUAGE: Final[str] = DEFAULT_LANGUAGE

    @staticmethod
    def render(
        code: ErrorCatalog | str,
        facts: Mapping[str, object] | None = None,
        language: str | None = None,
    ) -> str:
        """Render the message of one error code in one language.

        Args:
            code: layered error code or catalog entry, for example ``content.param_missing``.
            facts: fact values keyed by fact parameter name; non-string values are ``str()``-ized
                and keys without a value keep their placeholder literal.
            language: message language, for example ``zh-CN``; ``None`` or blank falls back to
                ``en-US``.

        Returns:
            the rendered message; the bare code when no template exists in either language.
        """
        return render(code, facts, language)

    @staticmethod
    def template(code: ErrorCatalog | str, language: str | None = None) -> str | None:
        """Return the message template of one error code in one language, without rendering it.

        Args:
            code: layered error code or catalog entry.
            language: message language; ``None`` or blank falls back to ``en-US``.

        Returns:
            the template text, or ``None`` when no template exists in the requested language or
            ``en-US``.
        """
        return template(code, language)
