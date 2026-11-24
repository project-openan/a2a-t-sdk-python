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

"""Shared confirm-request mutual-exclusion validation of propose content (D14).

A propose round of the target and feasibility families has a third, derived message category
beyond the two enum actions: "requesting confirmation". The category is expressed by a non-blank
confirm-request text on the content, and it is strongly exclusive — a confirm-request round
carries only the summary and the confirm request, so the conditional item lists of the family must
all be empty and (feasibility only) the action must be ``REQUEST_FEASIBILITY_EVALUATION``.

Java implements this validation twice — once in the from-data generators
(``TargetProposeGenerator`` / ``FeasibilityProposeGenerator``) and once in the from-text extractor
(``DefaultNegotiationContentExtractor.mapTargetProposeContent`` / ``mapFeasibilityProposeContent``)
— which is evolutionary accident, not design (port plan J-4). This module is the single shared
validation function both call sites of the P5 generation pipeline consume; the templates and the
bilingual extraction prompts keep their soft wording as a second line of defence.

Error codes: every Java site raises ``negotiation.invalid_input`` carrying a single ``reason``
fact with a family-specific English sentence. The catalog also defines
``negotiation.mutually_exclusive_sections``, but no Java production site raises it (it exists for
LLM-returned semantic codes resolved by the validation pipeline), so the shared function raises
exactly what the Java sites raise — ``NegotiationGenerationError`` with
``ErrorCatalog.NEGOTIATION_INVALID_INPUT`` and the ``reason`` fact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import NegotiationGenerationError

from .enums import NegotiationAction
from .models import NegotiationItem

__all__ = ["ConfirmRequestStyle", "has_items", "present_text", "validate_confirm_request"]

#: Which of the two Java validation sites' wording the failure reason uses.
ConfirmRequestStyle = Literal["combined", "extracted"]


def validate_confirm_request(
    *,
    label: str,
    confirm_request: str | None,
    conditional_sections: Mapping[str, Sequence[NegotiationItem] | None],
    action: NegotiationAction | None = None,
    confirm_action: NegotiationAction | None = None,
    language: str | None = None,
    style: ConfirmRequestStyle = "combined",
) -> None:
    """Validate the confirm-request round of one propose content against its conditional sections.

    The check runs only when the confirm request is present (non-``None`` and non-blank). It then
    enforces, in the Java sites' order:

    1. the action the confirm-request category requires, when the family carries an action
       (feasibility only): a confirm request combined with another action is rejected first;
    2. the strong mutual exclusion: any conditional section holding at least one item next to the
       confirm request is rejected.

    The failure reason names every conditional section of the family, exactly like the static
    Java messages — not only the sections that actually carry items.

    Args:
        label: family label starting the failure reason, such as ``"Target"`` or ``"Feasibility"``.
        confirm_request: the confirm-request text of the content; blank or ``None`` means the
            round is not a confirm-request round and no validation applies.
        conditional_sections: every conditional section of the family, in template order, mapping
            its display name (used in the failure reason) to its item list; ``None`` and empty
            both omit the section.
        action: the action carried by the content, when the family has one.
        confirm_action: the action the confirm-request category requires, when the family has one.
        language: language used to render the failure message; ``None`` falls back to ``en-US``.
        style: ``"combined"`` renders the from-data generator wording ("must not be combined
            with"), ``"extracted"`` the from-text extractor wording ("extracted together with").

    Raises:
        NegotiationGenerationError: carrying ``negotiation.invalid_input`` with the ``reason``
            fact when the confirm request is combined with the wrong action or with any
            conditional section.
        TypeError: when ``confirm_action`` is given but ``action`` is ``None`` — a caller
            contract violation; both Java sites reject a missing action (with a
            ``NullPointerException``) before reaching this validation.
    """
    if not present_text(confirm_request):
        return
    if confirm_action is not None:
        if action is None:
            raise TypeError("Negotiation action must not be null when a confirm action is required.")
        if action is not confirm_action:
            carried = "content carries" if style == "combined" else "extracted action was"
            raise _invalid_input(
                language,
                f"{label} confirm request requires the {confirm_action.name} action but the {carried} {action.name}.",
            )
    if any(has_items(items) for items in conditional_sections.values()):
        joined = _join_section_names(list(conditional_sections))
        verb = "must not be combined with" if style == "combined" else "extracted together with"
        raise _invalid_input(
            language,
            f"{label} confirm request {verb} the {joined} sections; a confirm-request round "
            "carries only the summary and the confirm request.",
        )


def present_text(value: str | None) -> str | None:
    """Normalize one optional text field, mirroring the two Java ``presentText`` helpers.

    Args:
        value: candidate text; ``None`` and blank both mean absent.

    Returns:
        the text unchanged when present, ``None`` when ``None`` or blank.
    """
    return None if value is None or not value.strip() else value


def has_items(items: Sequence[NegotiationItem] | None) -> bool:
    """Report whether one optional item list holds at least one item.

    Args:
        items: candidate item list; ``None`` and empty both omit the section they drive.

    Returns:
        ``True`` when the list is non-``None`` and non-empty.
    """
    return items is not None and len(items) > 0


def _join_section_names(names: list[str]) -> str:
    """Join section display names the way the static Java reasons read them."""
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " or " + names[-1]


def _invalid_input(language: str | None, reason: str) -> NegotiationGenerationError:
    """Create the shared ``negotiation.invalid_input`` failure for one reason sentence."""
    return NegotiationGenerationError(ErrorCatalog.NEGOTIATION_INVALID_INPUT, {"reason": reason}, language=language)
