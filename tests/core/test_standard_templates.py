"""Tests for the built-in template constants and the D16/D17 lockstep contract.

The lockstep test (port-plan 修正2) is the lifetime cost of the dual-spelling
decision: every :class:`~a2a_t.core.template_uri.TemplateUri` constant, its
``*_URI`` string twin and the on-disk template files for both languages are
pinned together, so neither spelling can drift from the resource tree.
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

import pytest

from a2a_t.core import standard_templates
from a2a_t.core.standard_templates import TemplateUri

RESOURCE_LANGUAGES = ("en-US", "zh-CN")

# The exact expected raw URIs, pinned literally against the Java StandardTemplates.
EXPECTED_TEMPLATE_URIS = {
    "ENERGY_SAVING": "Task-T/network-layer/ran-energy-saving/v1",
    "PRIVATE_LINE_COMPLAINT": "Task-T/network-layer/private-line-complaint/v1",
    "RAN_INTELLIGENT_BACKUP_BATTERY_ENDURANCE": "Task-T/network-layer/ran-intelligent-backup-battery-endurance/v1",
    "SUBSCRIBE_INCIDENT": "Notification-T/network-layer/subscribe-incident/v1",
    "SERVICE_RECOVERY": "Notification-T/network-layer/service-recovery/v1",
    "AUTHORIZATION_POLICY_MANAGEMENT": "Authorization-T/authorization-policy-management/v1",
    "INFORMATION_NEGOTIATION_PROPOSE": "Negotiation-T/information-negotiation/propose/v1",
    "INFORMATION_NEGOTIATION_ACCEPT_REJECT": "Negotiation-T/information-negotiation/accept-reject/v1",
    "TARGET_NEGOTIATION_PROPOSE": "Negotiation-T/target-negotiation/propose/v1",
    "TARGET_NEGOTIATION_ACCEPT_REJECT": "Negotiation-T/target-negotiation/accept-reject/v1",
    "FEASIBILITY_NEGOTIATION_PROPOSE": "Negotiation-T/feasibility-negotiation/propose/v1",
    "FEASIBILITY_NEGOTIATION_ACCEPT_REJECT": "Negotiation-T/feasibility-negotiation/accept-reject/v1",
    "NEGOTIATION_ABORT": "Negotiation-T/common/abort/v1",
}

# (constant name, typed constant, raw string twin) for every built-in template,
# derived by introspection so a newly added constant cannot escape the lockstep;
# building the list raises AttributeError when a typed constant has no twin.
STANDARD_TEMPLATE_PAIRS: list[tuple[str, TemplateUri, str]] = sorted(
    (name, value, str(getattr(standard_templates, f"{name}_URI")))
    for name, value in vars(standard_templates).items()
    if isinstance(value, TemplateUri) and name.isupper()
)

# (family constant name, extension name, typed family, raw family, expected size)
FAMILIES = [
    ("TASK", "Task-T", standard_templates.TASK, standard_templates.TASK_URIS, 3),
    ("NOTIFICATION", "Notification-T", standard_templates.NOTIFICATION, standard_templates.NOTIFICATION_URIS, 2),
    (
        "AUTHORIZATION",
        "Authorization-T",
        standard_templates.AUTHORIZATION,
        standard_templates.AUTHORIZATION_URIS,
        1,
    ),
    ("NEGOTIATION", "Negotiation-T", standard_templates.NEGOTIATION, standard_templates.NEGOTIATION_URIS, 7),
]


def _template_resource_root() -> Path:
    """Resolve the packaged template resource tree via ``importlib.resources`` (D8)."""
    return Path(os.fspath(files("a2a_t").joinpath("prompt_resources", "templates")))


def test_standard_templates_pair_count() -> None:
    assert len(STANDARD_TEMPLATE_PAIRS) == 13


def test_the_twelve_expected_constants_exist() -> None:
    assert {name for name, _, _ in STANDARD_TEMPLATE_PAIRS} == set(EXPECTED_TEMPLATE_URIS)


def test_all_standard_template_uris_are_unique() -> None:
    raw_uris = [raw for _, _, raw in STANDARD_TEMPLATE_PAIRS]

    assert len(raw_uris) == 13
    assert len(set(raw_uris)) == 13


@pytest.mark.parametrize(("name", "typed", "raw"), STANDARD_TEMPLATE_PAIRS)
def test_standard_template_uris_match_the_java_spelling(name: str, typed: TemplateUri, raw: str) -> None:
    assert typed.uri == EXPECTED_TEMPLATE_URIS[name]
    assert raw == EXPECTED_TEMPLATE_URIS[name]
    assert typed.template_version == "v1"


@pytest.mark.parametrize(("name", "typed", "raw"), STANDARD_TEMPLATE_PAIRS)
def test_lockstep_typed_and_string_spellings_agree(name: str, typed: TemplateUri, raw: str) -> None:
    """D16 lockstep: the typed constant and its ``*_URI`` twin must be the same URI."""
    assert typed.uri == raw


@pytest.mark.parametrize(("name", "typed", "raw"), STANDARD_TEMPLATE_PAIRS)
def test_standard_templates_round_trip_through_parse(name: str, typed: TemplateUri, raw: str) -> None:
    assert TemplateUri.parse(raw) == typed


@pytest.mark.parametrize(("family_name", "extension_name", "typed_family", "raw_family", "size"), FAMILIES)
def test_family_lists_are_consistent(
    family_name: str,
    extension_name: str,
    typed_family: tuple[TemplateUri, ...],
    raw_family: tuple[str, ...],
    size: int,
) -> None:
    assert len(typed_family) == size
    assert raw_family == tuple(template.uri for template in typed_family)
    assert all(template.extension_name == extension_name for template in typed_family)
    assert len(set(typed_family)) == size


def test_all_families_together_cover_the_twelve_templates() -> None:
    all_typed = [template for _, _, typed_family, _, _ in FAMILIES for template in typed_family]

    assert len(all_typed) == 13
    assert len(set(all_typed)) == 13


def test_extension_name_constants() -> None:
    assert standard_templates.TASK_EXTENSION_NAME == "Task-T"
    assert standard_templates.NOTIFICATION_EXTENSION_NAME == "Notification-T"
    assert standard_templates.AUTHORIZATION_EXTENSION_NAME == "Authorization-T"
    assert standard_templates.NEGOTIATION_EXTENSION_NAME == "Negotiation-T"
    assert standard_templates.NETWORK_LAYER_SEGMENT == "network-layer"


@pytest.mark.parametrize(("family_name", "extension_name", "typed_family", "raw_family", "size"), FAMILIES)
def test_network_layer_domain_segment_placement(
    family_name: str,
    extension_name: str,
    typed_family: tuple[TemplateUri, ...],
    raw_family: tuple[str, ...],
    size: int,
) -> None:
    # Task-T and Notification-T carry the network-layer domain segment; the
    # Authorization-T and Negotiation-T families do not.
    if family_name in ("TASK", "NOTIFICATION"):
        assert all(template.path_segments[0] == standard_templates.NETWORK_LAYER_SEGMENT for template in typed_family)
        assert all(
            raw.startswith(f"{extension_name}/{standard_templates.NETWORK_LAYER_SEGMENT}/") for raw in raw_family
        )
    else:
        assert all(template.path_segments[0] != standard_templates.NETWORK_LAYER_SEGMENT for template in typed_family)
        assert all(f"{standard_templates.NETWORK_LAYER_SEGMENT}/" not in raw for raw in raw_family)


@pytest.mark.parametrize("language", RESOURCE_LANGUAGES)
@pytest.mark.parametrize(("name", "typed", "raw"), STANDARD_TEMPLATE_PAIRS)
def test_lockstep_template_files_exist_for_both_languages(
    language: str, name: str, typed: TemplateUri, raw: str
) -> None:
    """D16 lockstep: both languages' template files exist under the resource tree."""
    template_path = _template_resource_root().joinpath(*typed.segments, language, "template.md")

    assert template_path.is_file(), f"missing template resource for {raw} ({language})"
