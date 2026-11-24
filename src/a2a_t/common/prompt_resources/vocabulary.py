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

"""Negotiation vocabulary loading with strict canonical-key validation (D31).

The vocabulary is the single source of the section titles, slot names, appended line labels and
list punctuation used when rendering negotiation messages and when recognising sections inside
received messages. The canonical keys are language-neutral; every supported language exposes
exactly the same key set, while the values match the bundled template bytes of that language
verbatim.

Unlike the Java 1.1.0 ``Vocabulary`` (classpath-fixed, never configurable), the vocabulary is a
routed resource in this port (D13 as overridden by D31): loaded from the package by default, and
locally overridable in ``local_file`` mode through the same frozen-snapshot semantics as every
other routed category. Validation is unchanged and fail-fast: a vocabulary that does not exist,
cannot be read, is malformed (including duplicate JSON keys, non-string or blank values) or drifts
from the pinned canonical key set raises a catalog-coded ``infra.resource_read_failed`` error
instead of silently degrading — vocabulary drift must explode at load time, never at render time
(port plan 7.2).

The bundled resource is the source of truth for the key set: 37 canonical keys. (The port plan's
"41 keys" was a typo carried from the pre-port gap analysis; the live
``negotiation-vocabulary/{language}/vocabulary.json`` resource is the truth.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, KeysView

from a2a_t.core.errors.exceptions import A2ATError, ResourceNotFoundError
from a2a_t.core.path_segments import is_simple_segment
from a2a_t.core.prompt_resource_key import PromptResourceKey

from .json_source import DuplicateJsonKeyError, ResourceReader, parse_json_document, read_failed

__all__ = ["CANONICAL_KEYS", "Vocabulary", "load_vocabulary", "vocabulary_key"]

_VOCABULARY_DIRECTORY: Final[str] = "negotiation-vocabulary"
_VOCABULARY_FILE_NAME: Final[str] = "vocabulary.json"

#: The language-neutral canonical key set every vocabulary file must define exactly, in the fixed
#: order pinned by the Java ``Vocabulary.CANONICAL_KEYS`` list. Both bundled languages expose this
#: key set verbatim (bilingual parity).
CANONICAL_KEYS: Final[tuple[str, ...]] = (
    "section.termination_reason",
    "section.info_items",
    "section.info_static",
    "section.info_conclusion",
    "section.info_result_content",
    "section.target",
    "section.target_intent",
    "section.target_alignment",
    "section.target_clarification",
    "section.target_confirm_request",
    "section.target_conclusion",
    "section.target_result_content",
    "section.feasibility",
    "section.feasibility_evaluate",
    "section.feasibility_infeasible",
    "section.feasibility_confirm_request",
    "section.feasibility_conclusion",
    "section.feasibility_confirm",
    "slot.termination_reason",
    "slot.info_items",
    "slot.info_conclusion",
    "slot.info_result_content",
    "slot.target",
    "slot.target_intent",
    "slot.target_alignment",
    "slot.target_clarification",
    "slot.target_confirm_request",
    "slot.target_conclusion",
    "slot.target_result_content",
    "slot.feasibility",
    "slot.feasibility_evaluate",
    "slot.feasibility_infeasible",
    "slot.feasibility_confirm_request",
    "slot.feasibility_conclusion",
    "slot.feasibility_confirm",
    "label.relationship",
    "punct.list_colon",
)

_CANONICAL_KEY_SET: Final[frozenset[str]] = frozenset(CANONICAL_KEYS)

_SUPPORTED_LANGUAGES_HINT: Final[str] = "supported languages are zh-CN and en-US, configure A2AT_LANGUAGE accordingly"


@dataclass(frozen=True)
class Vocabulary:
    """Language-specific text constants for negotiation templates.

    Attributes:
        language: locale identifier this vocabulary is bound to, such as ``zh-CN``.
        entries: canonical key to language-specific text mapping; treat it as immutable.
    """

    language: str
    entries: dict[str, str]

    def get(self, canonical_key: str) -> str:
        """Return the text constant registered under one canonical key.

        Args:
            canonical_key: canonical vocabulary key such as ``section.termination_reason`` or
                ``punct.list_colon``.

        Returns:
            the language-specific text constant.

        Raises:
            KeyError: when the key is not part of the vocabulary (the key set is validated against
                :data:`CANONICAL_KEYS` at load time, so this is a caller programming error).
        """
        try:
            return self.entries[canonical_key]
        except KeyError:
            raise KeyError(
                f"Unknown negotiation vocabulary key {canonical_key} for language {self.language}."
            ) from None

    def canonical_keys(self) -> KeysView[str]:
        """Return all canonical keys exposed by this vocabulary — identical for every language.

        Returns:
            the canonical key set, in the pinned order of :data:`CANONICAL_KEYS`.
        """
        return self.entries.keys()


def vocabulary_key(language: str) -> PromptResourceKey:
    """Return the resource key of one language's negotiation vocabulary.

    Args:
        language: locale identifier such as ``zh-CN`` or ``en-US``.

    Returns:
        the resource key addressing ``negotiation-vocabulary/<language>/vocabulary.json``.
    """
    return PromptResourceKey(_VOCABULARY_DIRECTORY, (), language, _VOCABULARY_FILE_NAME)


def load_vocabulary(reader: ResourceReader, language: str) -> Vocabulary:
    """Load the negotiation vocabulary of one language from the routed source.

    Args:
        reader: routed reader resolving the vocabulary tree.
        language: locale identifier such as ``zh-CN`` or ``en-US``.

    Returns:
        the vocabulary holding the validated text constants of the language.

    Raises:
        ValueError: when the language is not a non-blank simple path segment.
        A2ATError: carrying ``infra.resource_read_failed`` when the vocabulary does not exist,
            cannot be read, is malformed (duplicate keys, non-string or blank values) or does not
            define exactly the canonical key set.
    """
    if not is_simple_segment(language):
        raise ValueError(f"Negotiation vocabulary language must be a non-blank simple path segment but was {language}.")
    key = vocabulary_key(language)
    relative_path = key.relative_path()
    try:
        text = reader.read_text(key)
    except ResourceNotFoundError as error:
        detail = f"The negotiation vocabulary does not exist for the configured language; {_SUPPORTED_LANGUAGES_HINT}."
        raise read_failed(relative_path, language, detail=detail) from error
    except (OSError, UnicodeDecodeError) as error:
        raise read_failed(relative_path, language, cause=error) from error
    return _parse_vocabulary(language, text, relative_path)


def _parse_vocabulary(language: str, payload: str, origin: str) -> Vocabulary:
    """Parse and validate one vocabulary payload against the canonical key set."""
    try:
        document = parse_json_document(payload, origin=origin)
    except DuplicateJsonKeyError as error:
        raise _malformed(language, origin, "it contains duplicate keys", cause=error) from error
    except ValueError as error:
        raise _malformed(language, origin, "it is not valid JSON", cause=error) from error
    if not isinstance(document, dict):
        raise _malformed(language, origin, "it is not a flat JSON object of string values")
    entries: dict[str, str] = {}
    for name, value in document.items():
        if not isinstance(value, str):
            raise _malformed(language, origin, f"the value of key '{name}' is not a string")
        if not value.strip():
            raise _malformed(language, origin, f"the value of key '{name}' is blank")
        entries[name] = value
    _validate_canonical_keys(language, entries, origin)
    return Vocabulary(language=language, entries=entries)


def _validate_canonical_keys(language: str, entries: dict[str, str], origin: str) -> None:
    """Require the entry key set to match the canonical key set exactly."""
    missing = [key for key in CANONICAL_KEYS if key not in entries]
    unexpected = sorted(set(entries) - _CANONICAL_KEY_SET)
    if missing or unexpected:
        raise read_failed(
            origin,
            language,
            detail=(
                "The negotiation vocabulary must define exactly the canonical vocabulary keys; "
                f"missing keys: {missing}, unexpected keys: {unexpected}."
            ),
        )


def _malformed(language: str, origin: str, detail: str, *, cause: BaseException | None = None) -> A2ATError:
    """Create the malformed-vocabulary failure for one detail sentence."""
    return read_failed(origin, language, cause=cause, detail=f"The vocabulary is malformed: {detail}.")
