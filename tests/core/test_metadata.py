"""Tests for the metadata model of generated A2A-T messages.

Port of the Java ``MetadataContentTest``, ``NegotiationContextTest`` and ``NegotiationPerformativeTest``:
every wire key, key order and validation message is pinned to the Java behavior.
"""

from __future__ import annotations

import pytest

from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
    MetadataContent,
    NegotiationContext,
    NegotiationPerformative,
    decode_extension_value,
    extension_value,
)
from a2a_t.core.standard_templates import INFORMATION_NEGOTIATION_PROPOSE_URI

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"
TEMPLATE_URI = INFORMATION_NEGOTIATION_PROPOSE_URI

CONTEXT = NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE)

ALL_PERFORMATIVES = list(NegotiationPerformative)


# ---------------------------------------------------------------- NegotiationPerformative


def test_values_pin_the_four_wire_constants_in_declaration_order() -> None:
    assert ALL_PERFORMATIVES == [
        NegotiationPerformative.PROPOSE,
        NegotiationPerformative.ACCEPT,
        NegotiationPerformative.REJECT,
        NegotiationPerformative.ABORT,
    ]


@pytest.mark.parametrize("performative", ALL_PERFORMATIVES, ids=lambda p: p.value)
def test_wire_values_are_pinned_to_upper_case_names(performative: NegotiationPerformative) -> None:
    assert performative.value == performative.name
    assert performative.value == performative.value.upper()


@pytest.mark.parametrize("performative", ALL_PERFORMATIVES, ids=lambda p: p.value)
def test_try_parse_accepts_the_exact_wire_values(performative: NegotiationPerformative) -> None:
    assert NegotiationPerformative.try_parse(performative.value) is performative


@pytest.mark.parametrize("value", ["propose", "accept", "reject", "abort"], ids=lambda v: v)
def test_try_parse_rejects_lower_case_variants(value: str) -> None:
    assert NegotiationPerformative.try_parse(value) is None


@pytest.mark.parametrize(
    "value",
    [None, "", "Propose", "PROPOSE ", "COUNTER", "PROPOSE, ACCEPT"],
    ids=["none", "empty", "mixed-case", "trailing-space", "unknown", "list"],
)
def test_try_parse_rejects_null_and_unknown_values(value: str | None) -> None:
    assert NegotiationPerformative.try_parse(value) is None


# ---------------------------------------------------------------- NegotiationContext


def test_construction_accepts_legal_context() -> None:
    context = NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE)

    assert context.id == SESSION_ID
    assert context.round == 1
    assert context.max_rounds == 5
    assert context.performative is NegotiationPerformative.PROPOSE


@pytest.mark.parametrize(
    "id",
    [None, "", "   "],
    ids=["none", "empty", "whitespace"],
)
def test_blank_id_raises_with_distinguishable_message(id: str | None) -> None:
    with pytest.raises(ValueError, match=r"id.*blank"):
        NegotiationContext(id, 1, 5, NegotiationPerformative.PROPOSE)


@pytest.mark.parametrize("round", [0, -1], ids=["zero", "negative"])
def test_non_positive_round_raises_with_distinguishable_message(round: int) -> None:
    with pytest.raises(ValueError, match=rf"round.*{round}"):
        NegotiationContext(SESSION_ID, round, 5, NegotiationPerformative.PROPOSE)


@pytest.mark.parametrize("max_rounds", [0, -3], ids=["zero", "negative"])
def test_non_positive_max_rounds_raises_with_distinguishable_message(max_rounds: int) -> None:
    with pytest.raises(ValueError, match=rf"maxRounds.*{max_rounds}"):
        NegotiationContext(SESSION_ID, 1, max_rounds, NegotiationPerformative.PROPOSE)


def test_null_performative_raises_with_distinguishable_message() -> None:
    with pytest.raises(ValueError, match=r"performative.*null"):
        NegotiationContext(SESSION_ID, 1, 5, None)  # type: ignore[arg-type]


def test_round_greater_than_max_rounds_is_allowed_at_construction() -> None:
    context = NegotiationContext(SESSION_ID, 6, 5, NegotiationPerformative.PROPOSE)

    assert context.round == 6
    assert context.is_exhausted() is True


@pytest.mark.parametrize(
    ("round", "max_rounds", "exhausted"),
    [(4, 5, False), (5, 5, False), (6, 5, True)],
    ids=["below-budget", "at-boundary", "beyond-budget"],
)
def test_is_exhausted_is_false_at_boundary_and_true_beyond_it(round: int, max_rounds: int, exhausted: bool) -> None:
    context = NegotiationContext(SESSION_ID, round, max_rounds, NegotiationPerformative.PROPOSE)

    assert context.is_exhausted() is exhausted


def test_next_round_returns_new_instance_and_leaves_original_unchanged() -> None:
    context = NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE)

    advanced = context.next_round()

    assert advanced.round == 2
    assert advanced.id == SESSION_ID
    assert advanced.max_rounds == 5
    assert advanced.performative is NegotiationPerformative.PROPOSE
    assert context.round == 1
    assert advanced != context


def test_with_performative_stamps_intent_and_keeps_session_fields() -> None:
    handle = NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.PROPOSE)

    stamped = handle.with_performative(NegotiationPerformative.ACCEPT)

    assert stamped.performative is NegotiationPerformative.ACCEPT
    assert stamped.id == SESSION_ID
    assert stamped.round == 2
    assert stamped.max_rounds == 5
    assert handle.performative is NegotiationPerformative.PROPOSE
    assert stamped != handle


def test_with_performative_overwrites_previous_stamp() -> None:
    first = NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE)

    second = first.with_performative(NegotiationPerformative.REJECT)

    assert second.performative is NegotiationPerformative.REJECT
    assert first.performative is NegotiationPerformative.PROPOSE
    assert second != first


def test_with_performative_rejects_none() -> None:
    context = NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE)

    with pytest.raises(ValueError, match=r"performative.*null"):
        context.with_performative(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("first", "second", "equal"),
    [
        (
            NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE),
            NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.ABORT),
            False,
        ),
        (
            NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE),
            NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE),
            True,
        ),
    ],
    ids=["differing-performative", "same-values"],
)
def test_equals_takes_performative_into_account(
    first: NegotiationContext, second: NegotiationContext, equal: bool
) -> None:
    assert (first == second) is equal
    assert (first != second) is (not equal)


def test_factory_applies_default_max_rounds() -> None:
    context = NegotiationContext.of(SESSION_ID, 2, NegotiationPerformative.PROPOSE)

    assert NegotiationContext.DEFAULT_MAX_ROUNDS == 5
    assert context.max_rounds == 5
    assert context.round == 2
    assert context.id == SESSION_ID
    assert context.performative is NegotiationPerformative.PROPOSE


def test_factory_rejects_none_performative() -> None:
    with pytest.raises(ValueError, match=r"performative.*null"):
        NegotiationContext.of(SESSION_ID, 2, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------- MetadataContent


def test_content_exposes_all_components() -> None:
    content = MetadataContent(TEMPLATE_URI, "rendered message", "https://example/ext")

    assert content.template_uri == TEMPLATE_URI
    assert content.prompt_text == "rendered message"
    assert content.extension_uri == "https://example/ext"
    assert content.negotiation_context is None


def test_contents_with_same_values_are_equal() -> None:
    first = MetadataContent("template-uri", "prompt-text", "extension-uri")
    second = MetadataContent("template-uri", "prompt-text", "extension-uri")

    assert first == second
    assert first is not second


def test_build_metadata_content_returns_exactly_two_deterministic_entries_without_context() -> None:
    content = MetadataContent(TEMPLATE_URI, "rendered message", "https://example/ext")

    metadata = content.build_metadata_content()

    assert len(metadata) == 2
    assert metadata[content.extension_uri] == "rendered message"
    assert metadata[TEMPLATE_URI_METADATA_KEY] == TEMPLATE_URI
    assert content.extension_uri in metadata
    assert metadata == content.build_metadata_content()


def test_build_metadata_content_keeps_fixed_key_order() -> None:
    content = MetadataContent(TEMPLATE_URI, "rendered message", "https://example/ext")

    metadata = content.build_metadata_content()

    assert list(metadata) == [content.extension_uri, TEMPLATE_URI_METADATA_KEY]


def test_build_metadata_content_never_returns_empty_even_with_none_fields() -> None:
    content = MetadataContent(None, None, "extension-uri")

    metadata = content.build_metadata_content()

    assert len(metadata) == 2
    assert TEMPLATE_URI_METADATA_KEY in metadata
    assert metadata["extension-uri"] is None
    assert metadata[TEMPLATE_URI_METADATA_KEY] is None


def test_build_metadata_content_carries_negotiation_context_as_third_key() -> None:
    content = MetadataContent(TEMPLATE_URI, "rendered message", "https://example/ext", CONTEXT)

    metadata = content.build_metadata_content()

    assert len(metadata) == 3
    assert list(metadata) == [content.extension_uri, TEMPLATE_URI_METADATA_KEY, NEGOTIATION_CONTEXT_METADATA_KEY]
    nested_context = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert isinstance(nested_context, dict)
    assert nested_context["id"] == CONTEXT.id
    assert nested_context["round"] == CONTEXT.round
    assert nested_context["maxRounds"] == CONTEXT.max_rounds
    assert nested_context["performative"] == "PROPOSE"
    assert len(nested_context) == 4
    assert metadata == content.build_metadata_content()


def test_build_metadata_context_carries_performative_as_fourth_key_in_upper_case() -> None:
    context = NegotiationContext("session-id", 2, 5, NegotiationPerformative.REJECT)
    content = MetadataContent(TEMPLATE_URI, "rendered message", "https://example/ext", context)

    metadata = content.build_metadata_content()

    nested_context = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert isinstance(nested_context, dict)
    assert list(nested_context) == ["id", "round", "maxRounds", "performative"]
    assert nested_context["performative"] == "REJECT"
    assert nested_context["id"] == "session-id"
    assert nested_context["round"] == 2
    assert nested_context["maxRounds"] == 5
    assert metadata == content.build_metadata_content()


@pytest.mark.parametrize("performative", ALL_PERFORMATIVES, ids=lambda p: p.value)
def test_build_metadata_context_pins_upper_case_wire_value_for_every_performative(
    performative: NegotiationPerformative,
) -> None:
    context = NegotiationContext("session-id", 1, 5, performative)
    content = MetadataContent(TEMPLATE_URI, "rendered message", "https://example/ext", context)

    metadata = content.build_metadata_content()

    nested_context = metadata[NEGOTIATION_CONTEXT_METADATA_KEY]
    assert isinstance(nested_context, dict)
    assert nested_context["performative"] == performative.value


@pytest.mark.parametrize("performative", ALL_PERFORMATIVES, ids=lambda p: p.value)
def test_metadata_content_equality_takes_the_negotiation_context_into_account(
    performative: NegotiationPerformative,
) -> None:
    plain = MetadataContent(TEMPLATE_URI, "rendered message", "https://example/ext")
    with_context = MetadataContent(
        TEMPLATE_URI,
        "rendered message",
        "https://example/ext",
        NegotiationContext("session-id", 1, 5, performative),
    )

    assert plain != with_context
    assert with_context == MetadataContent(
        TEMPLATE_URI,
        "rendered message",
        "https://example/ext",
        NegotiationContext("session-id", 1, 5, performative),
    )


# ---------------------------------------------------------------- extension value decoding


def test_decode_extension_value_reads_a_json_object_string() -> None:
    assert decode_extension_value('{"message": []}') == {"message": []}


def test_decode_extension_value_reads_a_json_array_string() -> None:
    assert decode_extension_value('[{"message": []}]') == [{"message": []}]


def test_decode_extension_value_reads_a_padded_json_document() -> None:
    assert decode_extension_value('  [{"message": []}]\n') == [{"message": []}]


def test_decode_extension_value_keeps_rendered_prompt_text() -> None:
    prompt = "## Task Context\nReduce the rate to 2 Mbps."
    assert decode_extension_value(prompt) == prompt


def test_decode_extension_value_keeps_malformed_json() -> None:
    truncated = '[{"message": [{"text": "## Feasibility Negotiation"}]}'
    assert decode_extension_value(truncated) == truncated


@pytest.mark.parametrize("value", ['"123"', "123", "true", "null"], ids=["string", "number", "bool", "null"])
def test_decode_extension_value_keeps_json_scalars(value: str) -> None:
    assert decode_extension_value(value) == value


def test_decode_extension_value_passes_structured_values_through() -> None:
    payload = {"message": []}

    assert decode_extension_value(payload) is payload
    assert decode_extension_value([payload]) == [payload]
    assert decode_extension_value(None) is None


def test_extension_value_decodes_the_named_extension() -> None:
    metadata = {
        NEGOTIATION_T_EXTENSION_URI: '[{"message": []}]',
        TEMPLATE_URI_METADATA_KEY: "Negotiation-T/info-negotiation/propose/v1",
    }

    assert extension_value(metadata, NEGOTIATION_T_EXTENSION_URI) == [{"message": []}]


def test_extension_value_returns_none_for_a_missing_key_or_map() -> None:
    assert extension_value(None, NEGOTIATION_T_EXTENSION_URI) is None
    assert extension_value({}, NEGOTIATION_T_EXTENSION_URI) is None


def test_extension_value_reads_the_protobuf_struct_of_an_a2a_message() -> None:
    from google.protobuf.struct_pb2 import Struct

    metadata = Struct()
    metadata.update({NEGOTIATION_T_EXTENSION_URI: '[{"message": []}]'})

    assert extension_value(metadata, NEGOTIATION_T_EXTENSION_URI) == [{"message": []}]


def test_extension_value_returns_none_for_an_unsupported_container() -> None:
    assert extension_value([NEGOTIATION_T_EXTENSION_URI], NEGOTIATION_T_EXTENSION_URI) is None


def test_decode_extension_value_keeps_deeply_nested_json() -> None:
    """``json`` raises ``RecursionError`` on a peer-controlled deep document; never propagate it."""
    deeply_nested = "[" * 5000 + "]" * 5000

    assert decode_extension_value(deeply_nested) == deeply_nested
