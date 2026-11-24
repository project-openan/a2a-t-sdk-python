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

"""Tests of the ``(type, performative)`` generator registry and its dispatch gauntlet.

Port of the Java ``NegotiationGeneratorRegistryTest``: every type-and-phase combination resolves
its generator, the family / runtime-type / conclusion guards reject the mismatches with the exact
Java messages (programming errors as ``TypeError``/``ValueError``), a conclusion mismatch carries
the coded ``negotiation.conclusion_mismatch`` failure, and an unknown registry key is a programming
error, never a business failure.
"""

from __future__ import annotations

import pytest

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError, NegotiationGenerationError
from a2a_t.core.metadata import NegotiationPerformative
from a2a_t.negotiation.content import (
    FeasibilityEndingContent,
    FeasibilityProposeContent,
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationAction,
    NegotiationConclusion,
    NegotiationItem,
    NegotiationType,
    TargetEndingContent,
    TargetProposeContent,
)
from a2a_t.negotiation.generation import generators as generators_module
from a2a_t.negotiation.generation.generators import (
    GENERATOR_FUNCTIONS,
    generate_abort,
    generate_ending,
    generate_feasibility_propose,
    generate_information_propose,
    generate_target_propose,
    resolve,
)

ITEM = NegotiationItem("名称", "值")


def _resolve(type: NegotiationType | None, phase: NegotiationPerformative, content: object) -> object:
    """Resolve one generator through the registry's zh-CN default of the Java tests."""
    return resolve(type, phase, content, "zh-CN")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("type_", "phase", "content", "expected"),
    [
        (
            NegotiationType.INFORMATION,
            NegotiationPerformative.PROPOSE,
            InformationProposeContent([], None),
            generate_information_propose,
        ),
        (
            NegotiationType.TARGET,
            NegotiationPerformative.PROPOSE,
            TargetProposeContent("描述", None, None, None, None),
            generate_target_propose,
        ),
        (
            NegotiationType.FEASIBILITY,
            NegotiationPerformative.PROPOSE,
            FeasibilityProposeContent("描述", NegotiationAction.REQUEST_FEASIBILITY_EVALUATION, [ITEM], None, None),
            generate_feasibility_propose,
        ),
        (
            NegotiationType.INFORMATION,
            NegotiationPerformative.ACCEPT,
            InformationEndingContent(NegotiationConclusion.ACCEPT, []),
            generate_ending,
        ),
        (
            NegotiationType.INFORMATION,
            NegotiationPerformative.REJECT,
            InformationEndingContent(NegotiationConclusion.REJECT, []),
            generate_ending,
        ),
        (
            NegotiationType.TARGET,
            NegotiationPerformative.ACCEPT,
            TargetEndingContent(NegotiationConclusion.ACCEPT, "确认的意图", None),
            generate_ending,
        ),
        (
            NegotiationType.TARGET,
            NegotiationPerformative.REJECT,
            TargetEndingContent(NegotiationConclusion.REJECT, None, "失败原因"),
            generate_ending,
        ),
        (
            NegotiationType.FEASIBILITY,
            NegotiationPerformative.ACCEPT,
            FeasibilityEndingContent(NegotiationConclusion.ACCEPT, "评估结论"),
            generate_ending,
        ),
        (
            NegotiationType.FEASIBILITY,
            NegotiationPerformative.REJECT,
            FeasibilityEndingContent(NegotiationConclusion.REJECT, "评估结论"),
            generate_ending,
        ),
        (None, NegotiationPerformative.ABORT, NegotiationAbortContent("终止原因"), generate_abort),
    ],
    ids=[
        "information-propose",
        "target-propose",
        "feasibility-propose",
        "information-accept",
        "information-reject",
        "target-accept",
        "target-reject",
        "feasibility-accept",
        "feasibility-reject",
        "common-abort",
    ],
)
def test_dispatches_every_type_and_phase_combination(
    type_: NegotiationType | None,
    phase: NegotiationPerformative,
    content: object,
    expected: object,
) -> None:
    assert _resolve(type_, phase, content) is expected


def test_the_registry_holds_exactly_ten_keys_over_five_functions() -> None:
    functions = set(GENERATOR_FUNCTIONS.values())

    assert len(GENERATOR_FUNCTIONS) == 10
    assert functions == {
        generate_information_propose,
        generate_target_propose,
        generate_feasibility_propose,
        generate_ending,
        generate_abort,
    }
    assert set(GENERATOR_FUNCTIONS) == {
        (None, NegotiationPerformative.ABORT),
        (NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE),
        (NegotiationType.TARGET, NegotiationPerformative.PROPOSE),
        (NegotiationType.FEASIBILITY, NegotiationPerformative.PROPOSE),
        (NegotiationType.INFORMATION, NegotiationPerformative.ACCEPT),
        (NegotiationType.INFORMATION, NegotiationPerformative.REJECT),
        (NegotiationType.TARGET, NegotiationPerformative.ACCEPT),
        (NegotiationType.TARGET, NegotiationPerformative.REJECT),
        (NegotiationType.FEASIBILITY, NegotiationPerformative.ACCEPT),
        (NegotiationType.FEASIBILITY, NegotiationPerformative.REJECT),
    }


@pytest.mark.parametrize(
    ("type_", "phase", "content", "fragment"),
    [
        (
            NegotiationType.INFORMATION,
            NegotiationPerformative.ACCEPT,
            InformationProposeContent([], None),
            "ACCEPT phase requires ending content but received propose content of type InformationProposeContent",
        ),
        (
            NegotiationType.INFORMATION,
            NegotiationPerformative.PROPOSE,
            InformationEndingContent(NegotiationConclusion.ACCEPT, []),
            "PROPOSE phase requires propose content but received ending content of type InformationEndingContent",
        ),
    ],
    ids=["propose-content-in-terminal-phase", "ending-content-in-propose-phase"],
)
def test_rejects_propose_content_in_terminal_phase_and_ending_content_in_propose_phase(
    type_: NegotiationType, phase: NegotiationPerformative, content: object, fragment: str
) -> None:
    with pytest.raises(ValueError) as info:
        _resolve(type_, phase, content)

    assert fragment in str(info.value)


@pytest.mark.parametrize(
    ("type_", "phase", "content"),
    [
        (
            NegotiationType.INFORMATION,
            NegotiationPerformative.PROPOSE,
            TargetProposeContent("描述", None, None, None, None),
        ),
        (
            NegotiationType.TARGET,
            NegotiationPerformative.ACCEPT,
            InformationEndingContent(NegotiationConclusion.ACCEPT, []),
        ),
    ],
    ids=["propose-family", "ending-family"],
)
def test_rejects_content_runtime_type_not_matching_the_negotiation_type(
    type_: NegotiationType, phase: NegotiationPerformative, content: object
) -> None:
    with pytest.raises(ValueError) as info:
        _resolve(type_, phase, content)

    assert "requires content of type" in str(info.value)


def test_the_type_mismatch_message_names_both_content_types() -> None:
    with pytest.raises(ValueError) as info:
        _resolve(
            NegotiationType.INFORMATION,
            NegotiationPerformative.PROPOSE,
            TargetProposeContent("描述", None, None, None, None),
        )

    assert (
        "Negotiation type INFORMATION requires content of type InformationProposeContent but received "
        "TargetProposeContent" in str(info.value)
    )


@pytest.mark.parametrize(
    ("type_", "phase", "content", "expected", "actual"),
    [
        (
            NegotiationType.INFORMATION,
            NegotiationPerformative.ACCEPT,
            InformationEndingContent(NegotiationConclusion.REJECT, []),
            "Accept",
            "Reject",
        ),
        (
            NegotiationType.TARGET,
            NegotiationPerformative.REJECT,
            TargetEndingContent(NegotiationConclusion.ACCEPT, "确认的意图", None),
            "Reject",
            "Accept",
        ),
    ],
    ids=["reject-content-on-accept", "accept-content-on-reject"],
)
def test_rejects_ending_conclusion_not_matching_the_phase(
    type_: NegotiationType, phase: NegotiationPerformative, content: object, expected: str, actual: str
) -> None:
    with pytest.raises(NegotiationGenerationError) as info:
        _resolve(type_, phase, content)

    assert info.value.code is ErrorCatalog.NEGOTIATION_CONCLUSION_MISMATCH
    assert info.value.code_str == "negotiation.conclusion_mismatch"
    assert info.value.facts == {"expected": expected, "actual": actual}


def test_rejects_ending_content_without_conclusion() -> None:
    with pytest.raises(TypeError) as info:
        _resolve(NegotiationType.INFORMATION, NegotiationPerformative.ACCEPT, InformationEndingContent(None, []))

    assert "conclusion must not be null" in str(info.value)


@pytest.mark.parametrize(
    ("type_", "phase", "content", "message"),
    [
        (
            None,
            NegotiationPerformative.PROPOSE,
            InformationProposeContent([], None),
            "Negotiation type must not be null for the PROPOSE phase.",
        ),
        (NegotiationType.INFORMATION, None, InformationProposeContent([], None), "Negotiation phase must not be null."),
        (NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, None, "Negotiation content must not be null."),
    ],
    ids=["null-type", "null-phase", "null-content"],
)
def test_rejects_null_arguments(
    type_: NegotiationType | None, phase: NegotiationPerformative | None, content: object | None, message: str
) -> None:
    with pytest.raises(TypeError) as info:
        resolve(type_, phase, content, "zh-CN")  # type: ignore[arg-type]

    assert str(info.value) == message


@pytest.mark.parametrize(
    ("type_", "content", "fragment"),
    [
        (
            NegotiationType.INFORMATION,
            NegotiationAbortContent("终止原因"),
            "The ABORT phase is type-independent and must not carry a type but carried",
        ),
        (
            None,
            InformationProposeContent([], None),
            "The ABORT phase requires abort content but received InformationProposeContent",
        ),
    ],
    ids=["typed-abort", "non-abort-content"],
)
def test_the_abort_phase_is_type_independent_and_requires_abort_content(
    type_: NegotiationType | None, content: object, fragment: str
) -> None:
    with pytest.raises(ValueError) as info:
        _resolve(type_, NegotiationPerformative.ABORT, content)

    assert fragment in str(info.value)


def test_an_unknown_registry_key_is_a_programming_error() -> None:
    # A type that names no negotiation type cannot be dispatched; the Java terminal branch
    # ("No negotiation generator is registered for type ... and phase ...") is the semantics.
    with pytest.raises(ValueError) as info:
        _resolve("information-negotiation", NegotiationPerformative.PROPOSE, InformationProposeContent([], None))

    assert str(info.value) == (
        "No negotiation generator is registered for type information-negotiation and phase PROPOSE."
    )
    assert not isinstance(info.value, A2ATBusinessError)


def test_a_missing_registry_entry_is_a_programming_error() -> None:
    # The final lookup branch is reachable only when a registered pair is removed; pin it by
    # removing one entry and restoring it afterwards.
    key = (NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
    removed = GENERATOR_FUNCTIONS.pop(key)
    try:
        with pytest.raises(ValueError) as info:
            _resolve(
                NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, InformationProposeContent([ITEM], None)
            )

        assert str(info.value) == "No negotiation generator is registered for type INFORMATION and phase PROPOSE."
    finally:
        generators_module.GENERATOR_FUNCTIONS[key] = removed


def test_the_language_only_renders_the_conclusion_mismatch_message() -> None:
    with pytest.raises(NegotiationGenerationError) as zh:
        resolve(
            NegotiationType.INFORMATION,
            NegotiationPerformative.ACCEPT,
            InformationEndingContent(NegotiationConclusion.REJECT, []),
            "zh-CN",
        )

    assert str(zh.value) == "报文结论为「Reject」,与该方法的预期「Accept」不符"

    with pytest.raises(NegotiationGenerationError) as en:
        resolve(
            NegotiationType.INFORMATION,
            NegotiationPerformative.ACCEPT,
            InformationEndingContent(NegotiationConclusion.REJECT, []),
            "en-US",
        )

    assert str(en.value) == "The message conclusion is 'Reject' but 'Accept' is expected for this method"
