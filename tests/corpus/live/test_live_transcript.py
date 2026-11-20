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

"""Direct unit tests of :mod:`tests.corpus.live.transcript` (port of the Java
``LiveTranscriptTest``): the run directory layout, the pretty-printed JSON array of case verdicts
in the Java camelCase shape, and the aggregated
:class:`~tests.corpus.live.results.LiveRunSummary` with the M5 schema-adherence statistic.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.corpus.live.results import LiveCaseResult, Outcome
from tests.corpus.live.transcript import LiveTranscript
from tests.corpus.llm_stub import LiveLlmCall

MESSAGES: list[dict[str, str]] = [{"role": "user", "content": "投诉文本"}]


def _call(content: str, usage: dict[str, int]) -> LiveLlmCall:
    """One answered call fixture."""
    return LiveLlmCall(
        messages=list(MESSAGES),
        json_schema={"type": "object"},
        temperature=0.0,
        max_tokens=64,
        content=content,
        model="qwen3-27b",
        usage=usage,
        duration_ms=1,
    )


def test_a_run_creates_a_timestamped_directory_under_the_given_root(tmp_path: Path) -> None:
    run = LiveTranscript.create_run(tmp_path)

    directory_name = run.directory.name
    assert re.fullmatch(r"\d{8}-\d{6}(-\d+)?", directory_name), (
        f"the run directory carries the yyyyMMdd-HHmmss timestamp, but was: {directory_name}"
    )
    assert run.directory.is_dir(), "create_run creates the run directory eagerly"


def test_two_runs_in_the_same_second_get_distinct_directories(tmp_path: Path) -> None:
    first = LiveTranscript.create_run(tmp_path)
    second = LiveTranscript.create_run(tmp_path)

    assert first.directory != second.directory, (
        f"two runs of the same second must not share a directory: {first.directory} vs {second.directory}"
    )


def test_write_flushes_the_case_array_and_the_aggregate_as_valid_json(tmp_path: Path) -> None:
    run = LiveTranscript.create_run(tmp_path)
    run.append_case(
        LiveCaseResult(
            "LIVE-GEN-01/zh-CN",
            Outcome.PASS,
            "scenario + slot subset",
            "深圳访问广州的专线时延骤升，OSS侧事件流水号event-id-20260511-09013。",
            "private-line-complaint",
            {"accessPort": "P533-01"},
            [
                LiveLlmCall(
                    messages=list(MESSAGES),
                    json_schema={"type": "object"},
                    temperature=0.0,
                    max_tokens=None,
                    content='{"scenarioCode":"private-line-complaint"}',
                    model="qwen3-27b",
                    usage={"prompt_tokens": 120, "completion_tokens": 30},
                    duration_ms=8,
                    error=None,
                ),
                LiveLlmCall(
                    messages=list(MESSAGES),
                    json_schema=None,
                    temperature=None,
                    max_tokens=None,
                    content="<not a json object>",
                    model="qwen3-27b",
                    usage={"prompt_tokens": 200, "completion_tokens": 50},
                    duration_ms=5,
                    error=None,
                ),
            ],
            1234,
            None,
        )
    )
    run.append_case(
        LiveCaseResult(
            "LIVE-VAL-01/zh-CN",
            Outcome.FAIL,
            "paramsAbsent",
            None,
            None,
            None,
            [
                LiveLlmCall(
                    messages=list(MESSAGES),
                    json_schema=None,
                    temperature=None,
                    max_tokens=None,
                    content=None,
                    model=None,
                    usage={},
                    duration_ms=3,
                    error="a2a_t.llm.errors.LLMRuntimeError: connection reset",
                )
            ],
            900,
            "faultTime was filled but expected absent: 2026-05-11",
        )
    )
    run.append_case(
        LiveCaseResult("LIVE-GEN-02/zh-CN", Outcome.SKIP, "no live endpoint", None, None, None, [], 0, None)
    )

    transcript_file = run.write()

    assert transcript_file == run.directory / "transcript.json"
    assert (run.directory / "summary.json").is_file(), "the aggregate is flushed too"

    transcript = json.loads(transcript_file.read_text(encoding="utf-8"))
    assert isinstance(transcript, list), "the transcript is one JSON array of case results"
    assert len(transcript) == 3
    assert transcript[0]["caseId"] == "LIVE-GEN-01/zh-CN"
    assert transcript[0]["outcome"] == "PASS"
    assert "event-id-20260511-09013" in transcript[0]["inputSummary"], "the transcript carries the input summary"
    assert transcript[0]["scenarioCode"] == "private-line-complaint"
    assert transcript[0]["params"]["accessPort"] == "P533-01"
    assert len(transcript[0]["llmCalls"]) == 2, "the recorded calls are embedded per case"
    assert transcript[0]["llmCalls"][0]["content"] == '{"scenarioCode":"private-line-complaint"}'
    assert transcript[0]["llmCalls"][0]["maxTokens"] is None, "the camelCase projection keeps null fields"
    assert transcript[1]["outcome"] == "FAIL"
    assert "faultTime" in transcript[1]["failureDiff"]
    assert transcript[2]["outcome"] == "SKIP"
    assert "\n  " in transcript_file.read_text(encoding="utf-8"), "the transcript is pretty-printed"


def test_summary_aggregates_verdicts_tokens_and_schema_parse_failures(tmp_path: Path) -> None:
    run = LiveTranscript.create_run(tmp_path)
    run.append_case(
        LiveCaseResult(
            "LIVE-GEN-01/zh-CN",
            Outcome.PASS,
            "scenario",
            "深圳访问广州的专线时延骤升。",
            "s",
            {},
            [
                _call('{"a":1}', {"prompt_tokens": 10, "completion_tokens": 4}),
                _call("plain text, not json", {"prompt_tokens": 100, "completion_tokens": 40}),
            ],
            10,
            None,
        )
    )
    run.append_case(
        LiveCaseResult(
            "LIVE-VAL-01/zh-CN",
            Outcome.FAIL,
            "subset",
            None,
            None,
            None,
            [_call("[1,2]", {"prompt_tokens": 1, "completion_tokens": 1})],
            10,
            "diff",
        )
    )
    run.append_case(
        LiveCaseResult(
            "LIVE-VAL-02/zh-CN",
            Outcome.ERROR,
            "engine blew up",
            None,
            None,
            None,
            [
                LiveLlmCall(
                    messages=list(MESSAGES),
                    json_schema=None,
                    temperature=None,
                    max_tokens=None,
                    content=None,
                    model=None,
                    usage={},
                    duration_ms=2,
                    error="a2a_t.llm.errors.LLMRuntimeError: timeout",
                )
            ],
            10,
            "boom",
        )
    )
    run.append_case(LiveCaseResult("LIVE-GEN-02/zh-CN", Outcome.SKIP, "no endpoint", None, None, None, [], 0, None))

    summary = run.summary()

    assert summary.total_cases == 4
    assert summary.pass_count == 1
    assert summary.fail_count == 1
    assert summary.skip_count == 1
    assert summary.error_count == 1
    assert summary.total_llm_calls == 4
    assert summary.total_prompt_tokens == 10 + 100 + 1
    assert summary.total_completion_tokens == 4 + 40 + 1
    assert summary.schema_parse_failure_count == 2, (
        "plain text and a JSON array are schema parse failures; the thrown call's null content is not"
    )


def test_an_empty_run_writes_an_empty_array_and_zero_counts(tmp_path: Path) -> None:
    run = LiveTranscript.create_run(tmp_path)

    transcript_file = run.write()

    transcript = json.loads(transcript_file.read_text(encoding="utf-8"))
    assert transcript == []
    summary = json.loads((run.directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["totalCases"] == 0
    assert summary["totalLlmCalls"] == 0
    assert summary["schemaParseFailureCount"] == 0


def test_append_case_rejects_a_null_verdict(tmp_path: Path) -> None:
    run = LiveTranscript.create_run(tmp_path)

    with pytest.raises(TypeError):
        run.append_case(None)  # type: ignore[arg-type]


def test_the_written_files_are_lf_normalized(tmp_path: Path) -> None:
    run = LiveTranscript.create_run(tmp_path)
    run.append_case(LiveCaseResult("LIVE-GEN-01/zh-CN", Outcome.PASS, "s", None, None, None, [], 1, None))
    run.write()

    for file_name in ("transcript.json", "summary.json"):
        raw = (run.directory / file_name).read_bytes()
        assert b"\r" not in raw, f"{file_name} must be LF-normalized"
