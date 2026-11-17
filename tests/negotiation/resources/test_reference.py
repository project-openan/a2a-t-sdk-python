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

"""Tests of the negotiation template addressing key (port of the Java ``NegotiationReferenceTest``).

Pins URI composition (prefix, type segment, performative segment, version), the ``try_parse`` /
``from_template_uri`` accept and reject matrices for all six typed URIs plus the common abort URI,
the constructor's abort/type binding, and the performative-to-segment mapping. The Java NPE rows
become :class:`TypeError` rows per the port's programming-error convention.
"""

from __future__ import annotations

import pytest

from a2a_t.core.metadata import NegotiationPerformative
from a2a_t.core.standard_templates import (
    ENERGY_SAVING,
    FEASIBILITY_NEGOTIATION_ACCEPT_REJECT,
    INFORMATION_NEGOTIATION_PROPOSE,
    NEGOTIATION_ABORT,
    TARGET_NEGOTIATION_ACCEPT_REJECT,
)
from a2a_t.core.template_uri import TemplateUri
from a2a_t.negotiation.content import NegotiationType
from a2a_t.negotiation.resources import COMMON_TYPE_SEGMENT, NegotiationReference, uri_segment_of

ALL_TYPES = list(NegotiationType)


def propose_uri(type_: NegotiationType) -> str:
    return f"Negotiation-T/{type_.type_segment}/propose/v1"


def accept_reject_uri(type_: NegotiationType) -> str:
    return f"Negotiation-T/{type_.type_segment}/accept-reject/v1"


def propose_template(type_: NegotiationType) -> TemplateUri:
    return TemplateUri.of("Negotiation-T", type_.type_segment, "propose")


def accept_reject_template(type_: NegotiationType) -> TemplateUri:
    return TemplateUri.of("Negotiation-T", type_.type_segment, "accept-reject")


def require_present(reference: NegotiationReference | None) -> NegotiationReference:
    assert reference is not None, "expected a parsed reference but the result was empty"
    return reference


# ---------------------------------------------------------------------------
# URI composition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_", "performative", "language", "expected"),
    [
        (NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, "en-US", INFORMATION_NEGOTIATION_PROPOSE),
        (NegotiationType.TARGET, NegotiationPerformative.ACCEPT, "zh-CN", TARGET_NEGOTIATION_ACCEPT_REJECT),
        (NegotiationType.FEASIBILITY, NegotiationPerformative.REJECT, "zh-CN", FEASIBILITY_NEGOTIATION_ACCEPT_REJECT),
    ],
    ids=["information-propose", "target-accept", "feasibility-reject"],
)
def test_uri_composes_prefix_version_type_segment_and_performative_segment(
    type_: NegotiationType,
    performative: NegotiationPerformative,
    language: str,
    expected: TemplateUri,
) -> None:
    reference = NegotiationReference(type_, performative, language)
    assert reference.uri == expected.uri
    assert reference.template_uri == expected


@pytest.mark.parametrize("type_", ALL_TYPES, ids=lambda t: t.name)
@pytest.mark.parametrize(
    "performative", [NegotiationPerformative.ACCEPT, NegotiationPerformative.REJECT], ids=lambda p: p.name
)
def test_accept_and_reject_share_one_template_uri_per_type(
    type_: NegotiationType, performative: NegotiationPerformative
) -> None:
    reference = NegotiationReference(type_, performative, "zh-CN")
    other = NegotiationReference(type_, NegotiationPerformative.ACCEPT, "zh-CN")
    assert reference.uri == other.uri
    assert reference.template_uri == other.template_uri


@pytest.mark.parametrize(
    ("performative", "segment"),
    [
        (NegotiationPerformative.PROPOSE, "propose"),
        (NegotiationPerformative.ACCEPT, "accept-reject"),
        (NegotiationPerformative.REJECT, "accept-reject"),
        (NegotiationPerformative.ABORT, "abort"),
    ],
    ids=lambda value: value if isinstance(value, str) else value.name,
)
def test_uri_segment_of_is_the_single_source_of_the_segment_spelling(
    performative: NegotiationPerformative, segment: str
) -> None:
    assert uri_segment_of(performative) == segment


def test_uri_segment_of_rejects_a_null_performative() -> None:
    with pytest.raises(TypeError, match="Negotiation performative must not be null."):
        uri_segment_of(None)  # type: ignore[arg-type]


def test_uri_composes_the_common_abort_uri_for_the_abort_performative() -> None:
    abort = NegotiationReference(None, NegotiationPerformative.ABORT, "en-US")
    assert abort.uri == NEGOTIATION_ABORT.uri
    assert abort.template_uri == NEGOTIATION_ABORT
    assert abort.type is None
    assert abort.type_segment == COMMON_TYPE_SEGMENT
    assert abort.type_segment == "common"


def test_references_are_value_objects() -> None:
    assert NegotiationReference(
        NegotiationType.TARGET, NegotiationPerformative.PROPOSE, "zh-CN"
    ) == NegotiationReference(NegotiationType.TARGET, NegotiationPerformative.PROPOSE, "zh-CN")
    assert NegotiationReference(
        NegotiationType.TARGET, NegotiationPerformative.PROPOSE, "zh-CN"
    ) != NegotiationReference(NegotiationType.TARGET, NegotiationPerformative.PROPOSE, "en-US")
    assert hash(NegotiationReference(NegotiationType.TARGET, NegotiationPerformative.PROPOSE, "zh-CN")) == hash(
        NegotiationReference(NegotiationType.TARGET, NegotiationPerformative.PROPOSE, "zh-CN")
    )


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_constructor_binds_the_abort_performative_to_a_null_type_only() -> None:
    with pytest.raises(ValueError, match="ABORT") as typed_abort:
        NegotiationReference(NegotiationType.INFORMATION, NegotiationPerformative.ABORT, "zh-CN")
    assert "type-independent" in str(typed_abort.value)
    assert "INFORMATION" in str(typed_abort.value)

    with pytest.raises(ValueError) as untyped_propose:
        NegotiationReference(None, NegotiationPerformative.PROPOSE, "zh-CN")
    assert "null" in str(untyped_propose.value)
    assert "PROPOSE" in str(untyped_propose.value)


@pytest.mark.parametrize("language", ["", "  ", "../escape", "a/b", None], ids=lambda v: repr(v))
def test_constructor_rejects_languages_that_are_not_simple_path_segments(language: str | None) -> None:
    with pytest.raises(ValueError, match="simple path segment"):
        NegotiationReference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, language)  # type: ignore[arg-type]


def test_constructor_rejects_a_null_performative() -> None:
    with pytest.raises(TypeError, match="Negotiation reference performative must not be null."):
        NegotiationReference(NegotiationType.INFORMATION, None, "zh-CN")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# tryParse / fromTemplateUri accept matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type_", ALL_TYPES, ids=lambda t: t.name)
@pytest.mark.parametrize(
    ("performative", "uri_of"),
    [
        (NegotiationPerformative.PROPOSE, propose_uri),
        (NegotiationPerformative.ACCEPT, accept_reject_uri),
        (NegotiationPerformative.REJECT, accept_reject_uri),
    ],
    ids=["propose", "accept", "reject"],
)
def test_try_parse_accepts_all_six_valid_uris(
    type_: NegotiationType, performative: NegotiationPerformative, uri_of: object
) -> None:
    uri = uri_of(type_)  # type: ignore[call-arg]
    reference = require_present(NegotiationReference.try_parse(uri, performative, "zh-CN"))
    assert reference.type is type_
    assert reference.performative is performative
    assert reference.uri == uri
    assert reference.language == "zh-CN"


@pytest.mark.parametrize("type_", ALL_TYPES, ids=lambda t: t.name)
@pytest.mark.parametrize(
    ("performative", "template_of"),
    [
        (NegotiationPerformative.PROPOSE, propose_template),
        (NegotiationPerformative.REJECT, accept_reject_template),
    ],
    ids=["propose", "reject"],
)
def test_from_template_uri_accepts_all_six_typed_uris(
    type_: NegotiationType, performative: NegotiationPerformative, template_of: object
) -> None:
    template = template_of(type_)  # type: ignore[call-arg]
    reference = require_present(NegotiationReference.from_template_uri(template, performative, "en-US"))
    assert reference.type is type_
    assert reference.performative is performative
    assert reference.template_uri == template


def test_try_parse_accepts_the_common_abort_uri() -> None:
    reference = require_present(
        NegotiationReference.try_parse(NEGOTIATION_ABORT.uri, NegotiationPerformative.ABORT, "zh-CN")
    )
    assert reference.type is None
    assert reference.performative is NegotiationPerformative.ABORT
    assert reference.uri == NEGOTIATION_ABORT.uri
    assert reference.language == "zh-CN"


def test_from_template_uri_accepts_the_common_abort_uri_and_is_its_exact_inverse() -> None:
    reference = require_present(
        NegotiationReference.from_template_uri(NEGOTIATION_ABORT, NegotiationPerformative.ABORT, "zh-CN")
    )
    assert reference.type is None
    assert reference.performative is NegotiationPerformative.ABORT
    assert reference.uri == NEGOTIATION_ABORT.uri
    assert reference.uri == NegotiationReference(None, NegotiationPerformative.ABORT, "en-US").uri


# ---------------------------------------------------------------------------
# tryParse / fromTemplateUri reject matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template_uri",
    [
        "information-negotiation/propose",
        "Negotiation-T/information-negotiation/propose/v1/extra",
        "foo",
        "",
        None,
    ],
    ids=["missing-prefix", "extra-segment", "single-segment", "blank", "null"],
)
def test_try_parse_rejects_wrong_segment_count(template_uri: str | None) -> None:
    assert NegotiationReference.try_parse(template_uri, NegotiationPerformative.PROPOSE, "zh-CN") is None


@pytest.mark.parametrize(
    "template_uri",
    [
        "Task-T/information-negotiation/propose/v1",
        "negotiation-t/information-negotiation/propose/v1",
        "Negotiation-T/information-negotiation/propose/v2",
    ],
    ids=["wrong-prefix", "lower-case-prefix", "wrong-version"],
)
def test_try_parse_rejects_wrong_prefix_and_version(template_uri: str) -> None:
    assert NegotiationReference.try_parse(template_uri, NegotiationPerformative.PROPOSE, "zh-CN") is None


@pytest.mark.parametrize(
    "type_segment",
    ["information", "information_negotiation", "unknown-negotiation"],
    ids=["missing-suffix", "underscore-variant", "unknown-type"],
)
def test_try_parse_rejects_invalid_type_segments(type_segment: str) -> None:
    assert (
        NegotiationReference.try_parse(
            f"Negotiation-T/{type_segment}/propose/v1", NegotiationPerformative.PROPOSE, "zh-CN"
        )
        is None
    )


@pytest.mark.parametrize(
    "performative_segment",
    ["propose-x", "accept", "reject", ""],
    ids=["suffixed-segment", "bare-accept", "bare-reject", "empty-segment"],
)
def test_try_parse_rejects_illegal_performative_segments(performative_segment: str) -> None:
    assert (
        NegotiationReference.try_parse(
            f"Negotiation-T/information-negotiation/{performative_segment}/v1",
            NegotiationPerformative.PROPOSE,
            "zh-CN",
        )
        is None
    )


def test_try_parse_rejects_performative_mismatch_against_the_expected_performative() -> None:
    assert (
        NegotiationReference.try_parse(INFORMATION_NEGOTIATION_PROPOSE.uri, NegotiationPerformative.ACCEPT, "zh-CN")
        is None
    )


@pytest.mark.parametrize(
    ("template_uri", "performative"),
    [
        ("Negotiation-T/common/propose/v1", NegotiationPerformative.PROPOSE),
        ("Negotiation-T/common/accept-reject/v1", NegotiationPerformative.ACCEPT),
        (NEGOTIATION_ABORT.uri, NegotiationPerformative.PROPOSE),
        (NEGOTIATION_ABORT.uri, NegotiationPerformative.ACCEPT),
        (NEGOTIATION_ABORT.uri, NegotiationPerformative.REJECT),
    ],
    ids=[
        "common-propose",
        "common-accept-reject",
        "abort-uri-vs-propose",
        "abort-uri-vs-accept",
        "abort-uri-vs-reject",
    ],
)
def test_try_parse_rejects_the_common_segment_for_typed_performatives(
    template_uri: str, performative: NegotiationPerformative
) -> None:
    assert NegotiationReference.try_parse(template_uri, performative, "zh-CN") is None


def test_try_parse_rejects_the_abort_segment_on_a_typed_reference() -> None:
    assert (
        NegotiationReference.try_parse(
            "Negotiation-T/information-negotiation/abort/v1", NegotiationPerformative.ABORT, "zh-CN"
        )
        is None
    )


@pytest.mark.parametrize(
    "template_uri",
    [
        TemplateUri.of("Negotiation-T", "common", "propose"),
        TemplateUri.of("Negotiation-T", "common", "abort"),
        TemplateUri.of("Negotiation-T", "information-negotiation", "abort"),
    ],
    ids=["common-propose", "common-abort-vs-propose", "typed-abort"],
)
def test_from_template_uri_rejects_common_with_typed_performatives_and_typed_abort(
    template_uri: TemplateUri,
) -> None:
    performative = (
        NegotiationPerformative.ABORT if template_uri.path_segments[0] != "common" else NegotiationPerformative.PROPOSE
    )
    assert NegotiationReference.from_template_uri(template_uri, performative, "zh-CN") is None


@pytest.mark.parametrize(
    "template_uri",
    [ENERGY_SAVING, TemplateUri.of("negotiation-t", "information-negotiation", "propose")],
    ids=["task-template", "lower-case-extension"],
)
def test_from_template_uri_rejects_non_negotiation_uris(template_uri: TemplateUri) -> None:
    assert NegotiationReference.from_template_uri(template_uri, NegotiationPerformative.PROPOSE, "zh-CN") is None


@pytest.mark.parametrize(
    "template_uri",
    [
        TemplateUri.of("Negotiation-T", "information-negotiation"),
        TemplateUri.of("Negotiation-T", "information-negotiation", "propose", "extra"),
    ],
    ids=["one-path-segment", "three-path-segments"],
)
def test_from_template_uri_rejects_wrong_path_segment_counts(template_uri: TemplateUri) -> None:
    assert NegotiationReference.from_template_uri(template_uri, NegotiationPerformative.PROPOSE, "zh-CN") is None


@pytest.mark.parametrize(
    "template_uri",
    [
        TemplateUri.of("Negotiation-T", "information-negotiation", "propose", template_version="v2"),
        TemplateUri.of("Negotiation-T", "information", "propose"),
        TemplateUri.of("Negotiation-T", "information_negotiation", "propose"),
        TemplateUri.of("Negotiation-T", "unknown-negotiation", "propose"),
        TemplateUri.of("Negotiation-T", "information-negotiation", "propose-x"),
    ],
    ids=["wrong-version", "missing-suffix", "underscore-variant", "unknown-type", "suffixed-segment"],
)
def test_from_template_uri_rejects_wrong_version_type_and_performative_segments(
    template_uri: TemplateUri,
) -> None:
    assert NegotiationReference.from_template_uri(template_uri, NegotiationPerformative.PROPOSE, "zh-CN") is None


def test_from_template_uri_rejects_performative_mismatch() -> None:
    assert (
        NegotiationReference.from_template_uri(INFORMATION_NEGOTIATION_PROPOSE, NegotiationPerformative.ACCEPT, "zh-CN")
        is None
    )


def test_try_parse_and_from_template_uri_throw_on_null_arguments() -> None:
    with pytest.raises(TypeError, match="Expected negotiation performative must not be null."):
        NegotiationReference.try_parse(INFORMATION_NEGOTIATION_PROPOSE.uri, None, "zh-CN")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="Template URI must not be null."):
        NegotiationReference.from_template_uri(None, NegotiationPerformative.PROPOSE, "zh-CN")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="Expected negotiation performative must not be null."):
        NegotiationReference.from_template_uri(
            INFORMATION_NEGOTIATION_PROPOSE,
            None,
            "zh-CN",  # type: ignore[arg-type]
        )


def test_try_parse_and_from_template_uri_agree_on_every_rejection() -> None:
    """The string entry point must reject exactly what the typed entry point rejects."""
    rejected_uris = [
        "Task-T/network-layer/ran-energy-saving/v1",
        "negotiation-t/information-negotiation/propose/v1",
        "Negotiation-T/information-negotiation/propose/v2",
        "Negotiation-T/information/propose/v1",
        "Negotiation-T/information_negotiation/propose/v1",
        "Negotiation-T/unknown-negotiation/propose/v1",
        "Negotiation-T/information-negotiation/propose-x/v1",
        "Negotiation-T/information-negotiation/accept/v1",
        "Negotiation-T/common/propose/v1",
        "Negotiation-T/common/accept-reject/v1",
        "Negotiation-T/information-negotiation/abort/v1",
    ]
    for uri in rejected_uris:
        for performative in NegotiationPerformative:
            parsed = NegotiationReference.try_parse(uri, performative, "zh-CN")
            from_uri = NegotiationReference.from_template_uri(TemplateUri.parse(uri), performative, "zh-CN")
            assert (parsed is None) == (from_uri is None), (uri, performative)
