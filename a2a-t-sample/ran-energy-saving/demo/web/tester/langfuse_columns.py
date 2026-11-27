"""Langfuse-derived columns of the tester arbitration sheet.

Each run's nine cells come from the vendored ``langfuse_export`` package: ``export_full``
fetches the trace observations and derives the same metrics the observe console uses. The
export is fail-open — a missing trace id, an unavailable project, a network error or an
unreadable metrics document yields nine empty cells plus one note on the report row, never an
exception that would abort the workbook.

A settled result is cached under ``runs/<runId>/langfuse-export/metrics.json`` so a second
export (and the tester's background prefetch) never re-spends Langfuse quota on the same run.
A fetch taken too soon after the run finished is cached as *unsettled* and refreshed on the
next export; a 429 starts a process-wide cooldown instead of burning the per-trace budget.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from langfuse_export.cli import export_full
from langfuse_export.config import Config

#: File name of the per-run metrics cache inside ``runs/<runId>/langfuse-export``.
METRICS_CACHE_FILENAME: Final[str] = "metrics.json"

#: Seconds a Langfuse 429 pauses every fetch (the public API admits 30 requests/minute).
RATE_LIMIT_COOLDOWN_SECONDS: Final[float] = 60.0

#: Wall-clock monotonic deadline of the shared 429 cooldown.
_cooldown_until: float = 0.0
_cooldown_lock: Final = threading.Lock()

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
    settled: bool = True,
) -> tuple[list[object], str]:
    """Return the nine Langfuse cells of one run plus an explanatory note.

    ``trace_id`` is the OTel/Langfuse trace id (``run.langfuse["traceId"]``), not the
    vendor-facing trace id of the first report column. A failure returns nine empty cells and
    the reason to append to the row's ``备注``; this function never raises.

    A settled cache under ``out_root`` is served without a request. ``settled`` marks whether
    the fetch happened long enough after the run finished for Langfuse to hold every span; an
    unsettled result is cached but never served, so the next export refreshes it.
    """
    if config is None:
        return list(_EMPTY_ROW), _failure("未配置 Langfuse 凭据")
    if not trace_id:
        return list(_EMPTY_ROW), _failure("缺少 Langfuse trace id")
    if not _TRACE_ID_PATTERN.match(trace_id):
        return list(_EMPTY_ROW), _failure("Langfuse trace id 格式无效")
    if not run_id:
        return list(_EMPTY_ROW), _failure("缺少 run id，无法定位 Langfuse 导出目录")
    cached = _read_cache(out_root, trace_id)
    if cached is not None:
        return cached
    remaining = _cooldown_remaining()
    if remaining > 0:
        return list(_EMPTY_ROW), _failure(f"Langfuse 限流冷却中（剩余 {remaining} 秒）")
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
        note = _failure(f"{type(exc).__name__}: {exc}")
        _note_rate_limit(note)
        return list(_EMPTY_ROW), note
    metrics = result.get("metrics") if isinstance(result, Mapping) else None
    if not isinstance(metrics, Mapping):
        warnings = result.get("warnings") if isinstance(result, Mapping) else None
        detail = "；".join(str(item) for item in warnings) if isinstance(warnings, list) else ""
        note = _failure(detail or "导出结果缺少 metrics")
        _note_rate_limit(note)
        return list(_EMPTY_ROW), note
    _write_cache(out_root, trace_id, run_id, metrics, settled=settled)
    return _values(metrics), ""


def _cache_path(out_root: Path) -> Path:
    """Return the metrics cache file of one run."""
    return Path(out_root) / METRICS_CACHE_FILENAME


def _read_cache(out_root: Path, trace_id: str) -> tuple[list[object], str] | None:
    """Return the cached cells when a settled cache belongs to this trace, else ``None``."""
    path = _cache_path(out_root)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, Mapping):
        return None
    if str(document.get("traceId") or "") != trace_id or document.get("settled") is not True:
        return None
    metrics = document.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    return _values(metrics), ""


def _write_cache(
    out_root: Path,
    trace_id: str,
    run_id: str,
    metrics: Mapping[str, Any],
    *,
    settled: bool,
) -> None:
    """Write one run's metrics cache atomically; a cache failure never fails the export."""
    document = {
        "traceId": trace_id,
        "runId": run_id,
        "settled": settled,
        "fetchedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "metrics": metrics,
    }
    path = _cache_path(out_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, default=str), encoding="utf-8"
        )
        temporary.replace(path)
    except OSError:
        # The cache is an optimization; an unwritable run directory must not fail the export.
        return


def _cooldown_remaining() -> int:
    """Return the whole seconds left of the shared 429 cooldown, or ``0``."""
    with _cooldown_lock:
        remaining = _cooldown_until - time.monotonic()
    return max(0, int(remaining))


def _note_rate_limit(note: str) -> None:
    """Start the shared cooldown when a failure is a Langfuse 429."""
    lowered = note.lower()
    if "429" not in lowered and "rate limit" not in lowered and "rate_limit" not in lowered:
        return
    global _cooldown_until
    with _cooldown_lock:
        _cooldown_until = time.monotonic() + RATE_LIMIT_COOLDOWN_SECONDS


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
