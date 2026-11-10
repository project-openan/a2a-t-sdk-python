"""Deterministic Markdown report skeleton for one OSP AI batch.

The CLI writes the machine facts (environment, failure list, cleanup); the agent fills the
judgement sections marked with HTML comments. The renderer is pure: the same manifest, batch and
failure records always produce the same text, every value is redacted and the appendix inlines
bounded evidence so the blue zone does not need access to the yellow zone's ``runs-ai/``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from eval.osp.ai import evidence
from eval.osp.ai.types import FailureRecord

#: Marker comments naming the content the agent must fill.
MARKER_CONCLUSION = "<!-- AI 填：通过/失败计数、阻塞项、一句话结论 -->"
MARKER_ENVIRONMENT = (
    "<!-- 机器填：commit、worktree、是否 dirty、模型、网管、端口、协议、stopAfter、"
    "决策模式、用例集合、耗时、并发=1、清理状态 -->"
)
MARKER_FAILURES = "<!-- 机器填：按 stage / 失败类别聚合，逐条给 expected/actual/首个分歧/artifact 路径 -->"
MARKER_ANALYSIS = "<!-- AI 填：现象 → 根因 → 归属（我方/网管/网络）→ 置信度 → 下一步探针 -->"
MARKER_BLUE_FIX = "<!-- AI 填：文件:行、改法、验收用例 -->"
MARKER_FIELD_CONFIRM = "<!-- AI 填：需要网管/业务确认的问题，标注已排除项 -->"
MARKER_CLEANUP = "<!-- 机器填：非终态 Task、进程、端口；未清理项单列 -->"

#: Environment keys rendered in this order; each falls back from manifest to batch.
_ENVIRONMENT_ROWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("commit", ("gitHead", "commit", "head")),
    ("worktree", ("worktree", "gitWorktree")),
    ("dirty", ("gitDirty", "dirty")),
    ("model", ("model", "modelName", "llmModel", "modelId")),
    ("modelEndpoint", ("modelEndpoint",)),
    ("modelMode", ("modelMode",)),
    ("protocol", ("protocol",)),
    ("decisionMode", ("decisionMode",)),
    ("approval", ("approval",)),
    ("stopAfter", ("stopAfter",)),
    ("runsDir", ("runsDir",)),
    ("concurrency", ("concurrency",)),
    ("nmsEndpoint", ("nmsEndpoint", "nms")),
    ("nmsSim", ("nmsSim",)),
    ("hostname", ("hostname",)),
    ("profile", ("profile", "profilePath")),
    ("generatedAt", ("generatedAt",)),
)

#: Artifact keys rendered for every failure, in a stable order.
_ARTIFACT_ORDER: tuple[str, ...] = (
    "wire",
    "wireOss",
    "wireError",
    "serverTrace",
    "clientState",
    "ossTaskDb",
    "batch",
)

#: Summary keys rendered first, in this order; extra keys follow alphabetically.
_SUMMARY_ORDER: tuple[str, ...] = (
    "total",
    "passed",
    "failed",
    "partial",
    "error",
    "cancelled",
    "pending",
    "running",
    "stagePassed",
    "stageFailed",
    "stageRecorded",
    "stageError",
    "stageWaiting",
    "durationMs",
)

#: Run identity keys rendered in this order.
_IDENTITY_ORDER: tuple[str, ...] = ("runId", "traceId", "taskId", "contextId")


def render_report(
    *,
    manifest: Mapping[str, object],
    batch: Mapping[str, object],
    records: Sequence[FailureRecord],
    excerpts: Mapping[str, str] | None = None,
    detail_limit: int = 20,
) -> str:
    """Render one self-contained Markdown report.

    Args:
        manifest: The ``manifest.json`` document written by the CLI (commit, worktree, ports...).
        batch: The persisted tester batch document (``summary``, ``items``, ``protocols``...).
        records: One :class:`FailureRecord` per failing ``(case, protocol, stage)``.
        excerpts: Optional pre-collected evidence excerpts keyed by a stable label.
        detail_limit: Maximum lines inlined per evidence excerpt in the appendix.

    Returns:
        The report text, ending with one newline. The output is deterministic for equal inputs.
    """
    batch_id = _render_scalar(_first(manifest, ("batchId",)) or _first(batch, ("batchId",)), "unknown")
    sorted_records = sorted(records, key=lambda record: (record.case_id, record.protocol, record.stage))
    lines: list[str] = [f"# OSP AI 跑批报告 {batch_id}", ""]
    lines.extend(_conclusion_section())
    lines.extend(_environment_section(manifest, batch))
    lines.extend(_failure_section(batch, sorted_records))
    lines.extend(_ai_section("## 3. 逐条分析", MARKER_ANALYSIS))
    lines.extend(_ai_section("## 4. 需要蓝区跟改", MARKER_BLUE_FIX))
    lines.extend(_ai_section("## 5. 待现场确认", MARKER_FIELD_CONFIRM))
    lines.extend(_cleanup_section(manifest, batch))
    lines.extend(_appendix_section(excerpts, detail_limit))
    return "\n".join(lines).rstrip("\n") + "\n"


def write_report(path: Path, text: str) -> None:
    """Write one report as UTF-8 with ``\n`` line endings, creating parent directories."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def _conclusion_section() -> list[str]:
    """Return section 0: the agent's one-paragraph conclusion."""
    return ["## 0. 结论", MARKER_CONCLUSION, ""]


def _environment_section(manifest: Mapping[str, object], batch: Mapping[str, object]) -> list[str]:
    """Return section 1: machine facts about the instance and the batch."""
    lines = ["## 1. 环境", MARKER_ENVIRONMENT, "", "| 项 | 值 |", "|---|---|"]
    rows: list[tuple[str, object]] = [("batchId", _first(manifest, ("batchId",)) or _first(batch, ("batchId",)))]
    rows.append(("status", _first(batch, ("status",))))
    for label, keys in _ENVIRONMENT_ROWS:
        value = _first(manifest, keys)
        if value is None:
            value = _first(batch, keys)
        rows.append((label, value))
    rows.append(("protocols", _first(batch, ("protocols",)) or _first(manifest, ("protocols",))))
    rows.append(("cases", _case_ids(manifest, batch)))
    rows.append(("ports", _first(manifest, ("ports",))))
    rows.append(("startedAt", _first(batch, ("startedAt",))))
    rows.append(("finishedAt", _first(batch, ("finishedAt",))))
    for label, value in rows:
        lines.append(f"| {_cell(label)} | {_cell(_render_environment_value(label, value))} |")
    lines.append("")
    return lines


def _failure_section(batch: Mapping[str, object], records: Sequence[FailureRecord]) -> list[str]:
    """Return section 2: counts, the case/protocol matrix and one row per failing stage."""
    lines = ["## 2. 失败清单", MARKER_FAILURES, ""]
    lines.extend(_summary_lines(batch, records))
    lines.extend(_matrix_lines(batch, records))
    if not records:
        lines.extend(["（本批次没有失败记录）", ""])
        return lines

    distribution: dict[str, int] = {}
    for record in records:
        distribution[record.stage] = distribution.get(record.stage, 0) + 1
    summary = ", ".join(f"{stage}={count}" for stage, count in sorted(distribution.items()))
    lines.extend([f"**失败阶段分布**：{summary}", ""])
    lines.extend(
        [
            "| 用例 | 协议 | 阶段 | 状态 | 首个分歧 | 证据 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for record in records:
        lines.append(
            "| {case} | {protocol} | {stage} | {status} | {diff} | {artifacts} |".format(
                case=_cell(_case_label(record.case_id, record.name)),
                protocol=_cell(_render_scalar(record.protocol, "—")),
                stage=_cell(_render_scalar(record.stage, "—")),
                status=_cell(_render_scalar(record.stage_status, "—")),
                diff=_cell(_first_diff_cell(record.first_diff)),
                artifacts=_artifact_cell(record),
            )
        )
    lines.extend(["", "**失败条目明细**", ""])
    for record in records:
        lines.extend(_detail_lines(record))
    lines.append("")
    return lines


def _summary_lines(batch: Mapping[str, object], records: Sequence[FailureRecord]) -> list[str]:
    """Return the summary count table, or a note when the batch carries none."""
    counts = _summary_counts(batch, records)
    if not counts:
        return ["（批次未提供 summary 计数）", ""]
    lines = ["| 指标 | 值 |", "|---|---|"]
    for key in _ordered_keys(counts, _SUMMARY_ORDER):
        lines.append(f"| {_cell(key)} | {_cell(_render_scalar(counts[key], '—'))} |")
    lines.append("")
    return lines


def _summary_counts(
    batch: Mapping[str, object],
    records: Sequence[FailureRecord],
) -> dict[str, object]:
    """Return the batch summary, counting item statuses when the summary is absent."""
    raw = batch.get("summary")
    if isinstance(raw, Mapping) and raw:
        return {str(key): value for key, value in raw.items()}
    items = batch.get("items")
    if isinstance(items, list) and items:
        counts: dict[str, object] = {"total": len(items)}
        for item in items:
            if not isinstance(item, Mapping):
                continue
            status = item.get("status")
            if isinstance(status, str) and status:
                current = counts.get(status)
                counts[status] = (
                    current + 1 if isinstance(current, int) and not isinstance(current, bool) else 1
                )
        return counts
    if records:
        return {"failedStages": len(records)}
    return {}


def _matrix_lines(batch: Mapping[str, object], records: Sequence[FailureRecord]) -> list[str]:
    """Return the case/protocol matrix built from batch items (or from the failure records)."""
    matrix: dict[object, dict[str, str]] = {}
    items = batch.get("items")
    if isinstance(items, list) and items:
        for item in items:
            if not isinstance(item, Mapping):
                continue
            case = item.get("caseId")
            protocol = str(item.get("protocol") or "")
            matrix.setdefault(case, {})[protocol] = str(item.get("status") or "—")
    else:
        for record in records:
            matrix.setdefault(record.case_id, {})[record.protocol] = record.stage_status
    if not matrix:
        return ["（批次未提供 item 明细）", ""]

    protocols = _protocol_columns(batch, matrix)
    header = "| 用例 | " + " | ".join(_cell(protocol) for protocol in protocols) + " |"
    rule = "|---|" + "|".join("---" for _ in protocols) + "|"
    lines = ["**用例 / 协议矩阵**", "", header, rule]
    for case in sorted(matrix, key=_case_sort_key):
        cells = [_cell(matrix[case].get(protocol, "—")) for protocol in protocols]
        lines.append(f"| {_cell(_case_label_plain(case))} | " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def _protocol_columns(
    batch: Mapping[str, object],
    matrix: Mapping[object, Mapping[str, str]],
) -> list[str]:
    """Return protocol column names, preferring the batch's declared order."""
    declared = batch.get("protocols")
    columns: list[str] = []
    if isinstance(declared, list):
        columns.extend(str(item) for item in declared if str(item))
    for cells in matrix.values():
        for protocol in cells:
            if protocol and protocol not in columns:
                columns.append(protocol)
    return columns or ["—"]


def _detail_lines(record: FailureRecord) -> list[str]:
    """Return the per-failure bullets carrying expected/actual, run and baseline."""
    lines = [
        f"- **{_cell(_case_label(record.case_id, record.name))} "
        f"{_cell(_render_scalar(record.protocol, '—'))} "
        f"{_cell(_render_scalar(record.stage, '—'))} / "
        f"{_cell(_render_scalar(record.stage_status, '—'))}**"
    ]
    lines.append(f"  - operation: {_render_scalar(record.operation, '—')}")
    lines.append(f"  - message: {_render_brief(record.message)}")
    lines.append(f"  - run: {_identity_text(record.run.to_dict())}")
    lines.append(f"  - baseline: {_render_brief(record.baseline) if record.baseline else 'null'}")
    lines.append(f"  - signals: {_render_brief(dict(record.signals))}")
    lines.append(f"  - expected: {_render_brief(record.expected)}")
    lines.append(f"  - actual: {_render_brief(record.actual)}")
    return lines


def _artifact_cell(record: FailureRecord) -> str:
    """Return every artifact path of one failure, with ``null`` for what this topology omits."""
    artifacts = record.artifacts.to_dict()
    parts: list[str] = []
    for key in _ARTIFACT_ORDER:
        value = artifacts.get(key)
        parts.append(f"{key}={_render_scalar(value, 'null')}")
    return "<br>".join(_cell(part) for part in parts)


def _first_diff_cell(first_diff: Mapping[str, object] | None) -> str:
    """Return the first divergence path, or ``null`` when the record has none."""
    if first_diff is None:
        return "null"
    safe = evidence.redact(first_diff)
    if isinstance(safe, Mapping):
        path = safe.get("path")
        if path is not None:
            return f"`{_one_line(str(path))}`"
    return f"`{_one_line(json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str))}`"


def _ai_section(title: str, marker: str) -> list[str]:
    """Return one AI-filled section with its fill marker."""
    return [title, marker, ""]


def _cleanup_section(manifest: Mapping[str, object], batch: Mapping[str, object]) -> list[str]:
    """Return section 6: cleanup facts when the manifest/batch carry them."""
    lines = ["## 6. 清理结果", MARKER_CLEANUP, ""]
    value = _first(manifest, ("cleanup", "cleanupResult", "cleanupStatus"))
    if value is None:
        value = _first(batch, ("cleanup", "cleanupResult", "cleanupStatus"))
    if value is None or value == {} or value == []:
        lines.extend(["见 instance down 输出", ""])
        return lines
    safe = evidence.redact(value)
    fence = "json" if isinstance(safe, (Mapping, list)) else "text"
    lines.extend([f"```{fence}", evidence.excerpt(safe, max_lines=40), "```", ""])
    return lines


def _appendix_section(excerpts: Mapping[str, str] | None, detail_limit: int) -> list[str]:
    """Return the self-contained evidence appendix, bounded and redacted."""
    lines = [
        "## 附：关键证据摘录",
        "",
        f"> 机器生成，已脱敏；每段最多 {detail_limit} 行。",
        "",
    ]
    if not excerpts:
        lines.extend(["（未提供证据摘录）", ""])
        return lines
    for key in sorted(excerpts):
        lines.extend(
            [
                f"### {_one_line(str(key))}",
                "",
                "```text",
                evidence.excerpt(excerpts[key], max_lines=detail_limit),
                "```",
                "",
            ]
        )
    return lines


def _first(mapping: Mapping[str, object], keys: Sequence[str]) -> object | None:
    """Return the first present, non-``None`` value among ``keys``."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _case_ids(manifest: Mapping[str, object], batch: Mapping[str, object]) -> object | None:
    """Return the batch case ids, preferring the non-empty list."""
    for source in (manifest, batch):
        value = source.get("caseIds")
        if isinstance(value, list) and value:
            return value
    value = _first(manifest, ("caseIds",))
    return value if value is not None else _first(batch, ("caseIds",))


def _case_label(case_id: object, name: str) -> str:
    """Return ``#<id> <name>`` for one failure row."""
    label = _case_label_plain(case_id)
    clean_name = _render_scalar(name, "") if isinstance(name, str) else ""
    return f"{label} {clean_name}".strip()


def _case_label_plain(case_id: object) -> str:
    """Return ``#<id>`` for one case id."""
    safe = evidence.redact(case_id)
    if isinstance(safe, bool) or safe is None:
        return str(safe)
    if isinstance(safe, int):
        return f"#{safe}"
    return f"#{_one_line(str(safe))}"


def _case_sort_key(case_id: object) -> tuple[int, object]:
    """Sort numeric case ids before textual ones."""
    if isinstance(case_id, int) and not isinstance(case_id, bool):
        return (0, case_id)
    return (1, str(case_id))


def _ordered_keys(values: Mapping[str, object], preferred: Sequence[str]) -> list[str]:
    """Return ``preferred`` keys first, then the remaining keys alphabetically."""
    keys = [key for key in preferred if key in values]
    keys.extend(sorted(key for key in values if key not in preferred))
    return keys


def _render_environment_value(label: str, value: object) -> str:
    """Format one environment cell, keeping ports and lists readable."""
    if label == "ports":
        return _ports_text(value)
    if label in {"cases", "protocols"}:
        safe = evidence.redact(value)
        if isinstance(safe, list):
            return ", ".join(_one_line(str(item)) for item in safe)
    return _render_scalar(value)


def _ports_text(value: object) -> str:
    """Return ``label=port`` pairs sorted by label, or a missing-value note."""
    safe = evidence.redact(value)
    if not isinstance(safe, Mapping) or not safe:
        return "未记录"
    pairs = sorted(safe.items(), key=lambda item: str(item[0]))
    return ", ".join(f"{_one_line(str(key))}={_one_line(_render_scalar(item))}" for key, item in pairs)


def _identity_text(identity: Mapping[str, object]) -> str:
    """Return the four run identities as ``key=value`` pairs."""
    safe = evidence.redact(identity)
    if not isinstance(safe, Mapping):
        return ""
    return ", ".join(
        f"{key}={_render_scalar(safe.get(key), 'null')}" for key in _IDENTITY_ORDER
    )


def _render_scalar(value: object, default: str = "未记录") -> str:
    """Render one value as a single redacted line."""
    safe = evidence.redact(value)
    if safe is None:
        return default
    if isinstance(safe, bool):
        return "true" if safe else "false"
    if isinstance(safe, str):
        return _one_line(safe)
    if isinstance(safe, (Mapping, list)):
        return _one_line(json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str))
    return _one_line(str(safe))


def _render_brief(value: object, limit: int = 400) -> str:
    """Render one value as a redacted one-line string capped at ``limit`` characters."""
    safe = evidence.redact(value)
    if isinstance(safe, str):
        text = safe
    else:
        text = json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _one_line(value: str) -> str:
    """Collapse whitespace in one cell value."""
    return " ".join(value.split())


def _cell(value: object) -> str:
    """Escape one Markdown table cell."""
    return _one_line(str(value)).replace("|", r"\|")


__all__ = [
    "MARKER_ANALYSIS",
    "MARKER_BLUE_FIX",
    "MARKER_CLEANUP",
    "MARKER_CONCLUSION",
    "MARKER_ENVIRONMENT",
    "MARKER_FAILURES",
    "MARKER_FIELD_CONFIRM",
    "render_report",
    "write_report",
]
