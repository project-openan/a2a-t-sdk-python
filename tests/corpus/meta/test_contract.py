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

"""Contract meta test of the negotiation test corpus (port of Java ``CorpusContractTest``).

The corpus is checked against its own contracts instead of the production code, so a hole in the
corpus is caught before it silently narrows the suites. Checked in full strictness (design §8.4:
a missing dimension is a hard failure, not a TODO line): global id uniqueness, expectation-block
completeness, every expected error code being one of the content-layer error codes, the
content-layer failure coverage (all ten codes), the validate-family code coverage (all six codes),
the closed-loop task-API scoping and role binding (Q20–Q23), the drift-probe shape (Q22), the live
phase-1 scope and expectation completeness, the four operational definitions of the bilingual
parity (§7), and the corpus counts the INDEX.md statistics document.

The Q19 business-review soft gate (§8.7) is opt-in: P0 cases must be 100% approved in
``review-status.json`` when ``A2AT_CORPUS_REVIEW_GATE`` is set to a truthy value and the status
file exists, and is skipped otherwise — the Java ``-Dcorpus.review.gate=true`` system property
becomes an environment variable (the Python parity carrier of a JVM option).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Final

import pytest

from a2a_t.core.errors.catalog import ErrorCatalog
from tests.corpus.conftest import CORPUS_ROOT
from tests.corpus.models import (
    Family,
    LiveCase,
    LoadedCorpus,
    NegotiationApi,
    NegotiationCase,
    PromptSource,
    ScenarioCase,
)

#: Full strictness (design Q6/§8.4): every one of the content-layer error codes must be covered by
#: at least one failure case of the corpus; a missing code fails the build.
REQUIRE_FULL_ERROR_CODE_COVERAGE: Final[bool] = True

#: Full strictness (design Q6/§8.4): every code the validate family can fail with must be covered
#: by at least one validate-family failure case (the VAL-MAP/VAL-RULE/VAL-RETRY batches). The
#: unknown-code fallback of the LLM response parsing (``negotiation.rule_violation`` /
#: ``content.rule_violation``) cannot be expressed through the public exception surface and stays
#: a hand-written-suite concern.
REQUIRE_FULL_VALIDATE_CODE_COVERAGE: Final[bool] = True

#: The content-layer error codes of the production exception surface (ErrorCatalog codes).
CONTENT_LAYER_ERROR_CODES: Final[tuple[str, ...]] = (
    ErrorCatalog.TEMPLATE_NOT_FOUND.value,
    ErrorCatalog.NEGOTIATION_INVALID_INPUT.value,
    ErrorCatalog.NEGOTIATION_FIELD_MISSING.value,
    ErrorCatalog.NEGOTIATION_CONTENT_EXTRACT_FAILED.value,
    ErrorCatalog.NEGOTIATION_CONCLUSION_MISMATCH.value,
    ErrorCatalog.NEGOTIATION_CONTENT_INVALID.value,
    ErrorCatalog.NEGOTIATION_RULE_VIOLATION.value,
    ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED.value,
    ErrorCatalog.LLM_INVOCATION_FAILED.value,
    ErrorCatalog.LLM_RESPONSE_INVALID.value,
)

#: The codes the validate family fails with: the pipeline surfaces these directly (there is no code
#: mapping layer since the ErrorCatalog migration), so a validate-family failure case must expect
#: each of them.
VALIDATE_FAMILY_ERROR_CODES: Final[tuple[str, ...]] = (
    ErrorCatalog.TEMPLATE_NOT_FOUND.value,
    ErrorCatalog.NEGOTIATION_INVALID_INPUT.value,
    ErrorCatalog.NEGOTIATION_RULE_VIOLATION.value,
    ErrorCatalog.NEGOTIATION_SEMANTIC_REJECTED.value,
    ErrorCatalog.LLM_INVOCATION_FAILED.value,
    ErrorCatalog.LLM_RESPONSE_INVALID.value,
)

#: Corpus counts the INDEX.md statistics table documents (the D25 corpus index), pinned so an
#: accidentally partial corpus copy fails here instead of silently shrinking every suite.
DOCUMENTED_CASE_RECORDS: Final[int] = 154
DOCUMENTED_SCENARIO_RECORDS: Final[int] = 20
DOCUMENTED_SCENARIO_STEPS: Final[int] = 93
DOCUMENTED_LANGUAGE_UNITS: Final[int] = 234
DOCUMENTED_LIVE_RECORDS: Final[int] = 7
DOCUMENTED_PRIORITY_DISTRIBUTION: Final[dict[str, int]] = {"P0": 79, "P1": 63, "P2": 12}

#: Environment variable opting into the Q19 business-review gate (Java ``-Dcorpus.review.gate``).
REVIEW_GATE_ENV: Final[str] = "A2AT_CORPUS_REVIEW_GATE"

#: The single review status that counts as approved (``tools/corpus_review.py``: 通过 / 有疑问 / 否决).
APPROVED_STATUS: Final[str] = "通过"

#: Location of the review status file relative to the repository root.
_REVIEW_STATUS_RELATIVE: Final[Path] = Path("docs-local") / "review" / "review-status.json"


# ------------------------------------------------------------------ ids and expectation blocks


def test_ids_are_globally_unique_across_cases_and_scenarios(loaded_corpus: LoadedCorpus) -> None:
    """The id is the primary key of the whole corpus, and one base id belongs to exactly one file.

    Every expanded id of the cases, the scenario steps, the scenarios and the live records is
    globally unique, and all language expansions of one base id come from the same corpus file with
    a distinct language each — the loader already fails fast on duplicates at parse time; this
    re-asserts the invariant on the loaded corpus.
    """
    expanded_ids: list[str] = []
    all_case_records = _all_case_records(loaded_corpus)
    for test_case in all_case_records:
        expanded_ids.append(test_case.id)
    for scenario in loaded_corpus.scenarios:
        expanded_ids.append(scenario.id)
    for live_case in loaded_corpus.live_cases:
        expanded_ids.append(live_case.id)
    assert len(expanded_ids) == len(set(expanded_ids)), "the expanded ids must be globally unique"

    # Scenario step cases are derived records of a scenario (their base id is the scenario id), so
    # they are covered by the scenario grouping, not the case grouping.
    case_groups = _group_by_base_id([record for record in all_case_records if "#step-" not in record.id])
    for group in case_groups.values():
        _assert_one_file_one_language_per_base_id(list(group))
    scenario_groups = _group_by_base_id_scenarios(loaded_corpus.scenarios)
    for group in scenario_groups.values():
        _assert_one_file_one_language_per_base_id_scenario(list(group))
    live_groups = _group_by_base_id_live(loaded_corpus.live_cases)
    for group in live_groups.values():
        _assert_one_file_one_language_per_base_id_live(list(group))


def test_expectation_blocks_are_complete(loaded_corpus: LoadedCorpus) -> None:
    """A success expectation carries no failure fields; a failure expectation names its failure."""
    for test_case in _all_case_records(loaded_corpus):
        expect = test_case.expect
        if expect.success:
            assert expect.exception is None and expect.code is None, (
                f"{test_case.error_prefix()}: a success expectation must not carry an exception or an error code"
            )
        else:
            assert expect.exception is not None or expect.code is not None, (
                f"{test_case.error_prefix()}: a failure expectation must name the expected "
                "exception or the expected error code"
            )


def test_every_expected_error_code_is_a_known_negotiation_code(loaded_corpus: LoadedCorpus) -> None:
    """Every expected code is one of the ten content-layer error codes."""
    for test_case in _all_case_records(loaded_corpus):
        code = test_case.expect.code
        if code is not None:
            assert code in CONTENT_LAYER_ERROR_CODES, (
                f"{test_case.error_prefix()}: the expected code '{code}' is not one of the content-layer error codes"
            )


# ------------------------------------------------------------------ error-code coverage


def test_every_negotiation_error_code_is_covered_by_a_failure_case(loaded_corpus: LoadedCorpus) -> None:
    """All ten content-layer error codes are covered by at least one failure case (full strictness)."""
    covered = {
        test_case.expect.code
        for test_case in _all_case_records(loaded_corpus)
        if not test_case.expect.success and test_case.expect.code is not None
    }
    if REQUIRE_FULL_ERROR_CODE_COVERAGE:
        missing = [code for code in CONTENT_LAYER_ERROR_CODES if code not in covered]
        assert not missing, (
            "no failure case of the corpus expects the error code(s) "
            f"{', '.join(missing)} yet (full error-code coverage is required)"
        )


def test_every_validate_family_code_is_covered_by_a_validate_failure_case(
    loaded_corpus: LoadedCorpus,
) -> None:
    """All six validate-family codes are covered by a validate-family failure case (full strictness)."""
    covered = {
        test_case.expect.code
        for test_case in _all_case_records(loaded_corpus)
        if test_case.api.family is Family.VALIDATE
        and not test_case.expect.success
        and test_case.expect.code is not None
    }
    if REQUIRE_FULL_VALIDATE_CODE_COVERAGE:
        missing = [code for code in VALIDATE_FAMILY_ERROR_CODES if code not in covered]
        assert not missing, (
            "no validate-family failure case expects the error code(s) "
            f"{', '.join(missing)} yet (full validate-family code coverage is required)"
        )


# ------------------------------------------------------------------ closed-loop task APIs (Q20-Q23)


def test_every_task_api_is_exercised_by_scenario_steps_only(loaded_corpus: LoadedCorpus) -> None:
    """Every closed-loop task API runs as a scenario step, never as a standalone case record.

    The closed loop (task prompt generation, peer validation, then negotiation) only has a business
    meaning inside a scenario, so a standalone task case would bypass the 缺参 → 协商 → 补参 → 提取
    causal chain the corpus exists to model (Q21 full strictness).
    """
    task_apis: list[NegotiationApi] = []
    for scenario in loaded_corpus.scenarios:
        for step in scenario.steps:
            if step.case_data.api.family is Family.TASK:
                task_apis.append(step.case_data.api)
    for api in NegotiationApi:
        if api.family is not Family.TASK:
            continue
        assert api in task_apis, f"no scenario step exercises the closed-loop task API '{api.json_name}' yet"
    for test_case in loaded_corpus.cases:
        assert test_case.api.family is not Family.TASK, (
            f"{test_case.error_prefix()}: a task API must not appear as a standalone case record, "
            "only as a scenario step (the closed-loop causal chain lives in scenarios)"
        )


def test_task_steps_are_bound_to_their_closed_loop_roles(loaded_corpus: LoadedCorpus) -> None:
    """The workbench (A) generates the task prompt and the OMC (B) validates the peer message.

    The dual-session scenario numbers its roles A1/A2/B1/B2 — the letter prefix decides (Q21/Q23).
    """
    for scenario in loaded_corpus.scenarios:
        for step in scenario.steps:
            api = step.case_data.api
            if api.family is not Family.TASK:
                continue
            role = step.role or ""
            expected_side = "A" if api.json_name.startswith("generate") else "B"
            assert role.startswith(expected_side), (
                f"{scenario.id} step {step.step} ({api.json_name}) must be acted by the "
                f"{'workbench (A)' if expected_side == 'A' else 'OMC (B)'} side, but the role is "
                f"'{step.role}'"
            )


# ------------------------------------------------------------------ drift probes (Q22)


def test_drift_probes_are_compliant_peer_text_expecting_success(loaded_corpus: LoadedCorpus) -> None:
    """A drift probe is a compliant-but-reworded validate case with inline text expecting success.

    A drift probe that fails would reward exact-wording coupling instead of semantic compliance,
    and a golden fixture would misuse the regression equality anchor as the validate mainline
    input (Q22 full strictness).
    """
    seen = False
    for test_case in loaded_corpus.cases:
        if not test_case.base_id.startswith("VAL-DRIFT-"):
            continue
        seen = True
        assert test_case.api.family is Family.VALIDATE, (
            f"{test_case.error_prefix()}: a drift probe must be a validate-family case"
        )
        assert test_case.expect.success, (
            f"{test_case.error_prefix()}: a compliant-but-reworded peer message must pass validation"
        )
        assert isinstance(test_case.prompt, PromptSource.Text), (
            f"{test_case.error_prefix()}: a drift probe must carry the peer message as inline text "
            "(prompt.text), not a golden fixture or a fromStep reference"
        )
    assert seen, "the corpus must carry at least one VAL-DRIFT drift probe"


# ------------------------------------------------------------------ live family (live design §2.2)


def test_live_records_stay_in_the_phase1_scope(loaded_corpus: LoadedCorpus) -> None:
    """The live records stay in their phase-1 scope: the ``LIVE-`` prefix, zh-CN, the two task APIs."""
    assert loaded_corpus.live_cases, "the corpus must carry at least one live record"
    for live_case in loaded_corpus.live_cases:
        assert live_case.base_id.startswith("LIVE-"), (
            f"{live_case.error_prefix()}: the live family ids carry the 'LIVE-' prefix"
        )
        assert live_case.language == "zh-CN", f"{live_case.error_prefix()}: live phase 1 covers zh-CN only (Q6)"
        assert live_case.api in (
            NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT,
            NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING,
        ), (
            f"{live_case.error_prefix()}: live phase 1 covers the two task APIs (Q5) but the record"
            f" declares {live_case.api.json_name}"
        )


def test_live_expectation_blocks_are_complete(loaded_corpus: LoadedCorpus) -> None:
    """The loose live block still pins something checkable: non-null values, non-blank names, bounds."""
    for live_case in loaded_corpus.live_cases:
        expect = live_case.live_expect
        for key, value in expect.params_contains.items():
            assert value is not None, f"{live_case.error_prefix()}: paramsContains.{key} must pin a non-null value"
        for slot in expect.params_absent:
            assert slot.strip(), f"{live_case.error_prefix()}: paramsAbsent entries must not be blank"
        for fragment in expect.prompt_text_contains:
            assert fragment.strip(), f"{live_case.error_prefix()}: promptTextContains fragments must not be blank"
        if expect.max_llm_calls is not None:
            assert expect.max_llm_calls > 0, f"{live_case.error_prefix()}: maxLlmCalls must be a positive upper bound"


# ------------------------------------------------------------------ bilingual parity (§7, four definitions)


def test_happy_cases_declare_both_languages(loaded_corpus: LoadedCorpus) -> None:
    """Parity ①: every happy case of the case files declares both languages, so both run and pass."""
    for group in _group_by_base_id(loaded_corpus.cases).values():
        records = list(group)
        if not records[0].expect.success:
            continue
        languages = {record.language for record in records}
        assert languages == {"zh-CN", "en-US"}, (
            f"{records[0].error_prefix()}: a happy case must run in both languages (the suite executes every expansion)"
        )


def test_failure_cases_share_the_identical_expectation_across_languages(
    loaded_corpus: LoadedCorpus,
) -> None:
    """Parity ②: all language expansions of one record share the identical expectation block."""
    for group in _group_by_base_id(loaded_corpus.cases).values():
        records = list(group)
        reference = records[0].expect
        for expansion in records:
            assert expansion.expect == reference, (
                f"{expansion.error_prefix()}: every language expansion of one record must carry "
                "the identical expectation block, so a failure case fails with the same error code"
                " in both languages"
            )


def test_golden_fixture_counts_match_across_languages() -> None:
    """Parity ③: the golden fixture directories of the two languages carry the same number of fixtures."""
    assert _count_golden_fixtures("zh-CN") == _count_golden_fixtures("en-US"), (
        "the golden fixtures must exist in equal numbers for zh-CN and en-US"
    )


def test_records_expand_exactly_once_per_declared_language(loaded_corpus: LoadedCorpus) -> None:
    """Parity ④: every record expands exactly once per declared language — none dropped or doubled."""
    for group in _group_by_base_id(loaded_corpus.cases).values():
        records = list(group)
        languages = [record.language for record in records]
        assert len(set(languages)) == len(languages), (
            f"{records[0].error_prefix()}: every declared language must expand into exactly one case"
        )
    for group in _group_by_base_id_scenarios(loaded_corpus.scenarios).values():
        records = list(group)
        languages = [scenario.language for scenario in records]
        assert len(set(languages)) == len(languages), (
            f"{records[0].id}: every declared language must expand into exactly one scenario ({', '.join(languages)})"
        )


# ------------------------------------------------------------------ corpus counts (INDEX.md statistics)


def test_the_corpus_carries_the_documented_counts(loaded_corpus: LoadedCorpus) -> None:
    """The loaded corpus carries exactly the record counts the INDEX.md statistics document.

    A partial corpus copy (a lost family file, a dropped language) silently narrows every suite,
    so the counts are pinned here the way the family suite guards pin their per-family counts.
    """
    case_base_ids = {test_case.base_id for test_case in loaded_corpus.cases}
    assert len(case_base_ids) == DOCUMENTED_CASE_RECORDS, "the case records INDEX.md documents"
    scenario_base_ids = {scenario.base_id for scenario in loaded_corpus.scenarios}
    assert len(scenario_base_ids) == DOCUMENTED_SCENARIO_RECORDS, "the scenario records INDEX.md documents"
    scenario_steps = {(scenario.base_id, step.step) for scenario in loaded_corpus.scenarios for step in scenario.steps}
    assert len(scenario_steps) == DOCUMENTED_SCENARIO_STEPS, "the scenario steps INDEX.md documents"
    assert len(loaded_corpus.cases) + len(loaded_corpus.scenarios) == DOCUMENTED_LANGUAGE_UNITS, (
        "the language-expanded units INDEX.md documents (offline cases plus scenarios)"
    )
    assert len(loaded_corpus.live_cases) == DOCUMENTED_LIVE_RECORDS, "the live records INDEX.md documents"
    priority_counts: dict[str, int] = {}
    for base_id in case_base_ids:
        priority = next(test_case.priority for test_case in loaded_corpus.cases if test_case.base_id == base_id)
        if priority is not None:
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
    assert priority_counts == DOCUMENTED_PRIORITY_DISTRIBUTION, (
        "the priority distribution of the case records INDEX.md documents"
    )


# ------------------------------------------------------------------ business review soft gate (Q19, §8.7)


def test_p0_cases_are_fully_approved_by_the_business_review(loaded_corpus: LoadedCorpus) -> None:
    """When enabled, every P0 case must carry the review status 通过 in ``review-status.json``.

    The gate is strictly opt-in so it never blocks daily development: it runs only when
    ``A2AT_CORPUS_REVIEW_GATE`` is truthy AND the status file produced by
    ``tools/corpus_review.py collect`` exists; otherwise the test is skipped. Scenario records
    carry no priority and are out of the P0 gate's scope.
    """
    if os.environ.get(REVIEW_GATE_ENV, "").strip().lower() not in ("1", "true", "yes"):
        pytest.skip(f"the business review gate is opt-in (enable with {REVIEW_GATE_ENV}=true)")
    status_file = _find_review_status_file()
    if status_file is None:
        pytest.skip("docs-local/review/review-status.json not found (run tools/corpus_review.py collect to produce it)")
    reviewed_cases: dict[str, dict[str, object]] = json.loads(status_file.read_text(encoding="utf-8")).get("cases", {})
    not_approved: list[str] = []
    for base_id, records in _group_by_base_id(loaded_corpus.cases).items():
        if records[0].priority != "P0":
            continue
        status_entry = reviewed_cases.get(base_id, {})
        status = status_entry.get("status", "") if isinstance(status_entry, dict) else ""
        if status != APPROVED_STATUS:
            shown = status if status else "unreviewed"
            not_approved.append(f"{base_id} (status: '{shown}')")
    assert not not_approved, (
        "the P0 review-approval rate must be 100% before a release, not approved yet: " + ", ".join(not_approved)
    )


# ------------------------------------------------------------------ helpers


def _all_case_records(corpus: LoadedCorpus) -> list[NegotiationCase]:
    """All case records of the corpus: the case-file records plus the scenario step cases."""
    records = list(corpus.cases)
    for scenario in corpus.scenarios:
        for step in scenario.steps:
            records.append(step.case_data)
    return records


def _group_by_base_id(cases: list[NegotiationCase]) -> dict[str, list[NegotiationCase]]:
    """Case-file records grouped by base id (the loader puts scenario step cases into scenarios)."""
    groups: dict[str, list[NegotiationCase]] = {}
    for test_case in cases:
        groups.setdefault(test_case.base_id, []).append(test_case)
    assert groups, "the corpus must carry at least one case-file record"
    return groups


def _group_by_base_id_scenarios(
    scenarios: list[ScenarioCase],
) -> dict[str, list[ScenarioCase]]:
    """Scenario records grouped by base id."""
    groups: dict[str, list[ScenarioCase]] = {}
    for scenario in scenarios:
        groups.setdefault(scenario.base_id, []).append(scenario)
    return groups


def _group_by_base_id_live(live_cases: list[LiveCase]) -> dict[str, list[LiveCase]]:
    """Live records grouped by base id."""
    groups: dict[str, list[LiveCase]] = {}
    for live_case in live_cases:
        groups.setdefault(live_case.base_id, []).append(live_case)
    return groups


def _assert_one_file_one_language_per_base_id(group: list[NegotiationCase]) -> None:
    """All language expansions of one base id come from one file with a distinct language each."""
    source_file = group[0].source_file
    languages: list[str] = []
    for expansion in group:
        assert expansion.source_file == source_file, (
            f"the base id {expansion.base_id} must belong to exactly one corpus file"
        )
        assert expansion.language not in languages, f"duplicate language expansion {expansion.id}"
        languages.append(expansion.language)


def _assert_one_file_one_language_per_base_id_scenario(group: list[ScenarioCase]) -> None:
    """All language expansions of one scenario base id come from one file with a distinct language."""
    source_file = group[0].source_file
    languages: list[str] = []
    for expansion in group:
        assert expansion.source_file == source_file, (
            f"the base id {expansion.base_id} must belong to exactly one corpus file"
        )
        assert expansion.language not in languages, f"duplicate language expansion {expansion.id}"
        languages.append(expansion.language)


def _assert_one_file_one_language_per_base_id_live(group: list[LiveCase]) -> None:
    """All language expansions of one live base id come from one file with a distinct language."""
    source_file = group[0].source_file
    languages: list[str] = []
    for expansion in group:
        assert expansion.source_file == source_file, (
            f"the base id {expansion.base_id} must belong to exactly one corpus file"
        )
        assert expansion.language not in languages, f"duplicate language expansion {expansion.id}"
        languages.append(expansion.language)


def _count_golden_fixtures(language: str) -> int:
    """Count the committed golden fixtures of one language (``golden/<language>/*.md``)."""
    directory = CORPUS_ROOT / "golden" / language
    if not directory.is_dir():
        return 0
    return sum(1 for fixture in directory.iterdir() if fixture.name.endswith(".md"))


def _find_review_status_file() -> Path | None:
    """Locate ``docs-local/review/review-status.json`` by walking up from this test module."""
    for directory in Path(__file__).resolve().parents:
        candidate = directory / _REVIEW_STATUS_RELATIVE
        if candidate.is_file():
            return candidate
    return None
