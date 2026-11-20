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

"""Loader tests of the negotiation test corpus.

Port of the Java ``NegotiationCaseLoaderTest`` plus the Python-only strictness and statistics
layers of the port (D21):

* **Java parity** — hand-built corpora in temporary directories, without ``corpus-schema.json``
  so the strict binding layer answers exactly like the Java strict Jackson binding: the
  happy-path expansion (:func:`test_loads_and_expands_a_good_corpus`) and every fail-fast
  violation of the corpus format (:func:`test_fail_fast_violations`), each error carrying the
  (corpus file, record id, JSON path) triple.
* **Two-layer strictness** — with ``corpus-schema.json`` present the schema layer answers
  first; an unknown key, an enum violation or a constraint violation is a
  :class:`~tests.corpus.loader.CorpusLoadException` on either layer
  (:func:`test_unknown_key_fails_on_both_strict_layers`), and the three documented in-memory
  schema repairs stay pinned so the shipped corpus keeps validating.
* **The shipped corpus** — loads clean with exactly the statistics the machine-generated
  ``INDEX.md`` documents (154 case records + 20 scenario records = 234 language-expanded
  units), every suite file contributing its exact share, every ``$ref`` resolved, live records
  never mixing into the offline lists.
* **Shipped-corpus mutations** — temporary copies of the real corpus with one injected defect
  each (:func:`test_shipped_corpus_mutations_fail_fast`), the realistic variant of the
  fail-fast modes a corpus author actually hits.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import pytest

from tests.corpus.conftest import CORPUS_ROOT, SESSION_ID, expanded_cases
from tests.corpus.loader import CorpusLoadException, load
from tests.corpus.models import (
    ContextSpec,
    LlmFailMarker,
    LlmScriptStep,
    LoadedCorpus,
    NegotiationApi,
    NegotiationCase,
    PromptSource,
)

# ------------------------------------------------------------------ shared corpus constants

#: The fixed negotiation session id of the corpus records (the golden session id).
_SESSION_ID = SESSION_ID

#: The two shared reference files of the Java loader test, mirrored verbatim.
_SHARED_RESPONSES = {
    "extract.information.accept.full": '{"conclusion": "Accept", "items": []}',
    "extract.information.propose.full": '{"items": [{"name": "节能区域信息", "value": "松山湖"}]}',
}

_SHARED_SCHEMAS = {"flat": {"type": "object", "properties": {"id": {"type": "string"}}}}


def _write(root: Path, relative: str, document: Any) -> None:
    """Write one corpus file as UTF-8 JSON, creating parent directories."""
    file = root / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_shipped_schema(root: Path) -> None:
    """Copy the shipped corpus format definition next to a hand-built corpus."""
    shutil.copy(CORPUS_ROOT / "corpus-schema.json", root / "corpus-schema.json")


def _assert_load_fails(root: Path, *fragments: str) -> None:
    """Assert that loading the corpus fails, naming every expected message fragment."""
    with pytest.raises(CorpusLoadException) as raised:
        load(root)
    message = str(raised.value)
    for fragment in fragments:
        assert fragment in message, f"fragment {fragment!r} missing from: {message}"


def _case_by_id(corpus: LoadedCorpus, expanded_id: str) -> NegotiationCase:
    """The one expanded case of the loaded corpus carrying the given id."""
    found = [case for case in corpus.cases if case.id == expanded_id]
    assert found, f"the corpus must expand the case {expanded_id}"
    return found[0]


def _mutate_shipped(tmp_path: Path, relative: str, mutate: Callable[[list[dict[str, Any]]], None]) -> Path:
    """Copy the shipped corpus into a temporary root and inject one defect into one record file."""
    root = tmp_path / "negotiation-cases"
    shutil.copytree(CORPUS_ROOT, root)
    file = root / relative
    document = json.loads(file.read_text(encoding="utf-8"))
    mutate(document)
    file.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def _first_success_record(document: list[dict[str, Any]]) -> dict[str, Any]:
    return next(record for record in document if record["expect"]["outcome"] == "success")


def _first_scripted_record(document: list[dict[str, Any]]) -> dict[str, Any]:
    return next(record for record in document if record.get("llm"))


def _typo_next_to_template_uri(document: list[dict[str, Any]]) -> None:
    document[0]["templeteUri"] = "Negotiation-T/information-negotiation/accept-reject/v1"


def _typo_inside_expectation(document: list[dict[str, Any]]) -> None:
    _first_success_record(document)["expect"]["diferential"] = True


def _typo_inside_scenario_step(document: list[dict[str, Any]]) -> None:
    document[0]["steps"][0]["rol"] = "A"


def _offline_key_in_live_record(document: list[dict[str, Any]]) -> None:
    document[0]["llm"] = {"script": ["{}"]}


def _duplicate_a_record_id(document: list[dict[str, Any]]) -> None:
    document.append({**document[0], "id": document[1]["id"]})


def _dangling_script_ref(document: list[dict[str, Any]]) -> None:
    _first_scripted_record(document)["llm"]["script"][0] = {"$ref": "responses/no.such.payload"}


def _unknown_priority(document: list[dict[str, Any]]) -> None:
    document[0]["priority"] = "P9"


def _context_round_zero(document: list[dict[str, Any]]) -> None:
    document[0]["context"]["round"] = 0


# ------------------------------------------------------------------ Java parity: happy path


def _write_good_corpus(root: Path) -> None:
    """Write the good corpus of the Java loader test: one case file, one validate file, one scenario."""
    _write(root, "shared/llm-responses.json", _SHARED_RESPONSES)
    _write(root, "shared/schemas.json", _SHARED_SCHEMAS)
    _write(
        root,
        "from-text/happy.json",
        [
            {
                "id": "FT-HAPPY-01",
                "api": "generateAcceptFromText",
                "languages": ["zh-CN", "en-US"],
                "priority": "P1",
                "tags": ["happy", "accept-reject"],
                "summary": "信息确认后接受，成功",
                "context": {"id": _SESSION_ID, "round": 2, "maxRounds": 5},
                "templateUri": "Negotiation-T/information-negotiation/accept-reject/v1",
                "input": {
                    "text": {"zh-CN": "我确认第一阶段的信息。", "en-US": "I confirm the first-stage information."},
                    "data": {"items": [], "relationship": None},
                },
                "llm": {
                    "maxAttempts": 3,
                    "script": [{"$fail": "non-json"}, {"$ref": "responses/extract.information.accept.full"}],
                },
                "expect": {
                    "outcome": "success",
                    "llmCalls": 2,
                    "promptTextEqualsGolden": "information_accept",
                    "metadata": {
                        "templateUriEcho": "Negotiation-T/information-negotiation/accept-reject/v1",
                        "contextEcho": True,
                    },
                    "contracts": ["conclusionLiteralPresent"],
                    "differential": True,
                },
            }
        ],
    )
    _write(
        root,
        "validate/happy.json",
        [
            {
                "id": "VAL-HAPPY-01",
                "api": "validateProposePromptAndDataFilling",
                "languages": ["zh-CN"],
                "context": {"id": _SESSION_ID, "round": 1, "maxRounds": 5},
                "templateUri": "Negotiation-T/information-negotiation/propose/v1",
                "prompt": {"golden": "information_propose"},
                "schema": {"$ref": "schemas/flat"},
                "llm": {"script": ['{"verdict": true}']},
                "expect": {
                    "outcome": "success",
                    "llmCalls": 1,
                    "params": {"id": _SESSION_ID, "round": 1, "maxRounds": 5},
                },
            }
        ],
    )
    _write(
        root,
        "scenarios/flows.json",
        [
            {
                "id": "SC-INFO-01",
                "summary": "提议后语义拒绝",
                "languages": ["zh-CN"],
                "roles": ["A", "B"],
                "steps": [
                    {
                        "step": 1,
                        "role": "A",
                        "api": "generateProposeFromText",
                        "context": {"id": _SESSION_ID, "round": 1, "maxRounds": 5},
                        "templateUri": "Negotiation-T/information-negotiation/propose/v1",
                        "input": {"text": {"zh-CN": "请提供节能区域信息"}},
                        "llm": {"script": [{"$ref": "responses/extract.information.propose.full"}]},
                        "expect": {"outcome": "success", "llmCalls": 1},
                    },
                    {
                        "step": 2,
                        "role": "B",
                        "api": "validateProposePromptAndDataFilling",
                        "prompt": {"fromStep": 1},
                        "context": {"id": _SESSION_ID, "round": 1, "maxRounds": 5},
                        "templateUri": "Negotiation-T/information-negotiation/propose/v1",
                        "schema": {"$ref": "schemas/flat"},
                        "llm": {"script": [{"$fail": "llm-error"}]},
                        "expect": {"outcome": "failure", "code": "negotiation.semantic_rejected", "llmCalls": 1},
                    },
                ],
                "expectFlow": {"terminalCondition": "reject", "roundsUsed": 1, "distinctMessages": True},
            }
        ],
    )


def _minimal_case(record_id: str) -> list[dict[str, Any]]:
    """The minimal well-formed case record, parameterized by its id only."""
    return [
        {
            "id": record_id,
            "api": "generateAcceptFromText",
            "languages": ["zh-CN"],
            "context": {"id": _SESSION_ID, "round": 2, "maxRounds": 5},
            "templateUri": "Negotiation-T/information-negotiation/accept-reject/v1",
            "input": {"text": {"zh-CN": "我确认"}},
            "llm": {"script": ["{}"]},
            "expect": {"outcome": "success"},
        }
    ]


def test_loads_and_expands_a_good_corpus(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    _write_good_corpus(root)

    corpus = load(root)

    assert len(corpus.cases) == 3, "FT-HAPPY-01 expands to two languages, VAL-HAPPY-01 to one"
    assert len(corpus.scenarios) == 1

    accept_zh = _case_by_id(corpus, "FT-HAPPY-01/zh-CN")
    accept_en = _case_by_id(corpus, "FT-HAPPY-01/en-US")
    validate_zh = _case_by_id(corpus, "VAL-HAPPY-01/zh-CN")

    assert accept_zh.base_id == "FT-HAPPY-01"
    assert accept_zh.source_file == "from-text/happy.json"
    assert accept_zh.api is NegotiationApi.GENERATE_ACCEPT_FROM_TEXT
    assert accept_zh.language == "zh-CN"
    assert accept_zh.priority == "P1"
    assert accept_zh.input_text == "我确认第一阶段的信息。"
    assert accept_en.input_text == "I confirm the first-stage information."
    assert accept_zh.context == ContextSpec(id=_SESSION_ID, round=2, max_rounds=5)
    assert isinstance(accept_zh.input_data, dict), "the differential typed input data travels as a JSON object"
    assert accept_zh.expect.success
    assert accept_zh.expect.differential
    assert accept_zh.expect.llm_calls == 2
    assert accept_zh.expect.prompt_text_equals_golden == "information_accept"
    assert accept_zh.expect.metadata is not None
    assert accept_zh.expect.metadata.template_uri_echo == "Negotiation-T/information-negotiation/accept-reject/v1"
    assert accept_zh.expect.metadata.context_echo is True

    first_step = accept_zh.llm.steps[0]
    assert isinstance(first_step, LlmScriptStep.Fail)
    assert first_step.marker is LlmFailMarker.NON_JSON
    payload_step = accept_zh.llm.steps[1]
    assert isinstance(payload_step, LlmScriptStep.Payload)
    assert payload_step.json == '{"conclusion": "Accept", "items": []}'
    assert accept_zh.llm.max_attempts == 3

    assert validate_zh.api is NegotiationApi.VALIDATE_PROPOSE_PROMPT_AND_DATA_FILLING
    assert isinstance(validate_zh.prompt, PromptSource.Golden)
    assert validate_zh.prompt.golden == "information_propose"
    assert validate_zh.schema == corpus.shared_schemas["flat"]
    assert validate_zh.expect.params == {"id": _SESSION_ID, "round": 1, "maxRounds": 5}

    scenario = corpus.scenarios[0]
    assert scenario.id == "SC-INFO-01/zh-CN"
    assert scenario.base_id == "SC-INFO-01"
    assert len(scenario.steps) == 2
    assert scenario.steps[0].step == 1
    assert scenario.steps[0].role == "A"
    assert scenario.steps[0].case_data.id == "SC-INFO-01/zh-CN#step-1"
    assert scenario.steps[0].case_data.api is NegotiationApi.GENERATE_PROPOSE_FROM_TEXT
    step_two_prompt = scenario.steps[1].case_data.prompt
    assert isinstance(step_two_prompt, PromptSource.FromStep)
    assert step_two_prompt.step == 1
    assert scenario.expect_flow is not None
    assert scenario.expect_flow.terminal_condition == "reject"
    assert scenario.expect_flow.rounds_used == 1
    assert scenario.expect_flow.distinct_messages is True

    failed_step = scenario.steps[1].case_data
    assert not failed_step.expect.success
    assert failed_step.expect.code == "negotiation.semantic_rejected"
    assert failed_step.expect.llm_calls == 1
    llm_error_step = failed_step.llm.steps[0]
    assert isinstance(llm_error_step, LlmScriptStep.Fail)
    assert llm_error_step.marker is LlmFailMarker.LLM_ERROR


def test_skips_the_schema_file_and_loads_an_empty_corpus(tmp_path: Path) -> None:
    _write(tmp_path, "corpus-schema.json", {"formatVersion": 1})
    _write(tmp_path, "shared/llm-responses.json", _SHARED_RESPONSES)

    corpus = load(tmp_path)

    assert corpus.cases == []
    assert corpus.scenarios == []
    assert len(corpus.shared_responses) == 2, "the shared files load even without any case file"


def test_rejects_a_missing_corpus_root(tmp_path: Path) -> None:
    with pytest.raises(CorpusLoadException, match="not an existing directory"):
        load(tmp_path / "nope")


# ------------------------------------------------------------------ Java parity: fail-fast violations

#: A case record whose expect block carries the typo of the Java unknown-key probe.
_EXPECT_TYPO_CASE = [
    {
        "id": "FT-BAD-01",
        "api": "generateAcceptFromText",
        "languages": ["zh-CN"],
        "expect": {"outcome": "success", "expection": "Oops"},
    }
]


def _case(api: str, **overrides: Any) -> dict[str, Any]:
    """A minimal case record with per-test overrides merged in."""
    record: dict[str, Any] = {"id": "FT-BAD-01", "api": api, "languages": ["zh-CN"]}
    record.update(overrides)
    return record


#: Every fail-fast violation of the corpus format, each asserting the message fragments of its
#: Java counterpart: the corpus file, the offending record id and the defect itself.
_FAIL_FAST_VIOLATIONS = [
    pytest.param(
        {"from-text/bad.json": _EXPECT_TYPO_CASE},
        ("from-text/bad.json", "FT-BAD-01", "expection", "expect"),
        id="unknown-key",
    ),
    pytest.param(
        {
            "from-text/bad.json": [
                _case(
                    "generateAcceptFromText",
                    llm={"script": [{"$ref": "responses/missing.payload"}]},
                    expect={"outcome": "success"},
                )
            ]
        },
        ("dangling", "missing.payload", "FT-BAD-01"),
        id="dangling-ref",
    ),
    pytest.param(
        {
            "shared/schemas.json": _SHARED_SCHEMAS,
            "from-text/bad.json": [
                _case(
                    "generateAcceptFromText",
                    llm={"script": [{"$ref": "schemas/flat"}]},
                    expect={"outcome": "success"},
                )
            ],
        },
        ("out of scope", "responses/"),
        id="out-of-scope-ref",
    ),
    pytest.param(
        {
            "shared/schemas.json": {"a": {"$ref": "schemas/b"}, "b": {"type": "object"}},
            "validate/bad.json": [
                {
                    "id": "VAL-BAD-01",
                    "api": "validateProposePromptAndDataFilling",
                    "languages": ["zh-CN"],
                    "templateUri": "Negotiation-T/information-negotiation/propose/v1",
                    "prompt": {"text": "## 提议"},
                    "schema": {"$ref": "schemas/a"},
                    "expect": {"outcome": "success"},
                }
            ],
        },
        ("nested $ref", "VAL-BAD-01"),
        id="nested-ref",
    ),
    pytest.param(
        {
            "shared/schemas.json": {"a": {"$ref": "schemas/a"}},
            "validate/bad.json": [
                {
                    "id": "VAL-BAD-01",
                    "api": "validateProposePromptAndDataFilling",
                    "languages": ["zh-CN"],
                    "templateUri": "Negotiation-T/information-negotiation/propose/v1",
                    "prompt": {"text": "## 提议"},
                    "schema": {"$ref": "schemas/a"},
                    "expect": {"outcome": "success"},
                }
            ],
        },
        ("circular",),
        id="circular-ref",
    ),
    pytest.param(
        {"from-text/one.json": _minimal_case("FT-DUP-01"), "from-text/two.json": _minimal_case("FT-DUP-01")},
        ("duplicate id 'FT-DUP-01'", "from-text/one.json"),
        id="duplicate-id",
    ),
    pytest.param(
        {
            "from-text/bad.json": [
                _case("generateAcceptFromText", languages=["zh-CN", "zh-CN"], expect={"outcome": "success"})
            ]
        },
        ("duplicate language",),
        id="duplicate-expanded-id",
    ),
    pytest.param(
        {"from-text/bad.json": [_case("generateAcceptFromText", expect={"outcome": "failure"})]},
        ("failure expectation must name", "FT-BAD-01"),
        id="incomplete-failure-expectation",
    ),
    pytest.param(
        {
            "from-text/bad.json": [
                _case("generateAcceptFromText", expect={"outcome": "success", "code": "negotiation.invalid_input"})
            ]
        },
        ("success expectation must not carry failure-only fields",),
        id="failure-only-fields-on-success-expectation",
    ),
    pytest.param(
        {
            "from-text/bad.json": [
                _case("generateWhateverFromText", expect={"outcome": "success"}),
            ]
        },
        ("unknown api 'generateWhateverFromText'", "generateAcceptFromText"),
        id="unknown-api",
    ),
    pytest.param(
        {
            "from-text/bad.json": [
                _case("generateAcceptFromText", llm={"script": [{"$fail": "explodes"}]}, expect={"outcome": "success"})
            ]
        },
        ("unknown $fail marker 'explodes'", "non-json"),
        id="unknown-fail-marker",
    ),
    pytest.param(
        {"from-text/bad.json": [_case("generateAcceptFromText", languages=["zh_CN"], expect={"outcome": "success"})]},
        ("unsupported language 'zh_CN'",),
        id="unsupported-language",
    ),
    pytest.param(
        {
            "from-text/bad.json": [
                _case(
                    "generateAcceptFromText",
                    languages=["zh-CN", "en-US"],
                    input={"text": {"zh-CN": "我确认"}},
                    expect={"outcome": "success"},
                )
            ]
        },
        ("missing the 'en-US' text entry",),
        id="missing-language-text-entry",
    ),
    pytest.param(
        {
            "scenarios/bad.json": [
                {
                    "id": "SC-BAD-01",
                    "languages": ["zh-CN"],
                    "steps": [
                        {
                            "step": 2,
                            "api": "generateProposeFromText",
                            "context": {"id": _SESSION_ID, "round": 1, "maxRounds": 5},
                            "expect": {"outcome": "success"},
                        }
                    ],
                }
            ]
        },
        ("consecutively from 1", "SC-BAD-01"),
        id="non-consecutive-scenario-steps",
    ),
    pytest.param(
        {
            "scenarios/bad.json": [
                {
                    "id": "SC-BAD-01",
                    "languages": ["zh-CN"],
                    "steps": [
                        {
                            "step": 1,
                            "api": "generateProposeFromText",
                            "context": {"id": _SESSION_ID, "round": 1, "maxRounds": 5},
                            "rol": "A",
                            "expect": {"outcome": "success"},
                        }
                    ],
                }
            ]
        },
        ("rol", "SC-BAD-01"),
        id="unknown-key-inside-scenario-step",
    ),
    pytest.param(
        {
            "task/bad.json": [
                {
                    "id": "TASK-BAD-01",
                    "api": "validateTaskPromptAndDataFilling",
                    "languages": ["zh-CN"],
                    "templateUri": "Task-T/network-layer/private-line-complaint/v1",
                    "prompt": {"text": "## 任务类型(Task Type)"},
                    "schema": {"type": "object"},
                    "llm": {"script": ["{}"]},
                    "expect": {
                        "outcome": "failure",
                        "code": "negotiation.semantic_rejected",
                        "missingParams": ["accessPort"],
                    },
                }
            ]
        },
        ("success-only fields", "missingParams"),
        id="task-success-fields-on-failure-expectation",
    ),
    pytest.param(
        {
            "task/bad.json": [
                {
                    "id": "TASK-BAD-02",
                    "api": "validateTaskPromptAndDataFilling",
                    "languages": ["zh-CN"],
                    "templateUri": "Task-T/network-layer/private-line-complaint/v1",
                    "prompt": {"text": "## 任务类型(Task Type)"},
                    "schema": {"type": "object"},
                    "llm": {"script": ["{}"]},
                    "expect": {"outcome": "success", "params": {"accessPort": None}},
                }
            ]
        },
        ("accessPort", "missingParams"),
        id="json-null-inside-params",
    ),
    pytest.param(
        {
            "task/bad.json": [
                {
                    "id": "TASK-BAD-03",
                    "api": "validateTaskPromptAndDataFilling",
                    "languages": ["zh-CN"],
                    "templateUri": "Task-T/network-layer/private-line-complaint/v1",
                    "prompt": {"text": "## 任务类型(Task Type)"},
                    "schema": {"type": "object"},
                    "llm": {"script": ["{}"]},
                    "expect": {"outcome": "success", "paramsFromStep": 0},
                }
            ]
        },
        ("paramsFromStep",),
        id="params-from-step-below-one",
    ),
    pytest.param(
        {
            "scenarios/bad.json": [
                {
                    "id": "SC-BAD-02",
                    "languages": ["zh-CN"],
                    "roles": ["A", "B"],
                    "rolesDesc": {"C": "OSS（第三方）"},
                    "steps": [
                        {
                            "step": 1,
                            "api": "generateProposeFromText",
                            "context": {"id": _SESSION_ID, "round": 1, "maxRounds": 5},
                            "expect": {"outcome": "success"},
                        }
                    ],
                }
            ]
        },
        ("rolesDesc", "'C'"),
        id="roles-desc-outside-declared-roles",
    ),
    pytest.param(
        {
            "live/bad.json": [
                {
                    "id": "TASK-LIVE-01",
                    "api": "generateTaskPromptFromText",
                    "languages": ["zh-CN"],
                    "expect": {"success": True},
                }
            ]
        },
        ("LIVE-", "TASK-LIVE-01"),
        id="live-without-id-prefix",
    ),
    pytest.param(
        {
            "live/bad.json": [
                {
                    "id": "LIVE-BAD-01",
                    "api": "generateTaskPromptFromText",
                    "languages": ["zh-CN", "en-US"],
                    "expect": {"success": True},
                }
            ]
        },
        ("exactly the languages [zh-CN]",),
        id="live-outside-phase-one-languages",
    ),
    pytest.param(
        {
            "live/bad.json": [
                {
                    "id": "LIVE-BAD-01",
                    "api": "generateAcceptFromText",
                    "languages": ["zh-CN"],
                    "expect": {"success": True},
                }
            ]
        },
        ("live phase 1 supports only", "generateTaskPromptFromText", "validateTaskPromptAndDataFilling"),
        id="live-outside-task-family",
    ),
    pytest.param(
        {
            "live/bad.json": [
                {
                    "id": "LIVE-BAD-01",
                    "api": "generateTaskPromptFromText",
                    "languages": ["zh-CN"],
                    "input": {"text": {"zh-CN": "深圳访问广州的专线时延骤升。"}},
                    "expect": {"scenarioCode": "private-line-complaint"},
                }
            ]
        },
        ("missing required field 'success'",),
        id="live-expectation-without-success",
    ),
    pytest.param(
        {
            "live/bad.json": [
                {
                    "id": "LIVE-BAD-01",
                    "api": "generateTaskPromptFromText",
                    "languages": ["zh-CN"],
                    "expect": {"success": True},
                }
            ]
        },
        ("input.text", "generateTaskPromptFromText"),
        id="live-generate-without-input-text",
    ),
    pytest.param(
        {
            "live/bad.json": [
                {
                    "id": "LIVE-BAD-01",
                    "api": "validateTaskPromptAndDataFilling",
                    "languages": ["zh-CN"],
                    "prompt": {"golden": "task-happy-zh-CN"},
                    "expect": {"success": True},
                }
            ]
        },
        ("only the inline prompt.text", "golden"),
        id="live-golden-prompt",
    ),
    pytest.param(
        {
            "live/bad.json": [
                {
                    "id": "LIVE-BAD-01",
                    "api": "validateTaskPromptAndDataFilling",
                    "languages": ["zh-CN"],
                    "prompt": {"text": "## 任务类型(Task Type)"},
                    "expect": {"success": True, "promptTextContains": ["## 任务类型"]},
                }
            ]
        },
        ("promptTextContains", "no generated prompt"),
        id="live-validate-declaring-prompt-text-contains",
    ),
    pytest.param(
        {
            "from-text/dup.json": _minimal_case("LIVE-DUP-01"),
            "live/dup.json": [
                {
                    "id": "LIVE-DUP-01",
                    "api": "generateTaskPromptFromText",
                    "languages": ["zh-CN"],
                    "expect": {"success": True},
                }
            ],
        },
        ("duplicate id 'LIVE-DUP-01'", "from-text/dup.json"),
        id="live-id-colliding-with-offline-case",
    ),
]


@pytest.mark.parametrize(
    ("files", "fragments"),
    _FAIL_FAST_VIOLATIONS,
)
def test_fail_fast_violations(tmp_path: Path, files: dict[str, Any], fragments: tuple[str, ...]) -> None:
    """Every format violation fails the load before any case runs, naming file, id and defect."""
    for relative, document in files.items():
        _write(tmp_path, relative, document)
    _assert_load_fails(tmp_path, *fragments)


# ------------------------------------------------------------------ Java parity: task family format


def test_loads_task_family_records_with_the_closed_loop_expectation_fields(tmp_path: Path) -> None:
    _write(tmp_path, "shared/llm-responses.json", _SHARED_RESPONSES)
    _write(
        tmp_path,
        "task/happy.json",
        [
            {
                "id": "TASK-FT-01",
                "api": "generateTaskPromptFromText",
                "languages": ["zh-CN"],
                "summary": "工作台从原始投诉文本生成缺参任务报文",
                "templateUri": "Task-T/network-layer/private-line-complaint/v1",
                "input": {"text": {"zh-CN": "深圳访问广州的专线时延骤升，OSS侧事件流水号event-id-20260511-09013。"}},
                "llm": {"script": ['{"slots": {"任务对象": "", "任务上下文": "投诉分类：待补充"}, "slot_errors": []}']},
                "expect": {
                    "outcome": "success",
                    "llmCalls": 1,
                    "promptTextContains": ["## 任务类型", "## 任务对象"],
                },
            }
        ],
    )
    _write(
        tmp_path,
        "scenarios/task-closed-loop.json",
        [
            {
                "id": "SC-TASK-01",
                "languages": ["zh-CN"],
                "summary": "任务缺参发现与补参提取的因果闭环",
                "roles": ["A", "B"],
                "rolesDesc": {
                    "A": "工作台（client，任务发起/补数方）",
                    "B": "OMC（server，执行/要数方，协商发起方）",
                },
                "steps": [
                    {
                        "step": 1,
                        "role": "A",
                        "api": "generateTaskPromptFromText",
                        "context": None,
                        "templateUri": "Task-T/network-layer/private-line-complaint/v1",
                        "input": {
                            "text": {"zh-CN": "深圳访问广州的专线时延骤升，OSS侧事件流水号event-id-20260511-09013。"}
                        },
                        "llm": {
                            "script": [
                                '{"slots": {"任务对象": "", "任务上下文": "投诉分类：待补充"}, "slot_errors": []}'
                            ]
                        },
                        "expect": {"outcome": "success", "llmCalls": 1},
                    },
                    {
                        "step": 2,
                        "role": "B",
                        "api": "validateTaskPromptAndDataFilling",
                        "context": None,
                        "templateUri": "Task-T/network-layer/private-line-complaint/v1",
                        "prompt": {"fromStep": 1},
                        "schema": {"type": "object", "properties": {"accessPort": {"type": "string"}}},
                        "llm": {"script": ['{"semantic_verdict":true,"errors":[],"params":{"accessPort":null}}']},
                        "expect": {
                            "outcome": "success",
                            "llmCalls": 1,
                            "missingParams": ["accessPort"],
                            "params": {"faultTime": "2026-05-11T08:21:46Z"},
                        },
                    },
                    {
                        "step": 3,
                        "role": "B",
                        "api": "validateAcceptPromptAndDataFilling",
                        "context": {"id": _SESSION_ID, "round": 2, "maxRounds": 5},
                        "templateUri": "Negotiation-T/information-negotiation/accept-reject/v1",
                        "prompt": {"fromStep": 1},
                        "schema": {"type": "object"},
                        "llm": {"script": ['{"semantic_verdict":true,"errors":[],"params":{}}']},
                        "expect": {"outcome": "success", "llmCalls": 1, "paramsFromStep": 2},
                    },
                ],
                "expectFlow": {"terminalCondition": "accept", "missingParamsFilled": 2},
            }
        ],
    )

    corpus = load(tmp_path)

    task_case = _case_by_id(corpus, "TASK-FT-01/zh-CN")
    assert task_case.api is NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT
    assert task_case.expect.prompt_text_contains == ["## 任务类型", "## 任务对象"]

    scenario = corpus.scenarios[0]
    assert scenario.roles_desc == {
        "A": "工作台（client，任务发起/补数方）",
        "B": "OMC（server，执行/要数方，协商发起方）",
    }
    assert scenario.describe_role("A") == "A=工作台（client，任务发起/补数方）"
    validation_step = scenario.steps[1].case_data
    assert validation_step.api is NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING
    assert validation_step.expect.missing_params == ["accessPort"]
    assert validation_step.expect.params == {"faultTime": "2026-05-11T08:21:46Z"}
    assert scenario.steps[2].case_data.expect.params_from_step == 2
    assert scenario.expect_flow is not None
    assert scenario.expect_flow.missing_params_filled == 2


# ------------------------------------------------------------------ Java parity: live family


def test_loads_live_records_into_the_dedicated_live_cases_list(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "live/generate.json",
        [
            {
                "id": "LIVE-GEN-01",
                "api": "generateTaskPromptFromText",
                "languages": ["zh-CN"],
                "priority": "P0",
                "tags": ["live", "scenario-recognition"],
                "summary": "明确场景的典型投诉文本",
                "templateUri": "Task-T/network-layer/private-line-complaint/v1",
                "input": {"text": {"zh-CN": "深圳访问广州的专线时延骤升，OSS侧事件流水号event-id-20260511-09013。"}},
                "expect": {
                    "success": True,
                    "scenarioCode": "private-line-complaint",
                    "paramsContains": {"accessPort": "P533-01"},
                    "paramsAbsent": ["faultTime"],
                    "promptTextContains": ["## instruction", "event-id-20260511-09013"],
                    "maxLlmCalls": 4,
                },
            }
        ],
    )

    corpus = load(tmp_path)

    assert len(corpus.live_cases) == 1, "the live record expands once and lands in live_cases"
    assert corpus.cases == [], "a live record never mixes into the offline cases list"
    live = corpus.live_cases[0]
    assert live.id == "LIVE-GEN-01/zh-CN"
    assert live.base_id == "LIVE-GEN-01"
    assert live.source_file == "live/generate.json"
    assert live.api is NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT
    assert live.language == "zh-CN"
    assert live.priority == "P0"
    assert live.tags == ["live", "scenario-recognition"]
    assert live.input_text == "深圳访问广州的专线时延骤升，OSS侧事件流水号event-id-20260511-09013。"
    assert live.live_expect.success
    assert live.live_expect.scenario_code == "private-line-complaint"
    assert live.live_expect.params_contains == {"accessPort": "P533-01"}
    assert live.live_expect.params_absent == ["faultTime"]
    assert live.live_expect.prompt_text_contains == ["## instruction", "event-id-20260511-09013"]
    assert live.live_expect.max_llm_calls == 4
    assert live.error_prefix() == "live/generate.json [LIVE-GEN-01/zh-CN]"


def test_the_live_max_llm_calls_defaults_to_four(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "live/generate.json",
        [
            {
                "id": "LIVE-GEN-01",
                "api": "generateTaskPromptFromText",
                "languages": ["zh-CN"],
                "input": {"text": {"zh-CN": "深圳访问广州的专线时延骤升。"}},
                "expect": {"success": True},
            }
        ],
    )

    corpus = load(tmp_path)

    assert corpus.live_cases[0].live_expect.max_llm_calls == 4, "maxLlmCalls defaults to 4"


# ------------------------------------------------------------------ shared reference files


@pytest.mark.parametrize(
    ("shared_file", "document", "fragments"),
    [
        pytest.param(
            "shared/llm-responses.json",
            {"bad.payload": {"not": "a string"}},
            ("shared/llm-responses.json", "bad.payload", "JSON string"),
            id="response-payload-not-a-string",
        ),
        pytest.param(
            "shared/schemas.json",
            {"bad": "not an object"},
            ("shared/schemas.json", "bad", "JSON object"),
            id="schema-variant-not-an-object",
        ),
        pytest.param(
            "shared/llm-responses.json",
            ["not", "an", "object"],
            ("shared/llm-responses.json", "expected a JSON object mapping names to shared values"),
            id="shared-file-not-an-object",
        ),
    ],
)
def test_shared_reference_file_violations_fail_fast(
    tmp_path: Path, shared_file: str, document: Any, fragments: tuple[str, ...]
) -> None:
    """A malformed shared reference file fails the load with the file and the offending name."""
    _write(tmp_path, shared_file, document)
    _assert_load_fails(tmp_path, *fragments)


# ------------------------------------------------------------------ two-layer strictness (D21)


@pytest.mark.parametrize(
    ("with_schema", "expected_fragment"),
    [
        pytest.param(True, "Additional properties are not allowed", id="schema-layer-first"),
        pytest.param(False, "unknown property 'expection'", id="binding-layer-fallback"),
    ],
)
def test_unknown_key_fails_on_both_strict_layers(tmp_path: Path, with_schema: bool, expected_fragment: str) -> None:
    """A typo'd key is an error on either strict layer, never a silently ignored key."""
    _write(tmp_path, "from-text/bad.json", _EXPECT_TYPO_CASE)
    if with_schema:
        _write_shipped_schema(tmp_path)
    _assert_load_fails(
        tmp_path,
        "from-text/bad.json",
        "FT-BAD-01",
        "$[0].expect.expection",
        "expection",
        "expect",
        expected_fragment,
    )


def test_the_schema_layer_reports_a_violation_with_the_triple(tmp_path: Path) -> None:
    """A schema violation (unknown enum value) names the file, the record id and the JSON path."""
    _write_shipped_schema(tmp_path)
    _write(
        tmp_path, "from-text/bad.json", [_case("generateAcceptFromText", priority="P9", expect={"outcome": "success"})]
    )
    _assert_load_fails(
        tmp_path,
        "from-text/bad.json",
        "FT-BAD-01",
        "$[0].priority",
        "'P9' is not one of ['P0', 'P1', 'P2']",
    )


def test_the_schema_layer_names_an_unknown_key_over_the_cascade_errors(tmp_path: Path) -> None:
    """An unknown key inside an expectation outranks the sibling branch's spurious required error.

    The expectation block is a discriminated union (a ``oneOf`` of the success and failure
    shapes): a typo'd key makes the record match neither branch, so the schema layer also
    reports the failure branch's ``required`` complaint. The unknown key is the actual defect
    and is the one reported.
    """
    _write_shipped_schema(tmp_path)
    _write(
        tmp_path,
        "from-text/bad.json",
        [_case("generateAcceptFromText", expect={"outcome": "success", "diferential": True})],
    )
    with pytest.raises(CorpusLoadException) as raised:
        load(tmp_path)
    message = str(raised.value)
    assert "$[0].expect.diferential" in message
    assert "'diferential' was unexpected" in message
    assert "required property" not in message


def test_a_stub_schema_file_disables_the_schema_layer_but_not_the_binding_layer(tmp_path: Path) -> None:
    """A schema file without the corpus ``$defs`` disables the schema layer; strictness stays."""
    _write(tmp_path, "corpus-schema.json", {"formatVersion": 1})
    _write(tmp_path, "from-text/bad.json", _EXPECT_TYPO_CASE)
    _assert_load_fails(tmp_path, "unknown property 'expection'")


def test_the_binding_layer_rejects_a_wrong_json_field_type(tmp_path: Path) -> None:
    """A field of the wrong JSON type fails the strict binding with the field's path."""
    _write(
        tmp_path,
        "from-text/bad.json",
        [_case("generateAcceptFromText", languages="zh-CN", expect={"outcome": "success"})],
    )
    _assert_load_fails(tmp_path, "from-text/bad.json", "FT-BAD-01", "$[0].languages", "JSON array")


_PROBE_BASE = {
    "id": "VAL-X-01",
    "languages": ["zh-CN"],
    "templateUri": "Negotiation-T/information-negotiation/propose/v1",
    "prompt": {"text": "## 提议"},
    "llm": {"script": ["{}"]},
    "expect": {"outcome": "success"},
}


def _write_probe_corpus(tmp_path: Path, record: dict[str, Any], shared_schemas: dict[str, Any]) -> LoadedCorpus:
    """Write a one-record corpus under the shipped format definition and load it."""
    _write_shipped_schema(tmp_path)
    _write(tmp_path, "shared/schemas.json", shared_schemas)
    _write(tmp_path, "validate/probe.json", [record])
    return load(tmp_path)


def test_schema_repair_accepts_the_null_context_probe(tmp_path: Path) -> None:
    """``$defs/context`` documents the JSON ``null`` context of the validate family's probes.

    The shipped schema def rejects the null context its own description documents as the
    null-context probe form; the in-memory repair widens the def's type to ``["object", "null"]``
    so the probe loads with a ``None`` context.
    """
    record = {
        **_PROBE_BASE,
        "api": "validateProposePromptAndDataFilling",
        "context": None,
        "schema": {"$ref": "schemas/flat"},
    }

    corpus = _write_probe_corpus(tmp_path, record, {"flat": {"type": "object"}})

    assert len(corpus.cases) == 1
    assert corpus.cases[0].context is None


def test_schema_repair_accepts_a_schema_reference_object(tmp_path: Path) -> None:
    """A ``{"$ref": ...}`` object is both a JSON object and a schema reference.

    The shipped ``schema`` property uses ``oneOf`` where the reference object matches both
    branches — valid under neither, because ``oneOf`` demands exactly one; the repair widens
    the construct to ``anyOf`` (as ``$defs/liveCase`` already does) so the reference resolves.
    """
    record = {
        **_PROBE_BASE,
        "api": "validateProposePromptAndDataFilling",
        "context": {"id": _SESSION_ID, "round": 1, "maxRounds": 5},
        "schema": {"$ref": "schemas/flat"},
    }

    corpus = _write_probe_corpus(tmp_path, record, {"flat": {"type": "object"}})

    assert corpus.cases[0].schema == {"type": "object"}


def test_schema_repair_admits_the_upper_case_shared_schema_names(tmp_path: Path) -> None:
    """The shipped ``shared/schemas.json`` carries the drift-probe names ``noType``/``nestedArray``.

    The shipped name pattern limits shared schema names to lower case, which its own drift
    probes violate; the repair admits upper case letters as well.
    """
    record = {
        **_PROBE_BASE,
        "api": "validateTaskPromptAndDataFilling",
        "templateUri": "Task-T/network-layer/private-line-complaint/v1",
        "prompt": {"text": "## 任务类型(Task Type)"},
        "context": None,
        "schema": {"$ref": "schemas/noType"},
    }

    corpus = _write_probe_corpus(tmp_path, record, {"noType": {"type": "object"}, "nestedArray": {"type": "array"}})

    assert sorted(corpus.shared_schemas) == ["nestedArray", "noType"]
    assert corpus.cases[0].schema == {"type": "object"}


# ------------------------------------------------------------------ the shipped corpus


def test_shipped_corpus_statistics(loaded_corpus: LoadedCorpus) -> None:
    """The shipped corpus matches the statistics of the machine-generated INDEX.md."""
    assert len(loaded_corpus.cases) == 212
    assert len(loaded_corpus.scenarios) == 22
    assert len(loaded_corpus.live_cases) == 7
    assert len(loaded_corpus.cases) + len(loaded_corpus.scenarios) == 234, "the bilingual expanded units"
    assert len(loaded_corpus.shared_responses) == 35
    assert len(loaded_corpus.shared_schemas) == 8
    assert len({case.base_id for case in loaded_corpus.cases}) == 154, "the case records"
    assert len({scenario.base_id for scenario in loaded_corpus.scenarios}) == 20, "the scenario records"
    assert len({live.base_id for live in loaded_corpus.live_cases}) == 7, "the live records"


#: Per-suite-file record counts asserted against the family distribution table of INDEX.md:
#: (file, base records, language-expanded records).
_SHIPPED_FILE_COUNTS = [
    pytest.param("from-data/happy.json", 11, 22, id="from-data-happy"),
    pytest.param("from-data/programming-errors.json", 14, 14, id="from-data-programming-errors"),
    pytest.param("from-text/extraction-failures.json", 15, 15, id="from-text-extraction-failures"),
    pytest.param("from-text/happy.json", 13, 26, id="from-text-happy"),
    pytest.param("from-text/programming-errors.json", 8, 8, id="from-text-programming-errors"),
    pytest.param("from-text/retry.json", 13, 17, id="from-text-retry"),
    pytest.param("from-text/template-resolution.json", 4, 4, id="from-text-template-resolution"),
    pytest.param("validate/drift-probes.json", 4, 8, id="validate-drift-probes"),
    pytest.param("validate/error-code-mapping.json", 5, 5, id="validate-error-code-mapping"),
    pytest.param("validate/happy.json", 10, 20, id="validate-happy"),
    pytest.param("validate/llm-shape-and-retry.json", 9, 13, id="validate-llm-shape-and-retry"),
    pytest.param("validate/merge-and-schema.json", 6, 8, id="validate-merge-and-schema"),
    pytest.param("validate/programming-errors.json", 17, 17, id="validate-programming-errors"),
    pytest.param("validate/rule-gate.json", 8, 11, id="validate-rule-gate"),
    pytest.param("validate/semantic-verdicts.json", 17, 24, id="validate-semantic-verdicts"),
    pytest.param("scenarios/boundary-flows.json", 9, 9, id="scenarios-boundary-flows"),
    pytest.param("scenarios/feasibility-flows.json", 4, 5, id="scenarios-feasibility-flows"),
    pytest.param("scenarios/information-flows.json", 3, 4, id="scenarios-information-flows"),
    pytest.param("scenarios/target-flows.json", 4, 4, id="scenarios-target-flows"),
    pytest.param("live/task-apis.json", 7, 7, id="live-task-apis"),
]


@pytest.mark.parametrize(
    ("relative", "base_records", "expanded_records"),
    _SHIPPED_FILE_COUNTS,
)
def test_every_suite_file_loads_clean(
    loaded_corpus: LoadedCorpus, relative: str, base_records: int, expanded_records: int
) -> None:
    """Every corpus record file loads clean, contributing its documented share of records."""
    cases = [case for case in loaded_corpus.cases if case.source_file == relative]
    scenarios = [scenario for scenario in loaded_corpus.scenarios if scenario.source_file == relative]
    live_cases = [live for live in loaded_corpus.live_cases if live.source_file == relative]
    assert len(cases) + len(scenarios) + len(live_cases) == expanded_records
    assert len({record.base_id for record in [*cases, *scenarios, *live_cases]}) == base_records


def test_shipped_corpus_priority_distribution(loaded_corpus: LoadedCorpus) -> None:
    """The base case records distribute over the priorities exactly as INDEX.md documents."""
    priorities = {case.base_id: case.priority for case in loaded_corpus.cases}
    assert Counter(priorities.values()) == {"P0": 79, "P1": 63, "P2": 12}


def test_shipped_corpus_scenario_steps(loaded_corpus: LoadedCorpus) -> None:
    """The 20 scenario records carry the 93 steps INDEX.md documents (102 after expansion)."""
    steps_per_base = {scenario.base_id: len(scenario.steps) for scenario in loaded_corpus.scenarios}
    assert len(steps_per_base) == 20
    assert sum(steps_per_base.values()) == 93
    assert sum(len(scenario.steps) for scenario in loaded_corpus.scenarios) == 102


def test_shipped_corpus_expands_every_record_per_language(loaded_corpus: LoadedCorpus) -> None:
    """Every record is expanded once per declared language, the id appending ``/<language>``."""
    records = [*loaded_corpus.cases, *loaded_corpus.scenarios, *loaded_corpus.live_cases]
    assert records
    for record in records:
        assert record.id == f"{record.base_id}/{record.language}"
        assert record.language in ("zh-CN", "en-US"), "the bilingual id suffix stays -k filterable"
    assert len({record.id for record in records}) == len(records) == 241


def test_shipped_corpus_live_records_never_mix_into_the_offline_lists(loaded_corpus: LoadedCorpus) -> None:
    """The live family lands in its own list: LIVE- ids, zh-CN only, disjoint from the offline ids."""
    offline_ids = {case.id for case in loaded_corpus.cases} | {scenario.id for scenario in loaded_corpus.scenarios}
    live_ids = {live.id for live in loaded_corpus.live_cases}
    assert live_ids
    assert not offline_ids & live_ids
    assert all(live.id.startswith("LIVE-") for live in loaded_corpus.live_cases)
    assert all(live.language == "zh-CN" for live in loaded_corpus.live_cases)
    assert all(not case.id.startswith("LIVE-") for case in loaded_corpus.cases)


def test_shipped_corpus_error_prefix_names_file_and_id(loaded_corpus: LoadedCorpus) -> None:
    """The error prefix of every record names the corpus file and the expanded id."""
    for record in [*loaded_corpus.cases, *loaded_corpus.live_cases]:
        assert record.error_prefix() == f"{record.source_file} [{record.id}]"
    for scenario in loaded_corpus.scenarios:
        for step in scenario.steps:
            assert step.case_data.error_prefix() == f"{scenario.source_file} [{step.case_data.id}]"


def test_shipped_corpus_references_are_resolved_into_literals(loaded_corpus: LoadedCorpus) -> None:
    """After loading no reference survives: script steps are literal payloads, schemas are nodes."""
    scripted = [case for case in loaded_corpus.cases if case.llm is not None]
    assert scripted, "the corpus scripts LLM behavior"
    payloads = {step.json for case in scripted for step in case.llm.steps if isinstance(step, LlmScriptStep.Payload)}
    assert payloads & set(loaded_corpus.shared_responses.values()), "responses/ refs resolve to shared payloads"
    resolved_schemas = [case.schema for case in loaded_corpus.cases if case.schema is not None]
    assert resolved_schemas
    assert any(schema in loaded_corpus.shared_schemas.values() for schema in resolved_schemas)
    assert all("$ref" not in schema for schema in resolved_schemas), "schemas/ refs resolve to shared nodes"


def test_shipped_corpus_session_ids(loaded_corpus: LoadedCorpus) -> None:
    """The corpus carries the golden session id and the rule gate's malformed id probes.

    The loader only requires a non-blank context id — the UUID shape is the rule gate's job at
    run time — so the deliberately malformed probes of ``validate/rule-gate.json`` (an upper
    case UUID, the truncated golden id, a wrong character, a non-UUID) load verbatim.
    """
    context_ids = {case.context.id for case in loaded_corpus.cases if case.context is not None}
    assert SESSION_ID in context_ids, "the golden session id drives the deterministic families"
    rule_gate_ids = {
        case.context.id
        for case in loaded_corpus.cases
        if case.context is not None and case.source_file == "validate/rule-gate.json"
    }
    assert rule_gate_ids - {SESSION_ID}, "the rule gate probes carry malformed session id variants"
    assert "3dbc13b5" in rule_gate_ids, "the truncated golden id is one of the probes"


@pytest.mark.parametrize("directory", ["from-data", "from-text", "validate"])
def test_the_bilingual_parametrize_helper_selects_a_directory(loaded_corpus: LoadedCorpus, directory: str) -> None:
    """The conftest collection-time helper yields exactly the directory's expanded cases."""
    selected = expanded_cases(directory)
    expected = [case for case in loaded_corpus.cases if case.source_file.startswith(f"{directory}/")]
    assert selected == expected
    assert selected, f"the {directory} directory contributes cases"


# ------------------------------------------------------------------ shipped-corpus mutations


@pytest.mark.parametrize(
    ("relative", "mutate", "fragments"),
    [
        pytest.param(
            "from-text/retry.json",
            _typo_next_to_template_uri,
            ("from-text/retry.json", "FT-RETRY-01", "$[0].templeteUri", "'templeteUri' was unexpected"),
            id="top-level-unknown-key",
        ),
        pytest.param(
            "from-data/happy.json",
            _typo_inside_expectation,
            ("from-data/happy.json", "FD-HAPPY-01", "$[0].expect.diferential", "'diferential' was unexpected"),
            id="expectation-unknown-key",
        ),
        pytest.param(
            "scenarios/information-flows.json",
            _typo_inside_scenario_step,
            ("scenarios/information-flows.json", "SC-INFO-01", "steps[0].rol", "'rol' was unexpected"),
            id="scenario-step-unknown-key",
        ),
        pytest.param(
            "live/task-apis.json",
            _offline_key_in_live_record,
            ("live/task-apis.json", "LIVE-GEN-01", "$[0].llm", "'llm' was unexpected"),
            id="offline-key-in-live-record",
        ),
        pytest.param(
            "from-data/happy.json",
            _duplicate_a_record_id,
            ("duplicate id 'FD-HAPPY-02'", "from-data/happy.json"),
            id="duplicate-id",
        ),
        pytest.param(
            "from-text/happy.json",
            _dangling_script_ref,
            ("from-text/happy.json", "FT-HAPPY-01", "llm.script[0].$ref", "dangling $ref 'responses/no.such.payload'"),
            id="dangling-ref",
        ),
        pytest.param(
            "from-data/happy.json",
            _unknown_priority,
            ("from-data/happy.json", "FD-HAPPY-01", "$[0].priority", "'P9' is not one of"),
            id="schema-violation-priority",
        ),
        pytest.param(
            "from-data/happy.json",
            _context_round_zero,
            ("from-data/happy.json", "FD-HAPPY-01", "$[0].context.round", "less than the minimum of 1"),
            id="schema-violation-context-round",
        ),
    ],
)
def test_shipped_corpus_mutations_fail_fast(
    tmp_path: Path,
    relative: str,
    mutate: Callable[[list[dict[str, Any]]], None],
    fragments: tuple[str, ...],
) -> None:
    """One injected defect in a copy of the real corpus fails the load with the full triple."""
    root = _mutate_shipped(tmp_path, relative, mutate)
    _assert_load_fails(root, *fragments)
