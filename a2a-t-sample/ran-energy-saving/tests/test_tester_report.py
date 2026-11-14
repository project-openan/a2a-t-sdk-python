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
    "SDK抽取意图",
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
    #: The SDK's own pre-send extraction, deliberately different from the server payload.
    sdk_intent = {"Operation type": "Create", "Energy saving region": "SDKRegion"}
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
                "intent": sdk_intent,
                "serverIntent": intent,
                "intentMeta": {
                    "rawRequest": "Implement energy-saving in A2ATestRegion",
                    "normalizedText": "Implement energy-saving in A2ATestRegion",
                    "source": "server",
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
    assert json.loads(str(row[11]))["Energy saving region"] == "A2ATestRegion"
    assert json.loads(str(row[12]))["Energy saving region"] == "SDKRegion"
    assert row[13] == "server"
    assert json.loads(str(row[14]))["Operation type"] == "Create"
    assert row[15] == "完全一致"
    assert json.loads(str(row[16]))["code"] == "SUCCESS"
    assert json.loads(str(row[17]))["taskId"] == "task-1"
    assert row[18] == (
        "### Round 1 | Client | Task-T\n\n"
        "### Round 0 | Server | TASK_STATE_SUBMITTED"
    )
    assert row[19] == "SUCCESS"
    assert row[20] == 60163
    assert row[21] == 0
    assert row[22] == "false"
    assert row[23] == "true"
    assert row[27] == 10
    assert row[28] == 10
    assert row[29] == "通过"
    assert row[30] in (None, "")
    assert row[31] in (None, "")


def test_a_pure_a2a_row_leaves_the_intent_columns_blank_and_says_so() -> None:
    batch = _snapshot()
    item = batch["items"][0]
    item["protocol"] = "a2a"
    item["intent"] = None
    item["serverIntent"] = None
    item["intentMeta"]["source"] = ""
    item["stages"].pop("intent")

    rows = _reference_sheet(batch)
    row = rows[1]

    assert row[11] in (None, "")
    assert row[12] in (None, "")
    assert row[15] in (None, "")
    assert "纯 A2A 未采集服务端意图" in str(row[32])


def test_a_row_without_a_server_payload_keeps_the_sdk_column_and_says_why() -> None:
    batch = _snapshot()
    item = batch["items"][0]
    item["serverIntent"] = None

    rows = _reference_sheet(batch)
    row = rows[1]

    assert row[11] in (None, "")
    assert json.loads(str(row[12]))["Energy saving region"] == "SDKRegion"
    assert row[15] in (None, "")
    assert "下游未返回结构化意图" in str(row[32])


def test_a_validate_only_row_does_not_blame_the_downstream() -> None:
    batch = _snapshot()
    item = batch["items"][0]
    item["serverIntent"] = None
    item["answer"] = None

    rows = _reference_sheet(batch)
    note = str(rows[1][32])

    assert "未执行发送阶段" in note
    assert "下游未返回结构化意图" not in note


def test_a_v2_item_without_export_fields_still_exports() -> None:
    batch = _snapshot()
    item = batch["items"][0]
    for key in ("traceId", "intent", "serverIntent", "intentMeta", "refs", "answer"):
        item.pop(key)
    item.pop("stages")

    rows = _reference_sheet(batch)
    row = rows[1]

    assert row[0] in (None, "")
    assert row[11] in (None, "")
    assert row[16] in (None, "")
    assert "本批次未采集意图" in str(row[32])


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


def _with_baseline(status: str = "failed") -> dict[str, Any]:
    batch = _snapshot()
    item = batch["items"][0]
    item["operation"] = "Modify"
    item["baseline"] = {
        "runId": "c" * 32,
        "protocol": "a2a-t",
        "status": status,
        "taskId": None if status == "failed" else "task-base",
        "contextId": "ctx-base",
        "state": "TASK_STATE_INPUT_REQUIRED" if status == "failed" else "TASK_STATE_SUBMITTED",
        "error": (
            "Modify 基线 Create 触发了协商（TASK_STATE_INPUT_REQUIRED），不是可续传状态。"
            if status == "failed"
            else None
        ),
    }
    return batch


def test_the_results_sheet_carries_the_baseline_columns() -> None:
    """A baseline failure must be diagnosable from the export, not only from the live drawer."""
    rows = _results_sheet(_with_baseline())
    headers, row = rows[0], rows[1]

    assert row[headers.index("基线状态")] == "failed"
    assert row[headers.index("基线 Run ID")] == "c" * 32
    assert row[headers.index("基线 taskId")] in (None, "")
    assert row[headers.index("基线 state")] == "TASK_STATE_INPUT_REQUIRED"
    assert "协商" in str(row[headers.index("基线错误")])


def test_the_arbitration_note_names_a_langfuse_export_failure() -> None:
    batch = _snapshot()
    batch["items"][0]["langfuseWarning"] = (
        "Langfuse ingestion rejected (HTTP 403): suspended"
    )

    rows = _reference_sheet(batch)

    assert "Langfuse 导出失败" in str(rows[1][32])
    assert "HTTP 403" in str(rows[1][32])


def test_the_arbitration_note_names_a_failed_baseline() -> None:
    """The arbitration sheet is the comparison view; a baseline failure must not look silent."""
    rows = _reference_sheet(_with_baseline())

    assert "Modify 基线 Create 失败" in str(rows[1][32])


def test_the_stage_diff_sheet_labels_interception_stages() -> None:
    batch = _with_interception()
    batch["items"][0]["interception"]["stages"]["validate"]["diffs"] = [
        {"path": "blocked", "expected": "blocked", "actual": "pass", "reason": "test"}
    ]

    workbook = load_workbook(BytesIO(write_report(batch)))
    rows = list(workbook["阶段差异"].iter_rows(values_only=True))

    assert any(row[2] == "拦截/validate" for row in rows)


def _multi_round_snapshot() -> dict[str, Any]:
    """Two independent passes of one item; the second one failed."""
    batch = _snapshot()
    first = dict(batch["items"][0])
    second = json.loads(json.dumps(first))
    first["round"] = 1
    second["round"] = 2
    second["status"] = "failed"
    second["runId"] = "d" * 32
    batch["items"] = [first, second]
    batch["rounds"] = 2
    batch["currentRound"] = 2
    batch["roundSummaries"] = [
        {"round": 1, "summary": {"total": 1, "passed": 1}},
        {"round": 2, "summary": {"total": 1, "failed": 1}},
    ]
    batch["crossRound"] = {"stable": 0, "total": 1}
    return batch


def test_the_multi_round_report_adds_the_round_column_and_summary_sheet() -> None:
    workbook = load_workbook(BytesIO(write_report(_multi_round_snapshot())))

    assert workbook.sheetnames == [
        "仲裁格式",
        "用例结果",
        "阶段差异",
        "汇总",
        "轮次汇总",
    ]
    reference = list(workbook["仲裁格式"].iter_rows(values_only=True))
    assert reference[0][:3] == ("traceId", "协议", "轮次")
    assert [row[2] for row in reference[1:]] == [1, 2]

    results = list(workbook["用例结果"].iter_rows(values_only=True))
    assert results[0][3] == "轮次"
    assert [row[3] for row in results[1:]] == [1, 2]

    diffs = list(workbook["阶段差异"].iter_rows(values_only=True))
    assert diffs[0][2] == "轮次"

    summary = dict(list(workbook["汇总"].iter_rows(values_only=True)))
    assert summary["rounds"] == 2

    rounds = list(workbook["轮次汇总"].iter_rows(values_only=True))
    assert rounds[0][0] == "轮次"
    assert [row[0] for row in rounds[1:3]] == [1, 2]


def test_a_single_round_report_keeps_the_existing_columns() -> None:
    rows = _reference_sheet(_snapshot())

    assert rows[0] == REFERENCE_HEADERS
    assert len(rows[0]) == len(REFERENCE_HEADERS)
