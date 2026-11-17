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

"""Data-driven suite of the validate family corpus: every validate case becomes one test.

Port of the Java ``ValidateCorpusSuiteTest`` (through the case-test half of ``CorpusSuites``):
every record under ``tests/resources/negotiation-cases/validate/`` — the eight families
VAL-HAPPY, VAL-RULE, VAL-SEM, VAL-RETRY, VAL-MERGE, VAL-MAP, VAL-PROG and VAL-DRIFT, each
expanded once per declared language — becomes one parametrized test executed by the
:class:`~tests.corpus.engine.CaseEngine` against the production negotiation content wiring: the
four ``validate*PromptAndDataFilling`` service methods over the real rule gate, semantic
validator and parameter extractor, with the LLM seam scripted per case (zero-call probes run on
the assertion-only client).

The engine owns the per-case assertions — the outcome, the Java exception name through the D22
shim, the error code, message fragments, the exact bag of slot errors, the exact ``llmCalls``
count, the merged parameter map and the P0 contracts — so the suite test itself is one engine
run, exactly like the Java dynamic test. What the suite adds is the collection half: the guard
tests pin that the parametrize sweeps the whole directory — every corpus file contributes, all
four validate APIs and both prompt-source kinds run, and the zero-call rule-gate probes sit side
by side with the multi-call retry legs — so an accidentally empty or partial collection fails
loudly instead of silently contributing no test (the fail-fast inversion of the Java
``noteEmpty`` stdout note). One flipped expectation of a shipped case proves the driver is not a
rubber stamp.

The corpus ids are the pytest ids, so a subset runs through ``-k`` the way the Java suites use
``-Dcase.filter``: ``-k VAL-RULE`` runs the rule gate in both languages, ``-k "VAL-MAP and
zh-CN"`` the zh-CN-only error-code mapping.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from tests.corpus.assemblers import INJECT_FAILING_SEMANTIC_VALIDATOR
from tests.corpus.conftest import (
    CORPUS_ROOT,
    corpus_case_parametrize,
    expanded_cases,
)
from tests.corpus.engine import CaseEngine
from tests.corpus.models import LoadedCorpus, NegotiationApi, NegotiationCase

#: The four validate-family content-service APIs the corpus exercises.
VALIDATE_APIS: tuple[NegotiationApi, ...] = (
    NegotiationApi.VALIDATE_PROPOSE_PROMPT_AND_DATA_FILLING,
    NegotiationApi.VALIDATE_ACCEPT_PROMPT_AND_DATA_FILLING,
    NegotiationApi.VALIDATE_REJECT_PROMPT_AND_DATA_FILLING,
    NegotiationApi.VALIDATE_ABORT_PROMPT_AND_DATA_FILLING,
)


@pytest.fixture(scope="module")
def case_engine() -> CaseEngine:
    """One case engine for the whole family run — the single engine of the Java suite."""
    return CaseEngine()


@corpus_case_parametrize("validate")
def test_validate_corpus_case(case_engine: CaseEngine, case: NegotiationCase) -> None:
    """Execute one expanded validate case; the engine asserts its whole expectation block."""
    case_engine.run(case)


# ------------------------------------------------------------------ collection guards


def test_the_suite_collects_the_whole_validate_family(loaded_corpus: LoadedCorpus) -> None:
    """The parametrize sweeps every validate record: 76 records expanded into 106 language units.

    Every JSON file the directory ships contributes at least one expanded case, and the
    collected records are exactly the validate cases of the loaded corpus in load order — an
    empty or half-collected family directory fails here instead of contributing no test. The
    counts are the validate rows of the INDEX.md family distribution table.
    """
    collected = expanded_cases("validate")
    expected = [case for case in loaded_corpus.cases if case.source_file.startswith("validate/")]
    assert collected == expected
    assert len(collected) == 106, "the language-expanded validate units INDEX.md documents"
    assert len({case.base_id for case in collected}) == 76, "the validate case records"
    shipped_files = sorted(path.name for path in (CORPUS_ROOT / "validate").glob("*.json"))
    contributing_files = sorted({Path(case.source_file).name for case in collected})
    assert contributing_files == shipped_files, "every corpus file of the directory contributes"


@pytest.mark.parametrize("api", VALIDATE_APIS, ids=lambda api: api.json_name)
def test_every_validate_api_of_the_content_service_runs(api: NegotiationApi) -> None:
    """All four validate APIs of the production service run in this suite."""
    assert any(case.api is api for case in expanded_cases("validate"))


def test_the_zero_call_and_multi_call_legs_run_side_by_side() -> None:
    """The exact-``llmCalls`` families run together: the rule gate proves zero calls, the retry
    legs count every attempt, and the happy semantic leg is exactly one call."""
    counts = {case.expect.llm_calls for case in expanded_cases("validate")}
    assert 0 in counts, "the rule gate and the non-negotiation probes expect no LLM call at all"
    assert 1 in counts, "the happy semantic-validation legs expect exactly one call"
    assert any(count is not None and count >= 2 for count in counts), "the retry legs count every attempt"


def test_the_outcome_and_expectation_kinds_run_together() -> None:
    """Both outcomes, the slot-error bag and the P0 contracts are all exercised."""
    cases = expanded_cases("validate")
    assert {case.expect.success for case in cases} == {True, False}, "success and failure outcomes both run"
    assert any(case.expect.slot_errors for case in cases), "the rule gate and semantic verdicts report slot errors"
    assert any(case.expect.contracts for case in cases), "the P0 contracts run in the validate family"


def test_the_prompt_source_kinds_and_the_inject_hook_run() -> None:
    """Both prompt-source kinds, the null-prompt probes and the semantic-validator inject run."""
    cases = expanded_cases("validate")
    kinds = {type(case.prompt).__name__ for case in cases}
    assert {"Golden", "Text"} <= kinds, "the golden regressions and the inline drift probes both run"
    assert any(case.prompt is None for case in cases), "the VAL-PROG null-prompt probes run"
    assert any(case.inject == INJECT_FAILING_SEMANTIC_VALIDATOR for case in cases), (
        "the VAL-MAP prompt-resource-not-found mapping runs"
    )


# ------------------------------------------------------------------ the driver is not a rubber stamp


def test_a_flipped_expectation_of_a_shipped_case_fails_the_suite_driver(
    loaded_corpus: LoadedCorpus,
) -> None:
    """One flipped outcome of a shipped case fails the driver with the case id and JSON path."""
    shipped = next(case for case in loaded_corpus.cases if case.base_id == "VAL-HAPPY-01" and case.language == "zh-CN")
    mutated = dataclasses.replace(shipped, expect=dataclasses.replace(shipped.expect, success=False))
    with pytest.raises(AssertionError, match=r"\$\.expect\.outcome"):
        CaseEngine().run(mutated)
