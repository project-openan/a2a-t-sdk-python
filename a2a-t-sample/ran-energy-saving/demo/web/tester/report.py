"""XLSX export of one tester batch.

Excel is no longer an input; it remains a convenient report format for the test team. The first
sheet is the arbitration view the test team compares with the reference workbook; the original
result / stage-difference / summary sheets follow it and stay the troubleshooting view.

The arbitration sheet is built from the data collected while the batch ran: the SDK intent, the
frozen case reference, the run answer and the trace id. Nothing here reads a run directory.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from io import BytesIO
from typing import Any, Final

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from demo.web.tester.export_view import (
    render_intent_diff,
    render_trajectory,
    result_label,
    score_for,
)

_STAGE_ORDER: Final[tuple[str, ...]] = (
    "intent",
    "task_t",
    "validate",
    "send",
    "negotiate",
    "accepted",
    "working",
    "final",
)

#: Column order of the arbitration worksheet (the reference workbook's naming).
_REFERENCE_HEADERS: Final[tuple[str, ...]] = (
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

#: Excel's hard limit of one cell's text length.
_MAX_CELL_CHARS: Final[int] = 32000

#: Note written when an item never produced a run answer (validate-only batch).
_NO_ANSWER_NOTE: Final[str] = "未执行发送阶段"
#: Note written when the downstream accepted the Task without returning its reading.
_NO_SERVER_INTENT_NOTE: Final[str] = "下游未返回结构化意图"
#: Note written when the SDK intent stage did not run (pure A2A has no SDK stages).
_NO_SERVER_INTENT_PURE_NOTE: Final[str] = "纯 A2A 未采集服务端意图"
#: Note written when an old batch has no intent data at all.
_NO_INTENT_NOTE: Final[str] = "本批次未采集意图"
#: Note written when the traceparent could not be read from the wire log.
_REBUILT_TRACE_NOTE: Final[str] = "traceparent 为重建值"


def write_report(batch: Mapping[str, Any]) -> bytes:
    """Build the XLSX report of one batch snapshot."""
    workbook = Workbook()
    reference = workbook.active
    reference.title = "仲裁格式"
    _write_reference_sheet(reference, batch)
    results = workbook.create_sheet("用例结果")
    headers = (
        "用例编号",
        "用例名称",
        "协议",
        "执行状态",
        "耗时(ms)",
        "Token 总量",
        "Run ID",
        "决策模式",
        "传输",
        "发现",
        "拦截校验",
        "拦截后补充信息",
        *[f"{stage} 状态" for stage in _STAGE_ORDER],
        "错误信息",
    )
    results.append(list(headers))
    _style_header(results)
    items = batch.get("items")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        stages = item.get("stages")
        stage_map = stages if isinstance(stages, Mapping) else {}
        tokens = item.get("tokens")
        token_map = tokens if isinstance(tokens, Mapping) else {}
        results.append(
            [
                item.get("caseId", ""),
                item.get("name", ""),
                item.get("protocol", ""),
                item.get("status", ""),
                item.get("durationMs", ""),
                token_map.get("total", ""),
                item.get("runId", ""),
                batch.get("decisionMode", ""),
                batch.get("transport", ""),
                batch.get("discovery", ""),
                _interception_verdict(item),
                _interception_followup(item),
                *[
                    (stage_map.get(stage, {}).get("status", "") if isinstance(stage_map.get(stage), Mapping) else "")
                    for stage in _STAGE_ORDER
                ],
                item.get("error", ""),
            ]
        )
    _set_widths(
        results,
        (12, 42, 10, 10, 12, 12, 34, 10, 10, 10, 14, 48, 12, 12, 12, 12, 14, 12, 12, 12, 40),
    )

    diffs = workbook.create_sheet("阶段差异")
    diffs.append(["用例编号", "协议", "阶段", "状态", "字段", "预期", "实际", "说明"])
    _style_header(diffs)
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        stages = item.get("stages")
        stage_map = stages if isinstance(stages, Mapping) else {}
        sources: list[tuple[str, Mapping[str, object]]] = []
        interception = item.get("interception")
        blocked = interception.get("stages") if isinstance(interception, Mapping) else None
        if isinstance(blocked, Mapping):
            sources.append(("拦截/", blocked))
        sources.append(("", stage_map))
        for prefix, stage_source in sources:
            for stage_name, stage in stage_source.items():
                if not isinstance(stage, Mapping):
                    continue
                label = f"{prefix}{stage_name}"
                raw_diffs = stage.get("diffs")
                if isinstance(raw_diffs, list) and raw_diffs:
                    for diff in raw_diffs:
                        if not isinstance(diff, Mapping):
                            continue
                        diffs.append(
                            [
                                item.get("caseId", ""),
                                item.get("protocol", ""),
                                label,
                                stage.get("status", ""),
                                diff.get("path", ""),
                                _text(diff.get("expected")),
                                _text(diff.get("actual")),
                                diff.get("reason", ""),
                            ]
                        )
                elif stage.get("message"):
                    diffs.append(
                        [
                            item.get("caseId", ""),
                            item.get("protocol", ""),
                            label,
                            stage.get("status", ""),
                            "",
                            _text(stage.get("expected")),
                            _text(stage.get("actual")),
                            stage.get("message", ""),
                        ]
                    )
    _set_widths(diffs, (12, 10, 12, 10, 30, 48, 48, 34))

    summary = workbook.create_sheet("汇总")
    summary.append(["指标", "值"])
    _style_header(summary)
    raw_summary = batch.get("summary")
    summary_map = raw_summary if isinstance(raw_summary, Mapping) else {}
    for key, value in summary_map.items():
        summary.append([key, value])
    summary.append(["stopAfter", batch.get("stopAfter", "")])
    summary.append(["approval", batch.get("approval", "")])
    summary.append(["decisionMode", batch.get("decisionMode", "")])
    summary.append(["transport", batch.get("transport", "")])
    summary.append(["discovery", batch.get("discovery", "")])
    summary.append(["batchId", batch.get("batchId", "")])
    _set_widths(summary, (28, 36))

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_reference_sheet(sheet: Worksheet, batch: Mapping[str, Any]) -> None:
    """Write the arbitration worksheet: one row per batch item, reference column order."""
    sheet.append(list(_REFERENCE_HEADERS))
    _style_header(sheet)
    items = batch.get("items")
    for item in items if isinstance(items, list) else []:
        if isinstance(item, Mapping):
            sheet.append([_cell(value) for value in _reference_row(item)])
    _set_widths(
        sheet,
        (
            34, 8, 10, 42, 10, 12, 36, 10, 60, 48, 48, 60, 60, 10, 60, 60, 60, 60, 50,
            16, 14, 10, 10, 10, 12, 12, 10, 10, 10, 10, 10, 12, 40,
        ),
    )


def _reference_row(item: Mapping[str, Any]) -> list[object]:
    """Build one arbitration row from the data collected while the item ran."""
    refs = _map_of(item.get("refs"))
    intent_meta = _map_of(item.get("intentMeta"))
    stages = _map_of(item.get("stages"))
    intent_stage = _map_of(stages.get("intent"))
    # The stage ``actual`` is the server payload once the Task is accepted, so it must not be
    # used as a fallback for the SDK column; ``item.intent`` is the SDK document.
    sdk_intent = item.get("intent")
    if not isinstance(sdk_intent, Mapping):
        sdk_intent = None
    server_intent = item.get("serverIntent")
    if not isinstance(server_intent, Mapping):
        server_intent = None
    expected_intent = refs.get("expectedIntent")
    if not isinstance(expected_intent, Mapping):
        expected_intent = intent_stage.get("expected")
    answer = _map_of(item.get("answer"))
    exchanges = answer.get("exchanges")
    token_map = _map_of(item.get("tokens"))
    status = str(item.get("status") or "")
    raw_request = intent_meta.get("rawRequest") or refs.get("userInput") or ""
    case_id = item.get("caseId")
    return [
        str(item.get("traceId") or ""),
        str(item.get("protocol") or ""),
        case_id if case_id != 0 else "",
        refs.get("caseName") or item.get("name", ""),
        refs.get("operation", ""),
        refs.get("qaId", ""),
        refs.get("uid", ""),
        refs.get("designer", ""),
        refs.get("taskT", ""),
        raw_request,
        intent_meta.get("normalizedText") or raw_request,
        _json_cell(server_intent, indent=2),
        _json_cell(sdk_intent, indent=2),
        intent_meta.get("source", ""),
        _json_cell(expected_intent, indent=2),
        render_intent_diff(
            expected_intent if isinstance(expected_intent, Mapping) else None,
            server_intent,
        ),
        _json_cell(answer, indent=2),
        _json_cell(answer, indent=None),
        render_trajectory(exchanges if isinstance(exchanges, list) else []),
        _task_state(item, answer),
        item.get("durationMs", ""),
        _negotiation_rounds(answer, stages),
        _bool_cell(refs.get("negotiationExpected")),
        _negotiation_success(item, refs, answer),
        token_map.get("output", ""),
        token_map.get("total", ""),
        status,
        _score_cell(status),
        _score_cell(status),
        result_label(status),
        "",
        "",
        _reference_note(item, refs, intent_meta, answer, server_intent, sdk_intent),
    ]


def _reference_note(
    item: Mapping[str, Any],
    refs: Mapping[str, Any],
    intent_meta: Mapping[str, Any],
    answer: Mapping[str, Any],
    server_intent: Mapping[str, Any] | None,
    sdk_intent: object,
) -> str:
    """Explain the empty arbitration cells of one item."""
    notes: list[str] = []
    if item.get("scratch") is not None:
        notes.append("临时请求")
    elif not refs:
        notes.append("本批次未采集用例元数据")
    if item.get("scratch") is None:
        if not refs:
            notes.append(_NO_INTENT_NOTE)
        elif server_intent is None and answer:
            # A validate-only batch never sent anything; ``_NO_ANSWER_NOTE`` says that instead of
            # blaming a downstream that was never asked.
            if str(item.get("protocol") or "") == "a2a":
                notes.append(_NO_SERVER_INTENT_PURE_NOTE)
            else:
                notes.append(_NO_SERVER_INTENT_NOTE)
        if not isinstance(sdk_intent, Mapping):
            notes.append("SDK 抽取意图未采集")
    if not answer:
        notes.append(_NO_ANSWER_NOTE)
    if intent_meta.get("traceparentSource") == "reconstructed":
        notes.append(_REBUILT_TRACE_NOTE)
    return "；".join(notes)


def _task_state(item: Mapping[str, Any], answer: Mapping[str, Any]) -> str:
    """Return the Task state shown by the reference workbook."""
    code = answer.get("code")
    if isinstance(code, str) and code:
        return code
    stages = _map_of(item.get("stages"))
    final = _map_of(stages.get("final"))
    if final.get("actual") == "task_completed":
        return "SUCCESS"
    accepted = _map_of(stages.get("accepted"))
    state = accepted.get("actual")
    if isinstance(state, Mapping) and state.get("state"):
        return str(state["state"]).removeprefix("TASK_STATE_")
    return str(item.get("status") or "")


def _negotiation_rounds(answer: Mapping[str, Any], stages: Mapping[str, Any]) -> object:
    """Return the observed negotiation round count."""
    summary = _map_of(answer.get("negotiationSummary"))
    rounds = summary.get("negotiationRounds")
    if isinstance(rounds, int):
        return rounds
    negotiate = _map_of(stages.get("negotiate"))
    actual = _map_of(negotiate.get("actual"))
    return actual.get("rounds", "")


def _negotiation_success(
    item: Mapping[str, Any],
    refs: Mapping[str, Any],
    answer: Mapping[str, Any],
) -> str:
    """Return whether the request reached its goal, ``""`` while it is not judged yet."""
    if not refs.get("negotiationExpected"):
        return "true"
    if answer.get("code") == "SUCCESS":
        return "true"
    status = str(item.get("status") or "")
    if status in {"failed", "error", "cancelled"}:
        return "false"
    return ""


def _bool_cell(value: object) -> str:
    """Render a bool as the workbook's ``true`` / ``false`` text."""
    return "true" if value else "false"


def _score_cell(status: str) -> int | str:
    """Render the automatic score of one status, empty when not judged."""
    score = score_for(status)
    return "" if score is None else score


def _json_cell(value: object, *, indent: int | None) -> str:
    """Render one object cell as JSON text, empty when the value is absent."""
    if not isinstance(value, Mapping) or not value:
        return ""
    if indent is None:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(value, ensure_ascii=False, indent=indent)


def _map_of(value: object) -> dict[str, Any]:
    """Return a shallow dict copy of a JSON object from the batch snapshot."""
    return {str(key): item for key, item in value.items()} if isinstance(value, Mapping) else {}


def _cell(value: object) -> object:
    """Truncate one cell to Excel's limit so an oversized origin_answer never fails the export."""
    if isinstance(value, str) and len(value) > _MAX_CELL_CHARS:
        return value[:_MAX_CELL_CHARS] + "…（已截断）"
    return value


def _interception_verdict(item: Mapping[str, Any]) -> str:
    """Return ``<status>: <detail>`` of the interception validation, or an empty cell."""
    record = item.get("interception")
    if not isinstance(record, Mapping):
        return ""
    stages = record.get("stages")
    validate = stages.get("validate") if isinstance(stages, Mapping) else None
    if not isinstance(validate, Mapping):
        return ""
    status = str(validate.get("status") or "")
    detail = str(validate.get("message") or "") or _text(validate.get("actual"))
    return f"{status}: {detail}".strip(": ")


def _interception_followup(item: Mapping[str, Any]) -> str:
    """Return the replacement request a blocked case was continued with, if any."""
    record = item.get("interception")
    return str(record.get("followupInput") or "") if isinstance(record, Mapping) else ""


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple):
        return "\n".join(_text(item) for item in value)
    if isinstance(value, Mapping):
        return "\n".join(f"{key}: {_text(item)}" for key, item in value.items())
    return str(value)


def _style_header(sheet: Worksheet) -> None:
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="FFF1E4")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.freeze_panes = "A2"


def _set_widths(sheet: Worksheet, widths: tuple[int, ...]) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
