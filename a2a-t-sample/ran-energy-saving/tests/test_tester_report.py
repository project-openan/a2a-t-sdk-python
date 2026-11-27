"""Tests of the arbitration worksheet of the tester XLSX report."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
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
    "交互报文",
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
    "判定原因",
    "仲裁得分",
    "人工标记结果",
    "备注",
    "输入token",
    "输入缓存token",
    "输出token",
    "总token",
    "是否触发协商",
    "协商时延(ms)",
    "协商轮次",
    "端到端时延(ms)",
    "客户端拦截次数",
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
    # Without a runs directory the interaction column says why it is empty; the answer stays in Q.
    assert row[17] == "导出时未提供 run 目录，无法读取交互报文。"
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
    assert row[32] in (None, "")
    assert row[33] in (None, "")


def test_a_partial_row_names_the_stage_that_left_it_unproven() -> None:
    batch = _snapshot()
    item = batch["items"][0]
    item["status"] = "partial"
    item["needsReview"] = True
    item["intentVerdict"] = {"residual": [{"path": "intent.Energy saving time"}]}
    item["stages"]["intent"] = {
        "stage": "intent",
        "status": "recorded",
        "message": "没有可用的独立判定模型，转人工复核",
    }

    rows = _reference_sheet(batch)
    headers = list(rows[0])
    reason = str(rows[1][headers.index("判定原因")])

    assert "intent: 没有可用的独立判定模型，转人工复核" in reason
    assert rows[1][headers.index("汇总结果")] == "待定"


def test_an_error_row_carries_the_run_error_next_to_the_summary() -> None:
    batch = _snapshot()
    item = batch["items"][0]
    item["status"] = "error"
    item["error"] = "NmsProtocolError: HTTP 500"
    item["stages"]["accepted"] = {
        "stage": "accepted",
        "status": "error",
        "message": "NmsProtocolError: HTTP 500",
    }

    rows = _reference_sheet(batch)
    headers = list(rows[0])

    assert rows[1][headers.index("汇总结果")] == "错误"
    assert rows[1][headers.index("判定原因")] == "NmsProtocolError: HTTP 500"


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
    assert "纯 A2A 未采集服务端意图" in str(row[33])


def test_a_row_without_a_server_payload_keeps_the_sdk_column_and_says_why() -> None:
    batch = _snapshot()
    item = batch["items"][0]
    item["serverIntent"] = None

    rows = _reference_sheet(batch)
    row = rows[1]

    assert row[11] in (None, "")
    assert json.loads(str(row[12]))["Energy saving region"] == "SDKRegion"
    assert row[15] in (None, "")
    assert "下游未返回结构化意图" in str(row[33])


def test_a_validate_only_row_does_not_blame_the_downstream() -> None:
    batch = _snapshot()
    item = batch["items"][0]
    item["serverIntent"] = None
    item["answer"] = None

    rows = _reference_sheet(batch)
    note = str(rows[1][33])

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
    assert "本批次未采集意图" in str(row[33])


def _write_wire(run_dir: Path, name: str, records: list[dict[str, Any]]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / name).write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _wire_cell(tmp_path: Path, batch: dict[str, Any]) -> str:
    workbook = load_workbook(BytesIO(write_report(batch, runs_dir=tmp_path)))
    rows = list(workbook["仲裁格式"].iter_rows(values_only=True))
    return str(rows[1][17])


def test_the_interaction_column_prints_every_raw_frame(tmp_path: Path) -> None:
    """A reviewer (or a model) must see the request, the SSE reply and the webhook push verbatim."""
    batch = _snapshot()
    run_id = str(batch["items"][0]["runId"])
    _write_wire(
        tmp_path / run_id,
        "wire.jsonl",
        [
            {
                "ts": "2026-11-26T01:00:00.000Z",
                "dir": "out",
                "method": "POST",
                "url": "http://nms.test/a2a/json/message:stream",
                "headers": {"Content-Type": "application/json", "Authorization": "***"},
                "body": {"message": {"messageId": "m-1", "parts": [{"text": "create task"}]}},
            },
            {
                "ts": "2026-11-26T01:00:00.100Z",
                "dir": "in",
                "method": "POST",
                "url": "http://nms.test/a2a/json/message:stream",
                "status": 200,
                "headers": {"content-type": "text/event-stream"},
                "elapsedMs": 12,
                "stream": True,
            },
            {
                "ts": "2026-11-26T01:00:00.200Z",
                "dir": "in",
                "line": 'data: {"kind": "status", "state": "TASK_STATE_SUBMITTED"}',
            },
            {
                "ts": "2026-11-26T01:00:01.000Z",
                "dir": "in",
                "source": "webhook",
                "dropped": "duplicate",
                "body": {"taskId": "t-1", "status": {"state": "WORKING"}},
            },
        ],
    )

    cell = _wire_cell(tmp_path, batch)

    assert cell.startswith(f"run {run_id} | 4 帧 | wire.jsonl / wire-oss.jsonl / wire-error.jsonl")
    assert "[1] 2026-11-26T01:00:00.000Z OUT POST http://nms.test/a2a/json/message:stream" in cell
    assert '"messageId":"m-1"' in cell
    assert "IN 200 POST http://nms.test/a2a/json/message:stream 12ms stream" in cell
    assert 'line: data: {"kind": "status", "state": "TASK_STATE_SUBMITTED"}' in cell
    assert "IN webhook (dropped: duplicate)" in cell
    assert '"taskId":"t-1"' in cell


def test_the_trajectory_column_summarises_the_wire_frames(tmp_path: Path) -> None:
    """The trajectory answers "who said what" without a reader parsing the raw interaction cell."""
    batch = _snapshot()
    run_id = str(batch["items"][0]["runId"])
    _write_wire(
        tmp_path / run_id,
        "wire.jsonl",
        [
            {
                "ts": "2026-11-26T01:00:00.000Z",
                "dir": "out",
                "method": "POST",
                "url": "http://nms.test/a2a/json/message:stream",
                "body": {
                    "message": {
                        "parts": [{"text": "create task"}],
                        "metadata": {
                            "https://example/Task-T/v1": "## Operation Type\nCreate"
                        },
                    }
                },
            },
            {
                "ts": "2026-11-26T01:00:00.200Z",
                "dir": "in",
                "line": (
                    'data: {"task": {"status": {"state": "TASK_STATE_SUBMITTED", '
                    '"message": {"parts": [{"text": "Task creation succeeded{\\"a\\":1}"}]}}}}'
                ),
            },
            {
                "ts": "2026-11-26T01:00:01.000Z",
                "dir": "in",
                "source": "webhook",
                "dropped": "duplicate",
                "body": {"taskId": "t-1", "status": {"state": "WORKING"}},
            },
        ],
    )

    workbook = load_workbook(BytesIO(write_report(batch, runs_dir=tmp_path)))
    rows = list(workbook["仲裁格式"].iter_rows(values_only=True))

    assert rows[1][18] == (
        "R1 Client Task-T | ## Operation Type Create\n"
        "R1 Server SUBMITTED | Task creation succeeded"
    )


def test_a_rejected_frame_is_included_from_the_error_log(tmp_path: Path) -> None:
    """A frame the SDK rejected never reaches wire.jsonl; the column must still show it."""
    batch = _snapshot()
    run_id = str(batch["items"][0]["runId"])
    _write_wire(
        tmp_path / run_id,
        "wire.jsonl",
        [{"ts": "2026-11-26T01:00:00.000Z", "dir": "out", "method": "POST",
          "url": "http://nms.test/a2a/json/message:send", "body": {"message": {}}}],
    )
    _write_wire(
        tmp_path / run_id,
        "wire-error.jsonl",
        [{
            "ts": "2026-11-26T01:00:02.000Z",
            "type": "sdk_rejected",
            "message": "content.param_missing",
            "status": 400,
            "headers": {"x-reason": "bad"},
            "frame": {"message": {"parts": []}},
        }],
    )

    cell = _wire_cell(tmp_path, batch)

    assert "[2] 2026-11-26T01:00:02.000Z ERROR sdk_rejected: content.param_missing" in cell
    assert "status: 400" in cell
    assert 'headers: {"x-reason":"bad"}' in cell
    assert 'frame: {"message":{"parts":[]}}' in cell


def test_an_oversized_transcript_keeps_every_frame_on_the_fallback_sheet(tmp_path: Path) -> None:
    """Excel caps one cell at 32767 characters; the tail must move to its own sheet, not vanish."""
    batch = _snapshot()
    run_id = str(batch["items"][0]["runId"])
    body = "x" * 40000
    _write_wire(
        tmp_path / run_id,
        "wire.jsonl",
        [
            {"ts": "2026-11-26T01:00:00.000Z", "dir": "out", "method": "POST",
             "url": "http://nms.test/a2a/json/message:send",
             "body": {"message": {"parts": [{"text": body}]}}},
            {"ts": "2026-11-26T01:00:01.000Z", "dir": "in", "source": "webhook",
             "body": {"ok": True}},
        ],
    )

    workbook = load_workbook(BytesIO(write_report(batch, runs_dir=tmp_path)))
    rows = list(workbook["仲裁格式"].iter_rows(values_only=True))
    cell = str(rows[1][17])

    assert len(cell) <= 32000
    assert "已截断" in cell and "「交互报文」工作表" in cell
    wire_rows = list(workbook["交互报文"].iter_rows(values_only=True))
    assert wire_rows[0] == ("用例编号", "协议", "轮次", "Run ID", "帧号", "分段", "交互报文")
    assert wire_rows[1][4] == 1 and wire_rows[1][5] == "1/2"
    assert wire_rows[2][5] == "2/2"
    assert wire_rows[3][4] == 2
    assert body in "".join(str(row[6]) for row in wire_rows[1:])


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


def test_the_results_sheet_marks_a_skipped_followup() -> None:
    batch = _with_interception()
    interception = batch["items"][0]["interception"]
    interception["followupSkipped"] = True

    rows = _results_sheet(batch)
    headers = rows[0]

    assert rows[1][headers.index("拦截后补充信息")] == "（未执行）clarified request"


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

    assert "Langfuse 导出失败" in str(rows[1][33])
    assert "HTTP 403" in str(rows[1][33])


def test_the_arbitration_note_names_a_failed_baseline() -> None:
    """The arbitration sheet is the comparison view; a baseline failure must not look silent."""
    rows = _reference_sheet(_with_baseline())

    assert "Modify 基线 Create 失败" in str(rows[1][33])


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


#: One client decision-quality appendix of a negotiation item (P2).
CLIENT_QUALITY: dict[str, Any] = {
    "quality": "fail",
    "method": "model",
    "needsHumanReview": True,
    "reason": "the accepted rate is 5Mbps instead of 2Mbps",
    "rounds": [
        {
            "round": 1,
            "proposal": "Reduce the throughput target to 5Mbps.",
            "expected": "Reduce throughput target from 10Mbps to 2Mbps.",
            "decision": "accept",
            "action": "pass",
            "value": "fail",
            "structure": "pass",
            "review": "pass",
            "coverage": "pass",
            "quality": "fail",
            "method": "model",
            "reason": "the accepted rate is 5Mbps instead of 2Mbps",
        }
    ],
}


def _quality_sheet(batch: dict[str, Any]) -> list[tuple[object, ...]]:
    workbook = load_workbook(BytesIO(write_report(batch)))
    assert "client 决策质量" in workbook.sheetnames
    return list(workbook["client 决策质量"].iter_rows(values_only=True))


def test_the_client_quality_appendix_is_exported_as_its_own_sheet() -> None:
    batch = _snapshot()
    batch["items"][0]["clientQuality"] = CLIENT_QUALITY

    rows = _quality_sheet(batch)

    header = [str(cell) for cell in rows[0]]
    for column in (
        "用例编号",
        "协议",
        "协商轮次",
        "决策",
        "提案",
        "期望",
        "动作",
        "值",
        "结构",
        "复核",
        "覆盖",
        "质量",
        "人工复核",
        "判定方式",
        "理由",
    ):
        assert column in header
    row = dict(zip(header, rows[1], strict=False))
    assert row["用例编号"] == 1
    assert row["协议"] == "a2a-t"
    assert row["协商轮次"] == 1
    assert row["决策"] == "accept"
    assert row["值"] == "fail"
    assert row["质量"] == "fail"
    assert row["人工复核"] == "是"
    assert row["判定方式"] == "model"
    assert row["理由"] == "the accepted rate is 5Mbps instead of 2Mbps"


def test_the_agent_pure_reply_reference_is_exported_as_its_own_sheet() -> None:
    """The P column is not lost when the agent authors the A2A reply: it stays as reference."""
    batch = _snapshot()
    batch["negotiationSource"] = "agent"
    batch["items"][0]["protocol"] = "a2a"
    batch["items"][0]["refs"] = {
        "clientReplies": ["The region is A2ATestRegion.", "The target is 10Mbps."]
    }

    workbook = load_workbook(BytesIO(write_report(batch)))

    assert "客户端答复参考" in workbook.sheetnames
    rows = list(workbook["客户端答复参考"].iter_rows(values_only=True))
    assert rows[0] == ("用例编号", "协议", "轮次", "记录答复")
    assert rows[1] == (1, "a2a", 1, "The region is A2ATestRegion.")
    assert rows[2] == (1, "a2a", 2, "The target is 10Mbps.")


def test_a_script_batch_does_not_export_the_reply_reference() -> None:
    """The script source replays P; exporting it again would duplicate the case."""
    batch = _snapshot()
    batch["negotiationSource"] = "script"
    batch["items"][0]["refs"] = {"clientReplies": ["The region is A2ATestRegion."]}

    workbook = load_workbook(BytesIO(write_report(batch)))

    assert "客户端答复参考" not in workbook.sheetnames


def test_the_batch_plan_is_exported_as_its_own_sheet() -> None:
    """One batch's models, principles version and environment must travel with the report."""
    batch = _snapshot()
    batch["plan"] = {
        "protocols": ["a2a-t"],
        "environment": {"transport": "sse", "discovery": "nms", "nms": True, "nmsSim": False},
        "negotiationModel": "negotiation-model",
        "judgeModel": "judge-model",
        "principlesVersion": "2026-11-23.1",
        "nmsVersion": "NMS-1.2",
        "decisionSource": "agent",
        "stopAfter": "accepted",
        "rounds": 1,
    }

    workbook = load_workbook(BytesIO(write_report(batch)))

    assert "批次计划" in workbook.sheetnames
    rows = list(workbook["批次计划"].iter_rows(values_only=True))
    plan = {str(key): value for key, value in rows[1:]}
    assert plan["协商模型"] == "negotiation-model"
    assert plan["判定模型"] == "judge-model"
    assert plan["协商原则版本"] == "2026-11-23.1"
    assert plan["NMS 版本"] == "NMS-1.2"
    assert plan["协商答复来源"] == "agent"


def test_a_batch_without_client_quality_keeps_the_four_existing_sheets() -> None:
    workbook = load_workbook(BytesIO(write_report(_snapshot())))

    assert workbook.sheetnames == ["仲裁格式", "用例结果", "阶段差异", "汇总"]


def _trace_sheet(
    batch: dict[str, Any],
    trace_columns: dict[str, tuple[list[object], str]],
) -> tuple[list[tuple[object, ...]], Any]:
    workbook = load_workbook(BytesIO(write_report(batch, trace_columns=trace_columns)))
    return list(workbook["仲裁格式"].iter_rows(values_only=True)), workbook["仲裁格式"]


def test_the_langfuse_columns_follow_the_note_column() -> None:
    values: list[object] = [1261, 630, 2035, 3296, "是", 4200, 2, 9000, 1]
    rows, sheet = _trace_sheet(_snapshot(), {"a" * 32: (values, "")})

    row = rows[1]
    assert len(row) == len(REFERENCE_HEADERS)
    assert row[-9:] == tuple(values)
    assert rows[0][-9:] == REFERENCE_HEADERS[-9:]
    # The existing columns keep their letters and the note stays before the new block.
    assert row[33] is None
    assert sheet.cell(row=1, column=35).comment is not None
    assert "Gross" in str(sheet.cell(row=1, column=35).comment.text)


def test_a_trace_failure_joins_the_note_cell() -> None:
    values: list[object] = [""] * 9
    rows, __ = _trace_sheet(_snapshot(), {"a" * 32: (values, "Langfuse 指标未取到：boom")})

    assert rows[1][33] == "Langfuse 指标未取到：boom"
    assert rows[1][-9:] == (None,) * 9


def test_a_run_without_trace_columns_keeps_blank_cells() -> None:
    rows = _reference_sheet(_snapshot())

    assert rows[1][-9:] == (None,) * 9
    assert rows[1][33] is None


def test_the_old_columns_are_unchanged_when_langfuse_is_not_requested() -> None:
    plain = _reference_sheet(_snapshot())
    rows, __ = _trace_sheet(_snapshot(), {"a" * 32: ([""] * 9, "")})

    assert rows[1][:34] == plain[1][:34]


def test_a_multi_round_report_keeps_round_second_and_langfuse_last() -> None:
    values: list[object] = [1, 2, 3, 4, "否", "", 0, 5, 0]
    batch = _multi_round_snapshot()
    rows, __ = _trace_sheet(batch, {"a" * 32: (values, ""), "d" * 32: ([""] * 9, "缺失")})

    assert rows[0][:3] == ("traceId", "协议", "轮次")
    assert rows[0][-9:] == REFERENCE_HEADERS[-9:]
    assert [row[2] for row in rows[1:]] == [1, 2]
    assert rows[1][-9:] == (1, 2, 3, 4, "否", None, 0, 5, 0)
    assert rows[2][-10] == "缺失"


def test_every_langfuse_header_carries_its_definition() -> None:
    __, sheet = _trace_sheet(_snapshot(), {})

    commented = [cell.comment is not None for cell in sheet[1][-9:]]
    assert commented == [True] * 9
    assert "S3 + S4" in str(sheet.cell(row=1, column=40).comment.text)
