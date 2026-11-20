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

"""Packaged resource reads through ``importlib.resources`` (D8): frozen cache, missing never cached.

Pins :mod:`a2a_t.common.prompt_resources.packaged_access`: successful reads are cached at module
level, and a missing resource raises on every call — never cached — so a resource appearing later
in the same process is picked up.
"""

from __future__ import annotations

from importlib.resources.abc import Traversable
from typing import Any

import pytest

from a2a_t.common.prompt_resources import packaged_access
from a2a_t.core.errors.exceptions import ResourceNotFoundError
from a2a_t.core.prompt_resource_key import PromptResourceKey
from a2a_t.core.template_uri import TemplateUri
from tests.common.prompt_resources.conftest import LANGUAGES, packaged_file_text

#: A resource key that exists in no packaged tree; unique to these tests so the module-level cache
#: entries it creates can never leak into any other test.
_LATE_KEY = PromptResourceKey("templates", ("Probe-T", "late", "v1"), "en-US", "template.md")
_LATE_TEXT = "late content"

#: The cached-read probe key: a real packaged resource that other suites also read, so its cache
#: entry must be dropped around every test here to keep the probe counts deterministic.
_CACHE_PROBE_KEY = PromptResourceKey.prompt("slot_extraction", "en-US", "system.md")


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    """Drop the cache entries this module's tests create, before and after each test."""
    packaged_access._text_cache.pop(_LATE_KEY.relative_path(), None)
    packaged_access._text_cache.pop(_CACHE_PROBE_KEY.relative_path(), None)
    yield
    packaged_access._text_cache.pop(_LATE_KEY.relative_path(), None)
    packaged_access._text_cache.pop(_CACHE_PROBE_KEY.relative_path(), None)


class _FakeResource:
    """Traversable standing in for one packaged file with known content."""

    def __init__(self, text: str) -> None:
        self._text = text

    def is_file(self) -> bool:
        return True

    def read_text(self, encoding: str = "utf-8") -> str:
        return self._text


class _MissingResource:
    """Traversable standing in for one packaged path that does not exist."""

    def is_file(self) -> bool:
        return False


class _FakePackage:
    """Package root whose ``joinpath`` serves only the files it was given."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def joinpath(self, *segments: str) -> Traversable:
        path = "/".join(segments)
        if path in self._files:
            return _FakeResource(self._files[path])
        return _MissingResource()


@pytest.mark.parametrize("language", LANGUAGES)
def test_load_text_returns_the_packaged_file_content(language: str) -> None:
    template_uri = TemplateUri.parse("Task-T/network-layer/ran-energy-saving/v1")
    assert template_uri is not None
    key = PromptResourceKey.template(template_uri, language, "template.md")
    assert packaged_access.load_text(key) == packaged_file_text(
        f"templates/Task-T/network-layer/ran-energy-saving/v1/{language}/template.md"
    )


def test_load_text_caches_successful_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    real_files = packaged_access._resource_files
    probes: list[str] = []

    def counting_files(package: str) -> Any:
        probes.append(package)
        return real_files(package)

    key = PromptResourceKey.prompt("slot_extraction", "en-US", "system.md")
    monkeypatch.setattr(packaged_access, "_resource_files", counting_files)
    first = packaged_access.load_text(key)
    second = packaged_access.load_text(key)
    assert first == second == packaged_file_text("prompts/slot_extraction/en-US/system.md")
    assert len(probes) == 1  # the second read came from the module-level cache


def test_missing_resource_raises_on_every_call_and_is_never_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    real_files = packaged_access._resource_files
    probes: list[str] = []

    def counting_files(package: str) -> Any:
        probes.append(package)
        return real_files(package)

    monkeypatch.setattr(packaged_access, "_resource_files", counting_files)
    for _ in range(3):
        with pytest.raises(ResourceNotFoundError):
            packaged_access.load_text(_LATE_KEY)
    assert len(probes) == 3  # every call re-probed: the miss was never cached
    assert _LATE_KEY.relative_path() not in packaged_access._text_cache


def test_resource_appearing_later_is_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ResourceNotFoundError):
        packaged_access.load_text(_LATE_KEY)
    monkeypatch.setattr(
        packaged_access,
        "_resource_files",
        lambda package: _FakePackage({_LATE_KEY.relative_path(): _LATE_TEXT}),
    )
    assert packaged_access.load_text(_LATE_KEY) == _LATE_TEXT


def test_list_category_directories_discovers_extension_types() -> None:
    template_types = packaged_access.list_category_directories("templates")
    assert {"Task-T", "Notification-T", "Negotiation-T", "Authorization-T"} <= set(template_types)
    slot_types = packaged_access.list_category_directories("slots")
    assert {"Task-T", "Notification-T", "Authorization-T"} <= set(slot_types)
    assert "Negotiation-T" not in slot_types
    assert packaged_access.list_category_directories("does-not-exist") == ()
