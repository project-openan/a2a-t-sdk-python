"""Tests for the structured template URI (port of Java ``TemplateUri``)."""

from __future__ import annotations

import pytest

from a2a_t.core.standard_templates import (
    AUTHORIZATION,
    NEGOTIATION,
    NOTIFICATION,
    TASK,
)
from a2a_t.core.template_uri import DEFAULT_TEMPLATE_VERSION, TemplateUri

ALL_STANDARD_TEMPLATES = (*TASK, *NOTIFICATION, *AUTHORIZATION, *NEGOTIATION)

# The 13 built-in templates pinned against the Java StandardTemplates spelling.
STANDARD_TEMPLATE_URIS = [
    "Task-T/network-layer/ran-energy-saving/v1",
    "Task-T/network-layer/private-line-complaint/v1",
    "Task-T/network-layer/ran-intelligent-backup-battery-endurance/v1",
    "Notification-T/network-layer/subscribe-incident/v1",
    "Notification-T/network-layer/service-recovery/v1",
    "Authorization-T/authorization-policy-management/v1",
    "Negotiation-T/information-negotiation/propose/v1",
    "Negotiation-T/information-negotiation/accept-reject/v1",
    "Negotiation-T/target-negotiation/propose/v1",
    "Negotiation-T/target-negotiation/accept-reject/v1",
    "Negotiation-T/feasibility-negotiation/propose/v1",
    "Negotiation-T/feasibility-negotiation/accept-reject/v1",
    "Negotiation-T/common/abort/v1",
]


def test_of_defaults_the_template_version() -> None:
    uri = TemplateUri.of("Task-T", "network-layer", "ran-energy-saving")

    assert uri.extension_name == "Task-T"
    assert uri.path_segments == ("network-layer", "ran-energy-saving")
    assert uri.template_version == DEFAULT_TEMPLATE_VERSION == "v1"
    assert uri.uri == "Task-T/network-layer/ran-energy-saving/v1"


def test_of_accepts_an_explicit_template_version() -> None:
    uri = TemplateUri.of("Task-T", "network-layer", "ran-energy-saving", template_version="v2")

    assert uri.template_version == "v2"
    assert uri.uri == "Task-T/network-layer/ran-energy-saving/v2"


def test_of_accepts_multiple_path_segments() -> None:
    uri = TemplateUri.of("Negotiation-T", "information-negotiation", "propose")

    assert uri.path_segments == ("information-negotiation", "propose")
    assert uri.segments == ("Negotiation-T", "information-negotiation", "propose", "v1")


def test_constructor_normalizes_path_segments_to_a_tuple() -> None:
    uri = TemplateUri("Task-T", ["network-layer", "ran-energy-saving"], "v1")  # type: ignore[arg-type]

    assert uri.path_segments == ("network-layer", "ran-energy-saving")


def test_constructor_copies_the_path_segments_defensively() -> None:
    mutable_segments = ["network-layer", "ran-energy-saving"]

    uri = TemplateUri("Task-T", mutable_segments, "v1")  # type: ignore[arg-type]
    mutable_segments.append("extra")

    assert uri.path_segments == ("network-layer", "ran-energy-saving")


@pytest.mark.parametrize("template", ALL_STANDARD_TEMPLATES)
def test_of_parse_round_trip(template: TemplateUri) -> None:
    parsed = TemplateUri.parse(template.uri)

    assert parsed == template
    assert parsed is not None
    assert parsed.uri == template.uri


@pytest.mark.parametrize("raw_uri", STANDARD_TEMPLATE_URIS)
def test_parse_accepts_every_standard_template(raw_uri: str) -> None:
    parsed = TemplateUri.parse(raw_uri)

    assert parsed is not None
    assert parsed.uri == raw_uri
    assert parsed.template_version == "v1"


@pytest.mark.parametrize(
    "raw_uri",
    [
        None,
        "",
        "   ",
        # fewer than three segments
        "Task-T",
        "Task-T/v1",
        # traversal
        "../Task-T/network-layer/v1",
        "Task-T/../network-layer/v1",
        "Task-T/network-layer/../v1",
        "Task-T/network-layer/ran-energy-saving/../v1",
        "Task-T/..",
        # absolute paths resolve to an empty leading segment
        "/Task-T/network-layer/v1",
        "C:\\Task-T\\network-layer\\v1",
        # empty segments
        "Task-T//network-layer/v1",
        "Task-T/network-layer//v1",
        "Task-T/network-layer/ran-energy-saving/",
        "//Task-T/network-layer/v1",
        # non-simple version segment
        "Task-T/network-layer/ran-energy-saving/v..1",
        "Task-T/network-layer/ran-energy-saving/..",
    ],
)
def test_parse_rejects_malformed_uris(raw_uri: str | None) -> None:
    assert TemplateUri.parse(raw_uri) is None


def test_parse_strips_surrounding_whitespace() -> None:
    parsed = TemplateUri.parse("  Negotiation-T/common/abort/v1 \n")

    assert parsed == TemplateUri.of("Negotiation-T", "common", "abort")


def test_parse_rejects_backslash_separated_uris() -> None:
    assert TemplateUri.parse("Task-T\\network-layer\\ran-energy-saving\\v1") is None


def test_parse_accepts_structurally_valid_unknown_families() -> None:
    # Java parity: TemplateUri is purely structural — family/type semantics
    # (which families and scenarios exist) are resolved by the resource layer,
    # never by the URI type itself.
    parsed = TemplateUri.parse("Custom-T/some-scenario/v9")

    assert parsed == TemplateUri.of("Custom-T", "some-scenario", template_version="v9")


@pytest.mark.parametrize(
    ("extension_name", "path_segments", "template_version"),
    [
        ("", ("network-layer",), "v1"),
        ("   ", ("network-layer",), "v1"),
        ("Task-T", (), "v1"),
        ("Task-T", ("",), "v1"),
        ("Task-T", ("   ",), "v1"),
        ("Task-T", ("network layer", "a/b"), "v1"),
        ("Task-T", ("a\\b",), "v1"),
        ("Task-T", ("..",), "v1"),
        ("Task-T", ("network-layer", "a..b"), "v1"),
        ("Task-T", ("network-layer",), ""),
        ("Task-T", ("network-layer",), "   "),
        ("Task-T", ("network-layer",), "v/1"),
        ("Task-T", ("network-layer",), "v\\1"),
        ("Task-T", ("network-layer",), ".."),
    ],
)
def test_constructor_rejects_invalid_components(
    extension_name: str, path_segments: tuple[str, ...], template_version: str
) -> None:
    with pytest.raises(ValueError):
        TemplateUri(extension_name, path_segments, template_version)


@pytest.mark.parametrize(
    ("extension_name", "path_segments", "template_version"),
    [
        (None, ("network-layer",), "v1"),
        ("Task-T", None, "v1"),
        ("Task-T", ("network-layer",), None),
    ],
)
def test_constructor_rejects_none_components(
    extension_name: str | None, path_segments: tuple[str, ...] | None, template_version: str | None
) -> None:
    with pytest.raises(TypeError):
        TemplateUri(extension_name, path_segments, template_version)


def test_constructor_rejects_empty_path_segments_with_specific_message() -> None:
    with pytest.raises(ValueError, match="Template URI must have at least one path segment."):
        TemplateUri("Task-T", (), "v1")


@pytest.mark.parametrize(
    ("extension_name", "template_version"),
    [(None, "v1"), ("Task-T", None)],
)
def test_of_rejects_none_components(extension_name: str | None, template_version: str | None) -> None:
    with pytest.raises(TypeError):
        TemplateUri.of(extension_name, "network-layer", template_version=template_version)  # type: ignore[arg-type]


def test_template_uri_is_immutable() -> None:
    uri = TemplateUri.of("Task-T", "network-layer", "ran-energy-saving")

    with pytest.raises(AttributeError):
        uri.extension_name = "Notification-T"  # type: ignore[misc]


def test_template_uri_is_hashable_and_usable_in_sets() -> None:
    uri = TemplateUri.of("Task-T", "network-layer", "ran-energy-saving")

    assert TemplateUri.parse("Task-T/network-layer/ran-energy-saving/v1") in {uri}
    assert TemplateUri.of("Task-T", "network-layer", "private-line-complaint") not in {uri}


def test_str_returns_the_canonical_uri() -> None:
    uri = TemplateUri.of("Negotiation-T", "information-negotiation", "propose")

    assert str(uri) == "Negotiation-T/information-negotiation/propose/v1"
