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

"""Sectioned template rendering under the two blank-slot policies (port of the Java SPI).

Java 1.1.0 ships the sectioned template grammar as the ``SectionedTemplateRenderer`` SPI
with two implementations whose behavior is deliberately NOT interchangeable:

* **collapse policy** — Java ``TaskPromptRenderer``: a slot section keeps its scaffolding;
  its standalone slot line collapses to the bare slot placeholder, and when the slot value
  is blank only the section title plus a single blank separator line survive. This is the
  policy behind the task prompt pipeline.
* **drop policy** — Java ``DropBlankSlotSectionRenderer``: a slot section whose value is
  null or blank is removed from the rendered output entirely, title included, and any
  content before the first ``## `` title (such as a leading HTML description comment) is
  discarded. Rendered sections are joined with a single blank line and the result carries
  no trailing newline. This is the policy the negotiation content layer renders through,
  because a negotiation message must not show empty section headings for information the
  counterparty did not provide.

The two policies are deliberately not merged into one parameterized function (port-plan
D12): both are load-bearing for their own extension families, so the grammar of section
splitting (what is a ``## `` title, what is a standalone slot line) is shared here while
each policy keeps its own slot-line and substitution rules exactly as the Java
implementations do. Notably the collapse policy raises :class:`TaskPromptRenderError`
(``template.render_failed``) on unbalanced braces and unknown double-braced slots, while
the drop policy substitutes leniently and never fails on template content — a blank or
unknown inline placeholder in a static section simply stays verbatim.

Value semantics (both policies): slot values are strings; ``None`` counts as blank, and a
whitespace-only value counts as blank wherever the Java source checks ``isBlank()``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .errors import TaskPromptRenderError

# --- shared section grammar -------------------------------------------------

# A `## ` title line. Java SECTION_HEADER_PATTERN ("^##\\s+.+$" matched with
# Matcher.matches()); ``re.ASCII`` keeps ``\s`` ASCII-only like the Java default, so a
# full-width space after ``##`` does not start a title.
_SECTION_HEADER_PATTERN = re.compile(r"##\s+.+", re.ASCII)

# Collapse policy: a standalone slot line is optional whitespace, the ``{{name}}`` token,
# then any suffix — marker text, parenthesized prose, nothing (Java
# STANDALONE_SLOT_LINE_PATTERN, matched with Matcher.matches()).
_STANDALONE_SLOT_LINE_PATTERN = re.compile(r"\s*(\{\{([^{}]+)\}\}).*", re.ASCII)

# Collapse policy placeholder: a single- or double-braced slot token with optional inner
# padding (Java PLACEHOLDER_PATTERN, "\\{\\{?\\s*([^{}]+?)\\s*\\}\\}?").
_PLACEHOLDER_PATTERN = re.compile(r"\{\{?\s*([^{}]+?)\s*\}\}?", re.ASCII)

# Drop policy: the literal prefix every title line starts with (Java SECTION_TITLE_PREFIX).
_SECTION_TITLE_PREFIX = "## "

# Drop policy: a slot section is one whose first non-blank body line begins with a
# double-braced placeholder (Java SLOT_LINE_PATTERN, matched on line.strip()).
_SLOT_LINE_PATTERN = re.compile(r"\{\{([^{}]+)\}\}.*", re.ASCII)

# Drop policy: inline placeholder tokens inside static section bodies (Java
# SLOT_TOKEN_PATTERN).
_SLOT_TOKEN_PATTERN = re.compile(r"\{\{([^{}]+)\}\}", re.ASCII)


def collapse_sections(template_text: str, values: Mapping[str, str | None] | None = None) -> str:
    """Render a sectioned template under the collapse blank-slot policy.

    This is the port of the Java ``TaskPromptRenderer`` and the behavior the task prompt
    pipeline depends on: a section whose first non-empty body line is a standalone slot
    line keeps its scaffolding and that line collapses to the bare slot placeholder;
    whatever follows the placeholder (marker text, parenthesized prose, nothing) is
    discarded on collapse. Single-brace example text (e.g. ``{00:00~06:00,2Mbps}``) never
    triggers slot-section identification and is kept verbatim when it names no known slot.

    A blank slot value (``None`` or whitespace-only) keeps only the section title followed
    by a single blank separator line — no placeholder line and no doubled separator — so
    the section scaffolding of the template is preserved.

    Args:
        template_text: full template text whose sections are filled under the collapse
            policy.
        values: slot values keyed by slot name; ``None`` values render as blank.

    Returns:
        The rendered prompt text.

    Raises:
        TaskPromptRenderError: with the ``template.render_failed`` code when the template
            text has unbalanced braces or references an unknown double-braced slot (Java
            ``TaskPromptRenderException`` mapping).
        TypeError: when the template text or the slot values have the wrong type; a
            missing template is a programming error and stays outside the error tree,
            mirroring the Java ``NullPointerException`` guard.
    """
    _require_template_text(template_text)
    safe_values = _normalize_values(values)
    if template_text.count("{") != template_text.count("}"):
        raise TaskPromptRenderError("Template text has unbalanced braces.")
    collapsed_text = _collapse_slot_driven_sections(template_text, safe_values)
    return _substitute_placeholders(collapsed_text, safe_values)


def drop_sections(template_text: str, values: Mapping[str, str | None] | None = None) -> str:
    """Render a sectioned template under the drop blank-slot policy.

    This is the port of the Java ``DropBlankSlotSectionRenderer`` and the strategy the
    negotiation content layer depends on: the template is split into sections on
    ``## `` title lines and any content before the first title — including a leading HTML
    description comment — is discarded. A section whose first non-empty body line begins
    with a double-braced placeholder ``{{name}}`` is a slot section: it is rendered as the
    title followed by the stripped slot value, or dropped entirely (title included) when
    the slot value is null or blank. Every other section is static and passes through with
    placeholder substitution applied. Rendered sections are joined with a single blank
    line and the result carries no trailing newline.

    Unlike the collapse policy this renderer is lenient: substitution never fails on
    template content, and a blank or unknown inline placeholder in a static section stays
    verbatim, because the drop policy only removes whole slot sections — never inline
    placeholders.

    Args:
        template_text: full template text whose slot sections are filled or dropped.
        values: slot values keyed by slot name; a ``None`` or blank value drops the slot
            section.

    Returns:
        The rendered message text with sections joined by one blank line and no trailing
        newline; an empty string when no section remains.

    Raises:
        TypeError: when the template text or the slot values have the wrong type; a
            missing template is a programming error and stays outside the error tree,
            mirroring the Java ``NullPointerException`` guard.
    """
    _require_template_text(template_text)
    safe_values = _normalize_values(values)
    rendered_sections: list[str] = []
    for section in _split_sections(template_text):
        slot_name = _slot_name_of(section)
        if slot_name is not None:
            value = safe_values.get(slot_name)
            if value is None or not value.strip():
                # An unfilled slot drops the whole section, title included.
                continue
            rendered_sections.append(f"{_SECTION_TITLE_PREFIX}{section.title}\n{value.strip()}")
        else:
            rendered_sections.append(
                f"{_SECTION_TITLE_PREFIX}{section.title}\n{_substitute_slot_tokens(section.body, safe_values).rstrip()}"
            )
    return "\n\n".join(rendered_sections)


# --- collapse policy internals (Java TaskPromptRenderer) ---------------------


def _collapse_slot_driven_sections(template_text: str, values: dict[str, str | None]) -> str:
    """Split the template on ``## `` titles and collapse each slot-driven section."""
    lines = _normalize_line_endings(template_text).split("\n")
    collapsed: list[str] = []
    last_index = len(lines) - 1
    index = 0
    while index < len(lines):
        if not _is_section_header(lines[index]):
            collapsed.append(_append_line(lines[index], index < last_index))
            index += 1
            continue

        next_section_index = index + 1
        while next_section_index < len(lines) and not _is_section_header(lines[next_section_index]):
            next_section_index += 1

        _append_collapsed_section(
            collapsed,
            lines,
            index,
            next_section_index,
            next_section_index < len(lines),
            values,
        )
        index = next_section_index
    return "".join(collapsed)


def _append_collapsed_section(
    collapsed: list[str],
    lines: list[str],
    section_start: int,
    next_section_start: int,
    append_trailing_newline: bool,
    values: dict[str, str | None],
) -> None:
    """Collapse one markdown section whose first effective body line is a standalone slot."""
    collapsed.append(_append_line(lines[section_start], True))

    slot_match = _first_standalone_slot_match(lines, section_start + 1, next_section_start)
    if slot_match is None:
        for index in range(section_start + 1, next_section_start):
            collapsed.append(_append_line(lines[index], index < len(lines) - 1))
        return

    slot_name = slot_match.group(2).strip()
    slot_value = values.get(slot_name)
    if _is_blank(slot_value):
        # Blank slot: keep only the section title followed by a single blank separator
        # line; no placeholder line and no doubled separator.
        if append_trailing_newline:
            collapsed.append("\n")
        return

    first_effective_index = _first_effective_line_index(lines, section_start + 1, next_section_start)
    for index in range(section_start + 1, first_effective_index):
        collapsed.append(_append_line(lines[index], True))
    collapsed.append(_append_line(slot_match.group(1), True))
    if append_trailing_newline:
        collapsed.append("\n")


def _first_standalone_slot_match(lines: list[str], start_inclusive: int, end_exclusive: int) -> re.Match[str] | None:
    """Match the standalone slot line when the section's first effective line is one."""
    first_effective_index = _first_effective_line_index(lines, start_inclusive, end_exclusive)
    if first_effective_index < 0:
        return None
    return _STANDALONE_SLOT_LINE_PATTERN.fullmatch(lines[first_effective_index])


def _first_effective_line_index(lines: list[str], start_inclusive: int, end_exclusive: int) -> int:
    """Return the index of the first non-blank line in the range, or -1 when there is none."""
    for index in range(start_inclusive, end_exclusive):
        if not _is_blank(lines[index]):
            return index
    return -1


def _substitute_placeholders(collapsed_text: str, values: dict[str, str | None]) -> str:
    """Replace slot placeholders with normalized slot values over the whole text."""

    def replace(match: re.Match[str]) -> str:
        slot_name = match.group(1).strip()
        double_braced = match.group(0).startswith("{{")
        if slot_name not in values:
            if not double_braced:
                # Single-brace text that is not a slot is example prose
                # (e.g. {00:00~06:00,2Mbps}); keep it verbatim.
                return match.group(0)
            raise TaskPromptRenderError(f"Unknown slot referenced by template: {slot_name}")
        value = values[slot_name]
        return "" if value is None else value

    return _PLACEHOLDER_PATTERN.sub(replace, collapsed_text)


def _is_section_header(line: str) -> bool:
    """Return whether the line is a ``## `` title line."""
    return _SECTION_HEADER_PATTERN.fullmatch(line) is not None


def _append_line(line: str, append_trailing_newline: bool) -> str:
    """Join one line and its trailing newline (Java ``appendLine`` as a pure helper)."""
    return line + ("\n" if append_trailing_newline else "")


def _normalize_line_endings(template_text: str) -> str:
    """Normalize CRLF and CR line endings to LF before splitting (Java parity)."""
    return template_text.replace("\r\n", "\n").replace("\r", "\n")


# --- drop policy internals (Java DropBlankSlotSectionRenderer) ---------------


@dataclass(frozen=True)
class _Section:
    """One section of a template: a title plus the body lines up to the next title."""

    title: str
    body: str


def _split_sections(template_text: str) -> list[_Section]:
    """Split the template into ``## `` sections, discarding content before the first title."""
    sections: list[_Section] = []
    current_title: str | None = None
    current_body: list[str] = []
    for line in template_text.split("\n"):
        if line.startswith(_SECTION_TITLE_PREFIX):
            if current_title is not None:
                sections.append(_Section(title=current_title, body="\n".join(current_body)))
            current_title = line[len(_SECTION_TITLE_PREFIX) :].strip()
            current_body = []
        elif current_title is not None:
            current_body.append(line)
        # Lines before the first title, such as the leading HTML comment, are discarded.
    if current_title is not None:
        sections.append(_Section(title=current_title, body="\n".join(current_body)))
    return sections


def _slot_name_of(section: _Section) -> str | None:
    """Return the slot name when the section's first non-blank body line is a slot line."""
    for line in section.body.split("\n"):
        if _is_blank(line):
            continue
        match = _SLOT_LINE_PATTERN.fullmatch(line.strip())
        if match is not None:
            return match.group(1)
        return None
    return None


def _substitute_slot_tokens(body: str, values: dict[str, str | None]) -> str:
    """Fill inline slot tokens in a static section body, keeping blank ones verbatim."""

    def replace(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        if value is None or not value.strip():
            return match.group(0)
        return value

    return _SLOT_TOKEN_PATTERN.sub(replace, body)


# --- shared input handling ---------------------------------------------------


def _is_blank(value: str | None) -> bool:
    """Return whether the value is blank (Java ``isBlank()``: null or whitespace-only)."""
    return value is None or not value.strip()


def _require_template_text(template_text: object) -> None:
    """Mirror the Java null guard: a missing template is a programming error."""
    if template_text is None:
        raise TypeError("Template text must not be null.")
    if not isinstance(template_text, str):
        raise TypeError(f"Template text must be a string, got {type(template_text).__name__}.")


def _normalize_values(values: Mapping[str, str | None] | None) -> dict[str, str | None]:
    """Normalize the slot values: a null map behaves as an empty map (Java parity)."""
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise TypeError(f"Slot values must be a mapping, got {type(values).__name__}.")
    normalized: dict[str, str | None] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            raise TypeError(f"Slot names must be strings, got {type(key).__name__}.")
        if value is not None and not isinstance(value, str):
            raise TypeError(f"Slot values must be strings or None, got {type(value).__name__} for slot {key!r}.")
        normalized[key] = value
    return normalized
