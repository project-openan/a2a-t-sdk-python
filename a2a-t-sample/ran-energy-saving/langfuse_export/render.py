"""Render transcript.md."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from . import __version__
from .config import region_of
from .models import NetworkInfo, Observation, Score, TraceBundle

FOLD_LIMIT = 4000
MEDIA_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "video/mp4": ".mp4",
}


def fmt_dt_utc(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    utc = value.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def fmt_latency(seconds: float | None) -> str:
    return f"{seconds:.3f}s" if seconds is not None else "n/a"


def fmt_int(value: int | None) -> str:
    return f"{value:,}" if value is not None else "n/a"


def fmt_cost(value: Decimal | None) -> str:
    return f"${value:.4f}" if value is not None else "n/a"


def fmt_bytes(value: int) -> str:
    return f"{value:,} B"


def md_cell(value: Any) -> str:
    """Render one Markdown table cell: escape pipes and collapse newlines."""
    if value is None:
        return "—"
    text = str(value)
    if not text:
        return "—"
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def media_extension(content_type: str | None) -> str:
    if content_type:
        base = content_type.split(";")[0].strip().lower()
        if base in MEDIA_EXTENSIONS:
            return MEDIA_EXTENSIONS[base]
    return ".bin"


def render_network(network: NetworkInfo) -> str:
    """One human line for the transcript header: effective proxy + TLS setup."""
    if network.proxy:
        proxy = f"代理 `{network.proxy}`（显式，忽略 NO_PROXY）"
    elif network.no_proxy:
        proxy = "直连（--no-proxy）"
    else:
        proxy = "直连（默认）"
    tls = "开启" if network.tls_verify else "关闭（--insecure）"
    parts = [proxy, f"TLS 校验={tls}"]
    if network.ca_bundle:
        parts.append(f"自定义 CA=`{network.ca_bundle}`")
    return " · ".join(parts)


def _json_or_text(text: str | None) -> tuple[str, str]:
    """Return (body, language) for an io string: pretty JSON when parseable."""
    if text is None:
        return "", "text"
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
            return json.dumps(parsed, indent=2, ensure_ascii=False), "json"
        except (ValueError, TypeError):
            pass
    return text, "text"


def _fence(body: str, language: str) -> list[str]:
    return [f"```{language}", body.rstrip("\n"), "```"]


def _folded_block(body: str, language: str) -> list[str]:
    full_len = len(body)
    shown = body[:FOLD_LIMIT]
    lines = _fence(shown, language)
    if full_len > FOLD_LIMIT:
        lines.append("")
        lines.append("```text")
        lines.append(f"... [truncated: {FOLD_LIMIT:,} of {full_len:,} chars shown] ...")
        lines.append("```")
    return lines


def _documents_count(obs: Observation) -> int | None:
    if not obs.output_raw:
        return None
    try:
        parsed = json.loads(obs.output_raw)
    except (ValueError, TypeError):
        return None
    documents = parsed.get("documents") if isinstance(parsed, dict) else None
    return len(documents) if isinstance(documents, list) else None


def _obs_line(obs: Observation, prefix: str, connector: str) -> str:
    parts = [f"{obs.name or '(unnamed)'} [{obs.type}]"]
    if obs.model and obs.type == "GENERATION":
        parts.append(obs.model)
    parts.append(fmt_latency(obs.latency))
    if obs.type == "GENERATION" and (obs.usage.input is not None or obs.usage.output is not None):
        parts.append(f"{fmt_int(obs.usage.input)}/{fmt_int(obs.usage.output)} tok")
    if obs.type == "GENERATION" and obs.usage.cost_total is not None:
        parts.append(fmt_cost(obs.usage.cost_total))
    parts.append(obs.level)
    if obs.is_root:
        parts.append("(root)")
    if obs.type == "RETRIEVER":
        documents = _documents_count(obs)
        if documents is not None:
            parts.append(f"({documents} docs)")
    return prefix + connector + " ".join(parts)


def render_tree(roots: list[Observation]) -> str:
    lines: list[str] = []
    stack: list[tuple[Observation, str, bool]] = [
        (roots[index], "", index == len(roots) - 1) for index in range(len(roots) - 1, -1, -1)
    ]
    while stack:
        obs, prefix, is_last = stack.pop()
        connector = "└── " if is_last else "├── "
        lines.append(_obs_line(obs, prefix, connector))
        child_prefix = prefix + ("    " if is_last else "│   ")
        children = obs.children
        for index in range(len(children) - 1, -1, -1):
            stack.append((children[index], child_prefix, index == len(children) - 1))
    return "\n".join(lines)


def _subject_label(score: Score) -> str:
    if score.subject_kind == "observation":
        return f"observation `{score.subject_id}`"
    if score.subject_kind == "session":
        return f"session `{score.subject_id}`"
    if score.subject_kind == "trace":
        return f"trace `{score.subject_id or score.subject_trace_id}`"
    if score.subject_kind:
        return f"{score.subject_kind} `{score.subject_id}`"
    return "—"


def _score_value(score: Score) -> str:
    if isinstance(score.value, bool):
        return "true" if score.value else "false"
    if isinstance(score.value, str):
        return f'"{score.value}"'
    return str(score.value)


def render_transcript(bundle: TraceBundle, exported_at: datetime) -> str:
    summary = bundle.summary
    warnings = bundle.warnings
    lines: list[str] = []

    status = "complete" if not warnings else f"complete（{len(warnings)} warnings）"
    if summary.start_time and summary.end_time:
        time_range = f"{fmt_dt_utc(summary.start_time)} → {fmt_dt_utc(summary.end_time)}"
    else:
        time_range = "n/a"

    tokens = (
        f"输入 {fmt_int(summary.input_tokens)} / 输出 {fmt_int(summary.output_tokens)} / 合计 {fmt_int(summary.total_tokens)}"
        if any(v is not None for v in (summary.input_tokens, summary.output_tokens, summary.total_tokens))
        else "n/a"
    )
    latency = fmt_latency(summary.latency_ms / 1000 if summary.latency_ms is not None else None)

    lines.append(f"# Langfuse 导出：{summary.name or summary.trace_id}")
    lines.append("")
    lines.append("| 字段 | 值 |")
    lines.append("| --- | --- |")
    rows = [
        ("traceId", f"`{summary.trace_id}`"),
        ("name", summary.name or "n/a"),
        ("sessionId", f"`{summary.session_id}`" if summary.session_id else "n/a"),
        ("userId", summary.user_id or "n/a"),
        ("tags", ", ".join(f"`{tag}`" for tag in summary.tags) if summary.tags else "n/a"),
        ("release", summary.release or "n/a"),
        ("environment", summary.environment or "n/a"),
        ("时间范围", time_range),
        ("耗时", latency),
        ("成本", fmt_cost(summary.total_cost)),
        ("Token", tokens),
        ("Observation", f"{summary.observation_count} 条"),
        ("Scores", f"{summary.score_count} 条"),
        ("导出时间", exported_at.isoformat(timespec="seconds")),
        ("host", f"`{bundle.host}`（source: {bundle.host_source}, region: {region_of(bundle.host)}）"),
        ("网络", render_network(bundle.network)),
        ("API", f"{bundle.api_version}（backend: {bundle.backend}）"),
        ("导出状态", status),
    ]
    # TLS notices are informational (never feed --strict); they live next to the
    # network row so a reader can see why verification was weakened.
    rows.extend(("notice", notice) for notice in bundle.notices)
    for key, value in rows:
        lines.append(f"| {key} | {value} |")

    lines.append("")
    lines.append("## 调用树")
    lines.append("")
    lines.append("```text")
    lines.append(render_tree(bundle.roots) if bundle.roots else "(no observations)")
    lines.append("```")
    lines.append("")
    lines.append("## Observation 详情")
    lines.append("")

    ordered = sorted(
        bundle.observations,
        key=lambda obs: (obs.start_time or datetime.min.replace(tzinfo=timezone.utc), obs.id),
    )
    by_id = {obs.id: obs for obs in bundle.observations}
    for index, obs in enumerate(ordered, start=1):
        lines.append(f"### {index:02d} [{obs.type}] {obs.name or '(unnamed)'}")
        lines.append("")
        lines.append(f"- id: `{obs.id}`")
        if obs.parent_id:
            parent = by_id.get(obs.parent_id)
            label = f"{obs.parent_id}（{parent.name}）" if parent and parent.name else obs.parent_id
            lines.append(f"- 父节点: `{label}`")
        else:
            lines.append("- 父节点: （无，root）")
        lines.append(f"- 时间: {fmt_dt_utc(obs.start_time)} → {fmt_dt_utc(obs.end_time)}（{fmt_latency(obs.latency)}）")
        if obs.level not in (None, "DEFAULT"):
            suffix = f" — {obs.status_message}" if obs.status_message else ""
            lines.append(f"- level: **{obs.level}**{suffix}")
        else:
            lines.append(f"- level: {obs.level or 'DEFAULT'}")
        if obs.model:
            params = ""
            if obs.model_parameters:
                params = "（" + ", ".join(f"{k}={v}" for k, v in obs.model_parameters.items()) + "）"
            lines.append(f"- model: `{obs.model}`{params}")
        if obs.prompt_name:
            version = f" v{obs.prompt_version}" if obs.prompt_version else ""
            lines.append(f"- prompt: `{obs.prompt_name}`{version}")
        if obs.usage.has_any():
            lines.append(
                "- usage: "
                f"输入 {fmt_int(obs.usage.input)} / 输出 {fmt_int(obs.usage.output)} / 合计 {fmt_int(obs.usage.total)} tok，"
                f"成本 {fmt_cost(obs.usage.cost_total)}"
            )
        lines.append("")

        if obs.input_raw is not None:
            body, language = _json_or_text(obs.input_raw)
            lines.append("**Input**")
            lines.append("")
            lines.extend(_folded_block(body, language))
            lines.append("")

        if obs.output_raw is not None:
            body, language = _json_or_text(obs.output_raw)
            lines.append("**Output**")
            lines.append("")
            lines.extend(_folded_block(body, language))
            lines.append("")

        if obs.metadata:
            body = json.dumps(obs.metadata, indent=2, ensure_ascii=False, sort_keys=False)
            lines.append("**Metadata**")
            lines.append("")
            lines.extend(_folded_block(body, "json"))
            lines.append("")

        if index != len(ordered):
            lines.append("---")
            lines.append("")

    lines.append("## Scores")
    lines.append("")
    if bundle.scores:
        lines.append("| name | dataType | value | source | subject | comment |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for score in bundle.scores:
            lines.append(
                f"| {md_cell(score.name)} | {score.data_type or '—'} | {md_cell(_score_value(score))} | "
                f"{score.source or '—'} | {md_cell(_subject_label(score))} | {md_cell(score.comment)} |"
            )
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## Media")
    lines.append("")
    if bundle.media_assets:
        lines.append("| mediaId | 类型 | 大小 | 本地文件 | URL 过期 | 备注 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for asset in bundle.media_assets:
            local = f"`{asset.local_path}`" if asset.local_path else "—"
            size = fmt_bytes(asset.content_length) if asset.content_length is not None else "—"
            note = md_cell(asset.error)
            lines.append(
                f"| `{asset.media_id}` | {asset.content_type or '—'} | {size} | {local} | "
                f"{md_cell(asset.url_expiry)} | {note} |"
            )
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## Raw data")
    lines.append("")
    lines.append("| 文件 | 说明 |")
    lines.append("| --- | --- |")
    lines.append("| `trace-summary.json` | 派生概要（非 API 原文） |")
    lines.append("| `transcript.md` | 本文件 |")
    lines.append(f"| `raw/observations.jsonl` | {len(bundle.raw_observations)} 行，observations 原始行 |")
    lines.append(f"| `raw/scores.json` | {len(bundle.raw_scores)} 条，scores 原始响应 |")
    if bundle.legacy_trace:
        lines.append("| `raw/legacy-trace.json` | v3 legacy trace 原始响应 |")
    if bundle.session_rows:
        lines.append(
            f"| `raw/session-observations.jsonl` | {len(bundle.session_rows)} 行，同 session 原始行（--with-session） |"
        )
    for asset in bundle.media_assets:
        if asset.local_path:
            lines.append(f"| `{asset.local_path}` | 媒体下载结果 |")
    lines.append("")
    lines.append("文件大小与 sha256 见 `manifest.json`。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"<sub>由 langfuse-export {__version__} 生成 · 数据时间统一为 UTC</sub>")
    lines.append("")
    return "\n".join(lines)
