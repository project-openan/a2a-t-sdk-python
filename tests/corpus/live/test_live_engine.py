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

"""Offline unit tests of the :class:`~tests.corpus.live.engine.LiveCaseEngine` judging logic
(port of the Java ``LiveCaseEngineTest``).

A step-playing stub client drives the real production assembly exactly the way a live endpoint
would (the scripted responses reuse the response shapes the offline task scenarios script), so
the expectation assertions, the infra-only retry and the transcript verdicts are verified without
any endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.models import LLMResponse
from a2a_t.llm.provider import LLMClient
from tests.corpus.conftest import CORPUS_ROOT
from tests.corpus.live.config import LiveLlmConfig
from tests.corpus.live.engine import (
    DEFAULT_INFRA_RETRIES,
    INFRA_RETRIES_VARIABLE,
    LiveCaseEngine,
    _infra_retry_limit,
    _is_infrastructure_failure,
)
from tests.corpus.live.env_writer import env_file_for
from tests.corpus.live.results import Outcome
from tests.corpus.live.transcript import LiveTranscript
from tests.corpus.models import LiveCase, LiveExpectation, NegotiationApi, PromptSource

#: Slot-extraction response of the generate step, the shape the offline task scenarios script.
SLOT_EXTRACTION_RESPONSE = (
    '{"slots":{"任务对象":"P533-珠江旧城-PTN3900-23-TPA1EG24-1",'
    '"任务上下文":"投诉分类：专线质差；问题发生时间：2026-05-11T08:21:46Z；'
    'OSS侧事件流水号：event-id-20260511-09013；投诉详情：深圳访问广州的专线时延从平均12ms骤升至320ms"},'
    '"slot_errors":[]}'
)

#: Validate-step response of a semantically compliant prompt with every parameter filled.
VALIDATE_PASS_RESPONSE = (
    '{"semantic_verdict":true,"errors":[],"params":{"accessPort":"P533-珠江旧城-PTN3900-23-TPA1EG24-1",'
    '"bizScenario":"专线质差","faultTime":"2026-05-11T08:21:46Z","eventSerialNo":"event-id-20260511-09013"}}'
)

#: Validate-step response whose two schema-required parameters stay null (the paramsAbsent probes).
VALIDATE_PARTIAL_RESPONSE = (
    '{"semantic_verdict":true,"errors":[],"params":{"accessPort":null,"bizScenario":null,'
    '"faultTime":"2026-05-11T08:21:46Z","eventSerialNo":"event-id-20260511-09013"}}'
)

#: Validate-step response of a semantically contradictory prompt (the semantic-reject probes).
VALIDATE_REJECT_RESPONSE = (
    '{"semantic_verdict":false,"errors":[{"slot_name":"bizScenario","code":"content.semantic_conflict",'
    '"facts":{"section_label":"投诉分类","reason":"投诉分类声明为专线质差，投诉详情却描述完全中断"}}],"params":{}}'
)

#: Hand-authored prompt following the Task-T private-line template structure (the validate input).
VALIDATE_PROMPT = """## 任务类型(Task Type)
传输专线业务投诉诊断

## 任务描述(Task Description)
基于<任务对象>、<任务上下文> 进行投诉场景的网络侧故障根因诊断, 达成<任务目标>中定义的投诉诊断目标，按照<预期输出>中定义的结构返回任务处理结果。

## 任务目标(Task Target)
对网络侧故障进行诊断，返回故障根因和修复建议等诊断结果信息。

## 任务对象(Task Object)
接入端口名称：P533-珠江旧城-PTN3900-23-TPA1EG24-1

## 任务上下文(Task Context)
1. 投诉分类：“专线质差”
2. 问题发生时间：“2026-05-11T08:21:46Z”
3. OSS侧事件流水号：“event-id-20260511-09013”
4. 投诉详情：“从5月11号早上8点半开始，深圳访问广州的响应延迟从平均12ms骤升至320ms，柜面和手机银行的交易接口频繁报‘连接超时’。”

## 预期输出(Expected Output)
要求投诉诊断任务的结果包含如下信息：
1. 诊断结果；参数的取值范围包括：成功、失败；(必选)
2. 诊断结果详细信息； (必选)
3. 修复建议； (可选)"""


class StepStubClient:
    """Step-playing stub client: it answers the canned response (or re-raises the canned
    exception) of the next script step and repeats the last step once the script is exhausted —
    the offline stand-in for the live endpoint."""

    def __init__(self, steps: list[Any]) -> None:
        """Carry the canned steps: a response string or an exception instance per step."""
        self._steps = list(steps)
        self._cursor = 0

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Answer the canned step of the next call."""
        step = self._steps[min(self._cursor, len(self._steps) - 1)]
        self._cursor += 1
        if isinstance(step, Exception):
            raise step
        return LLMResponse(content=step, model="stub-model", usage={}, metadata={})


@pytest.fixture
def transcript_run(tmp_path: Path) -> Any:
    """A transcript run under a temporary root."""
    return LiveTranscript.create_run(tmp_path)


def _complaint_params_schema() -> dict[str, Any]:
    """The real shared complaint schema of the corpus, so the tests judge against the actual
    validate step."""
    schemas = json.loads((CORPUS_ROOT / "shared" / "schemas.json").read_text(encoding="utf-8"))
    return schemas["biz.complaint.params"]


def _generate_case(expect: LiveExpectation) -> LiveCase:
    """One generate-API live case of the engine test fixture."""
    return LiveCase(
        id="LIVE-TEST-GEN/zh-CN",
        base_id="LIVE-TEST-GEN",
        source_file="live/task-apis.json",
        api=NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT,
        language="zh-CN",
        tags=["live"],
        live_expect=expect,
        priority="P0",
        summary="engine test case of the generate API",
        template_uri="Task-T/network-layer/private-line-complaint/v1",
        input_text=(
            "深圳访问广州的政企专线自5月11日起时延骤升，柜面交易频繁超时。"
            "接入端口名称：P533-珠江旧城-PTN3900-23-TPA1EG24-1；投诉分类：专线质差。"
        ),
        schema=_complaint_params_schema(),
    )


def _validate_case(expect: LiveExpectation) -> LiveCase:
    """One validate-API live case of the engine test fixture."""
    return LiveCase(
        id="LIVE-TEST-VAL/zh-CN",
        base_id="LIVE-TEST-VAL",
        source_file="live/task-apis.json",
        api=NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING,
        language="zh-CN",
        tags=["live"],
        live_expect=expect,
        priority="P0",
        summary="engine test case of the validate API",
        template_uri="Task-T/network-layer/private-line-complaint/v1",
        prompt=PromptSource.Text(VALIDATE_PROMPT),
        schema=_complaint_params_schema(),
    )


def _engine(steps: list[Any], run: Any, infra_retries: int = 2) -> LiveCaseEngine:
    """The engine seam around a step-playing stub client (no endpoint, scripted minimal env)."""
    return LiveCaseEngine.for_testing(StepStubClient(steps), infra_retries, None, run)


def test_the_env_bridge_drives_the_configured_run_path(tmp_path: Path) -> None:
    """The real ``LiveLlmEnvWriter`` bridge loads through the SDK config and drives the same
    verdict as the scripted minimal env — the offline proof of the configured-run wiring."""
    run = LiveTranscript.create_run(tmp_path)
    config = LiveLlmConfig("https://live.example.test/v1", "live-key", "qwen3-27b", "0", "60")
    engine = LiveCaseEngine.for_testing(
        StepStubClient([SLOT_EXTRACTION_RESPONSE, VALIDATE_PASS_RESPONSE]),
        2,
        env_file_for(config),
        run,
    )
    test_case = _generate_case(
        LiveExpectation(
            success=True,
            scenario_code="private-line-complaint",
            params_contains={"bizScenario": "专线质差"},
            params_absent=[],
            prompt_text_contains=["## 任务类型"],
            max_llm_calls=4,
        )
    )

    result = engine.run(test_case)

    assert result.outcome is Outcome.PASS
    assert result.scenario_code == "private-line-complaint"
    assert len(result.llm_calls) == 2


# --------------------------------------------------------------------- generate


def test_generate_case_runs_the_closed_loop_and_asserts_the_key_fields(transcript_run: Any) -> None:
    engine = _engine([SLOT_EXTRACTION_RESPONSE, VALIDATE_PASS_RESPONSE], transcript_run)
    test_case = _generate_case(
        LiveExpectation(
            success=True,
            scenario_code="private-line-complaint",
            params_contains={
                "accessPort": "P533-珠江旧城-PTN3900-23-TPA1EG24-1",
                "bizScenario": "专线质差",
            },
            params_absent=[],
            prompt_text_contains=["## 任务类型", "event-id-20260511-09013"],
            max_llm_calls=4,
        )
    )

    result = engine.run(test_case)

    assert result.outcome is Outcome.PASS
    assert result.scenario_code == "private-line-complaint"
    assert "深圳访问广州的政企专线" in (result.input_summary or ""), "the verdict carries the input summary"
    assert result.params is not None and result.params["accessPort"] == "P533-珠江旧城-PTN3900-23-TPA1EG24-1"
    # The generation case is a mini closed loop: one slot-extraction call plus one validate call.
    assert len(result.llm_calls) == 2
    summary = transcript_run.summary()
    assert summary.total_cases == 1
    assert summary.pass_count == 1
    assert summary.total_llm_calls == 2


# --------------------------------------------------------------------- validate


def test_validate_case_asserts_params_contains_and_params_absent(transcript_run: Any) -> None:
    engine = _engine([VALIDATE_PARTIAL_RESPONSE], transcript_run)
    test_case = _validate_case(
        LiveExpectation(
            success=True,
            params_contains={
                "faultTime": "2026-05-11T08:21:46Z",
                "eventSerialNo": "event-id-20260511-09013",
            },
            params_absent=["accessPort", "bizScenario"],
            prompt_text_contains=[],
            max_llm_calls=3,
        )
    )

    result = engine.run(test_case)

    assert result.outcome is Outcome.PASS
    assert len(result.llm_calls) == 1
    assert result.params is not None and result.params["accessPort"] is None
    assert result.params["eventSerialNo"] == "event-id-20260511-09013"
    assert transcript_run.summary().pass_count == 1


def test_expected_failure_verdict_passes_when_the_pipeline_rejects(transcript_run: Any) -> None:
    engine = _engine([VALIDATE_REJECT_RESPONSE], transcript_run)
    test_case = _validate_case(
        LiveExpectation(success=False, params_contains={}, params_absent=[], prompt_text_contains=[], max_llm_calls=3)
    )

    result = engine.run(test_case)

    assert result.outcome is Outcome.PASS
    assert len(result.llm_calls) == 1
    assert result.params is None
    assert transcript_run.summary().pass_count == 1


# --------------------------------------------------------------------- mismatches


def test_assertion_mismatch_fails_with_the_case_diff(transcript_run: Any) -> None:
    engine = _engine([SLOT_EXTRACTION_RESPONSE, VALIDATE_PASS_RESPONSE], transcript_run)
    test_case = _generate_case(
        LiveExpectation(
            success=True,
            params_contains={"accessPort": "P999-不存在-PTN0000-0-TPA1EG24-0"},
            params_absent=[],
            prompt_text_contains=[],
            max_llm_calls=4,
        )
    )

    with pytest.raises(AssertionError) as failure:
        engine.run(test_case)

    message = str(failure.value)
    assert test_case.error_prefix() in message
    assert "$.expect.paramsContains" in message
    assert transcript_run.summary().fail_count == 1, "the failed verdict still lands in the transcript"


def test_max_llm_calls_is_an_upper_bound(transcript_run: Any) -> None:
    engine = _engine([SLOT_EXTRACTION_RESPONSE, VALIDATE_PASS_RESPONSE], transcript_run)
    test_case = _generate_case(
        LiveExpectation(
            success=True,
            params_contains={"bizScenario": "专线质差"},
            params_absent=[],
            prompt_text_contains=[],
            max_llm_calls=1,
        )
    )

    with pytest.raises(AssertionError) as failure:
        engine.run(test_case)

    assert "$.expect.maxLlmCalls" in str(failure.value)
    assert "at most 1 LLM calls" in str(failure.value)


def test_unbounded_llm_calls_when_the_record_omits_the_limit(transcript_run: Any) -> None:
    """A hand-built expectation without ``max_llm_calls`` means unbounded (loader defaults it)."""
    engine = _engine([SLOT_EXTRACTION_RESPONSE, VALIDATE_PASS_RESPONSE], transcript_run)
    test_case = _generate_case(
        LiveExpectation(
            success=True,
            params_contains={"bizScenario": "专线质差"},
            params_absent=[],
            prompt_text_contains=[],
            max_llm_calls=None,
        )
    )

    assert engine.run(test_case).outcome is Outcome.PASS


# --------------------------------------------------------------------- infra retries


def test_infrastructure_failures_retry_on_fresh_clients(transcript_run: Any) -> None:
    engine = _engine(
        [LLMRuntimeError("endpoint unreachable"), SLOT_EXTRACTION_RESPONSE, VALIDATE_PASS_RESPONSE],
        transcript_run,
    )
    test_case = _generate_case(
        LiveExpectation(
            success=True,
            params_contains={"bizScenario": "专线质差"},
            params_absent=[],
            prompt_text_contains=[],
            max_llm_calls=4,
        )
    )

    result = engine.run(test_case)

    assert result.outcome is Outcome.PASS
    # The failed attempt's call is kept in the transcript while the judged attempt made the two
    # closed-loop calls.
    assert len(result.llm_calls) == 3
    assert transcript_run.summary().pass_count == 1
    assert transcript_run.summary().total_llm_calls == 3


def test_exhausted_infrastructure_retries_record_an_error_and_stay_red(transcript_run: Any) -> None:
    engine = _engine([LLMRuntimeError("endpoint unreachable")], transcript_run, infra_retries=0)
    test_case = _generate_case(
        LiveExpectation(
            success=True,
            params_contains={"bizScenario": "专线质差"},
            params_absent=[],
            prompt_text_contains=[],
            max_llm_calls=4,
        )
    )

    with pytest.raises(Exception) as thrown:  # noqa: B017, PT011 - the pipeline wraps the outage
        engine.run(test_case)

    assert _is_infrastructure_failure(thrown.value), (
        f"the pipeline's LLM-infrastructure wrapping must stay classifiable: {thrown.value}"
    )

    # The transcript verdict matches the test verdict: the re-raised failure is an ERROR, not a
    # skip.
    assert transcript_run.summary().error_count == 1, "the given-up case is recorded as ERROR"
    assert transcript_run.summary().total_llm_calls == 1


def test_semantic_rejections_never_retry(transcript_run: Any) -> None:
    """A semantic rejection is a verdict, not an outage: zero retries, one recorded call."""
    engine = _engine([VALIDATE_REJECT_RESPONSE], transcript_run)
    test_case = _validate_case(
        LiveExpectation(success=False, params_contains={}, params_absent=[], prompt_text_contains=[], max_llm_calls=3)
    )

    assert engine.run(test_case).outcome is Outcome.PASS
    assert transcript_run.summary().total_llm_calls == 1


# --------------------------------------------------------------------- retry limit


def test_infra_retry_limit_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(INFRA_RETRIES_VARIABLE, raising=False)
    assert _infra_retry_limit() == DEFAULT_INFRA_RETRIES

    monkeypatch.setenv(INFRA_RETRIES_VARIABLE, "5")
    assert _infra_retry_limit() == 5

    monkeypatch.setenv(INFRA_RETRIES_VARIABLE, "-3")
    assert _infra_retry_limit() == 0, "negative values clamp to zero"

    monkeypatch.setenv(INFRA_RETRIES_VARIABLE, "not-a-number")
    assert _infra_retry_limit() == DEFAULT_INFRA_RETRIES, "garbage falls back to the default"


def test_the_step_stub_satisfies_the_llm_client_protocol() -> None:
    """The step stub satisfies the structural LLM client seam the recording wrapper delegates to."""
    assert isinstance(StepStubClient([SLOT_EXTRACTION_RESPONSE]), LLMClient)
