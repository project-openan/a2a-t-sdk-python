"""Negotiation vocabulary loading through the D31 resource access layer.

Pins :mod:`a2a_t.common.prompt_resources.vocabulary`: the 37 canonical keys match the live
packaged resource for both languages (bilingual parity), and every drift — duplicate keys,
non-string values, blank values, missing or unexpected keys — fails fast with a catalog-coded
``infra.resource_read_failed`` error. The vocabulary is routed (D31): packaged by default, locally
overridable in ``local_file`` mode.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from a2a_t.common.prompt_resources import CANONICAL_KEYS, PromptResourceAccess, Vocabulary, create
from a2a_t.common.prompt_resources.packaged_access import PackagedResourceReader
from a2a_t.common.prompt_resources.vocabulary import load_vocabulary
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError
from tests.common.prompt_resources.conftest import (
    LANGUAGES,
    copy_packaged_vocabulary,
    packaged_vocabulary_entries,
    write_local_resource,
)

#: The live resource is the truth: 37 canonical keys, not the 41 the pre-port plan draft claimed.
CANONICAL_KEY_COUNT = 37


def _local_access(root: Path) -> PromptResourceAccess:
    """Create one local_file-mode access object for one root directory."""
    return create(PromptRuntimeConfig(source_type="local_file", local_root_dir=str(root)))


@pytest.mark.parametrize("language", LANGUAGES)
def test_canonical_keys_match_the_live_resource_exactly(language: str) -> None:
    live = packaged_vocabulary_entries(language)
    assert len(CANONICAL_KEYS) == CANONICAL_KEY_COUNT
    assert len(live) == CANONICAL_KEY_COUNT
    assert set(live) == set(CANONICAL_KEYS)


def test_canonical_keys_carry_no_duplicates() -> None:
    assert len(CANONICAL_KEYS) == len(set(CANONICAL_KEYS))


def test_bilingual_parity_of_the_live_resources() -> None:
    en = packaged_vocabulary_entries("en-US")
    zh = packaged_vocabulary_entries("zh-CN")
    assert set(en) == set(zh)


@pytest.mark.parametrize("language", LANGUAGES)
def test_packaged_vocabulary_loads_both_languages(packaged_access: PromptResourceAccess, language: str) -> None:
    live = packaged_vocabulary_entries(language)
    vocabulary = packaged_access.load_vocabulary(language)
    assert isinstance(vocabulary, Vocabulary)
    assert vocabulary.language == language
    assert set(vocabulary.canonical_keys()) == set(CANONICAL_KEYS)
    assert vocabulary.get("punct.list_colon") == live["punct.list_colon"]
    assert vocabulary.get("section.termination_reason") == live["section.termination_reason"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_local_vocabulary_override_is_honored(tmp_path: Path, language: str) -> None:
    entries = copy_packaged_vocabulary(tmp_path, language)
    entries["punct.list_colon"] = "LOCAL COLON"
    write_local_resource(
        tmp_path,
        f"negotiation-vocabulary/{language}/vocabulary.json",
        json.dumps(entries, ensure_ascii=False),
    )
    access = _local_access(tmp_path)
    assert access.load_vocabulary(language).get("punct.list_colon") == "LOCAL COLON"


@pytest.mark.parametrize("language", LANGUAGES)
def test_missing_local_vocabulary_falls_back_to_the_packaged_copy(tmp_path: Path, language: str) -> None:
    """The Java ADR 0005 overlay: the packaged vocabulary serves a local root without one."""
    access = _local_access(tmp_path)
    vocabulary = access.load_vocabulary(language)
    assert set(vocabulary.canonical_keys()) == set(CANONICAL_KEYS)
    assert vocabulary.get("punct.list_colon") == packaged_vocabulary_entries(language)["punct.list_colon"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_missing_packaged_vocabulary_language_fails_fast(language: str) -> None:
    reader = PackagedResourceReader()
    with pytest.raises(A2ATError) as info:
        load_vocabulary(reader, "xx-XX")
    assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
    assert "xx-XX" in str(info.value)


@pytest.mark.parametrize("language", LANGUAGES)
def test_blank_value_fails_fast(tmp_path: Path, language: str) -> None:
    entries = copy_packaged_vocabulary(tmp_path, language)
    entries["punct.list_colon"] = ""
    write_local_resource(
        tmp_path,
        f"negotiation-vocabulary/{language}/vocabulary.json",
        json.dumps(entries, ensure_ascii=False),
    )
    access = _local_access(tmp_path)
    with pytest.raises(A2ATError) as info:
        access.load_vocabulary(language)
    assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
    assert "blank" in str(info.value)
    assert "punct.list_colon" in str(info.value)


@pytest.mark.parametrize("language", LANGUAGES)
def test_whitespace_value_fails_fast(tmp_path: Path, language: str) -> None:
    entries = copy_packaged_vocabulary(tmp_path, language)
    entries["slot.target"] = "   "
    write_local_resource(
        tmp_path,
        f"negotiation-vocabulary/{language}/vocabulary.json",
        json.dumps(entries, ensure_ascii=False),
    )
    access = _local_access(tmp_path)
    with pytest.raises(A2ATError) as info:
        access.load_vocabulary(language)
    assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
    assert "blank" in str(info.value)


@pytest.mark.parametrize("language", LANGUAGES)
def test_extra_key_fails_fast(tmp_path: Path, language: str) -> None:
    entries = copy_packaged_vocabulary(tmp_path, language)
    entries["section.extra"] = "unexpected"
    write_local_resource(
        tmp_path,
        f"negotiation-vocabulary/{language}/vocabulary.json",
        json.dumps(entries, ensure_ascii=False),
    )
    access = _local_access(tmp_path)
    with pytest.raises(A2ATError) as info:
        access.load_vocabulary(language)
    assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
    assert "unexpected keys" in str(info.value)
    assert "section.extra" in str(info.value)
    assert "missing keys: []" in str(info.value)


@pytest.mark.parametrize("language", LANGUAGES)
def test_missing_key_fails_fast(tmp_path: Path, language: str) -> None:
    entries = copy_packaged_vocabulary(tmp_path, language)
    del entries["section.info_static"]
    write_local_resource(
        tmp_path,
        f"negotiation-vocabulary/{language}/vocabulary.json",
        json.dumps(entries, ensure_ascii=False),
    )
    access = _local_access(tmp_path)
    with pytest.raises(A2ATError) as info:
        access.load_vocabulary(language)
    assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
    assert "missing keys" in str(info.value)
    assert "section.info_static" in str(info.value)
    assert "unexpected keys: []" in str(info.value)


@pytest.mark.parametrize("language", LANGUAGES)
def test_duplicate_key_fails_fast(tmp_path: Path, language: str) -> None:
    entries = copy_packaged_vocabulary(tmp_path, language)
    payload = json.dumps(entries, ensure_ascii=False, indent=2)
    duplicated = payload[:-1] + ',\n  "punct.list_colon": "duplicate"\n}'
    write_local_resource(tmp_path, f"negotiation-vocabulary/{language}/vocabulary.json", duplicated)
    access = _local_access(tmp_path)
    with pytest.raises(A2ATError) as info:
        access.load_vocabulary(language)
    assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
    assert "duplicate" in str(info.value)


@pytest.mark.parametrize("language", LANGUAGES)
def test_non_string_value_fails_fast(tmp_path: Path, language: str) -> None:
    entries = copy_packaged_vocabulary(tmp_path, language)
    entries["label.relationship"] = 42
    write_local_resource(
        tmp_path,
        f"negotiation-vocabulary/{language}/vocabulary.json",
        json.dumps(entries, ensure_ascii=False),
    )
    access = _local_access(tmp_path)
    with pytest.raises(A2ATError) as info:
        access.load_vocabulary(language)
    assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
    assert "is not a string" in str(info.value)


@pytest.mark.parametrize("language", LANGUAGES)
def test_non_object_root_fails_fast(tmp_path: Path, language: str) -> None:
    write_local_resource(tmp_path, f"negotiation-vocabulary/{language}/vocabulary.json", '["not", "an", "object"]')
    access = _local_access(tmp_path)
    with pytest.raises(A2ATError) as info:
        access.load_vocabulary(language)
    assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
    assert "flat JSON object" in str(info.value)


@pytest.mark.parametrize("language", LANGUAGES)
def test_invalid_json_fails_fast(tmp_path: Path, language: str) -> None:
    write_local_resource(tmp_path, f"negotiation-vocabulary/{language}/vocabulary.json", "{not json")
    access = _local_access(tmp_path)
    with pytest.raises(A2ATError) as info:
        access.load_vocabulary(language)
    assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
    assert "not valid JSON" in str(info.value)


@pytest.mark.parametrize("language", ["", "  ", "../escape", "a/b"])
def test_malformed_language_is_rejected(packaged_access: PromptResourceAccess, language: str) -> None:
    with pytest.raises(ValueError):
        packaged_access.load_vocabulary(language)


def test_get_unknown_key_raises_key_error(packaged_access: PromptResourceAccess) -> None:
    vocabulary = packaged_access.load_vocabulary("en-US")
    with pytest.raises(KeyError):
        vocabulary.get("section.does_not_exist")


def test_local_vocabulary_edits_after_capture_are_invisible(tmp_path: Path) -> None:
    """The routed vocabulary rides the same frozen snapshot as every other routed category."""
    entries = copy_packaged_vocabulary(tmp_path, "en-US")
    access = _local_access(tmp_path)
    entries["punct.list_colon"] = "EDITED AFTER CAPTURE"
    write_local_resource(
        tmp_path,
        "negotiation-vocabulary/en-US/vocabulary.json",
        json.dumps(entries, ensure_ascii=False),
    )
    assert access.load_vocabulary("en-US").get("punct.list_colon") != "EDITED AFTER CAPTURE"
