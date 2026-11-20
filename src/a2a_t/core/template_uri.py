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

"""Structured template URI addressing (port of Java ``TemplateUri``).

A template URI is composed of an extension name (``Task-T``, ``Notification-T``,
``Negotiation-T``, ``Authorization-T``), at least one path segment (a scenario
code, optionally prefixed with the ``network-layer`` domain segment, or the type
and phase segments of a negotiation template) and a trailing template version.
Constructing a :class:`TemplateUri` validates every segment, so a value of this
type can never carry a malformed URI. The URI mirrors the resource directory
layout one-to-one:
``templates/<extension_name>/<path_segments>/<template_version>/<language>/template.md``.

The language is deliberately not part of the URI: it is global runtime context
resolved from the prompt configuration by whichever component needs it, never an
addressability dimension of the template.

Per port decision D16 both spellings coexist ("双拼写并存"): the typed
:class:`TemplateUri` is the identity representation used for template
comparisons and by internal seams, while the public facades take the raw
``str`` form; :attr:`TemplateUri.uri` is the canonical bridge between the two,
and a lockstep consistency test pins them together.
"""

from __future__ import annotations

from dataclasses import dataclass

from a2a_t.core.path_segments import is_simple_segment

__all__ = ["DEFAULT_TEMPLATE_VERSION", "TemplateUri"]

DEFAULT_TEMPLATE_VERSION = "v1"


@dataclass(frozen=True)
class TemplateUri:
    """Structured, always-valid identifier of a content template.

    Attributes:
        extension_name: first URI segment identifying the template family, such
            as ``Task-T``.
        path_segments: middle URI segments, such as
            ``("network-layer", "ran-energy-saving")`` or
            ``("information-negotiation", "propose")``.
        template_version: trailing URI segment, such as ``v1``; defaults to
            :data:`DEFAULT_TEMPLATE_VERSION`.
    """

    extension_name: str
    path_segments: tuple[str, ...]
    template_version: str = DEFAULT_TEMPLATE_VERSION

    def __post_init__(self) -> None:
        """Validate every component and defensively copy the path segments.

        Raises:
            TypeError: when the extension name or the template version is
                ``None``.
            ValueError: when any component is not a simple path segment or the
                path segment sequence is empty.
        """
        object.__setattr__(self, "path_segments", tuple(self.path_segments))
        _validate_segment(self.extension_name, "Extension name")
        if not self.path_segments:
            raise ValueError("Template URI must have at least one path segment.")
        for segment in self.path_segments:
            _validate_segment(segment, "Template URI path segment")
        _validate_segment(self.template_version, "Template version")

    @classmethod
    def of(
        cls,
        extension_name: str,
        *path_segments: str,
        template_version: str = DEFAULT_TEMPLATE_VERSION,
    ) -> TemplateUri:
        """Create a template URI from its components.

        Args:
            extension_name: first URI segment identifying the template family,
                such as ``Task-T``.
            path_segments: middle URI segments, such as
                ``"network-layer", "ran-energy-saving"``.
            template_version: trailing URI segment, such as ``v2``; defaults to
                :data:`DEFAULT_TEMPLATE_VERSION`.

        Returns:
            validated template URI.

        Raises:
            TypeError: when the extension name, any path segment or the template
                version is ``None``.
            ValueError: when any component is not a simple path segment or no
                path segment is given.
        """
        return cls(extension_name, path_segments, template_version)

    @classmethod
    def parse(cls, template_uri: str | None) -> TemplateUri | None:
        """Try to parse a raw template URI into its components.

        Args:
            template_uri: template URI such as
                ``Task-T/network-layer/ran-energy-saving/v1``; may be ``None``.

        Returns:
            parsed template URI, or ``None`` when the input is ``None``, blank,
            has fewer than three segments or contains a segment that is not a
            simple path segment.
        """
        if template_uri is None or template_uri.strip() == "":
            return None
        parts = template_uri.strip().split("/")
        if len(parts) < 3:
            return None
        if not all(is_simple_segment(part) for part in parts):
            return None
        return cls(parts[0], tuple(parts[1:-1]), parts[-1])

    @property
    def segments(self) -> tuple[str, ...]:
        """Full URI segment sequence: extension, path segments, version.

        Returns:
            every URI segment in order, such as
            ``("Task-T", "network-layer", "ran-energy-saving", "v1")``.
        """
        return (self.extension_name, *self.path_segments, self.template_version)

    @property
    def uri(self) -> str:
        """Raw template URI, such as ``Negotiation-T/information-negotiation/propose/v1``.

        Returns:
            the canonical string spelling of this URI — the form the public facades accept and
            the bridge between the typed and the string spelling (D16).
        """
        return "/".join(self.segments)

    def __str__(self) -> str:
        """Return the raw URI string; identical to :attr:`uri`."""
        return self.uri


def _validate_segment(value: str | None, label: str) -> None:
    """Validate one URI component, mirroring the Java compact-constructor checks."""
    if value is None:
        raise TypeError(f"{label} must not be None.")
    if not is_simple_segment(value):
        raise ValueError(f"{label} must be a non-blank simple path segment but was {value}.")
