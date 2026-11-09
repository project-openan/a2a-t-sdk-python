"""Tests of the arbitration worksheet of the tester XLSX report."""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any

from demo.web.tester.report import write_report
from openpyxl import load_workbook

#: Expected column order of the arbitration worksheet.
REFERENCE_HEADERS: tuple[str, ...] = (
    "traceId",
    "协议",
    "用例编号",
    "用例名称",
    "任务类型",
    "QA_ID",
    "UID",
    "设计人",
    "问题",
    "原始请求",
    "标准化请求",
    "意图理解结果(结构化JSON)",
    "意图来源",
    "期望意图",
    "answer与reference差异点",
    "被测模型生成答案",
    "仲裁人-EVAL-额外结果-origin_answer",
    "仲裁人-EVAL-额外结果-执行轨迹",
    "仲裁人-EVAL-额外结果-任务状态",
    "仲裁人-EVAL-额外结果-端到端时延(ms)",
    "协商轮次",
    "协商触发",
    "协商成功",
    "仲裁人-EVAL-额外结果-输出token",
    "仲裁人-EVAL-额外结果-总token",
    "执行状态",
    "自动评分",
    "最终得分",
    "汇总结果",
    "仲裁得分",
    "人工标记结果",
    "备注",
)


def _snapshot() -> dict[str, Any]:
    intent = {"Operation type": "Create", "Energy saving region": "A2ATestRegion"}
    answer = {
        "code": "SUCCESS",
        "message": "异步任务已创建",
        "finalResult": "Task creation succeeded",
        "negotiationSummary": {"missingItems": [], "filledParams": {}, "negotiationRounds": 0},
        "exchanges": [
            {"round": 1, "direction": "OUT", "messageType": "Task-T"},
            {"round": 1, "direction": "IN", "messageType": "RESULT"},
            {"round": 0, "direction": "IN", "messageType": "TASK_STATE_SUBMITTED"},
        ],
        "traceparent": "00-" + "b" * 32 + "-" + "0" * 15 + "1-01",
        "taskId": "task-1",
    }
    return {
        "schema": "osp-tester/v3",
        "batchId": "batch-1",
        "status": "finished",
        "decisionMode": "scripted",
        "transport": "sse",
        "discovery": "direct",
        "summary": {"total": 1, "passed": 1},
        "items": [
            {
                "caseId": 1,
                "name": "速率保障目标：视频码率720p",
                "protocol": "a2a-t",
                "status": "passed",
                "stages": {
                    "intent": {
                        "stage": "intent",
                        "status": "passed",
                        "expected": intent,
                        "actual": intent,
                    },
                    "final": {"stage": "final", "status": "passed", "actual": "task_completed"},
                    "negotiate": {
                        "stage": "negotiate",
                        "status": "passed",
                        "actual": {"rounds": 0, "kinds": []},
                    },
                },
                "runId": "a" * 32,
                "durationMs": 60163,
                "tokens": {"output": 0, "total": 0},
                "error": None,
                "traceId": "b" * 32,
                "intent": intent,
                "intentMeta": {
                    "rawRequest": "Implement energy-saving in A2ATestRegion",
                    "normalizedText": "Implement energy-saving in A2ATestRegion",
                    "source": "client",
                },
                "refs": {
                    "caseName": "速率保障目标：视频码率720p",
                    "operation": "Create",
                    "designer": "徐峰",
                    "qaId": "TC00000001",
                    "uid": "",
                    "taskT": "## Operation Type\nCreate",
                    "userInput": "Implement energy-saving in A2ATestRegion",
                    "expectedIntent": intent,
                    "negotiationExpected": False,
                    "expectedRounds": 0,
                    "expectedKinds": [],
                    "expectedFinalState": None,
                },
                "answer": answer,
            }
        ],
    }


def _reference_sheet(batch: dict[str, Any]) -> list[tuple[object, ...]]:
    workbook = load_workbook(BytesIO(write_report(batch)))
    assert workbook.sheetnames == ["仲裁格式", "用例结果", "阶段差异", "汇总"]
    return list(workbook["仲裁格式"].iter_rows(values_only=True))


def test_the_arbitration_sheet_comes_first_and_matches_the_reference_headers() -> None:
    rows = _reference_sheet(_snapshot())

    assert rows[0] == REFERENCE_HEADERS


def test_the_arbitration_row_carries_the_intent_diff_and_answer() -> None:
    rows = _reference_sheet(_snapshot())
    row = rows[1]

    assert row[0] == "b" * 32
    assert row[1] == "a2a-t"
    assert row[2] == 1
    assert row[5] == "TC00000001"
    assert row[7] == "徐峰"
    assert row[11] is not None
    assert json.loads(str(row[11]))["Energy saving region"] == "A2ATestRegion"
    assert row[12] == "client"
    assert json.loads(str(row[13]))["Operation type"] == "Create"
    assert row[14] == "完全一致"
    assert json.loads(str(row[15]))["code"] == "SUCCESS"
    assert json.loads(str(row[16]))["taskId"] == "task-1"
    assert row[17] == (
        "### Round 1 | Client | Task-T\n\n"
        "### Round 0 | Server | TASK_STATE_SUBMITTED"
    )
    assert row[18] == "SUCCESS"
    assert row[19] == 60163
    assert row[20] == 0
    assert row[21] == "false"
    assert row[22] == "true"
    assert row[26] == 10
    assert row[27] == 10
    assert row[28] == "通过"
    assert row[29] in (None, "")
    assert row[30] in (None, "")


def test_a_pure_a2a_row_leaves_the_intent_columns_blank_and_says_so() -> None:
    batch = _snapshot()
    item = batch["items"][0]
    item["protocol"] = "a2a"
    item["intent"] = None
    item["intentMeta"]["source"] = ""
    item["stages"].pop("intent")

    rows = _reference_sheet(batch)
    row = rows[1]

    assert row[11] in (None, "")
    assert row[14] in (None, "")
    assert "纯 A2A 未采集 SDK 意图" in str(row[31])


def test_a_v2_item_without_export_fields_still_exports() -> None:
    batch = _snapshot()
    item = batch["items"][0]
    for key in ("traceId", "intent", "intentMeta", "refs", "answer"):
        item.pop(key)
    item.pop("stages")

    rows = _reference_sheet(batch)
    row = rows[1]

    assert row[0] in (None, "")
    assert row[11] in (None, "")
    assert row[15] in (None, "")
    assert "本批次未采集意图" in str(row[31])


def _results_sheet(batch: dict[str, Any]) -> list[tuple[object, ...]]:
    workbook = load_workbook(BytesIO(write_report(batch)))
    return list(workbook["用例结果"].iter_rows(values_only=True))


def _with_interception() -> dict[str, Any]:
    batch = _snapshot()
    item = batch["items"][0]
    item["interception"] = {
        "input": "ambiguous request",
        "followupInput": "clarified request",
        "stages": {
            "intent": {"stage": "intent", "status": "recorded"},
            "task_t": {"stage": "task_t", "status": "recorded"},
            "validate": {
                "stage": "validate",
                "status": "passed",
                "expected": "blocked",
                "actual": "blocked",
            },
        },
    }
    return batch


def test_the_results_sheet_carries_the_interception_columns() -> None:
    rows = _results_sheet(_with_interception())
    headers = rows[0]
    row = rows[1]

    assert row[headers.index("拦截校验")] == "passed: blocked"
    assert row[headers.index("拦截后补充信息")] == "clarified request"


def test_the_stage_diff_sheet_labels_interception_stages() -> None:
    batch = _with_interception()
    batch["items"][0]["interception"]["stages"]["validate"]["diffs"] = [
        {"path": "blocked", "expected": "blocked", "actual": "pass", "reason": "test"}
    ]

    workbook = load_workbook(BytesIO(write_report(batch)))
    rows = list(workbook["阶段差异"].iter_rows(values_only=True))

    assert any(row[2] == "拦截/validate" for row in rows)
