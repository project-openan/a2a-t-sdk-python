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

"""Data-driven suite of the E2E scenario corpus: every scenario becomes one test.

Port of the Java ``ScenarioCorpusSuiteTest`` (through the scenario-test half of
``CorpusSuites``): every record under ``tests/resources/negotiation-cases/scenarios/`` — the
five families SC-INFO, SC-TGT, SC-FSB, SC-ERR and SC-EXH, the bilingual ones expanded once per
declared language — becomes one parametrized test executed by the
:class:`~tests.corpus.engine.ScenarioEngine`: every step is a full corpus case run by the case
engine against the production wiring with its own scripted LLM behavior, plus the scenario-level
``prompt.fromStep`` resolution, the ``expect.paramsFromStep`` causal chain, the fail-fast step
ordering, the per-role LLM accounting and the flow-level expectation (terminal condition, rounds
used, distinct messages, missing parameters filled).

The scenario engine owns the step and flow assertions, so the suite test is one engine run plus
a cross-check of the returned :class:`~tests.corpus.engine.ScenarioRunResult` against the
scenario record: every step left its footprint (a prompt text for the generation steps, filled
parameter data for the validation steps), the per-role call accounting covers exactly the acting
roles and every scripted call, and the round bookkeeping mirrors the largest round and budget
any step's context carried. The collection guards pin that the parametrize sweeps the whole
directory — every corpus file contributes — and the two red paths prove the flow expectation
and the fail-fast step ordering actually assert.

The corpus ids are the pytest ids, so a subset runs through ``-k`` the way the Java suites use
``-Dcase.filter``: ``-k SC-INFO`` runs the information flows in both languages.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Final

import pytest

from tests.corpus.conftest import (
    CORPUS_ROOT,
    corpus_scenario_parametrize,
    expanded_scenarios,
)
from tests.corpus.engine import ScenarioEngine, ScenarioRunResult
from tests.corpus.models import LoadedCorpus, NegotiationApi, ScenarioCase

#: APIs whose step produces a message whose prompt text later steps can reference.
_MESSAGE_STEP_APIS: Final[frozenset[NegotiationApi]] = frozenset(
    {
        NegotiationApi.GENERATE_PROPOSE_FROM_TEXT,
        NegotiationApi.GENERATE_ACCEPT_FROM_TEXT,
        NegotiationApi.GENERATE_REJECT_FROM_TEXT,
        NegotiationApi.GENERATE_ABORT_FROM_TEXT,
        NegotiationApi.GENERATE_PROPOSE_FROM_DATA,
        NegotiationApi.GENERATE_ACCEPT_FROM_DATA,
        NegotiationApi.GENERATE_REJECT_FROM_DATA,
        NegotiationApi.GENERATE_ABORT_FROM_DATA,
        NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT,
        NegotiationApi.GENERATE_TASK_PROMPT_FROM_DATA_WITH_SCHEMA,
    }
)

#: APIs whose step produces filled parameter data (the validate legs of the closed loop).
_FILLED_STEP_APIS: Final[frozenset[NegotiationApi]] = frozenset(
    {
        NegotiationApi.VALIDATE_PROPOSE_PROMPT_AND_DATA_FILLING,
        NegotiationApi.VALIDATE_ACCEPT_PROMPT_AND_DATA_FILLING,
        NegotiationApi.VALIDATE_REJECT_PROMPT_AND_DATA_FILLING,
        NegotiationApi.VALIDATE_ABORT_PROMPT_AND_DATA_FILLING,
        NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING,
    }
)


@pytest.fixture(scope="module")
def scenario_engine() -> ScenarioEngine:
    """One scenario engine for the whole family run — the single engine of the Java suite."""
    return ScenarioEngine()


@corpus_scenario_parametrize("scenarios")
def test_scenario_corpus_case(scenario_engine: ScenarioEngine, scenario: ScenarioCase) -> None:
    """Run one expanded scenario: every step is a full case run, then the flow expectation."""
    result = scenario_engine.run_scenario(scenario)
    _assert_step_accounting(scenario, result)
    _assert_role_accounting(scenario, result)
    _assert_round_accounting(scenario, result)


def _assert_step_accounting(scenario: ScenarioCase, result: ScenarioRunResult) -> None:
    """Every succeeding step left its footprint: generation steps a prompt text, validation
    steps filled data. A step whose expectation is a failure (the SC-ERR error flows and the
    SC-EXH round-exhaustion boundary) leaves no footprint by design — the engine captured its
    exception instead, and the step expectation block asserted that failure."""
    for step in scenario.steps:
        api = step.case_data.api
        if not step.case_data.expect.success:
            continue
        if api in _MESSAGE_STEP_APIS:
            assert step.step in result.step_prompt_texts, (
                f"{scenario.id}: step {step.step} ({api.json_name}) produced no prompt text"
            )
        if api in _FILLED_STEP_APIS:
            assert step.step in result.step_filled_params, (
                f"{scenario.id}: step {step.step} ({api.json_name}) produced no filled parameter data"
            )
            assert step.step in result.step_missing_params, (
                f"{scenario.id}: step {step.step} carries no missing-parameter set"
            )


def _assert_role_accounting(scenario: ScenarioCase, result: ScenarioRunResult) -> None:
    """The per-role LLM accounting covers exactly the acting roles and every scripted call."""
    acting = {step.role if step.role is not None else "(no role)" for step in scenario.steps}
    assert set(result.role_call_counts) == acting, f"{scenario.id}: the role accounting misses an acting role"
    if all(step.case_data.expect.llm_calls is not None for step in scenario.steps):
        expected_total = sum(step.case_data.expect.llm_calls or 0 for step in scenario.steps)
        assert sum(result.role_call_counts.values()) == expected_total, (
            f"{scenario.id}: the role accounting lost or duplicated an LLM call"
        )


def _assert_round_accounting(scenario: ScenarioCase, result: ScenarioRunResult) -> None:
    """The round bookkeeping mirrors the largest round and budget any step's context carried."""
    rounds = [step.case_data.context.round for step in scenario.steps if step.case_data.context is not None]
    limits = [step.case_data.context.max_rounds for step in scenario.steps if step.case_data.context is not None]
    assert result.max_round == max(rounds, default=0), f"{scenario.id}: the round bookkeeping drifted"
    assert result.max_rounds_limit == max(limits, default=0), f"{scenario.id}: the round budget drifted"


# ------------------------------------------------------------------ collection guards


def test_the_suite_collects_the_whole_scenario_family(loaded_corpus: LoadedCorpus) -> None:
    """The parametrize sweeps every scenario: 20 records, 93 steps, 22 language expansions.

    Every JSON file the directory ships contributes at least one expanded scenario, and the
    collected records are exactly the scenarios of the loaded corpus in load order — an empty or
    half-collected family directory fails here instead of contributing no test. The counts are
    the scenarios rows of the INDEX.md family distribution table (the loader's INDEX parity test
    pins the full distribution; repeated here so the suite's own collection is what fails).
    """
    collected = expanded_scenarios("scenarios")
    expected = [scenario for scenario in loaded_corpus.scenarios if scenario.source_file.startswith("scenarios/")]
    assert collected == expected
    assert len(collected) == 22, "the language-expanded scenarios INDEX.md documents"
    assert len({scenario.base_id for scenario in collected}) == 20, "the scenario records"
    assert sum(len(scenario.steps) for scenario in collected) == 102, "the expanded scenario steps"
    per_base = {scenario.base_id: scenario for scenario in collected}
    assert sum(len(scenario.steps) for scenario in per_base.values()) == 93, "the scenario steps INDEX.md documents"
    shipped_files = sorted(path.name for path in (CORPUS_ROOT / "scenarios").glob("*.json"))
    contributing_files = sorted({Path(scenario.source_file).name for scenario in collected})
    assert contributing_files == shipped_files, "every corpus file of the directory contributes"
    assert all(scenario.expect_flow is not None for scenario in collected), (
        "every scenario carries a flow-level expectation"
    )


# ------------------------------------------------------------------ the driver is not a rubber stamp


def test_a_flipped_flow_expectation_fails_the_run(loaded_corpus: LoadedCorpus) -> None:
    """One extra expected round fails the run with the scenario id and the flow JSON path."""
    shipped = next(scenario for scenario in loaded_corpus.scenarios if scenario.id == "SC-INFO-01/zh-CN")
    flow = shipped.expect_flow
    assert flow is not None and flow.rounds_used is not None, "the seed scenario carries a rounds expectation"
    mutated = dataclasses.replace(shipped, expect_flow=dataclasses.replace(flow, rounds_used=flow.rounds_used + 1))
    with pytest.raises(AssertionError, match=r"expectFlow\.roundsUsed"):
        ScenarioEngine().run_scenario(mutated)


def test_a_wrong_step_call_count_fails_fast_before_the_later_steps(loaded_corpus: LoadedCorpus) -> None:
    """Fail-fast: a wrong per-step ``llmCalls`` expectation aborts the scenario at that step."""
    shipped = next(scenario for scenario in loaded_corpus.scenarios if scenario.id == "SC-INFO-01/zh-CN")
    first = shipped.steps[0]
    assert first.case_data.expect.llm_calls is not None, "the first step carries an exact call count"
    wrong = dataclasses.replace(first.case_data.expect, llm_calls=first.case_data.expect.llm_calls + 1)
    mutated_step = dataclasses.replace(first, case_data=dataclasses.replace(first.case_data, expect=wrong))
    mutated = dataclasses.replace(shipped, steps=[mutated_step, *shipped.steps[1:]])
    with pytest.raises(AssertionError, match=r"\$\.expect\.llmCalls"):
        ScenarioEngine().run_scenario(mutated)
