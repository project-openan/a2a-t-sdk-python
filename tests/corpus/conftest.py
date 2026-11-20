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

"""Shared fixtures and collection-time helpers of the corpus test suite.

Port of the corpus access role of the Java ``CorpusSuites`` class: the corpus root path
constants, the once-per-session loaded corpus and the bilingual parametrize helpers. The loader
expands every corpus record once per entry of its ``languages`` array (the expanded id appending
``/zh-CN`` or ``/en-US``), so parametrizing over the expanded records yields the bilingual ids
at collection time — ``-k FT-RETRY-02`` selects both language expansions of that record,
``-k zh-CN`` the whole zh-CN half.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest

from tests.corpus.loader import load
from tests.corpus.models import (
    ContextSpec,
    Expectation,
    LiveCase,
    LiveExpectation,
    LlmFailMarker,
    LlmScript,
    LlmScriptStep,
    LoadedCorpus,
    NegotiationApi,
    NegotiationCase,
    PromptSource,
    ScenarioCase,
)

#: Root directory of the shipped negotiation test corpus.
CORPUS_ROOT = Path(__file__).parents[1] / "resources" / "negotiation-cases"

#: The corpus format definition, validated by the loader's schema layer.
CORPUS_SCHEMA_FILE = CORPUS_ROOT / "corpus-schema.json"

#: The corpus index of record ids (machine generated, soft gate per D25).
CORPUS_INDEX_FILE = CORPUS_ROOT / "INDEX.md"

#: Family directories of the corpus, in load order.
CORPUS_SUITES = ("from-data", "from-text", "validate", "scenarios", "live")

#: Fixed negotiation session id shared by the corpus records (the golden session id).
SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"


@lru_cache(maxsize=1)
def _corpus() -> LoadedCorpus:
    """Load the shipped corpus once per process (collection time and session fixtures share it)."""
    return load(CORPUS_ROOT)


@pytest.fixture(scope="session")
def loaded_corpus() -> LoadedCorpus:
    """The whole negotiation test corpus, loaded once per session."""
    return _corpus()


@pytest.fixture(scope="session")
def corpus_cases(loaded_corpus: LoadedCorpus) -> list[NegotiationCase]:
    """Every expanded offline case record of the corpus, regardless of its family directory."""
    return loaded_corpus.cases


@pytest.fixture(scope="session")
def corpus_scenarios(loaded_corpus: LoadedCorpus) -> list[ScenarioCase]:
    """Every expanded scenario record of the corpus."""
    return loaded_corpus.scenarios


@pytest.fixture(scope="session")
def corpus_live_cases(loaded_corpus: LoadedCorpus) -> list[LiveCase]:
    """Every expanded live-LLM case record of the corpus (never mixed into the offline cases)."""
    return loaded_corpus.live_cases


def expanded_cases(directory: str) -> list[NegotiationCase]:
    """Every expanded case record of one family directory, at collection time.

    :param directory: family directory under the corpus root, such as ``from-text``
    :returns: the expanded cases of that directory in file and record order
    """
    return [case for case in _corpus().cases if case.source_file.startswith(f"{directory}/")]


def expanded_scenarios(directory: str) -> list[ScenarioCase]:
    """Every expanded scenario record of one family directory, at collection time."""
    return [scenario for scenario in _corpus().scenarios if scenario.source_file.startswith(f"{directory}/")]


def expanded_live_cases(directory: str) -> list[LiveCase]:
    """Every expanded live case record of one family directory, at collection time."""
    return [live for live in _corpus().live_cases if live.source_file.startswith(f"{directory}/")]


def corpus_case_parametrize(directory: str) -> pytest.MarkDecorator:
    """Parametrize a suite test over every expanded case of one family directory.

    The test ids are the expanded corpus ids (``FT-RETRY-02/zh-CN``, ``FT-RETRY-02/en-US``), so
    ``-k`` filters by case id or by language suffix. The corpus is loaded once per process, so
    collecting several suites does not re-read the corpus files.
    """
    cases = expanded_cases(directory)
    return pytest.mark.parametrize("case", cases, ids=[case.id for case in cases])


def corpus_scenario_parametrize(directory: str) -> pytest.MarkDecorator:
    """Parametrize a suite test over every expanded scenario of one family directory."""
    scenarios = expanded_scenarios(directory)
    return pytest.mark.parametrize("scenario", scenarios, ids=[scenario.id for scenario in scenarios])


def corpus_live_case_parametrize(directory: str) -> pytest.MarkDecorator:
    """Parametrize a live suite test over every expanded live case of one family directory."""
    live_cases = expanded_live_cases(directory)
    return pytest.mark.parametrize("live_case", live_cases, ids=[live.id for live in live_cases])


#: The model surface consumed by the engine suites; re-exported for one-stop imports.
__all__ = [
    "CORPUS_INDEX_FILE",
    "CORPUS_ROOT",
    "CORPUS_SCHEMA_FILE",
    "CORPUS_SUITES",
    "ContextSpec",
    "Expectation",
    "LiveCase",
    "LiveExpectation",
    "LoadedCorpus",
    "LlmFailMarker",
    "LlmScript",
    "LlmScriptStep",
    "NegotiationApi",
    "NegotiationCase",
    "PromptSource",
    "ScenarioCase",
    "SESSION_ID",
    "corpus_case_parametrize",
    "corpus_live_case_parametrize",
    "corpus_scenario_parametrize",
    "corpus_cases",
    "corpus_live_cases",
    "corpus_scenarios",
    "expanded_cases",
    "expanded_live_cases",
    "expanded_scenarios",
    "loaded_corpus",
]
