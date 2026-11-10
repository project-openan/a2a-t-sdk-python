"""Data-driven suite of the from-text family corpus: every from-text case becomes one test.

Port of the Java ``FromTextCorpusSuiteTest`` (through the case-test half of ``CorpusSuites``):
every record under ``tests/resources/negotiation-cases/from-text/`` — the five families
FT-HAPPY, FT-RETRY, FT-EXTRACT, FT-PROG and FT-TPL, the bilingual ones expanded once per
declared language — becomes one parametrized test executed by the
:class:`~tests.corpus.engine.CaseEngine` against the production negotiation content wiring: the
four ``generate*FromText`` service methods over the real orchestrator built through the real
builder, with the LLM extraction seam scripted per case (the retry legs replay infrastructure
failures, degenerate responses and recoveries; the template-resolution legs wire the failing
template loader onto the builder's resource-access seam).

The engine owns the per-case assertions — the outcome, the Java exception name through the D22
shim, the error code, the message fragments, the exact ``llmCalls`` count, the prompt-text
fragments, the metadata echoes and the P0 contracts — so the suite test itself is one engine run,
exactly like the Java dynamic test. What the suite adds is the collection half: the guard tests
pin that the parametrize sweeps the whole directory — every corpus file contributes, all four
from-text APIs and both language halves run, the exact-count legs sit side by side (zero-call
probes, one-call extractions and multi-attempt retries), the retry family counts every attempt
exactly (exhaustion, recovery and the non-retryable single call), the extraction failures pin
their contract codes and the three Java exception names assert through the shim — so an
accidentally empty or partial collection fails loudly instead of silently contributing no test
(the fail-fast inversion of the Java ``noteEmpty`` stdout note). The red paths prove the driver
is not a rubber stamp: a flipped outcome, a wrong exact call count and a wrong Java exception
name each fail with the case id and the JSON path.

The corpus ids are the pytest ids, so a subset runs through ``-k`` the way the Java suites use
``-Dcase.filter``: ``-k FT-RETRY`` runs the retry family in both languages, ``-k "FT-HAPPY and
en-US"`` the English happy legs.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Final

import pytest

from a2a_t.core.errors.exceptions import NegotiationGenerationError
from tests.corpus.assemblers import INJECT_FAILING_TEMPLATE_LOADER
from tests.corpus.conftest import CORPUS_ROOT, corpus_case_parametrize, expanded_cases
from tests.corpus.engine import CaseEngine
from tests.corpus.models import LoadedCorpus, NegotiationApi, NegotiationCase
from tests.corpus.shim import resolve_java_exception

#: The four from-text content-service APIs the corpus exercises.
FROM_TEXT_APIS: Final[tuple[NegotiationApi, ...]] = (
    NegotiationApi.GENERATE_PROPOSE_FROM_TEXT,
    NegotiationApi.GENERATE_ACCEPT_FROM_TEXT,
    NegotiationApi.GENERATE_REJECT_FROM_TEXT,
    NegotiationApi.GENERATE_ABORT_FROM_TEXT,
)

#: The Java exception names the from-text failure expectations carry, with their D22 shim
#: carriers — the family's whole exception vocabulary in one place.
_FROM_TEXT_EXCEPTION_SHIMS: Final[tuple[tuple[str, type[BaseException]], ...]] = (
    ("NullPointerException", TypeError),
    ("IllegalArgumentException", ValueError),
    ("NegotiationGenerationException", NegotiationGenerationError),
)

#: The extraction-contract error codes the FT-EXTRACT legs assert.
_EXTRACTION_FAILURE_CODES: Final[frozenset[str]] = frozenset(
    {
        "negotiation.field_missing",
        "negotiation.conclusion_mismatch",
        "negotiation.content_extract_failed",
        "negotiation.invalid_input",
    }
)

#: The retryable codes the extraction step retried to exhaustion re-raises unchanged.
_RETRYABLE_CODES: Final[frozenset[str]] = frozenset({"llm.response_invalid", "llm.invocation_failed"})

#: The non-retryable verdict codes that fail on the first call however much budget is left.
_NON_RETRYABLE_CODES: Final[frozenset[str]] = frozenset(
    {"negotiation.field_missing", "negotiation.conclusion_mismatch"}
)


@pytest.fixture(scope="module")
def case_engine() -> CaseEngine:
    """One case engine for the whole family run — the single engine of the Java suite."""
    return CaseEngine()


@corpus_case_parametrize("from-text")
def test_from_text_corpus_case(case_engine: CaseEngine, case: NegotiationCase) -> None:
    """Execute one expanded from-text case; the engine asserts its whole expectation block."""
    case_engine.run(case)


# ------------------------------------------------------------------ collection guards


def test_the_suite_collects_the_whole_from_text_family(loaded_corpus: LoadedCorpus) -> None:
    """The parametrize sweeps every from-text record: 53 records expanded into 70 language units.

    Every JSON file the directory ships contributes at least one expanded case, and the
    collected records are exactly the from-text cases of the loaded corpus in load order — an
    empty or half-collected family directory fails here instead of contributing no test. The
    counts are the from-text rows of the INDEX.md family distribution table (the loader's INDEX
    parity test pins the full distribution; repeated here so the suite's own collection is what
    fails).
    """
    collected = expanded_cases("from-text")
    expected = [case for case in loaded_corpus.cases if case.source_file.startswith("from-text/")]
    assert collected == expected
    assert len(collected) == 70, "the language-expanded from-text units INDEX.md documents"
    assert len({case.base_id for case in collected}) == 53, "the from-text case records"
    shipped_files = sorted(path.name for path in (CORPUS_ROOT / "from-text").glob("*.json"))
    contributing_files = sorted({Path(case.source_file).name for case in collected})
    assert contributing_files == shipped_files, "every corpus file of the directory contributes"
    per_file: dict[str, int] = {}
    for case in collected:
        per_file[case.source_file] = per_file.get(case.source_file, 0) + 1
    assert per_file == {
        "from-text/extraction-failures.json": 15,
        "from-text/happy.json": 26,
        "from-text/programming-errors.json": 8,
        "from-text/retry.json": 17,
        "from-text/template-resolution.json": 4,
    }
    assert {case.language for case in collected} == {"zh-CN", "en-US"}, "both language halves run"


@pytest.mark.parametrize("api", FROM_TEXT_APIS, ids=lambda api: api.json_name)
def test_every_from_text_api_of_the_content_service_runs(api: NegotiationApi) -> None:
    """All four from-text APIs of the production service run in this suite."""
    assert any(case.api is api for case in expanded_cases("from-text"))


def test_every_from_text_case_scripts_its_llm_leg() -> None:
    """Every from-text case carries an LLM script — even the zero-call legs whose failure
    happens before the LLM seam: their script would answer a regression that suddenly called
    the model, and the exact ``llmCalls`` count would catch the call."""
    cases = expanded_cases("from-text")
    assert cases
    assert all(case.llm is not None for case in cases), "a from-text case without an LLM script"


def test_the_exact_call_count_legs_run_side_by_side() -> None:
    """The whole count spectrum runs in one family: the zero-call probes (the programming errors
    and the template-not-found legs fail before the LLM seam), the one-call extractions of the
    happy and extraction-failure legs, and the two- and three-call retries."""
    counts = {case.expect.llm_calls for case in expanded_cases("from-text")}
    assert counts == {0, 1, 2, 3}, "the family covers the zero-call, single-call and retry counts"


def test_the_outcome_and_expectation_kinds_run_together() -> None:
    """Both outcomes and every expectation kind the from-text family can carry run together."""
    cases = expanded_cases("from-text")
    assert {case.expect.success for case in cases} == {True, False}, "both outcomes run"
    assert any(case.expect.prompt_text_contains for case in cases), "the happy legs assert prompt fragments"
    assert any(case.expect.metadata is not None for case in cases), "the metadata echoes run"
    assert any(case.expect.contracts for case in cases), "the P0 contracts run in this family"
    assert any(case.expect.code is not None for case in cases), "the failures assert their error codes"
    assert any(case.expect.message_contains for case in cases), "the programming errors assert message fragments"


@pytest.mark.parametrize(
    ("java_name", "python_type"),
    _FROM_TEXT_EXCEPTION_SHIMS,
    ids=[java_name for java_name, _ in _FROM_TEXT_EXCEPTION_SHIMS],
)
def test_the_java_exception_names_assert_through_the_d22_shim(java_name: str, python_type: type[BaseException]) -> None:
    """Every Java exception name the family's failure expectations carry resolves through the
    D22 shim onto the Python carrier the run asserts with, and at least one shipped case names
    it — the frozen corpus never sees a Python exception class name."""
    assert resolve_java_exception(java_name).python_type is python_type
    assert any(case.expect.exception == java_name for case in expanded_cases("from-text"))


def test_the_extraction_failures_assert_their_contract_codes() -> None:
    """The FT-EXTRACT legs pin the extraction contract's error codes: the slot-missing verdicts,
    the conclusion-vs-performative mismatches, the shape failures burning a single-attempt
    budget and the content contradictions — each after exactly one extraction call, the
    non-retryable verdicts with the default attempt budget left untouched."""
    extract_cases = [
        case for case in expanded_cases("from-text") if case.source_file == "from-text/extraction-failures.json"
    ]
    assert len(extract_cases) == 15
    assert {case.expect.code for case in extract_cases} == _EXTRACTION_FAILURE_CODES
    for case in extract_cases:
        assert case.expect.success is False
        assert case.expect.llm_calls == 1, "one extraction call per verdict, no retry of the verdict"
        if case.expect.code == "negotiation.content_extract_failed":
            assert case.llm is not None and case.llm.max_attempts == 1, (
                "the retryable shape failures carry a single-attempt budget"
            )


def test_the_retry_family_counts_every_attempt_exactly() -> None:
    """The FT-RETRY legs assert the exact call count of their retry semantics: exhaustion legs
    burn the whole attempt budget and re-raise the original code, recovery legs succeed within
    the budget (on the final attempt at the latest), and the non-retryable legs stop after one
    call however much budget is left — the retry whitelist pinned by counts, not just outcomes."""
    retry_cases = [case for case in expanded_cases("from-text") if case.source_file == "from-text/retry.json"]
    assert len(retry_cases) == 17
    recovered_within_budget = 0
    recovered_on_final_attempt = 0
    non_retryable_legs = 0
    for case in retry_cases:
        llm = case.llm
        assert llm is not None and llm.max_attempts is not None, f"{case.id}: the retry legs set a budget"
        budget = llm.max_attempts
        calls = case.expect.llm_calls
        assert calls is not None, f"{case.id}: the retry legs count their calls exactly"
        if case.expect.success:
            assert 1 <= calls <= budget, f"{case.id}: a recovery stays within the budget"
            if calls < budget:
                recovered_within_budget += 1
            if calls == budget:
                recovered_on_final_attempt += 1
        elif case.expect.code in _NON_RETRYABLE_CODES:
            assert calls == 1 < budget, f"{case.id}: a non-retryable verdict stops after one call"
            non_retryable_legs += 1
        else:
            assert case.expect.code in _RETRYABLE_CODES, f"{case.id}: an exhaustion leg re-raises a retryable code"
            assert calls == budget, f"{case.id}: an exhaustion leg burns the whole budget"
    assert recovered_within_budget >= 1, "a recovery leg succeeds before the budget is exhausted"
    assert recovered_on_final_attempt >= 1, "a recovery leg succeeds on the final attempt"
    assert non_retryable_legs >= 1, "the non-retryable single-call legs run"


def test_the_template_not_found_inject_hook_runs() -> None:
    """The FT-TPL legs wire the failing template loader onto the builder's resource-access seam
    and fail with ``template.not_found`` at zero LLM calls — one leg configures three retry
    attempts and still makes no call, proving the template load happens outside the retry
    loop."""
    tpl_cases = [
        case for case in expanded_cases("from-text") if case.source_file == "from-text/template-resolution.json"
    ]
    assert len(tpl_cases) == 4
    for case in tpl_cases:
        assert case.inject == INJECT_FAILING_TEMPLATE_LOADER
        assert case.expect.success is False
        assert case.expect.code == "template.not_found"
        assert case.expect.llm_calls == 0
    assert any(case.llm is not None and (case.llm.max_attempts or 0) >= 3 for case in tpl_cases), (
        "one leg carries a retry budget the template load ignores"
    )


# ------------------------------------------------------------------ the driver is not a rubber stamp


def test_a_flipped_expectation_of_a_shipped_case_fails_the_suite_driver(
    loaded_corpus: LoadedCorpus,
) -> None:
    """One flipped outcome of a shipped case fails the driver with the case id and JSON path."""
    shipped = next(case for case in loaded_corpus.cases if case.base_id == "FT-HAPPY-01" and case.language == "zh-CN")
    mutated = dataclasses.replace(shipped, expect=dataclasses.replace(shipped.expect, success=False))
    with pytest.raises(AssertionError, match=r"\$\.expect\.outcome"):
        CaseEngine().run(mutated)


def test_a_wrong_exact_call_count_fails_the_run(loaded_corpus: LoadedCorpus) -> None:
    """The retry legs' exact count asserts both directions: FT-RETRY-02's two-step script (an
    empty response, then blank content) must be counted as exactly two calls."""
    shipped = next(case for case in loaded_corpus.cases if case.id == "FT-RETRY-02/zh-CN")
    assert shipped.expect.llm_calls == 2 and shipped.llm is not None
    mutated = dataclasses.replace(shipped, expect=dataclasses.replace(shipped.expect, llm_calls=3))
    with pytest.raises(AssertionError, match=r"\$\.expect\.llmCalls: expected 3 but was 2"):
        CaseEngine().run(mutated)


def test_a_wrong_java_exception_name_fails_the_run(loaded_corpus: LoadedCorpus) -> None:
    """The D22 shim drives the exception assertion: expecting the IAE parity carrier where the
    pipeline raises the NPE parity carrier fails on ``$.expect.exception``."""
    shipped = next(case for case in loaded_corpus.cases if case.id == "FT-PROG-01/zh-CN")
    assert shipped.expect.exception == "NullPointerException"
    mutated = dataclasses.replace(
        shipped, expect=dataclasses.replace(shipped.expect, exception="IllegalArgumentException")
    )
    with pytest.raises(AssertionError, match=r"\$\.expect\.exception: expected IllegalArgumentException"):
        CaseEngine().run(mutated)
