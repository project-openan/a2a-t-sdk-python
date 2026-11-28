"""Tests of the caller-schema values read back from a rendered Task-T."""

from __future__ import annotations

from protocol.task_t_readback import read_task_t_fields

_EN_TASK_T = """## Operation Type
Create

## Task Type
RAN energy saving

## Task Description
Based on the object and context.

## Task Object
A2ATestRegion

## Task Target
Maximize energy saving.

## Task Context
Site list: ["NES_27A_321071", "NRES_XCS_6102937"],
Time-segmented rate guarantee target: {08:00-12:00, 10Mbps}, Background event: power outage from 08:00 to 12:00.

## Expected Output
Output the intent report.
"""

_ZH_TASK_T = """## 操作类型
创建

## 任务类型
RAN 节能

## 任务描述
基于对象与上下文。

## 任务对象
A2ATestRegion

## 任务目标
在任务上下文条件下最大化节能。

## 任务上下文
站点列表：["NES_27A_321071"], 分时段速率保障目标：{08:00-12:00, 10Mbps}, 背景事件：08:00 至 12:00 停电。

## 预期输出
输出意图报告。
"""


def test_reads_the_english_sections_and_labelled_context() -> None:
    fields = read_task_t_fields(_EN_TASK_T)

    assert fields["operationType"] == "Create"
    assert fields["region"] == "A2ATestRegion"
    assert fields["energySavingGoal"] == "Maximize energy saving."
    assert fields["siteList"] == "NES_27A_321071, NRES_XCS_6102937"
    assert fields["guaranteedThroughputTarget"] == "{08:00-12:00,10Mbps}"
    assert fields["backgroundEvent"] == "power outage from 08:00 to 12:00."


def test_reads_the_chinese_sections_and_labelled_context() -> None:
    fields = read_task_t_fields(_ZH_TASK_T)

    assert fields["operationType"] == "创建"
    assert fields["region"] == "A2ATestRegion"
    assert fields["energySavingGoal"] == "在任务上下文条件下最大化节能。"
    assert fields["siteList"] == "NES_27A_321071"
    assert fields["guaranteedThroughputTarget"] == "{08:00-12:00,10Mbps}"
    assert fields["backgroundEvent"] == "08:00 至 12:00 停电。"


def test_a_rate_outside_the_labelled_context_is_still_found() -> None:
    prompt = """## Operation Type
Create

## Task Object
A2ATestRegion

## Task Context
The target is 08:00 to 12:00, 10Mbps.
"""

    fields = read_task_t_fields(prompt)

    assert fields["guaranteedThroughputTarget"] == "{08:00-12:00,10Mbps}"


def test_a_phrase_rate_target_is_kept_verbatim() -> None:
    prompt = """## Operation Type
Create

## Task Object
A2ATestRegion

## Task Context
Rate guarantee target: {00:00-22:00, Ensuring user experience for 720p video.}
"""

    fields = read_task_t_fields(prompt)

    # The phrase has no numeric rate, so the rate grammar cannot canonicalize it; the accept
    # filter decides whether it can travel as a Task-T value.
    assert fields["guaranteedThroughputTarget"].startswith("{")


def test_an_empty_or_missing_prompt_reads_nothing() -> None:
    assert read_task_t_fields("") == {}
    assert read_task_t_fields("no sections here") == {}


def test_a_missing_rate_and_site_list_are_simply_absent() -> None:
    prompt = """## Operation Type
Modify

## Task Object
A2ATestRegion

## Task Target
Maximize energy saving.
"""

    fields = read_task_t_fields(prompt)

    assert fields == {
        "operationType": "Modify",
        "region": "A2ATestRegion",
        "energySavingGoal": "Maximize energy saving.",
    }
