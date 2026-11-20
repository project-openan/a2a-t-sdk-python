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

from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from typing import Any

from a2a_t.common.prompt_resources.models import ScenarioDefinition
from a2a_t.prompt.common.models import FetchResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_ENV_PATH = PROJECT_ROOT / "tests" / ".env"

_EMPTY_SLOT_JSON_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {},
    "required": [],
}


class FakePromptResourceAccess:
    """Configurable in-memory stand-in for the D31 resource access layer.

    Serves canned template text, raw slot schemas, instruction prompts and scenario catalogs, and
    records the addresses it was asked for. Passing an exception for one family makes the
    corresponding accessor raise it, which lets pipeline tests pin the failure boundaries.
    """

    def __init__(
        self,
        *,
        template_text: str | Exception = "",
        slot_json_schema: dict[str, object] | Exception | None = None,
        system_prompt: str | Exception = "",
        user_prompt: str | Exception = "",
        scenarios: list[ScenarioDefinition] | Exception | None = None,
    ) -> None:
        self._template_text = template_text
        self._slot_json_schema = _EMPTY_SLOT_JSON_SCHEMA if slot_json_schema is None else slot_json_schema
        self._system_prompt = system_prompt
        self._user_prompt = user_prompt
        self._scenarios: list[ScenarioDefinition] | Exception = [] if scenarios is None else scenarios
        self.template_calls: list[tuple[str, str]] = []
        self.slot_schema_calls: list[tuple[str, str]] = []
        self.prompt_calls: list[tuple[str, str, str]] = []
        self.scenario_calls: list[str] = []

    def load_scenarios(self, language: str) -> list[ScenarioDefinition]:
        self.scenario_calls.append(language)
        if isinstance(self._scenarios, Exception):
            raise self._scenarios
        return self._scenarios

    def template_text(self, template_uri: str, language: str) -> str:
        self.template_calls.append((template_uri, language))
        if isinstance(self._template_text, Exception):
            raise self._template_text
        return self._template_text

    def slot_schema(self, template_uri: str, language: str) -> dict[str, Any]:
        self.slot_schema_calls.append((template_uri, language))
        if isinstance(self._slot_json_schema, Exception):
            raise self._slot_json_schema
        return dict(self._slot_json_schema)

    def load_prompt(self, category: str, language: str, file_name: str) -> str:
        self.prompt_calls.append((category, language, file_name))
        if file_name == "system.md":
            if isinstance(self._system_prompt, Exception):
                raise self._system_prompt
            return self._system_prompt
        if isinstance(self._user_prompt, Exception):
            raise self._user_prompt
        return self._user_prompt


def build_markdown(
    *,
    name: str,
    language: str | None,
    title: str,
    description: str,
    body: str,
) -> str:
    lines = [
        "---",
        f"name: {name}",
    ]
    if language is not None:
        lines.append(f"language: {language}")
    lines.extend(
        [
            f"title: {title}",
            f"description: {description}",
            "---",
            body,
        ]
    )

    return "\n".join(lines) + "\n"


class FakeRemoteProvider:
    def __init__(self, responses: list[FetchResult | Exception]) -> None:
        self._responses = responses
        self.calls = 0

    def fetch(self, locator: str) -> FetchResult:
        response = self._responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response


class ManagedTempDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._managed_temp_dirs: list[Path] = []
        self.addCleanup(self.cleanup_temp_dirs)

    def make_temp_dir(self, name: str) -> Path:
        if not hasattr(self, "_managed_temp_dirs"):
            self._managed_temp_dirs = []
        temp_dir = PROJECT_ROOT / ".tmp_tests" / name / self._testMethodName
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._managed_temp_dirs.append(temp_dir)
        return temp_dir

    def cleanup_temp_dirs(self) -> None:
        for temp_dir in reversed(self._managed_temp_dirs):
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._cleanup_empty_parents(temp_dir.parent)
        self._managed_temp_dirs.clear()

    def _cleanup_empty_parents(self, directory: Path) -> None:
        temp_root = PROJECT_ROOT / ".tmp_tests"
        current = directory
        while current.exists() and current != PROJECT_ROOT:
            try:
                current.rmdir()
            except OSError:
                break
            if current == temp_root:
                break
            current = current.parent
