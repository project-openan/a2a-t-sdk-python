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

"""Shared fixtures and helpers for the D31 resource access layer tests."""

from __future__ import annotations

import json
import shutil
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Any, Final

import pytest

from a2a_t.common.prompt_resources import PromptResourceAccess, create
from a2a_t.common.prompt_resources.packaged_access import PackagedResourceReader
from a2a_t.config.models import PromptRuntimeConfig

LANGUAGES: Final[tuple[str, str]] = ("en-US", "zh-CN")

_PACKAGE: Final[str] = "a2a_t"
_PROMPT_RESOURCES: Final[str] = "prompt_resources"

#: Template URIs of every bundled template that carries a slot schema (negotiation templates
#: render without slots).
SLOT_TEMPLATE_URIS: Final[tuple[str, ...]] = (
    "Task-T/network-layer/ran-energy-saving/v1",
    "Task-T/network-layer/private-line-complaint/v1",
    "Notification-T/network-layer/subscribe-incident/v1",
    "Notification-T/network-layer/service-recovery/v1",
    "Authorization-T/authorization-policy-management/v1",
)

#: Template URIs of the bundled negotiation templates (routed through the same tree, D31).
NEGOTIATION_TEMPLATE_URIS: Final[tuple[str, ...]] = (
    "Negotiation-T/information-negotiation/propose/v1",
    "Negotiation-T/information-negotiation/accept-reject/v1",
    "Negotiation-T/target-negotiation/propose/v1",
    "Negotiation-T/target-negotiation/accept-reject/v1",
    "Negotiation-T/feasibility-negotiation/propose/v1",
    "Negotiation-T/feasibility-negotiation/accept-reject/v1",
    "Negotiation-T/common/abort/v1",
)


def packaged_file_text(resource_relative_path: str) -> str:
    """Read one packaged prompt resource file independently of the layer under test."""
    return (
        resource_files(_PACKAGE)
        .joinpath(_PROMPT_RESOURCES, *resource_relative_path.split("/"))
        .read_text(encoding="utf-8")
    )


def packaged_vocabulary_entries(language: str) -> dict[str, str]:
    """Return the live packaged vocabulary entries of one language, parsed independently."""
    return json.loads(packaged_file_text(f"negotiation-vocabulary/{language}/vocabulary.json"))


def packaged_root() -> Path:
    """Return the packaged prompt resource tree location (source and wheel layouts both work)."""
    return Path(str(resource_files(_PACKAGE).joinpath(_PROMPT_RESOURCES)))


@pytest.fixture
def packaged_access() -> PromptResourceAccess:
    """Access object serving routed resources from the packaged tree."""
    return create(PromptRuntimeConfig(source_type="packaged", local_root_dir=""))


@pytest.fixture
def packaged_reader() -> PackagedResourceReader:
    """Reader over the packaged prompt resource tree."""
    return PackagedResourceReader()


def write_local_resource(root: Path, relative_path: str, text: str) -> Path:
    """Write one text resource under a local root, creating parent directories."""
    target = root.joinpath(*relative_path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def write_local_vocabulary(root: Path, language: str, entries: dict[str, Any]) -> Path:
    """Write one local negotiation vocabulary from a raw entries mapping."""
    return write_local_resource(
        root,
        f"negotiation-vocabulary/{language}/vocabulary.json",
        json.dumps(entries, ensure_ascii=False, indent=2),
    )


def copy_packaged_vocabulary(root: Path, language: str) -> dict[str, str]:
    """Seed one local root with a copy of the packaged vocabulary, returning its entries."""
    entries = packaged_vocabulary_entries(language)
    write_local_vocabulary(root, language, entries)
    return entries


def copy_packaged_tree(root: Path) -> None:
    """Copy the whole packaged prompt resource tree into one local root."""
    shutil.copytree(packaged_root(), root, dirs_exist_ok=True)
