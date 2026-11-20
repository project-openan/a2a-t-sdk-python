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

"""Addressing key for one negotiation template (port of the Java ``resources/NegotiationReference``).

The reference composes the template URI from the type segment and the URI segment of the
performative, so the URI spelling has a single source (:func:`uri_segment_of`). The language is
query context rather than part of the resource identity and is therefore not part of the URI.

The performative has four values while the template URI layer only distinguishes three segments:
``ACCEPT`` and ``REJECT`` share the ``accept-reject`` segment, differing only in the conclusion
value filled into the template slot, and ``ABORT`` is addressed by the single type-independent
common abort template. Typed references address the templates of one negotiation type; the abort
performative is type-independent and is addressed by that common template with a ``None`` type.

This module is a domain type only: template loading stays in the common resource access layer
(D31), so unlike the Java ``resources`` package there is no loader, cache or path assembly here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from a2a_t.core.metadata import NegotiationPerformative
from a2a_t.core.path_segments import is_simple_segment
from a2a_t.core.standard_templates import NEGOTIATION_EXTENSION_NAME
from a2a_t.core.template_uri import DEFAULT_TEMPLATE_VERSION, TemplateUri

from ..content.enums import NegotiationType

__all__ = ["COMMON_TYPE_SEGMENT", "NegotiationReference", "uri_segment_of"]

#: Extension-name prefix of every negotiation template URI (``Negotiation-T``).
_URI_PREFIX: Final[str] = NEGOTIATION_EXTENSION_NAME

#: Trailing template-version segment every negotiation template URI carries.
_URI_VERSION_SEGMENT: Final[str] = DEFAULT_TEMPLATE_VERSION

#: Suffix every typed negotiation type segment ends with.
_TYPE_SEGMENT_SUFFIX: Final[str] = "-negotiation"

#: Type segment of the type-independent common abort template.
COMMON_TYPE_SEGMENT: Final[str] = "common"

#: Template URI segment of each performative: accept and reject share one segment.
_URI_SEGMENTS: Final[dict[NegotiationPerformative, str]] = {
    NegotiationPerformative.PROPOSE: "propose",
    NegotiationPerformative.ACCEPT: "accept-reject",
    NegotiationPerformative.REJECT: "accept-reject",
    NegotiationPerformative.ABORT: "abort",
}


@dataclass(frozen=True, slots=True)
class NegotiationReference:
    """Addressing key for one negotiation template: negotiation type, performative and language.

    Attributes:
        type: negotiation type addressed by the reference; ``None`` only for the abort
            performative, whose common template is type-independent.
        performative: communicative intent addressed by the reference; accept and reject share the
            same template.
        language: locale identifier such as ``zh-CN`` or ``en-US``.
    """

    type: NegotiationType | None
    performative: NegotiationPerformative
    language: str

    def __post_init__(self) -> None:
        """Validate the reference fields.

        Raises:
            TypeError: when the performative is ``None``.
            ValueError: when the type is ``None`` on a typed performative, non-``None`` on the
                abort performative, or the language is not a simple path segment.
        """
        if self.performative is None:
            raise TypeError("Negotiation reference performative must not be null.")
        if self.performative is NegotiationPerformative.ABORT and self.type is not None:
            raise ValueError(
                "Negotiation reference of the ABORT performative is type-independent; the type must be null but "
                f"was {self.type}."
            )
        if self.performative is not NegotiationPerformative.ABORT and self.type is None:
            raise ValueError(
                f"Negotiation reference type must not be null for the {self.performative} performative; only the "
                "ABORT performative is type-independent."
            )
        if not is_simple_segment(self.language):
            raise ValueError(
                f"Negotiation reference language must be a non-blank simple path segment but was {self.language}."
            )

    @property
    def type_segment(self) -> str:
        """Hyphenated URI segment of the referenced negotiation type.

        Returns:
            URI segment such as ``information-negotiation``; ``common`` for the type-independent
            abort performative.
        """
        return COMMON_TYPE_SEGMENT if self.type is None else self.type.type_segment

    @property
    def uri(self) -> str:
        """Template URI of the referenced template.

        Returns:
            template URI such as ``Negotiation-T/information-negotiation/propose/v1``.
        """
        return "/".join((_URI_PREFIX, self.type_segment, uri_segment_of(self.performative), _URI_VERSION_SEGMENT))

    @property
    def template_uri(self) -> TemplateUri:
        """Typed template URI of the referenced template.

        The exact inverse of :meth:`from_template_uri`: the URI is composed from the same
        extension-name constant and default version segment the parser accepts, so
        ``from_template_uri(reference.template_uri, reference.performative, ...)`` always addresses
        the same template.

        Returns:
            typed template URI such as ``Negotiation-T/information-negotiation/propose/v1``.
        """
        return TemplateUri.of(
            _URI_PREFIX,
            self.type_segment,
            uri_segment_of(self.performative),
            template_version=_URI_VERSION_SEGMENT,
        )

    @classmethod
    def try_parse(
        cls, template_uri: str | None, expected_performative: NegotiationPerformative, language: str
    ) -> NegotiationReference | None:
        """Try to parse a template URI into a reference, checking it against the expected performative.

        The URI layer cannot distinguish accept from reject because both share the
        ``accept-reject`` segment; the expected performative disambiguates the parsed result,
        which therefore always carries the expected performative.

        Args:
            template_uri: template URI to parse, such as
                ``Negotiation-T/target-negotiation/accept-reject/v1`` or
                ``Negotiation-T/common/abort/v1``; may be ``None``.
            expected_performative: performative the caller is operating on; the parsed reference
                carries this performative.
            language: locale identifier for the parsed reference.

        Returns:
            reference addressed by the URI carrying the expected performative, or ``None`` when
            the URI is ``None``, blank or malformed (wrong segment count, prefix, type segment or
            trailing version segment) or its URI segment does not match the expected performative.

        Raises:
            TypeError: when the expected performative is ``None``.
        """
        if expected_performative is None:
            raise TypeError("Expected negotiation performative must not be null.")
        parsed = TemplateUri.parse(template_uri)
        if parsed is None:
            return None
        return cls.from_template_uri(parsed, expected_performative, language)

    @classmethod
    def from_template_uri(
        cls, template_uri: TemplateUri, expected_performative: NegotiationPerformative, language: str
    ) -> NegotiationReference | None:
        """Derive a reference from a typed template URI, checking it against the expected performative.

        The typed variant of :meth:`try_parse`: because a :class:`TemplateUri` is always
        structurally well formed, the checks operate on the URI components directly instead of
        splitting a raw string. The URI layer cannot distinguish accept from reject because both
        share the ``accept-reject`` segment; the expected performative disambiguates the result,
        which therefore always carries the expected performative.

        The abort performative is addressed by the common abort template: a ``common``/``abort``
        segment pair yields a reference with a ``None`` type, and every other segment shape is
        rejected for that performative.

        Args:
            template_uri: typed template URI such as
                ``Negotiation-T/target-negotiation/accept-reject/v1`` or
                ``Negotiation-T/common/abort/v1``.
            expected_performative: performative the caller is operating on; the derived reference
                carries this performative.
            language: locale identifier for the derived reference.

        Returns:
            reference addressed by the URI carrying the expected performative, or ``None`` when
            the URI does not address a negotiation template of the expected performative (wrong
            extension name, path segment count, type segment, URI segment or template version).

        Raises:
            TypeError: when the template URI or the expected performative is ``None``.
        """
        if template_uri is None:
            raise TypeError("Template URI must not be null.")
        if expected_performative is None:
            raise TypeError("Expected negotiation performative must not be null.")
        if template_uri.extension_name != _URI_PREFIX:
            return None
        segments = template_uri.path_segments
        if len(segments) != 2:
            return None
        parsed_type: NegotiationType | None = None
        if segments[0] == COMMON_TYPE_SEGMENT:
            if expected_performative is not NegotiationPerformative.ABORT:
                return None
        else:
            parsed_type = _parse_type_segment(segments[0])
            if expected_performative is NegotiationPerformative.ABORT or parsed_type is None:
                return None
        if not _uri_segment_matches(segments[1], expected_performative):
            return None
        if template_uri.template_version != _URI_VERSION_SEGMENT:
            return None
        return cls(parsed_type, expected_performative, language)


def uri_segment_of(performative: NegotiationPerformative) -> str:
    """Return the template URI segment of a performative.

    The performative has four values but the template URI layer only distinguishes three
    segments: ``ACCEPT`` and ``REJECT`` share the ``accept-reject`` segment. This mapping is the
    single source of the segment spelling.

    Args:
        performative: performative to map to its template URI segment.

    Returns:
        ``propose`` for PROPOSE, ``accept-reject`` for ACCEPT and REJECT, and ``abort`` for ABORT.

    Raises:
        TypeError: when the performative is ``None``.
    """
    if performative is None:
        raise TypeError("Negotiation performative must not be null.")
    return _URI_SEGMENTS[performative]


def _parse_type_segment(type_segment: str) -> NegotiationType | None:
    """Parse one URI segment into a negotiation type, or ``None`` when it names no type."""
    if not type_segment.endswith(_TYPE_SEGMENT_SUFFIX):
        return None
    type_name = type_segment[: -len(_TYPE_SEGMENT_SUFFIX)]
    for candidate in NegotiationType:
        if candidate.name.lower() == type_name:
            return candidate
    return None


def _uri_segment_matches(uri_segment: str, expected_performative: NegotiationPerformative) -> bool:
    """Report whether one URI segment is a legal segment matching the expected performative."""
    if uri_segment not in ("propose", "accept-reject", "abort"):
        return False
    return uri_segment == uri_segment_of(expected_performative)
