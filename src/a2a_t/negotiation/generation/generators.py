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

"""Deterministic from-data negotiation message generators (port of the Java generator family, D15).

Java ships seven generator classes — three propose generators, three ending generators and one
abort generator — behind ``AbstractNegotiationGenerator``. This port collapses them into FIVE
function-style generators plus a ``(type, performative)`` registry (port plan D15):

===================  ==========================================================
function             serves
===================  ==========================================================
``generate_ending``  the shared ending generator of the information / target /
                     feasibility families (accept and reject phases)
``generate_information_propose``   information propose messages
``generate_target_propose``       target propose messages
``generate_feasibility_propose``  feasibility propose messages
``generate_abort``   the type-independent abort message (common template)
===================  ==========================================================

Sharing the ending generator across the three families is faithful to the Java behavior: the
family differences live entirely in the content model and the vocabulary — which slot keys carry
the conclusion literal and the result content, and which field ``requiredText`` / ``requiredItems``
validates — and each family branch below preserves its Java validation order and failure facts
verbatim. The one visible divergence: a directly-called Java family generator reports its own
family in the wrong-runtime-type ``IllegalArgumentException``, while the shared function names all
three ending content types; the registry (the only supported dispatch path) rejects those calls
before the generator is reached either way.

The shared plumbing of ``AbstractNegotiationGenerator`` lives here as module-private helpers:
``_content_of`` (exact runtime type), ``_renderable_conclusion`` (``None`` conclusion is a
programming error, the ``ABORT`` conclusion is rejected by the typed ending generators — D2),
``_required_text`` / ``_required_items`` (coded ``negotiation.content_invalid``), ``_format_items``
(vocabulary punctuation) and the render delegation through
:mod:`a2a_t.negotiation.generation.prompt_renderer`.

The confirm-request mutual exclusion of the target and feasibility propose rounds goes through the
single shared :func:`a2a_t.negotiation.content.confirm_request.validate_confirm_request` (D14) with
the ``combined`` wording, instead of the two duplicated Java implementations. The feasibility
action ``None`` check stays in front of that gate, exactly like the Java
``Objects.requireNonNull(action, ...)`` ordering.

ZERO LLM: this module is fully deterministic — no function accepts or touches an LLM client; the
from-text extraction step that does lives in the extractor and orchestrator layers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Final, TypeAlias, TypeVar, cast

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import NegotiationGenerationError
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative

from ..content.confirm_request import present_text, validate_confirm_request
from ..content.enums import NegotiationAction, NegotiationConclusion, NegotiationType
from ..content.models import (
    FeasibilityEndingContent,
    FeasibilityProposeContent,
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationContent,
    NegotiationEndingContent,
    NegotiationItem,
    NegotiationProposeContent,
    TargetEndingContent,
    TargetProposeContent,
)
from ..content.vocabulary import Vocabulary
from .item_formatter import format_items
from .prompt_renderer import render

__all__ = [
    "GENERATOR_FUNCTIONS",
    "NegotiationGenerator",
    "generate_abort",
    "generate_ending",
    "generate_feasibility_propose",
    "generate_information_propose",
    "generate_target_propose",
    "resolve",
]

logger = logging.getLogger(__name__)

_T = TypeVar("_T", bound=NegotiationContent)

#: Signature shared by the five function-style generators: negotiation context, typed content,
#: loaded template text and the vocabulary of the message language (the Java generator interface).
NegotiationGenerator: TypeAlias = Callable[[NegotiationContext, NegotiationContent, str, Vocabulary], str]


# ---------------------------------------------------------------------------
# The five generators (D15)
# ---------------------------------------------------------------------------


def generate_information_propose(
    context: NegotiationContext, content: NegotiationContent, template_text: str, vocabulary: Vocabulary
) -> str:
    """Generate an information negotiation propose message.

    The message carries the requested information items and, when present, a free-form line
    describing how the missing items relate to each other, appended after the item list.

    Args:
        context: negotiation context of the message.
        content: information propose content.
        template_text: information propose template to render.
        vocabulary: vocabulary of the message language.

    Returns:
        the rendered information propose message text.

    Raises:
        ValueError: when the content is ``None`` or of another runtime type.
        NegotiationGenerationError: carrying ``negotiation.content_invalid`` when the requested
            item list is ``None`` or empty.
    """
    propose = _content_of(content, InformationProposeContent, "Information propose generator")
    _required_items(propose.items, "items", "Information negotiation propose message requested items", vocabulary)
    slots = {vocabulary.get("slot.info_items"): _information_items_slot_value(propose, vocabulary)}
    return render(template_text, slots)


def generate_target_propose(
    context: NegotiationContext, content: NegotiationContent, template_text: str, vocabulary: Vocabulary
) -> str:
    """Generate a target negotiation propose message.

    Besides the required target negotiation summary, the message renders round-driven conditional
    sections: the intent understanding section appears only on the first round, the alignment and
    clarification section only on later rounds, and the clarification request section only when
    clarification items are present. A non-blank confirm request marks the "target clarified and
    requesting confirmation" category instead; the three conditional lists must then be empty, so
    only the summary and the confirm request section remain.

    Args:
        context: negotiation context of the message (its round drives the conditional sections).
        content: target propose content.
        template_text: target propose template to render.
        vocabulary: vocabulary of the message language.

    Returns:
        the rendered target propose message text.

    Raises:
        ValueError: when the content is ``None`` or of another runtime type.
        NegotiationGenerationError: carrying ``negotiation.content_invalid`` when the summary is
            blank, or ``negotiation.invalid_input`` when the confirm request is combined with any
            of the three conditional sections.
    """
    propose = _content_of(content, TargetProposeContent, "Target propose generator")
    _required_text(
        propose.target_negotiation_description,
        "content.targetNegotiationDescription",
        "Target negotiation description",
        vocabulary,
    )
    confirm_request = present_text(propose.target_confirm_request)
    validate_confirm_request(
        label="Target",
        confirm_request=confirm_request,
        conditional_sections={
            "intent understanding": propose.intent_understanding,
            "alignment and clarification": propose.alignment_and_clarification,
            "clarification request": propose.request_for_clarification,
        },
        language=vocabulary.language,
    )
    slots: dict[str, str] = {vocabulary.get("slot.target"): propose.target_negotiation_description}
    if context.round == 1:
        slots[vocabulary.get("slot.target_intent")] = _format_items(propose.intent_understanding, vocabulary)
    else:
        slots[vocabulary.get("slot.target_alignment")] = _format_items(propose.alignment_and_clarification, vocabulary)
    slots[vocabulary.get("slot.target_clarification")] = _format_items(propose.request_for_clarification, vocabulary)
    if confirm_request is not None:
        slots[vocabulary.get("slot.target_confirm_request")] = confirm_request
    return render(template_text, slots)


def generate_feasibility_propose(
    context: NegotiationContext, content: NegotiationContent, template_text: str, vocabulary: Vocabulary
) -> str:
    """Generate a feasibility negotiation propose message.

    The message action selects which conditional section is rendered: requesting a feasibility
    evaluation renders the contents to evaluate, proposing an alternative after an infeasible
    outcome renders the infeasibility details and proposal, and a non-blank confirm request marks
    the derived "assessed as feasible and requesting confirmation" category (action
    ``REQUEST_FEASIBILITY_EVALUATION`` with both lists empty) that renders only the confirm
    request. Exactly one of the three sections is always present.

    Args:
        context: negotiation context of the message.
        content: feasibility propose content.
        template_text: feasibility propose template to render.
        vocabulary: vocabulary of the message language.

    Returns:
        the rendered feasibility propose message text.

    Raises:
        TypeError: when the action is ``None`` — the Java null check runs before the confirm gate.
        ValueError: when the content is ``None`` or of another runtime type.
        NegotiationGenerationError: carrying ``negotiation.content_invalid`` when the summary is
            blank or the action-selected section is empty, or ``negotiation.invalid_input`` when
            the confirm request is combined with the wrong action or either conditional section.
    """
    propose = _content_of(content, FeasibilityProposeContent, "Feasibility propose generator")
    _required_text(
        propose.feasibility_negotiation_description,
        "content.feasibilityNegotiationDescription",
        "Feasibility negotiation description",
        vocabulary,
    )
    action = propose.action
    if action is None:
        raise TypeError(
            "Feasibility negotiation action must not be null; it selects the conditional sections of the message."
        )
    confirm_request = present_text(propose.feasibility_confirm_request)
    slots: dict[str, str] = {vocabulary.get("slot.feasibility"): propose.feasibility_negotiation_description}
    validate_confirm_request(
        label="Feasibility",
        confirm_request=confirm_request,
        conditional_sections={
            "contents to evaluate": propose.contents_to_evaluate,
            "infeasibility details and proposal": propose.infeasibility_details_and_proposal,
        },
        action=action,
        confirm_action=NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
        language=vocabulary.language,
    )
    if confirm_request is not None:
        slots[vocabulary.get("slot.feasibility_confirm_request")] = confirm_request
    elif action is NegotiationAction.REQUEST_FEASIBILITY_EVALUATION:
        slots[vocabulary.get("slot.feasibility_evaluate")] = _format_items(
            _required_items(
                propose.contents_to_evaluate,
                "content.contentsToEvaluate",
                "Contents to evaluate of a feasibility evaluation request",
                vocabulary,
            ),
            vocabulary,
        )
    else:
        slots[vocabulary.get("slot.feasibility_infeasible")] = _format_items(
            _required_items(
                propose.infeasibility_details_and_proposal,
                "content.infeasibilityDetailsAndProposal",
                "Infeasibility details and proposal of an alternative proposal",
                vocabulary,
            ),
            vocabulary,
        )
    return render(template_text, slots)


def generate_ending(
    context: NegotiationContext, content: NegotiationContent, template_text: str, vocabulary: Vocabulary
) -> str:
    """Generate the shared terminal (accept or reject) message of one typed negotiation family.

    One function serves the information, target and feasibility families for both the accept and
    the reject phases (D15): the family is read from the exact runtime content type, and each
    family branch preserves its Java generator's validation order, slot keys and failure facts.
    The information family requires non-empty result items, the feasibility family a non-blank
    summary, and the target family the field matching the conclusion (confirmed intent on accept,
    failure reason on reject).

    Args:
        context: negotiation context of the message.
        content: typed ending content of one of the three families.
        template_text: family ending template to render.
        vocabulary: vocabulary of the message language.

    Returns:
        the rendered terminal message text.

    Raises:
        TypeError: when the conclusion is ``None``.
        ValueError: when the conclusion is ``ABORT`` (D2: the abort message is generated through
            the common abort template instead) or the content is of another runtime type.
        NegotiationGenerationError: carrying ``negotiation.content_invalid`` when the family's
            required result field is blank or its result item list is ``None`` or empty.
    """
    if type(content) is InformationEndingContent:
        return _render_information_ending(content, template_text, vocabulary)
    if type(content) is TargetEndingContent:
        return _render_target_ending(content, template_text, vocabulary)
    if type(content) is FeasibilityEndingContent:
        return _render_feasibility_ending(content, template_text, vocabulary)
    raise ValueError(
        "Ending generator requires content of type InformationEndingContent, TargetEndingContent or "
        f"FeasibilityEndingContent but received {_received_name(content)}."
    )


def generate_abort(
    context: NegotiationContext, content: NegotiationContent, template_text: str, vocabulary: Vocabulary
) -> str:
    """Generate the type-independent abort negotiation message.

    The common abort template carries the fixed ``Abort`` conclusion section, so this generator
    fills only the termination reason slot.

    Args:
        context: negotiation context of the message.
        content: abort content carrying the termination reason.
        template_text: common abort template to render.
        vocabulary: vocabulary of the message language.

    Returns:
        the rendered abort message text.

    Raises:
        ValueError: when the content is ``None`` or of another runtime type.
        NegotiationGenerationError: carrying ``negotiation.content_invalid`` when the termination
            reason is blank.
    """
    abort_content = _content_of(content, NegotiationAbortContent, "Abort generator")
    slots = {
        vocabulary.get("slot.termination_reason"): _required_text(
            abort_content.termination_reason,
            "content.terminationReason",
            "Termination reason of an abort negotiation message",
            vocabulary,
        )
    }
    return render(template_text, slots)


# ---------------------------------------------------------------------------
# Registry: (type, performative) -> generator function
# ---------------------------------------------------------------------------

#: The dispatch table from one ``(negotiation type, performative)`` pair to its generator
#: function. ``None`` as the type addresses the type-independent abort generator. This table is
#: the D15 counterpart of the Java ``EnumMap`` pair inside ``NegotiationGeneratorRegistry``; an
#: unknown key is a programming error, never a business failure.
GENERATOR_FUNCTIONS: Final[dict[tuple[NegotiationType | None, NegotiationPerformative], NegotiationGenerator]] = {
    (None, NegotiationPerformative.ABORT): generate_abort,
    (NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE): generate_information_propose,
    (NegotiationType.TARGET, NegotiationPerformative.PROPOSE): generate_target_propose,
    (NegotiationType.FEASIBILITY, NegotiationPerformative.PROPOSE): generate_feasibility_propose,
    (NegotiationType.INFORMATION, NegotiationPerformative.ACCEPT): generate_ending,
    (NegotiationType.INFORMATION, NegotiationPerformative.REJECT): generate_ending,
    (NegotiationType.TARGET, NegotiationPerformative.ACCEPT): generate_ending,
    (NegotiationType.TARGET, NegotiationPerformative.REJECT): generate_ending,
    (NegotiationType.FEASIBILITY, NegotiationPerformative.ACCEPT): generate_ending,
    (NegotiationType.FEASIBILITY, NegotiationPerformative.REJECT): generate_ending,
}

_PROPOSE_CONTENT_CLASSES: Final[dict[NegotiationType, type[NegotiationProposeContent]]] = {
    NegotiationType.INFORMATION: InformationProposeContent,
    NegotiationType.TARGET: TargetProposeContent,
    NegotiationType.FEASIBILITY: FeasibilityProposeContent,
}

_ENDING_CONTENT_CLASSES: Final[dict[NegotiationType, type[NegotiationEndingContent]]] = {
    NegotiationType.INFORMATION: InformationEndingContent,
    NegotiationType.TARGET: TargetEndingContent,
    NegotiationType.FEASIBILITY: FeasibilityEndingContent,
}


def resolve(
    negotiation_type: NegotiationType | None,
    phase: NegotiationPerformative,
    content: NegotiationContent,
    language: str | None = None,
) -> NegotiationGenerator:
    """Resolve the generator for one ``(type, phase, content)`` triple.

    Dispatch requires an exact runtime type match between the content and the addressed
    ``(type, phase)`` pair: propose phases only accept propose content, terminal phases only
    accept ending content whose conclusion matches the phase, and the content type must match the
    negotiation type. The abort phase dispatches to the type-independent abort generator and
    requires abort content. Subtype matching is deliberately not supported; new content types must
    be registered explicitly.

    Args:
        negotiation_type: negotiation type addressed by the template URI; ``None`` only for the
            type-independent abort phase.
        phase: API-level phase addressed by the calling method.
        content: typed content of the message.
        language: message language used to render the failure message of a conclusion mismatch;
            ``None`` falls back to ``en-US``.

    Returns:
        the generator function registered for the exact ``(type, phase)`` pair.

    Raises:
        TypeError: when the phase or content is ``None``, the type is ``None`` on a typed phase, or
            an ending content carries no conclusion.
        ValueError: when the content family does not match the phase, the content runtime type
            does not match the negotiation type, the abort phase carries a type or non-abort
            content, or no generator is registered for the pair.
        NegotiationGenerationError: carrying ``negotiation.conclusion_mismatch`` when an ending
            content carries a conclusion that does not match the phase.
    """
    if phase is None:
        raise TypeError("Negotiation phase must not be null.")
    if content is None:
        raise TypeError("Negotiation content must not be null.")
    if phase is NegotiationPerformative.ABORT:
        if negotiation_type is not None:
            raise ValueError(
                f"The ABORT phase is type-independent and must not carry a type but carried {negotiation_type}."
            )
        if type(content) is not NegotiationAbortContent:
            raise ValueError(f"The ABORT phase requires abort content but received {type(content).__name__}.")
        logger.debug("negotiation_generator_dispatched generator=generate_abort type=common performative=ABORT")
        return generate_abort
    if negotiation_type is None:
        raise TypeError(f"Negotiation type must not be null for the {phase} phase.")
    propose_phase = phase is NegotiationPerformative.PROPOSE
    propose_content = isinstance(content, NegotiationProposeContent)
    if propose_phase != propose_content:
        raise ValueError(
            f"The {phase} phase requires {'propose' if propose_phase else 'ending'} content but received "
            f"{'propose' if propose_content else 'ending'} content of type {type(content).__name__}."
        )
    expected_class = (_PROPOSE_CONTENT_CLASSES if propose_phase else _ENDING_CONTENT_CLASSES).get(negotiation_type)
    if expected_class is None:
        raise ValueError(f"No negotiation generator is registered for type {negotiation_type} and phase {phase}.")
    if type(content) is not expected_class:
        raise ValueError(
            f"Negotiation type {negotiation_type} requires content of type {expected_class.__name__} "
            f"but received {type(content).__name__}."
        )
    if not propose_phase:
        _require_conclusion_matches_phase(cast(NegotiationEndingContent, content), phase, language)
    generator = GENERATOR_FUNCTIONS.get((negotiation_type, phase))
    if generator is None:
        raise ValueError(f"No negotiation generator is registered for type {negotiation_type} and phase {phase}.")
    logger.debug(
        "negotiation_generator_dispatched generator=%s type=%s performative=%s",
        generator.__name__,
        negotiation_type,
        phase,
    )
    return generator


# ---------------------------------------------------------------------------
# Shared plumbing (port of AbstractNegotiationGenerator)
# ---------------------------------------------------------------------------


def _content_of(content: NegotiationContent, expected_type: type[_T], generator_name: str) -> _T:
    """Cast the content to the exact type this generator serves (Java ``contentOf``).

    Raises:
        ValueError: when the content is ``None`` or of another runtime type (Java
            ``IllegalArgumentException`` semantics; plan 2.3).
    """
    if content is None or type(content) is not expected_type:
        raise ValueError(
            f"{generator_name} requires content of type {expected_type.__name__} "
            f"but received {_received_name(content)}."
        )
    return content


def _renderable_conclusion(conclusion: NegotiationConclusion | None) -> NegotiationConclusion:
    """Validate that a conclusion is renderable by a typed ending generator.

    Raises:
        TypeError: when the conclusion is ``None`` (Java ``NullPointerException``).
        ValueError: when the conclusion is ``ABORT`` — the abort message has no typed conclusion
            slot and is generated against the common abort template instead (Java
            ``IllegalArgumentException``).
    """
    if conclusion is None:
        raise TypeError(
            "Negotiation conclusion must not be null; accept and reject are the renderable "
            "conclusions of a typed negotiation."
        )
    if conclusion is NegotiationConclusion.ABORT:
        raise ValueError(
            "Typed negotiation templates carry no Abort conclusion slot; generate the abort message of a "
            "terminated negotiation with generate_abort_from_data against the common abort template instead."
        )
    return conclusion


def _required_text(value: str | None, field: str, description: str, vocabulary: Vocabulary) -> str:
    """Validate that a required text field is present.

    Raises:
        NegotiationGenerationError: carrying ``negotiation.content_invalid`` when the value is
            ``None`` or blank.
    """
    if value is None or not value.strip():
        raise _content_invalid(field, f"{description} must not be blank.", vocabulary)
    return value


def _required_items(
    items: Sequence[NegotiationItem] | None, field: str, description: str, vocabulary: Vocabulary
) -> Sequence[NegotiationItem]:
    """Validate that a required item list is present and non-empty.

    Raises:
        NegotiationGenerationError: carrying ``negotiation.content_invalid`` when the list is
            ``None`` or empty.
    """
    if items is None or len(items) == 0:
        raise _content_invalid(field, f"{description} must contain at least one item.", vocabulary)
    return items


def _content_invalid(field: str, reason: str, vocabulary: Vocabulary) -> NegotiationGenerationError:
    """Build the coded ``negotiation.content_invalid`` failure of a from-data generation."""
    return NegotiationGenerationError(
        ErrorCatalog.NEGOTIATION_CONTENT_INVALID,
        {"field": field, "reason": reason},
        language=vocabulary.language,
    )


def _format_items(items: Sequence[NegotiationItem] | None, vocabulary: Vocabulary) -> str:
    """Format an item list with the list punctuation of the message language."""
    return format_items(items, vocabulary.get("punct.list_colon"))


def _information_items_slot_value(content: InformationProposeContent, vocabulary: Vocabulary) -> str:
    """Build the information items slot value, appending the relationship line when present."""
    items = _format_items(content.items, vocabulary)
    if content.relationship is None or not content.relationship.strip():
        return items
    relationship_line = vocabulary.get("label.relationship") + content.relationship
    return relationship_line if items == "" else f"{items}\n{relationship_line}"


def _render_information_ending(content: InformationEndingContent, template_text: str, vocabulary: Vocabulary) -> str:
    """Render the information family ending message: conclusion literal plus result items."""
    _renderable_conclusion(content.conclusion)
    _required_items(content.items, "items", "Information negotiation terminal message result content", vocabulary)
    slots = {
        vocabulary.get("slot.info_conclusion"): content.conclusion.literal,
        vocabulary.get("slot.info_result_content"): _format_items(content.items, vocabulary),
    }
    return render(template_text, slots)


def _render_target_ending(content: TargetEndingContent, template_text: str, vocabulary: Vocabulary) -> str:
    """Render the target family ending message: conclusion literal plus the matching result field."""
    conclusion = _renderable_conclusion(content.conclusion)
    if conclusion is NegotiationConclusion.ACCEPT:
        result_content = _required_text(
            content.confirmed_intent,
            "content.confirmedIntent",
            "Confirmed intent of an accepting target negotiation message",
            vocabulary,
        )
    else:
        result_content = _required_text(
            content.failure_reason,
            "content.failureReason",
            "Failure reason of a rejecting target negotiation message",
            vocabulary,
        )
    slots = {
        vocabulary.get("slot.target_conclusion"): conclusion.literal,
        vocabulary.get("slot.target_result_content"): result_content,
    }
    return render(template_text, slots)


def _render_feasibility_ending(content: FeasibilityEndingContent, template_text: str, vocabulary: Vocabulary) -> str:
    """Render the feasibility family ending message: conclusion literal plus the summary.

    The summary slot name differs from its section title, so the vocabulary exception key
    (``slot.feasibility_confirm``) is used.
    """
    _renderable_conclusion(content.conclusion)
    summary = _required_text(
        content.feasibility_summary,
        "content.feasibilitySummary",
        "Feasibility summary of a terminal feasibility negotiation message",
        vocabulary,
    )
    slots = {
        vocabulary.get("slot.feasibility_conclusion"): content.conclusion.literal,
        vocabulary.get("slot.feasibility_confirm"): summary,
    }
    return render(template_text, slots)


def _require_conclusion_matches_phase(
    content: NegotiationEndingContent, phase: NegotiationPerformative, language: str | None
) -> None:
    """Require the conclusion of an ending content to match the addressed phase.

    Raises:
        TypeError: when the conclusion is ``None``.
        NegotiationGenerationError: carrying ``negotiation.conclusion_mismatch`` with the expected
            and actual conclusion literals when they differ.
    """
    conclusion = content.conclusion
    if conclusion is None:
        raise TypeError(f"Negotiation conclusion must not be null; the {phase} phase requires a conclusion.")
    expected = NegotiationConclusion.ACCEPT if phase is NegotiationPerformative.ACCEPT else NegotiationConclusion.REJECT
    if conclusion is not expected:
        raise NegotiationGenerationError(
            ErrorCatalog.NEGOTIATION_CONCLUSION_MISMATCH,
            {"expected": expected.literal, "actual": conclusion.literal},
            language=language,
        )


def _received_name(content: NegotiationContent | None) -> str:
    """Name the received content for a wrong-runtime-type failure message."""
    return "None" if content is None else type(content).__name__
