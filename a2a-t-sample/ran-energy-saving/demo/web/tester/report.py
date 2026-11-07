"""XLSX export of one tester batch.

Excel is no longer an input; it remains a convenient report format for the test team. The report
has one row per batch item, one row per stage difference, and one summary sheet.
"""

from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from typing import Any, Final

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

_STAGE_ORDER: Final[tuple[str, ...]] = (
    "intent",
    "task_t",
    "validate",
    "send",
    "negotiate",
    "final",
)


def write_report(batch: Mapping[str, Any]) -> bytes:
    """Build the XLSX report of one batch snapshot."""
    workbook = Workbook()
    results = workbook.active
    results.title = "用例结果"
    headers = (
        "用例编号",
        "用例名称",
        "协议",
        "执行状态",
        "耗时(ms)",
        "Token 总量",
        "Run ID",
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
                *[
                    (stage_map.get(stage, {}).get("status", "") if isinstance(stage_map.get(stage), Mapping) else "")
                    for stage in _STAGE_ORDER
                ],
                item.get("error", ""),
            ]
        )
    _set_widths(results, (12, 42, 10, 10, 12, 12, 34, 12, 12, 12, 12, 14, 12, 40))

    diffs = workbook.create_sheet("阶段差异")
    diffs.append(["用例编号", "协议", "阶段", "状态", "字段", "预期", "实际", "说明"])
    _style_header(diffs)
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        stages = item.get("stages")
        stage_map = stages if isinstance(stages, Mapping) else {}
        for stage_name, stage in stage_map.items():
            if not isinstance(stage, Mapping):
                continue
            raw_diffs = stage.get("diffs")
            if isinstance(raw_diffs, list) and raw_diffs:
                for diff in raw_diffs:
                    if not isinstance(diff, Mapping):
                        continue
                    diffs.append(
                        [
                            item.get("caseId", ""),
                            item.get("protocol", ""),
                            stage_name,
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
                        stage_name,
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
    summary.append(["batchId", batch.get("batchId", "")])
    _set_widths(summary, (28, 36))

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


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
