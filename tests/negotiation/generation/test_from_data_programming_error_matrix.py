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

"""The from-data programming-error matrix of the deterministic generation layer.

Full port of the Java ``FromDataProgrammingErrorMatrixTest``. The Java rows drive the orchestrator
entry points; this port drives the layer that owns each failure — the ``(type, performative)``
registry and the generator functions — because the orchestrator wiring is ported by the sibling
from-text agent. The mapping is one-to-one:

- ``null propose data`` / ``null ending data`` (the Java null guards of ``generateProposeFromData``
  / ``generateAcceptFromData``) map to the registry's null-content guard — the same
  programming-error class and message shape at this layer's entry;
- ``accept method with null conclusion`` is the registry's conclusion guard;
- ``feasibility propose without action`` is the feasibility generator's null-action guard, which
  runs BEFORE the confirm gate (the Java ``Objects.requireNonNull`` ordering);
- ``template URI type contradicts the content type`` is the registry's exact-runtime-type guard.

Every row fails with a standard ``TypeError`` (pure null arguments — Java ``NullPointerException``)
or ``ValueError`` (shape and constraint violations — Java ``IllegalArgumentException``) that is NOT
part of the SDK business-failure hierarchy and carries an English (ASCII) message pointing at the
offending input. Blank content fields are coded business failures carrying
``negotiation.content_invalid`` and are covered by the dedicated test below, exactly like the Java
split. No row ever reaches the LLM — pinned structurally, since no generator accepts an LLM seam.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

from a2a_t.common.prompt_resources.resource_access import PackagedPromptResourceAccess
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError, NegotiationGenerationError
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import (
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from a2a_t.negotiation.content import (
    FeasibilityEndingContent,
    FeasibilityProposeContent,
    NegotiationAction,
    NegotiationConclusion,
    NegotiationItem,
    NegotiationType,
    TargetEndingContent,
    TargetProposeContent,
    Vocabulary,
)
from a2a_t.negotiation.generation.generators import (
    GENERATOR_FUNCTIONS,
    generate_feasibility_propose,
    generate_target_propose,
    resolve,
)

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

ACCESS = PackagedPromptResourceAccess()
ZH = Vocabulary.for_language("zh-CN")

_CONTEXT = NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.PROPOSE)
_ACCEPT_CONTEXT = NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.ACCEPT)


def _generate_from_data(type_: Any, phase: NegotiationPerformative, content: Any) -> str:
    """Drive the deterministic from-data path: registry dispatch, then generator render."""
    generator = resolve(type_, phase, content, "zh-CN")
    assert generator is not None
    context = _ACCEPT_CONTEXT if phase is not NegotiationPerformative.PROPOSE else _CONTEXT
    template = ACCESS.template_text(_template_uri_of(type_, phase), "zh-CN")
    return generator(context, content, template, ZH)


def _template_uri_of(type_: Any, phase: NegotiationPerformative) -> str:
    """Resolve the bundled template URI of one registry pair."""
    type_segment = "common" if type_ is None else type_.type_segment
    phase_segment = (
        "accept-reject"
        if phase in (NegotiationPerformative.ACCEPT, NegotiationPerformative.REJECT)
        else ("propose" if phase is NegotiationPerformative.PROPOSE else "abort")
    )
    return f"Negotiation-T/{type_segment}/{phase_segment}/v1"


#: One row of the programming-error matrix: a failing call, the expected exception type and the
#: expected message fragment. These are the corpus-inexpressible rows (design §6 Q8); the other
#: fourteen rows (FD-PROG-01..14) are absorbed by the corpus ``from-data/programming-errors.json``
#: batch, which asserts the same exception type, message fragment and zero-LLM guarantee per case.
MATRIX: list[tuple[str, Callable[[], object], type[BaseException], str]] = [
    (
        "null propose data",
        lambda: _generate_from_data(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, None),
        TypeError,
        "Negotiation content must not be null.",
    ),
    (
        "null ending data",
        lambda: _generate_from_data(NegotiationType.TARGET, NegotiationPerformative.ACCEPT, None),
        TypeError,
        "Negotiation content must not be null.",
    ),
    (
        "accept method with null conclusion",
        lambda: _generate_from_data(
            NegotiationType.TARGET,
            NegotiationPerformative.ACCEPT,
            TargetEndingContent(None, "intent", None),
        ),
        TypeError,
        "Negotiation conclusion must not be null; the ACCEPT phase requires a conclusion.",
    ),
    (
        "feasibility propose without action",
        lambda: _generate_from_data(
            NegotiationType.FEASIBILITY,
            NegotiationPerformative.PROPOSE,
            FeasibilityProposeContent("请评估。", None, [NegotiationItem("目标", "2Mbps")], None, None),
        ),
        TypeError,
        "Feasibility negotiation action must not be null; it selects the conditional sections of the message.",
    ),
    (
        "template URI type contradicts the content type",
        lambda: _generate_from_data(
            NegotiationType.INFORMATION,
            NegotiationPerformative.PROPOSE,
            TargetProposeContent("目标协商概述。", None, None, None, None),
        ),
        ValueError,
        "Negotiation type INFORMATION requires content of type InformationProposeContent",
    ),
]


def test_every_matrix_row_fails_with_a_standard_python_exception() -> None:
    # The propose-versus-ending family mismatch of UT-GEN-002 is deliberately absent here: the
    # typed data records make that mismatch impossible to construct at the facade, so it cannot
    # occur at runtime (the registry family guard is pinned in test_registry.py instead).
    assert len(MATRIX) == 5, "exactly the corpus-inexpressible rows must stay (FD-PROG absorbs the rest)"
    for label, call, expected_exception, expected_fragment in MATRIX:
        with pytest.raises(expected_exception) as info:
            call()
        failure = info.value
        assert not isinstance(failure, A2ATError), (
            f"a programming error must not be part of the processing-error hierarchy: {label}"
        )
        assert str(failure), f"failure message must be present: {label}"
        assert _is_ascii_text(str(failure)), f"failure message must be English (ASCII): {failure}"
        assert expected_fragment in str(failure), (
            f"failure message must point at the problem of row {label} but was: {failure}"
        )


def test_no_generator_exposes_an_llm_seam() -> None:
    # Zero-LLM is structural in this layer: no generator exposes any LLM seam, so no matrix row
    # can call one (the Java row pins the same guarantee with a counting client).
    signatures = [str(inspect.signature(function)) for function in set(GENERATOR_FUNCTIONS.values())]

    assert signatures, "the registry must hold generator functions"
    assert all("llm" not in signature for signature in signatures)
    assert {function.__name__ for function in set(GENERATOR_FUNCTIONS.values())} == {
        "generate_information_propose",
        "generate_target_propose",
        "generate_feasibility_propose",
        "generate_ending",
        "generate_abort",
    }


@pytest.mark.parametrize(
    ("call", "expected_field"),
    [
        (
            lambda: generate_target_propose(
                _CONTEXT,
                TargetProposeContent(" ", None, None, None, None),
                ACCESS.template_text(TARGET_NEGOTIATION_PROPOSE_URI, "zh-CN"),
                ZH,
            ),
            "content.targetNegotiationDescription",
        ),
        (
            lambda: generate_feasibility_propose(
                _CONTEXT,
                FeasibilityProposeContent(
                    " ",
                    NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
                    [NegotiationItem("目标", "2Mbps")],
                    None,
                    None,
                ),
                ACCESS.template_text(FEASIBILITY_NEGOTIATION_PROPOSE_URI, "zh-CN"),
                ZH,
            ),
            "content.feasibilityNegotiationDescription",
        ),
        (
            lambda: _generate_from_data(
                NegotiationType.FEASIBILITY,
                NegotiationPerformative.ACCEPT,
                FeasibilityEndingContent(NegotiationConclusion.ACCEPT, " "),
            ),
            "content.feasibilitySummary",
        ),
    ],
    ids=["target-description", "feasibility-description", "feasibility-summary"],
)
def test_blank_required_content_fields_fail_with_the_coded_content_invalid_failure(
    call: Callable[[], object], expected_field: str
) -> None:
    with pytest.raises(NegotiationGenerationError) as info:
        call()

    assert info.value.code is ErrorCatalog.NEGOTIATION_CONTENT_INVALID
    assert info.value.code_str == "negotiation.content_invalid"
    assert info.value.facts["field"] == expected_field
    assert expected_field in str(info.value)


def _is_ascii_text(message: str) -> bool:
    """Report whether the message is pure ASCII, like the Java ``isAsciiText`` guard."""
    return all(ord(character) < 128 for character in message)
