"""Pure CSV contract for the Langfuse observe page (design 2026-11-09 §5).

No I/O, network, or clock access: callers pass in already-fetched metrics.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

MAIN_COLUMNS: tuple[str, ...] = (
    "traceId",
    "runId",
    "caseId",
    "batchId",
    "chainId",
    "attemptCount",
    "attemptRunIds",
    "protocol",
    "scenario",
    "runMode",
    "mode",
    "modelMode",
    "sourceKind",
    "receivedAt",
    "sdkCalledAt",
    "requestSentAt",
    "taskCreatedAt",
    "prepareMs",
    "sdkMs",
    "dispatchMs",
    "endToEndMs",
    "input",
    "cachedInput",
    "output",
    "total",
    "client.input",
    "client.cachedInput",
    "client.output",
    "client.total",
    "server.input",
    "server.cachedInput",
    "server.output",
    "server.total",
    "cacheHitRate",
    "inputExcludingCached",
    "negotiationTriggered",
    "rounds",
    "negotiationLatencyMs",
    "serverDurationMs",
    "observations",
    "runObservations",
    "clientObservations",
    "serverObservations",
    "generations",
    "toolCalls",
    "warnings",
    "exportStatus",
    "status",
    "finalState",
    "passed",
    "accuracy",
    "startedAt",
    "finishedAt",
)

_IDENTITY_COLUMNS: tuple[str, ...] = (
    "runId",
    "caseId",
    "batchId",
    "chainId",
    "attemptCount",
    "attemptRunIds",
)


def _text(value: object) -> str:
    """Render one scalar as a plain string (None -> empty, bool -> lowercase)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _bool_text(value: object) -> str:
    """Render a truth flag as lowercase ``true`` / ``false`` when it is one."""
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "false"}:
            return lowered
    return _text(value)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _int_value(value: object) -> int | None:
    """Best-effort integer from a metrics scalar (ints stay exact, floats round)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            try:
                return int(round(float(text)))
            except ValueError:
                return None
    return None


def _int_text(value: object) -> str:
    number = _int_value(value)
    return "" if number is None else str(number)


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _stage_ms(start: object, end: object) -> str | None:
    start_at = _parse_iso(start)
    end_at = _parse_iso(end)
    if start_at is None or end_at is None:
        return None
    return str(int(round((end_at - start_at).total_seconds() * 1000)))


def build_row(
    trace_id: str,
    status: str,
    metrics: Mapping[str, object] | None,
    warnings: Sequence[str],
    local_run: Mapping[str, object] | None,
) -> dict[str, str]:
    """Render one trace row of ``langfuse-metrics.csv``."""
    row = dict.fromkeys(MAIN_COLUMNS, "")
    row["traceId"] = trace_id
    row["exportStatus"] = status
    if status != "ok" or metrics is None:
        row["warnings"] = "; ".join(warnings)
        for column in _IDENTITY_COLUMNS:
            if local_run is not None:
                row[column] = _text(local_run.get(column))
        return row

    derived_warnings: list[str] = []
    for column in _IDENTITY_COLUMNS:
        value = metrics.get(column)
        if column == "attemptRunIds" and isinstance(value, Sequence) and not isinstance(value, str):
            row[column] = ", ".join(_text(item) for item in value)
        else:
            row[column] = _text(value)
    for column in (
        "protocol",
        "scenario",
        "runMode",
        "mode",
        "modelMode",
        "sourceKind",
    ):
        row[column] = _text(metrics.get(column))

    timeline = _mapping(metrics.get("timeline"))
    for column in ("receivedAt", "sdkCalledAt", "requestSentAt", "taskCreatedAt"):
        row[column] = _text(timeline.get(column))

    stages = (
        ("prepareMs", "receivedAt", "sdkCalledAt"),
        ("sdkMs", "sdkCalledAt", "requestSentAt"),
        ("dispatchMs", "requestSentAt", "taskCreatedAt"),
    )
    for column, start_key, end_key in stages:
        value = _stage_ms(timeline.get(start_key), timeline.get(end_key))
        if value is None:
            derived_warnings.append(f"stage latency unavailable: {column}")
        row[column] = value or ""
    end_to_end = _int_text(_mapping(metrics.get("latency")).get("endToEndMs"))
    if not end_to_end:
        derived_warnings.append("stage latency unavailable: endToEndMs")
    row["endToEndMs"] = end_to_end

    tokens = _mapping(metrics.get("tokens"))
    input_tokens = _int_value(tokens.get("input")) or 0
    cached_tokens = _int_value(tokens.get("cachedInput")) or 0
    for column in ("input", "cachedInput", "output", "total"):
        row[column] = _int_text(tokens.get(column)) or "0"

    by_side = _mapping(tokens.get("bySide"))
    for side in ("client", "server"):
        side_tokens = by_side.get(side)
        if not isinstance(side_tokens, Mapping):
            continue
        for column in ("input", "cachedInput", "output", "total"):
            row[f"{side}.{column}"] = _int_text(side_tokens.get(column)) or "0"

    row["cacheHitRate"] = "" if input_tokens == 0 else f"{cached_tokens / input_tokens:.4f}"
    if cached_tokens > input_tokens:
        row["inputExcludingCached"] = "0"
        derived_warnings.append("cachedInput exceeds input; inputExcludingCached clamped to 0")
    else:
        row["inputExcludingCached"] = str(input_tokens - cached_tokens)

    negotiation = _mapping(metrics.get("negotiation"))
    row["negotiationTriggered"] = _bool_text(negotiation.get("triggered"))
    row["rounds"] = _int_text(negotiation.get("rounds"))
    row["negotiationLatencyMs"] = _int_text(negotiation.get("latencyMs"))
    row["serverDurationMs"] = _int_text(negotiation.get("serverDurationMs"))

    counts = _mapping(metrics.get("counts"))
    for column in (
        "observations",
        "runObservations",
        "clientObservations",
        "serverObservations",
        "generations",
        "toolCalls",
    ):
        row[column] = _int_text(counts.get(column))

    if local_run is not None:
        for column in ("status", "finalState", "startedAt", "finishedAt"):
            row[column] = _text(local_run.get(column))
        row["passed"] = _bool_text(local_run.get("passed"))
        accuracy = local_run.get("accuracy")
        row["accuracy"] = f"{accuracy:.4f}" if isinstance(accuracy, float) else _text(accuracy)

    row["warnings"] = "; ".join([*warnings, *derived_warnings])
    return row


def _write_csv(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def main_csv(rows: Sequence[Mapping[str, str]]) -> str:
    """Render the main metrics table (no BOM; the HTTP layer adds utf-8-sig)."""
    return _write_csv(
        MAIN_COLUMNS,
        [[row.get(column, "") for column in MAIN_COLUMNS] for row in rows],
    )


ROUNDS_COLUMNS: tuple[str, ...] = (
    "traceId",
    "round",
    "proposeAt",
    "notifiedAt",
    "notifiedAtSource",
    "negotiationStartedAt",
    "negotiationStartedAtSource",
    "clock",
    "decisionAt",
    "sdkConstructedAt",
    "replySentAt",
    "negotiationType",
    "hop",
)


def rounds_csv(
    metrics_list: Sequence[tuple[str, Mapping[str, object]]],
) -> str | None:
    """Expand ``negotiation.roundsDetail`` into one row per round, if any."""
    rows: list[list[str]] = []
    for trace_id, metrics in metrics_list:
        detail = _mapping(metrics.get("negotiation")).get("roundsDetail")
        if not isinstance(detail, (list, tuple)):
            continue
        for entry in detail:
            entry_map = _mapping(entry)
            rows.append(
                [
                    trace_id,
                    _int_text(entry_map.get("round")),
                    *(_text(entry_map.get(column)) for column in ROUNDS_COLUMNS[2:]),
                ]
            )
    if not rows:
        return None
    return _write_csv(ROUNDS_COLUMNS, rows)


_MISSING_CAVEAT = "空值 = 数据缺失，不猜测填充。"


@dataclass(frozen=True)
class FieldNote:
    """One row of ``fields.csv`` and one entry of the page legend."""

    column: str
    group: str
    meaning_zh: str
    source: str
    caveat: str


def _note(
    column: str,
    group: str,
    meaning_zh: str,
    source: str,
    caveat: str = "",
) -> FieldNote:
    detail = caveat.strip()
    if _MISSING_CAVEAT not in detail:
        detail = f"{detail} {_MISSING_CAVEAT}".strip() if detail else _MISSING_CAVEAT
    return FieldNote(
        column=column,
        group=group,
        meaning_zh=meaning_zh,
        source=source,
        caveat=detail,
    )


FIELD_NOTES: tuple[FieldNote, ...] = (
    _note(
        "traceId",
        "A 身份",
        "Langfuse trace id（32 位十六进制），主表与 rounds.csv 的关联键。",
        "URL 粘贴输入，或本地 run 的 runs.trace_id。",
        "非 ok 行也保留 traceId。",
    ),
    _note(
        "runId",
        "A 身份",
        "样例 run id。",
        "metrics.json 顶层 runId（trace root metadata）。",
    ),
    _note(
        "caseId",
        "A 身份",
        "OSP 用例 id，仅 tester 启动的 run 有。",
        "trace root metadata.caseId。",
    ),
    _note(
        "batchId",
        "A 身份",
        "tester 批量 id，仅批量启动的 run 有。",
        "trace root metadata.batchId。",
    ),
    _note(
        "chainId",
        "A 身份",
        "修正链 id，同一 Task-T 的多次重试共享。",
        "metadata.chainId，缺失时回退 trace.sessionId。",
    ),
    _note(
        "attemptCount",
        "A 身份",
        "同一条 trace 里的 attempt 数：被拦截的 preflight 与修正后的重试各算一次。",
        "metrics.json attemptCount，或本地 runs 按 trace_id 反查的行数。",
        "普通单 attempt trace 为 1。",
    ),
    _note(
        "attemptRunIds",
        "A 身份",
        "同一条 trace 内的全部 run id，按新到旧排列。",
        "metrics.json attemptRunIds，或本地 runs 按 trace_id 反查。",
        "token/时延列只来自选中的 attempt；其余 attempt 可在 Langfuse 树中查看。",
    ),
    _note(
        "protocol",
        "B 运行属性",
        "本次 run 使用的协议（a2a 或 a2a-t）。",
        "trace root metadata.protocol。",
    ),
    _note(
        "scenario",
        "B 运行属性",
        "业务场景名。",
        "trace root metadata.scenario。",
    ),
    _note(
        "runMode",
        "B 运行属性",
        "run 的启动模式。",
        "trace root metadata.runMode。",
    ),
    _note(
        "mode",
        "B 运行属性",
        "客户端执行模式（auto / manual 等）。",
        "trace root metadata.mode。",
    ),
    _note(
        "modelMode",
        "B 运行属性",
        "模型模式（live / offline / scripted）。",
        "trace root metadata.modelMode。",
    ),
    _note(
        "sourceKind",
        "B 运行属性",
        "入口来源（presentation / tester / gateway / api）。",
        "trace root metadata.sourceKind。",
    ),
    _note(
        "receivedAt",
        "C 时间点",
        "客户端收到请求的时间点。",
        "metadata.receivedAt，缺失时回退 trace opened span.startTime。",
    ),
    _note(
        "sdkCalledAt",
        "C 时间点",
        "开始渲染 Task-T / 调用 SDK 的时间点。",
        "task_prompt_rendered.startTime。",
    ),
    _note(
        "requestSentAt",
        "C 时间点",
        "首个 message_sent 发出的时间点。",
        "第一个 message_sent.startTime。",
    ),
    _note(
        "taskCreatedAt",
        "C 时间点",
        "下游 Task 创建完成的时间点。",
        "task_created.startTime。",
    ),
    _note(
        "prepareMs",
        "D 阶段耗时",
        "prepareMs = sdkCalledAt − receivedAt。",
        "由 C 组时间点在同一条 client trace 内做差，不跨时钟。",
        "任一端缺失或解析失败则留空并追加 warning；真机 NMS 的 statusUpdate 路径没有 propose span，AO/AP 可能为空。",
    ),
    _note(
        "sdkMs",
        "D 阶段耗时",
        "sdkMs = requestSentAt − sdkCalledAt。",
        "由 C 组时间点在同一条 client trace 内做差，不跨时钟。",
        "任一端缺失或解析失败则留空并追加 warning；真机 NMS 的 statusUpdate 路径没有 propose span，AO/AP 可能为空。",
    ),
    _note(
        "dispatchMs",
        "D 阶段耗时",
        "dispatchMs = taskCreatedAt − requestSentAt。",
        "由 C 组时间点在同一条 client trace 内做差，不跨时钟。",
        "任一端缺失或解析失败则留空并追加 warning。",
    ),
    _note(
        "endToEndMs",
        "D 阶段耗时",
        "端到端耗时，直接取 metrics.json 的 latency.endToEndMs，不重新计算。",
        "latency.endToEndMs（= taskCreatedAt − receivedAt）。",
        "缺失时留空并追加 warning。",
    ),
    _note(
        "input",
        "E Token",
        "provider 上报的输入 token 总数，包含缓存命中部分（input 含 cache）。",
        "tokens.input：usageDetails.input / 顶层 inputUsage / OTel gen_ai.usage.input_tokens，按 side 汇总并去重。",
        "上游 README 的「input 不含缓存」说法是错误的（与代码实际行为不符），本表按代码行为输出。",
    ),
    _note(
        "cachedInput",
        "E Token",
        "输入 token 中命中缓存的部分，是 input 的子集。",
        "tokens.cachedInput：usageDetails.input_cached 或 gen_ai.usage.input_cached_tokens。",
        "是 input 的子集，不是额外 token。",
    ),
    _note(
        "output",
        "E Token",
        "provider 上报的输出 token 数。",
        "tokens.output：usageDetails.output / 顶层 outputUsage / gen_ai.usage.output_tokens。",
    ),
    _note(
        "total",
        "E Token",
        "provider 上报的总 token 数，通常等于 input + output。",
        "tokens.total：usageDetails.total / 顶层 totalUsage / gen_ai.usage.* 汇总。",
        "以上报值为准，不自行相加。",
    ),
    _note(
        "client.input",
        "E2 Token 分侧",
        "client 侧上报的 input token。",
        "tokens.bySide.client.input。",
        "上游 0.4.3 起，缺失侧输出 0；整块缺失（旧 trace）才留空。",
    ),
    _note(
        "client.cachedInput",
        "E2 Token 分侧",
        "client 侧 input 中命中缓存的部分。",
        "tokens.bySide.client.cachedInput。",
        "该侧缺失时整组留空；side 存在但键缺失时按 0。",
    ),
    _note(
        "client.output",
        "E2 Token 分侧",
        "client 侧上报的 output token。",
        "tokens.bySide.client.output。",
        "该侧缺失时整组留空；side 存在但键缺失时按 0。",
    ),
    _note(
        "client.total",
        "E2 Token 分侧",
        "client 侧上报的总 token。",
        "tokens.bySide.client.total。",
        "该侧缺失时整组留空；side 存在但键缺失时按 0。",
    ),
    _note(
        "server.input",
        "E2 Token 分侧",
        "a2a-t-server 侧上报的 input token。",
        "tokens.bySide.server.input，来自 a2a-t-server OTel gen_ai.usage.*；父/子 span 重复上报只计一次（去重）。",
        "上游 0.4.3 起，缺失侧输出 0；整块缺失（旧 trace）才留空。",
    ),
    _note(
        "server.cachedInput",
        "E2 Token 分侧",
        "a2a-t-server 侧 input 中命中缓存的部分。",
        "tokens.bySide.server.cachedInput，来自 a2a-t-server OTel gen_ai.usage.*；父/子 span 重复只计一次（去重）。",
        "该侧缺失时整组留空；side 存在但键缺失时按 0。",
    ),
    _note(
        "server.output",
        "E2 Token 分侧",
        "a2a-t-server 侧上报的 output token。",
        "tokens.bySide.server.output，来自 a2a-t-server OTel gen_ai.usage.*；父/子 span 重复上报只计一次（去重）。",
        "该侧缺失时整组留空；side 存在但键缺失时按 0。",
    ),
    _note(
        "server.total",
        "E2 Token 分侧",
        "a2a-t-server 侧上报的总 token。",
        "tokens.bySide.server.total，来自 a2a-t-server OTel gen_ai.usage.*；父/子 span 重复上报只计一次（去重）。",
        "该侧缺失时整组留空；side 存在但键缺失时按 0。",
    ),
    _note(
        "cacheHitRate",
        "E3 缓存派生",
        "cacheHitRate = cachedInput / input，保留 4 位小数；input == 0 时留空。",
        "由 E 组派生。",
        "只反映 provider 缓存命中，不等于业务缓存；分母为 0 时不猜测。",
    ),
    _note(
        "inputExcludingCached",
        "E3 缓存派生",
        "inputExcludingCached = input − cachedInput，即未命中缓存的输入 token。",
        "由 E 组派生。",
        "cachedInput > input 时输出 0 并追加 warning。",
    ),
    _note(
        "negotiationTriggered",
        "F 协商",
        "本 trace 是否发生协商。",
        "metrics.negotiation.triggered。",
        "true / false 小写。",
    ),
    _note(
        "rounds",
        "F 协商",
        "协商轮次数（propose / negotiation_started / decision 去重后的最大轮次）。",
        "metrics.negotiation.rounds。",
        "轮次存在但明细缺失时上游会追加 warning。",
    ),
    _note(
        "negotiationLatencyMs",
        "F 协商",
        "协商总耗时，只累加 client 时钟轮次。",
        "metrics.negotiation.latencyMs（sum(decisionAt − negotiationStartedAt)）。",
        "clock=server 的轮次不计入（server 时钟不可与 client 时钟做差）；无法计算时留空。",
    ),
    _note(
        "serverDurationMs",
        "F 协商",
        "a2a-t-server 上报的协商耗时。",
        "metadata attributes.gen_ai.agent.a2at.negotiation.total_duration_ms。",
        "真机 NMS 的 statusUpdate 路径可能没有该值。",
    ),
    _note(
        "observations",
        "G 规模/质量",
        "本次导出拉到的 observation 总数。",
        "metrics.counts.observations。",
    ),
    _note(
        "runObservations",
        "G 规模/质量",
        "归属该 run（metadata.runId == traceId）的 observation 数。",
        "metrics.counts.runObservations。",
    ),
    _note(
        "clientObservations",
        "G 规模/质量",
        "side=client 的 run observation 数。",
        "metrics.counts.clientObservations。",
    ),
    _note(
        "serverObservations",
        "G 规模/质量",
        "side=server 的 run observation 数；为 0 时可能 server 侧数据尚未上报。",
        "metrics.counts.serverObservations。",
        "不自动等待或重试，重新导出即可看到新数据。",
    ),
    _note(
        "generations",
        "G 规模/质量",
        "LLM generation 数。",
        "metrics.counts.generations。",
    ),
    _note(
        "toolCalls",
        "G 规模/质量",
        "tool 调用数。",
        "metrics.counts.toolCalls。",
    ),
    _note(
        "warnings",
        "G 规模/质量",
        "导出过程中的 warning，多条用 ; 连接。",
        "metrics.warnings 加上本表派生 warning。",
        "warning 不会被隐藏；空字符串表示没有 warning。",
    ),
    _note(
        "exportStatus",
        "G 规模/质量",
        "单条 trace 的导出状态。",
        "导出器结果（ok / not_found / auth / network / error / timeout）。",
        "非 ok 时只保留身份列与 warnings，指标列留空。",
    ),
    _note(
        "status",
        "H 本地 join",
        "本地 run 状态。",
        "client-state.sqlite3 的 run 摘要。",
        "仅当本地库能按 traceId 找到 run 时填写。",
    ),
    _note(
        "finalState",
        "H 本地 join",
        "本地 run 终态。",
        "client-state.sqlite3 的 run 摘要。",
        "仅当本地库能按 traceId 找到 run 时填写。",
    ),
    _note(
        "passed",
        "H 本地 join",
        "验收是否通过。",
        "read_run_metrics 的 passed。",
        "true / false 小写；找不到本地 run 时留空。",
    ),
    _note(
        "accuracy",
        "H 本地 join",
        "验收准确率。",
        "read_run_metrics 的 accuracy。",
        "浮点保留 4 位小数；字符串原样输出。",
    ),
    _note(
        "startedAt",
        "H 本地 join",
        "本地 run 开始时间。",
        "client-state.sqlite3 的 run 摘要。",
        "仅当本地库能按 traceId 找到 run 时填写。",
    ),
    _note(
        "finishedAt",
        "H 本地 join",
        "本地 run 结束时间。",
        "client-state.sqlite3 的 run 摘要。",
        "仅当本地库能按 traceId 找到 run 时填写。",
    ),
    _note(
        "round",
        "rounds 轮次明细",
        "协商轮次序号，从 0 开始。",
        "metrics.negotiation.roundsDetail[].round。",
    ),
    _note(
        "proposeAt",
        "rounds 轮次明细",
        "提案 / 协商开始的时间点。",
        "roundsDetail[].proposeAt（client propose span.startTime）。",
    ),
    _note(
        "notifiedAt",
        "rounds 轮次明细",
        "操作员被通知的时间点；full level 取 decision_requested，否则回退 proposeAt。",
        "roundsDetail[].notifiedAt。",
    ),
    _note(
        "notifiedAtSource",
        "rounds 轮次明细",
        "notifiedAt 的来源标签。",
        "roundsDetail[].notifiedAtSource（decision_requested / propose）。",
    ),
    _note(
        "negotiationStartedAt",
        "rounds 轮次明细",
        "协商开始时间点。",
        "roundsDetail[].negotiationStartedAt。",
        "可能是 clock=server；clock=server 的轮次不参与 negotiationLatencyMs。",
    ),
    _note(
        "negotiationStartedAtSource",
        "rounds 轮次明细",
        "negotiationStartedAt 的来源标签。",
        "roundsDetail[].negotiationStartedAtSource（client:negotiation_started / server:SendMessage*）。",
    ),
    _note(
        "clock",
        "rounds 轮次明细",
        "该轮次时间戳使用的时钟（client / server）。",
        "roundsDetail[].clock。",
        "clock=server 的轮次不计入 negotiationLatencyMs。",
    ),
    _note(
        "decisionAt",
        "rounds 轮次明细",
        "下游决策时间点。",
        "roundsDetail[].decisionAt。",
    ),
    _note(
        "sdkConstructedAt",
        "rounds 轮次明细",
        "SDK 构造协商回复的时间点。",
        "roundsDetail[].sdkConstructedAt（negotiation_* 阶段 LLM span.startTime）。",
        "真机 NMS 的 statusUpdate 路径可能为空。",
    ),
    _note(
        "replySentAt",
        "rounds 轮次明细",
        "协商回复发出的时间点。",
        "roundsDetail[].replySentAt（phase=negotiation/resolution 的 message_sent.startTime）。",
        "真机 NMS 的 statusUpdate 路径可能为空。",
    ),
    _note(
        "negotiationType",
        "rounds 轮次明细",
        "本轮协商的类型：information / target / feasibility（无法识别时为 unknown）。",
        "Langfuse metadata 的 detail.negotiationType（嵌套 detail 会被展平）；pure A2A 由 "
        "metadata.pure.negotiationKind 或 phase 解析，"
        "A2A-T 提案取 Negotiation-T 的类型。",
        "取不到时留空。",
    ),
    _note(
        "hop",
        "rounds 轮次明细",
        "pure A2A 提案所在的 OSS hop 标签。",
        "Langfuse metadata 的 detail.hop（run 的 pure hop 透传到 observation detail）。",
        "A2A-T 轮次为空；导出器未捕获 content 明细时也为空。",
    ),
)


def fields_csv() -> str:
    """Render the field legend as CSV (page legend and fields.csv share FIELD_NOTES)."""
    return _write_csv(
        ("column", "group", "meaningZh", "source", "caveat"),
        [[note.column, note.group, note.meaning_zh, note.source, note.caveat] for note in FIELD_NOTES],
    )
