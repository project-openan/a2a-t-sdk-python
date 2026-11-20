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

"""Negotiation render step on top of the drop blank-slot section policy (port of Java
``NegotiationPromptRenderer``).

A negotiation template is split into sections on ``## `` title lines; any content before the first
title, including a leading HTML description comment, is discarded. A section whose first non-empty
body line is a slot placeholder line such as ``{{required_information_items}} (required)`` — or the
same shape with the full-width required/optional markers used by zh-CN templates — is a slot
section: it is rendered as the title followed by the slot value, or dropped entirely when the slot
value is ``None`` or blank. Every other section is static and passes through with placeholder
substitution applied. Rendered sections are joined with a single blank line and the result carries
no trailing newline.

The rendering itself delegates to the shared :func:`a2a_t.prompt.task_rendering.sectioned_renderer.drop_sections`
of the prompt kernel, which owns the drop policy of the sectioned template grammar (D12). Unlike
the delegate's own ``TypeError`` null guard, this adapter rejects a ``None`` template text with the
internal :class:`NegotiationRenderError` instead, so the orchestration layer wraps the failure into
the typed negotiation generation failure ``template.render_failed`` (carrying the ``template_uri``
and ``reason`` facts) rather than letting it leak.
"""

from __future__ import annotations

from collections.abc import Mapping

from a2a_t.prompt.task_rendering.sectioned_renderer import drop_sections

__all__ = ["NegotiationRenderError", "render"]


class NegotiationRenderError(Exception):
    """Internal failure raised when rendering a negotiation template cannot proceed.

    Port of the Java ``NegotiationRenderException``: this exception never bubbles out of the public
    API; the orchestration layer maps it to a :class:`~a2a_t.core.errors.exceptions.NegotiationGenerationError`
    carrying ``template.render_failed`` with the ``template_uri`` and ``reason`` facts instead. It
    therefore deliberately stays outside the :class:`~a2a_t.core.errors.exceptions.A2ATError` tree.
    """


def render(template_text: str, slots: Mapping[str, str | None]) -> str:
    """Render one negotiation template text with the given slot values.

    Args:
        template_text: full template text whose slot sections are filled or dropped.
        slots: slot values keyed by the language-specific slot name; a ``None`` or blank value drops
            the slot section.

    Returns:
        the rendered message text with sections joined by one blank line and no trailing newline;
        an empty string when no section remains.

    Raises:
        NegotiationRenderError: when the template text is ``None``; the orchestration layer wraps
            this internal failure into a typed negotiation generation failure instead of letting it
            leak.
        TypeError: when the template text or the slot values have the wrong type (the delegate's
            programming-error guards).
    """
    if template_text is None:
        raise NegotiationRenderError("Negotiation template text must not be null.")
    return drop_sections(template_text, slots)
