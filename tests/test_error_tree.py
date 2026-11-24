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

"""Exception-tree membership tests for the re-parented flat exception families (plan section 2.2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.config.errors import ConfigError, ConfigFileNotFoundError
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError, A2ATError
from a2a_t.llm.errors import LLMConfigError, LLMError, LLMRuntimeError
from a2a_t.prompt.analysis.errors import PromptAnalysisError, ScenarioRecognitionError, SlotExtractionError
from a2a_t.prompt.common.errors import (
    PromptConfigError,
    PromptFetchError,
    PromptLoaderError,
    PromptSourceError,
    TaskPromptFormatError,
)
from a2a_t.prompt.task_rendering.errors import TaskPromptRenderError

#: (exception type, expected code) for the plain A2ATError families.
_A2AT_ERROR_FAMILIES = [
    PromptLoaderError,
    PromptSourceError,
    PromptConfigError,
    PromptFetchError,
    PromptAnalysisError,
    ScenarioRecognitionError,
    SlotExtractionError,
    LLMError,
    ConfigError,
]


@pytest.mark.parametrize("exception_type", _A2AT_ERROR_FAMILIES, ids=lambda exception_type: exception_type.__name__)
def test_flat_error_families_are_reparented_onto_the_a2at_root(exception_type: type[Exception]) -> None:
    assert issubclass(exception_type, A2ATError)


@pytest.mark.parametrize(
    ("exception_type", "expected_code"),
    [
        (LLMConfigError, ErrorCatalog.LLM_NOT_CONFIGURED),
        (LLMRuntimeError, ErrorCatalog.LLM_INVOCATION_FAILED),
        (ConfigError, ErrorCatalog.INFRA_CONFIG_INVALID),
        (ConfigFileNotFoundError, ErrorCatalog.INFRA_CONFIG_INVALID),
        (PromptLoaderError, ErrorCatalog.INFRA_RESOURCE_READ_FAILED),
        (PromptSourceError, ErrorCatalog.INFRA_RESOURCE_READ_FAILED),
    ],
    ids=lambda value: value.value if isinstance(value, ErrorCatalog) else value.__name__,
)
def test_infra_error_families_carry_their_catalog_code(
    exception_type: type[A2ATError],
    expected_code: ErrorCatalog,
) -> None:
    error: A2ATError = (
        exception_type(Path(".env-missing"))
        if exception_type is ConfigFileNotFoundError
        else exception_type("failure detail")
    )

    assert error.code is expected_code
    assert error.code_str == expected_code.value


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_facts"),
    [
        (TaskPromptRenderError("unknown slot"), ErrorCatalog.TEMPLATE_RENDER_FAILED, {"reason": "unknown slot"}),
    ],
    ids=["task-prompt-render"],
)
def test_business_error_families_carry_code_and_facts(
    error: A2ATBusinessError,
    expected_code: ErrorCatalog,
    expected_facts: dict[str, str],
) -> None:
    assert isinstance(error, A2ATBusinessError)
    assert error.code is expected_code
    assert error.facts == expected_facts


def test_task_prompt_format_error_stays_outside_the_tree_as_a_caller_contract_violation() -> None:
    assert issubclass(TaskPromptFormatError, ValueError)
    assert not issubclass(TaskPromptFormatError, A2ATError)


def test_config_file_not_found_error_renders_the_catalog_message() -> None:
    error = ConfigFileNotFoundError(Path("custom.env"))

    assert isinstance(error, A2ATError)
    assert error.code is ErrorCatalog.INFRA_CONFIG_INVALID
    assert str(error) == "Invalid configuration 'custom.env': config file does not exist"
    assert error.path == Path("custom.env")
