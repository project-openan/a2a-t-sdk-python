"""Issue #1 AF–AW table (metrics.json → spreadsheet rows)."""

from __future__ import annotations

from typing import Any

#: Business column letters used by the OSP workbook (Issue #1) for the run-level table.
ROUND_COLUMNS: tuple[tuple[str, str], ...] = (
    ("AJ", "协商发起时间点"),
    ("AK", "通知OSS用户处理时间点"),
    ("AL", "OSS用户答复时间点"),
    ("AM", "OSS调用SDK构造协商请求时间点"),
    ("AN", "OSS下发协商回复时间点"),
    ("AO", "意图理解（发起协商）时间点"),
)


def _metrics_list(metrics: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(metrics, dict):
        return [metrics]
    return [item for item in metrics if isinstance(item, dict)]


def _round_entries(metrics: dict[str, Any]) -> tuple[int, dict[int, dict[str, Any]]]:
    negotiation = metrics.get("negotiation") or {}
    detail = negotiation.get("roundsDetail") or []
    try:
        rounds = int(negotiation.get("rounds") or 0)
    except (TypeError, ValueError):
        rounds = 0
    rounds = max(1, rounds, len(detail))
    by_round = {int(entry.get("round") or 0): entry for entry in detail if isinstance(entry, dict)}
    return rounds, by_round


def _headers(rounds: int) -> list[str]:
    headers: list[str] = [
        "trace ID",
        "场景",
        "运行模式",
        "链 ID",
        "输入token数（客户端）",
        "输入缓存token数（客户端）",
        "输出token数（客户端）",
        "总token数（客户端）",
        "输入token数（服务端）",
        "输入缓存token数（服务端）",
        "输出token数（服务端）",
        "总token数（服务端）",
        "端到端时延(ms)",
        "原始意图起始时间点",
        "OSS调用A2A-T SDK时间点",
        "OSS下发请求时间点",
        "任务创建/修改成功时间点",
    ]
    for round_no in range(1, rounds + 1):
        for _, label in ROUND_COLUMNS:
            headers.append(f"第{round_no}轮 {label}")
    headers.extend(["协商时延(ms)", "协商轮次", "是否触发协商"])
    return headers


def _row(metrics: dict[str, Any], rounds: int) -> list[Any]:
    timeline = metrics.get("timeline") or {}
    latency = metrics.get("latency") or {}
    tokens = metrics.get("tokens") or {}
    negotiation = metrics.get("negotiation") or {}
    __, by_round = _round_entries(metrics)
    by_side = tokens.get("bySide") or {}
    client_tokens = by_side.get("client") or {}
    server_tokens = by_side.get("server") or {}
    row: list[Any] = [
        metrics.get("traceId"),
        metrics.get("scenario"),
        metrics.get("runMode"),
        metrics.get("chainId"),
        client_tokens.get("input"),
        client_tokens.get("cachedInput"),
        client_tokens.get("output"),
        client_tokens.get("total"),
        server_tokens.get("input"),
        server_tokens.get("cachedInput"),
        server_tokens.get("output"),
        server_tokens.get("total"),
        latency.get("endToEndMs"),
        timeline.get("receivedAt"),
        timeline.get("sdkCalledAt"),
        timeline.get("requestSentAt"),
        timeline.get("taskCreatedAt"),
    ]
    for round_no in range(1, rounds + 1):
        entry = by_round.get(round_no) or {}
        row.extend(
            [
                entry.get("proposeAt"),
                entry.get("notifiedAt"),
                entry.get("decisionAt"),
                entry.get("sdkConstructedAt"),
                entry.get("replySentAt"),
                entry.get("negotiationStartedAt"),
            ]
        )
    row.extend(
        [
            negotiation.get("latencyMs"),
            negotiation.get("rounds"),
            "是" if negotiation.get("triggered") else "否",
        ]
    )
    return row


def build_table(
    metrics: dict[str, Any] | list[dict[str, Any]],
) -> tuple[list[str], list[list[Any]]]:
    """Return ``(headers, rows)`` for one or many traces.

    One row per trace; the round block is sized to the widest trace, so a batch
    mixing 0-round and 2-round traces keeps a stable set of R1..R2 columns.
    """
    items = _metrics_list(metrics)
    rounds = max((_round_entries(item)[0] for item in items), default=1)
    return _headers(rounds), [_row(item, rounds) for item in items]
