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

"""Data-driven suite of the from-data family corpus: every from-data case becomes one test.

Port of the Java ``FromDataCorpusSuiteTest`` (through the case-test half of ``CorpusSuites``):
every record under ``tests/resources/negotiation-cases/from-data/`` — the two families
FD-HAPPY (the deterministic generation matrix, every case carrying a zero-LLM-call proof) and
FD-PROG (the absorbed 22-row programming-error matrix), the bilingual ones expanded once per
declared language — becomes one parametrized test executed by the
:class:`~tests.corpus.engine.CaseEngine` against the production negotiation content wiring: the
four ``generate*FromData`` service methods over the real orchestrator built through the real
builder, the typed input assembled from ``input.data`` by the real typed-input assembler.

The zero-LLM-call proof is structural: the engine runs every from-data case on the
assertion-only client — any call the deterministic pipeline made would fail the run with the
assertion marker — and every shipped case also expects ``llmCalls == 0``. The engine owns the
per-case assertions (the outcome, the Java exception name through the D22 shim, the error code,
the message fragments, the exact ``llmCalls`` count, the prompt-text fragments, the metadata
echoes and the P0 contracts), so the suite test itself is one engine run, exactly like the Java
dynamic test. What the suite adds is the collection half: the guard tests pin that the
parametrize sweeps the whole directory — every corpus file contributes, all four from-data APIs
and both language halves run, every case proves zero calls, and the programming-error matrix
covers both failure carriers (the coded business failures and the D22-shimmed programming
errors) — so an accidentally empty or partial collection fails loudly instead of silently
contributing no test (the fail-fast inversion of the Java ``noteEmpty`` stdout note). The red
paths prove the driver is not a rubber stamp: a flipped outcome, a non-zero call-count
expectation and a wrong Java exception name each fail with the case id and the JSON path.

The corpus ids are the pytest ids, so a subset runs through ``-k`` the way the Java suites use
``-Dcase.filter``: ``-k FD-PROG`` runs the programming-error matrix, ``-k "FD-HAPPY and
en-US"`` the English deterministic legs.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Final

import pytest

from a2a_t.core.errors.exceptions import NegotiationGenerationError
from tests.corpus.conftest import CORPUS_ROOT, corpus_case_parametrize, expanded_cases
from tests.corpus.engine import CaseEngine
from tests.corpus.models import LoadedCorpus, NegotiationApi, NegotiationCase
from tests.corpus.shim import resolve_java_exception

#: The four from-data content-service APIs the corpus exercises.
FROM_DATA_APIS: Final[tuple[NegotiationApi, ...]] = (
    NegotiationApi.GENERATE_PROPOSE_FROM_DATA,
    NegotiationApi.GENERATE_ACCEPT_FROM_DATA,
    NegotiationApi.GENERATE_REJECT_FROM_DATA,
    NegotiationApi.GENERATE_ABORT_FROM_DATA,
)

#: The Java exception names the from-data failure expectations carry, with their D22 shim
#: carriers — the family's whole exception vocabulary in one place.
_FROM_DATA_EXCEPTION_SHIMS: Final[tuple[tuple[str, type[BaseException]], ...]] = (
    ("NullPointerException", TypeError),
    ("IllegalArgumentException", ValueError),
    ("NegotiationGenerationException", NegotiationGenerationError),
)

#: The coded business failures the typed content validation of the from-data legs surfaces.
_FROM_DATA_BUSINESS_CODES: Final[frozenset[str]] = frozenset(
    {"negotiation.conclusion_mismatch", "negotiation.content_invalid"}
)


@pytest.fixture(scope="module")
def case_engine() -> CaseEngine:
    """One case engine for the whole family run — the single engine of the Java suite."""
    return CaseEngine()


@corpus_case_parametrize("from-data")
def test_from_data_corpus_case(case_engine: CaseEngine, case: NegotiationCase) -> None:
    """Execute one expanded from-data case; the engine asserts its whole expectation block."""
    case_engine.run(case)


# ------------------------------------------------------------------ collection guards


def test_the_suite_collects_the_whole_from_data_family(loaded_corpus: LoadedCorpus) -> None:
    """The parametrize sweeps every from-data record: 25 records expanded into 36 language units.

    Every JSON file the directory ships contributes at least one expanded case, and the
    collected records are exactly the from-data cases of the loaded corpus in load order — an
    empty or half-collected family directory fails here instead of contributing no test. The
    counts are the from-data rows of the INDEX.md family distribution table (the loader's INDEX
    parity test pins the full distribution; repeated here so the suite's own collection is what
    fails).
    """
    collected = expanded_cases("from-data")
    expected = [case for case in loaded_corpus.cases if case.source_file.startswith("from-data/")]
    assert collected == expected
    assert len(collected) == 36, "the language-expanded from-data units INDEX.md documents"
    assert len({case.base_id for case in collected}) == 25, "the from-data case records"
    shipped_files = sorted(path.name for path in (CORPUS_ROOT / "from-data").glob("*.json"))
    contributing_files = sorted({Path(case.source_file).name for case in collected})
    assert contributing_files == shipped_files, "every corpus file of the directory contributes"
    per_file: dict[str, int] = {}
    for case in collected:
        per_file[case.source_file] = per_file.get(case.source_file, 0) + 1
    assert per_file == {
        "from-data/happy.json": 22,
        "from-data/programming-errors.json": 14,
    }
    assert {case.language for case in collected} == {"zh-CN", "en-US"}, "both language halves run"


@pytest.mark.parametrize("api", FROM_DATA_APIS, ids=lambda api: api.json_name)
def test_every_from_data_api_of_the_content_service_runs(api: NegotiationApi) -> None:
    """All four from-data APIs of the production service run in this suite."""
    assert any(case.api is api for case in expanded_cases("from-data"))


def test_every_from_data_case_proves_zero_llm_calls() -> None:
    """The deterministic from-data legs are proven zero-call, twice over: no case scripts any
    LLM behavior, and every case expects exactly zero calls — the engine runs the family on the
    assertion-only client, so the first call a regression introduced would fail the run with
    the assertion marker before the count comparison even runs."""
    cases = expanded_cases("from-data")
    assert cases
    for case in cases:
        assert case.llm is None, f"{case.id}: a from-data case scripts no LLM behavior"
        assert case.expect.llm_calls == 0, f"{case.id}: the deterministic leg expects zero LLM calls"


def test_the_outcome_and_expectation_kinds_run_together() -> None:
    """Both outcomes and every expectation kind the from-data family can carry run together."""
    cases = expanded_cases("from-data")
    assert {case.expect.success for case in cases} == {True, False}, "both outcomes run"
    assert any(case.expect.prompt_text_contains for case in cases), "the happy legs assert prompt fragments"
    assert any(case.expect.metadata is not None for case in cases), "the metadata echoes run"
    assert any(case.expect.contracts for case in cases), "the P0 contracts run in this family"
    assert any(case.expect.code is not None for case in cases), "the business failures assert their codes"
    assert any(case.expect.message_contains for case in cases), "the programming errors assert message fragments"


@pytest.mark.parametrize(
    ("java_name", "python_type"),
    _FROM_DATA_EXCEPTION_SHIMS,
    ids=[java_name for java_name, _ in _FROM_DATA_EXCEPTION_SHIMS],
)
def test_the_java_exception_names_assert_through_the_d22_shim(java_name: str, python_type: type[BaseException]) -> None:
    """Every Java exception name the family's failure expectations carry resolves through the
    D22 shim onto the Python carrier the run asserts with, and at least one shipped case names
    it — the frozen corpus never sees a Python exception class name."""
    assert resolve_java_exception(java_name).python_type is python_type
    assert any(case.expect.exception == java_name for case in expanded_cases("from-data"))


def test_the_programming_error_matrix_covers_both_failure_carriers() -> None:
    """The FD-PROG matrix runs both failure carriers at zero LLM calls: the coded business
    failures of the typed content validation (the conclusion mismatches and the blank required
    fields) and the programming-error carriers mapped by the D22 shim — one null-context NPE
    and the five template-URI rejections the typed-input assembler raises as IAE parity
    errors."""
    prog_cases = [
        case for case in expanded_cases("from-data") if case.source_file == "from-data/programming-errors.json"
    ]
    assert len(prog_cases) == 14
    assert {case.expect.code for case in prog_cases if case.expect.code is not None} == _FROM_DATA_BUSINESS_CODES
    null_context = [case for case in prog_cases if case.expect.exception == "NullPointerException"]
    assert len(null_context) == 1 and null_context[0].base_id == "FD-PROG-01"
    uri_rejections = [case for case in prog_cases if case.expect.exception == "IllegalArgumentException"]
    assert len(uri_rejections) == 5, "the five template-URI rejection legs"
    assert all(case.expect.message_contains for case in uri_rejections), (
        "each URI rejection names the rejected URI in its message"
    )
    for case in prog_cases:
        assert case.expect.success is False
        assert case.expect.llm_calls == 0


# ------------------------------------------------------------------ the driver is not a rubber stamp


def test_a_flipped_expectation_of_a_shipped_case_fails_the_suite_driver(
    loaded_corpus: LoadedCorpus,
) -> None:
    """One flipped outcome of a shipped case fails the driver with the case id and JSON path."""
    shipped = next(case for case in loaded_corpus.cases if case.base_id == "FD-HAPPY-01" and case.language == "zh-CN")
    mutated = dataclasses.replace(shipped, expect=dataclasses.replace(shipped.expect, success=False))
    with pytest.raises(AssertionError, match=r"\$\.expect\.outcome"):
        CaseEngine().run(mutated)


def test_a_nonzero_call_count_expectation_fails_the_zero_call_proof(loaded_corpus: LoadedCorpus) -> None:
    """The zero of the deterministic legs is compared, not vacuous: expecting one call on a
    from-data leg fails on ``$.expect.llmCalls`` with both counts in the message."""
    shipped = next(case for case in loaded_corpus.cases if case.id == "FD-HAPPY-01/zh-CN")
    assert shipped.expect.llm_calls == 0
    mutated = dataclasses.replace(shipped, expect=dataclasses.replace(shipped.expect, llm_calls=1))
    with pytest.raises(AssertionError, match=r"\$\.expect\.llmCalls: expected 1 but was 0"):
        CaseEngine().run(mutated)


def test_a_wrong_java_exception_name_fails_the_run(loaded_corpus: LoadedCorpus) -> None:
    """The D22 shim drives the exception assertion of the programming-error matrix: expecting
    the coded generation carrier where the pipeline raises the NPE parity carrier fails on
    ``$.expect.exception``."""
    shipped = next(case for case in loaded_corpus.cases if case.id == "FD-PROG-01/zh-CN")
    assert shipped.expect.exception == "NullPointerException"
    mutated = dataclasses.replace(
        shipped, expect=dataclasses.replace(shipped.expect, exception="NegotiationGenerationException")
    )
    with pytest.raises(AssertionError, match=r"\$\.expect\.exception: expected NegotiationGenerationException"):
        CaseEngine().run(mutated)
