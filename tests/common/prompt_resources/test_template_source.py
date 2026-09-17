"""Template-addressed loading through the D31 resource access layer.

Pins :mod:`a2a_t.common.prompt_resources.template_source` through the public access API: full
template URIs for both prompt (Task-T) and negotiation (Negotiation-T) pipelines, the typed
``TemplateUri`` spelling, bare scenario-code probing, slot schema loading, the catalog-coded
not-found failures with their facts, and traversal rejection.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from a2a_t.common.prompt_resources import PromptResourceAccess, create
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError, A2ATError
from a2a_t.core.standard_templates import (
    AUTHORIZATION_POLICY_MANAGEMENT_URI,
    ENERGY_SAVING,
    ENERGY_SAVING_URI,
)
from a2a_t.core.template_uri import TemplateUri
from tests.common.prompt_resources.conftest import (
    LANGUAGES,
    NEGOTIATION_TEMPLATE_URIS,
    SLOT_TEMPLATE_URIS,
    packaged_file_text,
    write_local_resource,
)

#: Every bundled template URI: the prompt-pipeline families plus the negotiation family.
ALL_TEMPLATE_URIS: tuple[str, ...] = (
    ENERGY_SAVING_URI,
    "Task-T/network-layer/private-line-complaint/v1",
    "Notification-T/network-layer/subscribe-incident/v1",
    "Notification-T/network-layer/service-recovery/v1",
    AUTHORIZATION_POLICY_MANAGEMENT_URI,
    *NEGOTIATION_TEMPLATE_URIS,
)


def _local_access(root: Path) -> PromptResourceAccess:
    """Create one local_file-mode access object for one root directory."""
    return create(PromptRuntimeConfig(source_type="local_file", local_root_dir=str(root)))


@pytest.mark.parametrize("template_uri", ALL_TEMPLATE_URIS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_packaged_template_text_matches_the_bundled_file(
    packaged_access: PromptResourceAccess, template_uri: str, language: str
) -> None:
    expected = packaged_file_text(f"templates/{template_uri}/{language}/template.md")
    assert packaged_access.template_text(template_uri, language) == expected


@pytest.mark.parametrize("template_uri", ALL_TEMPLATE_URIS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_typed_template_uri_spelling_is_equivalent(
    packaged_access: PromptResourceAccess, template_uri: str, language: str
) -> None:
    parsed = TemplateUri.parse(template_uri)
    assert parsed is not None, template_uri
    assert packaged_access.template_text(parsed, language) == packaged_access.template_text(template_uri, language)


@pytest.mark.parametrize("template_uri", SLOT_TEMPLATE_URIS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_packaged_slot_schema_matches_the_bundled_file(
    packaged_access: PromptResourceAccess, template_uri: str, language: str
) -> None:
    expected = json.loads(packaged_file_text(f"slots/{template_uri}/{language}/slot.json"))
    schema = packaged_access.slot_schema(template_uri, language)
    assert isinstance(schema, dict)
    assert schema == expected
    assert schema["type"] == "object"
    assert isinstance(schema["properties"], dict)


@pytest.mark.parametrize("template_uri", SLOT_TEMPLATE_URIS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_typed_slot_schema_spelling_is_equivalent(
    packaged_access: PromptResourceAccess, template_uri: str, language: str
) -> None:
    parsed = TemplateUri.parse(template_uri)
    assert parsed is not None, template_uri
    assert packaged_access.slot_schema(parsed, language) == packaged_access.slot_schema(template_uri, language)


@pytest.mark.parametrize("language", LANGUAGES)
def test_unknown_template_uri_raises_template_not_found_with_facts(
    packaged_access: PromptResourceAccess, language: str
) -> None:
    unknown = "Task-T/network-layer/does-not-exist/v1"
    with pytest.raises(A2ATBusinessError) as info:
        packaged_access.template_text(unknown, language)
    error = info.value
    assert error.code is ErrorCatalog.TEMPLATE_NOT_FOUND
    assert error.code_str == "template.not_found"
    assert error.facts == {"template_uri": unknown, "language": language}


@pytest.mark.parametrize("language", LANGUAGES)
def test_unknown_slot_schema_raises_slot_schema_not_found_with_facts(
    packaged_access: PromptResourceAccess, language: str
) -> None:
    unknown = "Task-T/network-layer/does-not-exist/v1"
    with pytest.raises(A2ATBusinessError) as info:
        packaged_access.slot_schema(unknown, language)
    error = info.value
    assert error.code is ErrorCatalog.SLOT_SCHEMA_NOT_FOUND
    assert error.facts == {"template_uri": unknown, "language": language}


@pytest.mark.parametrize("template_uri", ALL_TEMPLATE_URIS)
def test_missing_language_raises_template_not_found(packaged_access: PromptResourceAccess, template_uri: str) -> None:
    with pytest.raises(A2ATBusinessError) as info:
        packaged_access.template_text(template_uri, "xx-XX")
    assert info.value.code is ErrorCatalog.TEMPLATE_NOT_FOUND
    assert info.value.facts == {"template_uri": template_uri, "language": "xx-XX"}


class TestBareScenarioCodeProbing:
    """Bare scenario codes resolve by probing the known types first, then discovered ones."""

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_bare_task_code_resolves_the_network_layer_layout(
        self, packaged_access: PromptResourceAccess, language: str
    ) -> None:
        expected = packaged_file_text(f"templates/{ENERGY_SAVING_URI}/{language}/template.md")
        assert packaged_access.template_text("ran-energy-saving", language) == expected

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_bare_code_resolves_discovered_extension_types(
        self, packaged_access: PromptResourceAccess, language: str
    ) -> None:
        # Authorization-T is not in the hardcoded probe list; it must be discovered from the
        # packaged tree (Java ClasspathPromptTemplateLoader discovery semantics).
        expected = packaged_file_text(f"templates/{AUTHORIZATION_POLICY_MANAGEMENT_URI}/{language}/template.md")
        assert packaged_access.template_text("authorization-policy-management", language) == expected

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_bare_slot_code_resolves_the_slot_schema(
        self, packaged_access: PromptResourceAccess, language: str
    ) -> None:
        expected = json.loads(packaged_file_text(f"slots/{ENERGY_SAVING_URI}/{language}/slot.json"))
        assert packaged_access.slot_schema("ran-energy-saving", language) == expected

    def test_unknown_bare_code_raises_template_not_found(self, packaged_access: PromptResourceAccess) -> None:
        with pytest.raises(A2ATBusinessError) as info:
            packaged_access.template_text("does-not-exist", "en-US")
        assert info.value.code is ErrorCatalog.TEMPLATE_NOT_FOUND
        assert info.value.facts == {"template_uri": "does-not-exist", "language": "en-US"}


@pytest.mark.parametrize(
    "identifier",
    [
        "../escape",
        "Task-T/../../escape",
        "Task-T/network-layer/../../../escape/v1",
        "..",
        "/etc/passwd",
        "Task-T\\network-layer",
    ],
)
def test_template_identifier_traversal_is_rejected(packaged_access: PromptResourceAccess, identifier: str) -> None:
    with pytest.raises(ValueError):
        packaged_access.template_text(identifier, "en-US")
    with pytest.raises(ValueError):
        packaged_access.slot_schema(identifier, "en-US")


@pytest.mark.parametrize("language", ["", "  ", "../escape", "a/b"])
def test_malformed_language_is_rejected(packaged_access: PromptResourceAccess, language: str) -> None:
    with pytest.raises(ValueError):
        packaged_access.template_text(ENERGY_SAVING_URI, language)


class TestLocalTemplateOverride:
    """Routed template loading in ``local_file`` mode (local override, packaged fallback).

    The Java ADR 0005 overlay: a locally captured file wins, a resource missing from the local
    snapshot falls back to the packaged copy with a one-time warning per resource path, and a
    resource missing both locally and in the package raises the plain not-found failure without a
    warning.
    """

    FALLBACK_LOGGER = "a2a_t.common.prompt_resources.builtin_fallback"

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_local_template_overrides_the_packaged_one(self, tmp_path: Path, language: str) -> None:
        write_local_resource(
            tmp_path,
            f"templates/{ENERGY_SAVING_URI}/{language}/template.md",
            "LOCAL OVERRIDE",
        )
        access = _local_access(tmp_path)
        assert access.template_text(ENERGY_SAVING_URI, language) == "LOCAL OVERRIDE"

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_missing_local_template_falls_back_to_the_packaged_copy(
        self, tmp_path: Path, language: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        write_local_resource(tmp_path, "templates/ignored/v1/en-US/template.md", "unrelated")
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            text = access.template_text(ENERGY_SAVING_URI, language)
        assert text == packaged_file_text(f"templates/{ENERGY_SAVING_URI}/{language}/template.md")
        fallbacks = [record.getMessage() for record in caplog.records]
        assert fallbacks == [
            "prompt_resource_builtin_fallback "
            f"path=prompt_resources/templates/{ENERGY_SAVING_URI}/{language}/template.md source=packaged"
        ]

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_missing_local_slot_schema_falls_back_to_the_packaged_copy(
        self, tmp_path: Path, language: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            schema = access.slot_schema(ENERGY_SAVING_URI, language)
        assert schema == json.loads(packaged_file_text(f"slots/{ENERGY_SAVING_URI}/{language}/slot.json"))
        fallbacks = [record.getMessage() for record in caplog.records]
        assert fallbacks == [
            "prompt_resource_builtin_fallback "
            f"path=prompt_resources/slots/{ENERGY_SAVING_URI}/{language}/slot.json source=packaged"
        ]

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_fallback_warns_only_once_per_resource_path(
        self, tmp_path: Path, language: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            access.template_text(ENERGY_SAVING_URI, language)
            access.template_text(ENERGY_SAVING_URI, language)
        assert len(caplog.records) == 1

    def test_double_missing_template_raises_not_found_without_a_fallback_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            with pytest.raises(A2ATBusinessError) as info:
                access.template_text("Task-T/network-layer/does-not-exist/v1", "en-US")
        assert info.value.code is ErrorCatalog.TEMPLATE_NOT_FOUND
        assert caplog.records == []

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_bare_code_probing_discovers_the_packaged_extension_types(
        self, tmp_path: Path, language: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        write_local_resource(
            tmp_path,
            f"templates/{ENERGY_SAVING_URI}/{language}/template.md",
            "LOCAL TASK TEMPLATE",
        )
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            text = access.template_text("authorization-policy-management", language)
        assert text == packaged_file_text(f"templates/{AUTHORIZATION_POLICY_MANAGEMENT_URI}/{language}/template.md")
        # A bare code reports the Java wildcard locator, not the concrete resolved path.
        assert any(
            f"path=prompt_resources/templates/*/network-layer/authorization-policy-management/v1/"
            f"{language}/template.md (or the layout without the network-layer segment)" in message
            for message in (record.getMessage() for record in caplog.records)
        )

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_local_plain_layout_override_wins_over_the_packaged_network_layer(
        self, tmp_path: Path, language: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Two-phase probing: a locally captured file wins whatever its layout (Java phase 1)."""
        write_local_resource(
            tmp_path,
            f"templates/Task-T/ran-energy-saving/v1/{language}/template.md",
            f"LOCAL PLAIN LAYOUT {language}",
        )
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            text = access.template_text("ran-energy-saving", language)
        assert text == f"LOCAL PLAIN LAYOUT {language}"
        assert [record.name for record in caplog.records if record.name == self.FALLBACK_LOGGER] == []

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_local_custom_extension_wins_over_the_packaged_known_type(
        self, tmp_path: Path, language: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Two-phase probing: a local file in a locally discovered type wins over the packaged
        copy of a known type that comes first in the probe order."""
        write_local_resource(
            tmp_path,
            f"templates/Custom-T/network-layer/ran-energy-saving/v1/{language}/template.md",
            f"LOCAL CUSTOM TYPE {language}",
        )
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            text = access.template_text("ran-energy-saving", language)
        assert text == f"LOCAL CUSTOM TYPE {language}"
        assert [record.name for record in caplog.records if record.name == self.FALLBACK_LOGGER] == []

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_slot_schema_bare_code_also_resolves_two_phase(
        self, tmp_path: Path, language: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The shared two-phase resolution covers the slot tree (plain-layout local slot.json)."""
        write_local_resource(
            tmp_path,
            f"slots/Task-T/ran-energy-saving/v1/{language}/slot.json",
            json.dumps({"type": "object", "properties": {"local_slot": {"type": "string", "description": language}}}),
        )
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            schema = access.slot_schema("ran-energy-saving", language)
        assert list(schema["properties"]) == ["local_slot"]
        assert schema["properties"]["local_slot"]["description"] == language
        assert [record.name for record in caplog.records if record.name == self.FALLBACK_LOGGER] == []

    def test_malformed_local_slot_schema_raises_resource_read_failed(self, tmp_path: Path) -> None:
        write_local_resource(tmp_path, f"slots/{ENERGY_SAVING_URI}/en-US/slot.json", "{not json")
        access = _local_access(tmp_path)
        with pytest.raises(A2ATError) as info:
            access.slot_schema(ENERGY_SAVING_URI, "en-US")
        assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
        assert "slots" in str(info.value)

    def test_local_slot_schema_with_duplicate_keys_fails_fast(self, tmp_path: Path) -> None:
        write_local_resource(
            tmp_path,
            f"slots/{ENERGY_SAVING_URI}/en-US/slot.json",
            '{"type": "object", "type": "array"}',
        )
        access = _local_access(tmp_path)
        with pytest.raises(A2ATError) as info:
            access.slot_schema(ENERGY_SAVING_URI, "en-US")
        assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED


def test_typed_template_uri_object_is_accepted_directly(packaged_access: PromptResourceAccess) -> None:
    """The typed spelling works without going through the string form first."""
    text = packaged_access.template_text(ENERGY_SAVING, "en-US")
    assert text == packaged_file_text(f"templates/{ENERGY_SAVING_URI}/en-US/template.md")
