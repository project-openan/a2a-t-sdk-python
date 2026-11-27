"""Langfuse-derived columns of the tester arbitration sheet.

Each run's nine cells come from the vendored ``langfuse_export`` package: ``export_full``
fetches the trace observations and derives the same metrics the observe console uses. The
export is fail-open — a missing trace id, an unavailable project, a network error or an
unreadable metrics document yields nine empty cells plus one note on the report row, never an
exception that would abort the workbook.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from langfuse_export.cli import export_full
from langfuse_export.config import Config

#: The nine columns appended after ``备注`` in the arbitration worksheet.
TRACE_COLUMN_HEADERS: Final[tuple[str, ...]] = (
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

TRACE_COLUMN_WIDTHS: Final[tuple[int, ...]] = (12, 14, 12, 12, 12, 14, 12, 14, 14)

#: Header comments explaining a column whose number has a second, different definition elsewhere
#: in the sheet. ``None`` leaves the header without a comment.
TRACE_COLUMN_NOTES: Final[tuple[str | None, ...]] = (
    "Langfuse tokens.input（Gross：含缓存；含 a2a-t-server 侧用量）",
    "Langfuse tokens.cachedInput（输入 token 的子集，不重复相加）",
    "Langfuse tokens.output（客户端与服务端合计）",
    "Langfuse tokens.total = input + output；与 sheet 中客户端口径的「总token」不同源",
    "服务端 INPUT_REQUIRED、客户端协商事件或 run 元数据任一存在即为「是」",
    "Σ(S3 + S4 + S8 + S7)，只统计完整轮次；纯 A2A 用服务端 SendMessage 时长和",
    "Langfuse 协商轮次（服务端 INPUT_REQUIRED 与客户端事件取最大值）",
    "taskCreatedAt − receivedAt；只到建任务，不是整条用例耗时",
    "每个「Task-T validation blocked」span 计一次；同一 run 纠正后重试不合并",
)

_TRACE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{32}$")

_EMPTY_ROW: Final[tuple[str, ...]] = ("",) * len(TRACE_COLUMN_HEADERS)


def build_trace_columns(
    config: Config | None,
    *,
    run_id: str,
    trace_id: str,
    out_root: Path,
) -> tuple[list[object], str]:
    """Return the nine Langfuse cells of one run plus an explanatory note.

    ``trace_id`` is the OTel/Langfuse trace id (``run.langfuse["traceId"]``), not the
    vendor-facing trace id of the first report column. A failure returns nine empty cells and
    the reason to append to the row's ``备注``; this function never raises.
    """
    if config is None:
        return list(_EMPTY_ROW), _failure("未配置 Langfuse 凭据")
    if not trace_id:
        return list(_EMPTY_ROW), _failure("缺少 Langfuse trace id")
    if not _TRACE_ID_PATTERN.match(trace_id):
        return list(_EMPTY_ROW), _failure("Langfuse trace id 格式无效")
    if not run_id:
        return list(_EMPTY_ROW), _failure("缺少 run id，无法定位 Langfuse 导出目录")
    try:
        result = export_full(
            config,
            trace_id,
            out_root,
            zip_file=False,
            run_id=run_id,
            expand_metadata=["detail"],
        )
    except Exception as exc:  # noqa: BLE001 - a report export must never fail on observability
        return list(_EMPTY_ROW), _failure(f"{type(exc).__name__}: {exc}")
    metrics = result.get("metrics") if isinstance(result, Mapping) else None
    if not isinstance(metrics, Mapping):
        warnings = result.get("warnings") if isinstance(result, Mapping) else None
        detail = "；".join(str(item) for item in warnings) if isinstance(warnings, list) else ""
        return list(_EMPTY_ROW), _failure(detail or "导出结果缺少 metrics")
    return _values(metrics), ""


def _values(metrics: Mapping[str, Any]) -> list[object]:
    """Map one metrics document onto the nine cells; a missing number is blank, not zero."""
    tokens = _map(metrics.get("tokens"))
    negotiation = _map(metrics.get("negotiation"))
    latency = _map(metrics.get("latency"))
    interception = _map(metrics.get("clientInterception"))
    rounds = _int(negotiation.get("rounds"))
    return [
        _int(tokens.get("input"), 0),
        _int(tokens.get("cachedInput"), 0),
        _int(tokens.get("output"), 0),
        _int(tokens.get("total"), 0),
        "" if rounds is None else ("是" if rounds > 0 else "否"),
        _or_blank(_int(negotiation.get("latencyMs"))),
        _or_blank(rounds),
        _or_blank(_int(latency.get("endToEndMs"))),
        _int(interception.get("count"), 0),
    ]


def _map(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int(value: object, default: int | None = None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _or_blank(value: int | None) -> object:
    return "" if value is None else value


def _failure(reason: str) -> str:
    text = " ".join(str(reason).split())
    if len(text) > 200:
        text = text[:200] + "…"
    return f"Langfuse 指标未取到：{text}"
