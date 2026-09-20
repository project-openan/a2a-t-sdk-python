"""Strict JSON loading of the routed JSON resources through the D31 access layer.

Pins :mod:`a2a_t.common.prompt_resources.json_source`: duplicate-key detection, object-root
enforcement, and the scenario / error-catalog loaders with their catalog-coded read failures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from a2a_t.common.prompt_resources import PromptResourceAccess, create, json_source
from a2a_t.common.prompt_resources.json_source import (
    DuplicateJsonKeyError,
    error_catalog_key,
    parse_json_document,
    parse_json_object,
    scenario_catalog_key,
)
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError
from tests.common.prompt_resources.conftest import LANGUAGES, packaged_file_text, write_local_resource


def _local_access(root: Path) -> PromptResourceAccess:
    """Create one local_file-mode access object for one root directory."""
    return create(PromptRuntimeConfig(source_type="local_file", local_root_dir=str(root)))


class _StubReader:
    """Minimal reader serving one canned payload for every key."""

    def __init__(self, text: str) -> None:
        self._text = text

    def read_text(self, key: object) -> str:
        return self._text

    def category_types(self, category: str) -> tuple[str, ...]:
        return ()


class TestStrictParsing:
    """The strict parser: duplicate keys and non-object roots fail fast."""

    @pytest.mark.parametrize(
        ("payload", "key"),
        [
            ('{"a": 1, "a": 2}', "a"),
            ('{"a": {"b": 1, "b": 2}}', "b"),
            ('{"a": 1, "b": 2, "a": 3}', "a"),
        ],
    )
    def test_duplicate_keys_fail_fast(self, payload: str, key: str) -> None:
        with pytest.raises(DuplicateJsonKeyError) as info:
            parse_json_document(payload, origin="test.json")
        assert info.value.key == key

    def test_duplicate_keys_carry_the_origin_when_wrapped(self) -> None:
        with pytest.raises(ValueError) as info:
            parse_json_object('{"a": 1, "a": 2}', origin="slots/Probe-T/v1/en-US/slot.json")
        assert "slots/Probe-T/v1/en-US/slot.json" in str(info.value)

    @pytest.mark.parametrize("payload", ["[1, 2]", '"text"', "42", "null"])
    def test_non_object_roots_fail_fast(self, payload: str) -> None:
        with pytest.raises(ValueError, match="JSON object at the root"):
            parse_json_object(payload, origin="test.json")

    @pytest.mark.parametrize("payload", ["{", "{'a': 1}", ""])
    def test_invalid_json_fails_fast(self, payload: str) -> None:
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_json_document(payload, origin="test.json")

    def test_valid_document_parses(self) -> None:
        assert parse_json_document('{"a": 1}', origin="test.json") == {"a": 1}
        assert parse_json_object('{"a": {"b": 2}}', origin="test.json") == {"a": {"b": 2}}


class TestResourceKeys:
    """The resource keys of the JSON categories assemble the canonical paths."""

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_scenario_catalog_key(self, language: str) -> None:
        assert scenario_catalog_key(language).relative_path() == f"prompt_resources/scenarios/{language}/scenarios.json"

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_error_catalog_key(self, language: str) -> None:
        assert error_catalog_key(language).relative_path() == f"prompt_resources/errors/{language}/errors.json"


class TestScenarioCatalog:
    """The routed scenario catalog loader."""

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_packaged_scenarios_load(self, packaged_access: PromptResourceAccess, language: str) -> None:
        scenarios = packaged_access.load_scenarios(language)
        assert len(scenarios) >= 1
        codes = {scenario.scenario_code for scenario in scenarios}
        assert {"ran-energy-saving", "subscribe-incident"} <= codes
        for scenario in scenarios:
            assert scenario.scenario_name
            assert scenario.description
            assert scenario.example

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_local_scenario_override_is_honored(self, tmp_path: Path, language: str) -> None:
        write_local_resource(
            tmp_path,
            f"scenarios/{language}/scenarios.json",
            json.dumps(
                {
                    "scenarios": [
                        {
                            "scenario_code": "local-scenario",
                            "scenario_name": "Local Scenario",
                            "description": "A scenario defined by the local root only.",
                            "example": "Local example",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )
        access = _local_access(tmp_path)
        scenarios = access.load_scenarios(language)
        assert [scenario.scenario_code for scenario in scenarios] == ["local-scenario"]

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_missing_local_scenarios_fall_back_to_the_packaged_catalog(self, tmp_path: Path, language: str) -> None:
        access = _local_access(tmp_path)
        scenarios = access.load_scenarios(language)
        assert {scenario.scenario_code for scenario in scenarios} >= {"ran-energy-saving", "subscribe-incident"}

    def test_unknown_language_fails_with_the_resource_path(self, packaged_access: PromptResourceAccess) -> None:
        with pytest.raises(A2ATError) as info:
            packaged_access.load_scenarios("xx-XX")
        assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
        assert "scenarios/xx-XX/scenarios.json" in str(info.value)

    def test_malformed_local_scenarios_fail_fast(self, tmp_path: Path) -> None:
        write_local_resource(tmp_path, "scenarios/en-US/scenarios.json", '{"scenarios": "not-a-list"}')
        access = _local_access(tmp_path)
        with pytest.raises(A2ATError) as info:
            access.load_scenarios("en-US")
        assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
        assert "scenarios" in str(info.value)

    def test_scenario_entry_with_missing_fields_fails_fast(self, tmp_path: Path) -> None:
        write_local_resource(
            tmp_path,
            "scenarios/en-US/scenarios.json",
            '{"scenarios": [{"scenario_code": "broken"}]}',
        )
        access = _local_access(tmp_path)
        with pytest.raises(A2ATError) as info:
            access.load_scenarios("en-US")
        assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
        assert "missing the fields" in str(info.value)
        assert "scenario_name" in str(info.value)

    def test_unknown_scenario_fields_are_ignored(self, tmp_path: Path) -> None:
        write_local_resource(
            tmp_path,
            "scenarios/en-US/scenarios.json",
            json.dumps(
                {
                    "scenarios": [
                        {
                            "scenario_code": "local-scenario",
                            "scenario_name": "Local Scenario",
                            "description": "description",
                            "example": "example",
                            "future_field": "ignored (Java @JsonIgnoreProperties parity)",
                        }
                    ]
                }
            ),
        )
        access = _local_access(tmp_path)
        assert len(access.load_scenarios("en-US")) == 1


class TestErrorCatalog:
    """The package-fixed error message catalog loader."""

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_packaged_errors_load(self, packaged_access: PromptResourceAccess, language: str) -> None:
        errors = packaged_access.load_errors(language)
        expected = json.loads(packaged_file_text(f"errors/{language}/errors.json"))
        assert errors == expected
        assert len(errors) == 42
        assert "{template_uri}" in errors["template.not_found"]
        assert "{resource_path}" in errors["infra.resource_read_failed"]

    def test_unknown_language_fails_with_the_resource_path(self, packaged_access: PromptResourceAccess) -> None:
        with pytest.raises(A2ATError) as info:
            packaged_access.load_errors("xx-XX")
        assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
        assert "errors/xx-XX/errors.json" in str(info.value)

    def test_local_errors_are_ignored_in_local_file_mode(self, tmp_path: Path) -> None:
        write_local_resource(tmp_path, "errors/en-US/errors.json", '{"template.not_found": "CUSTOM"}')
        access = _local_access(tmp_path)
        assert access.load_errors("en-US")["template.not_found"] != "CUSTOM"

    def test_non_string_values_fail_fast(self) -> None:
        reader = _StubReader('{"template.not_found": 42}')
        with pytest.raises(A2ATError) as info:
            json_source.load_error_messages(reader, "en-US")
        assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
        assert "non-string" in str(info.value)

    def test_malformed_errors_fail_fast(self) -> None:
        reader = _StubReader("{not json")
        with pytest.raises(A2ATError) as info:
            json_source.load_error_messages(reader, "en-US")
        assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
