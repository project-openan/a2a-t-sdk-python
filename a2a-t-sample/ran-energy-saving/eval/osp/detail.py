"""Human-readable detail report of the cases a runner report marked as failed.

The runner's JSON report already carries every stage status, expectation and diff; this module
turns the failing subset into Markdown, so a live run leaves behind a report that names the case,
the operator text and the exact expected/actual values without anyone reading the JSON.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["render_failures"]


def render_failures(
    report: Mapping[str, Any],
    cases: Mapping[int, Mapping[str, Any]] | None = None,
) -> str:
    """Render the failing cases of one runner report as Markdown.

    Args:
        report: a report produced by :func:`eval.osp.runner.build_report`.
        cases: optional corpus documents keyed by id; when given, each section adds the operation,
            the dimension, the expected validation and the operator text.

    Returns:
        a Markdown document; an empty string when no case failed.
    """
    rows = [case for case in report.get("cases") or () if isinstance(case, Mapping) and not case.get("passed")]
    if not rows:
        return ""
    summary = report.get("summary") or {}
    lines: list[str] = [
        "# OSP evaluation — failing cases",
        "",
        f"Run started {report.get('startedAt', 'unknown')}, stop after "
        f"`{report.get('stopAfter', 'unknown')}`.",
        "",
    ]
    if isinstance(summary, Mapping):
        lines.append(
            f"- cases: {summary.get('total')} total, {summary.get('passed')} passed, "
            f"{summary.get('failed')} failed, {summary.get('error')} error"
        )
        stages = summary.get("stages")
        if isinstance(stages, Mapping):
            for name in ("intent", "task_t", "validate"):
                counts = stages.get(name)
                if isinstance(counts, Mapping):
                    lines.append(
                        f"- `{name}`: {counts.get('passed', 0)} passed, "
                        f"{counts.get('failed', 0)} failed, "
                        f"{counts.get('recorded', 0)} recorded, {counts.get('error', 0)} error"
                    )
        lines.append("")
    for row in rows:
        lines.extend(_render_case(row, cases or {}))
    return "\n".join(lines).rstrip() + "\n"


def _render_case(
    row: Mapping[str, Any], cases: Mapping[int, Mapping[str, Any]]
) -> list[str]:
    case_id = row.get("id")
    document = cases.get(int(case_id)) if isinstance(case_id, int) else None
    name = str(row.get("name") or (document or {}).get("name") or "")
    lines: list[str] = [f"## {case_id}. {name}".rstrip(), ""]
    if document is not None:
        protocol = document["expect"]["protocols"]["a2a-t"]
        lines.extend(
            [
                f"- operation: `{document.get('operation')}`",
                f"- dimension: {document.get('dimension')}",
                f"- expected validation: `{protocol.get('validate')}` "
                f"({protocol.get('validate_label')})",
                "",
            ]
        )
    if row.get("error"):
        lines.extend([f"**error**: `{row['error']}`", ""])
    lines.extend(["```text", str((document or {}).get("user_input") or "").strip(), "```", ""])
    lines.extend(_render_segment("stages", row.get("stages")))
    interception = row.get("interception")
    if isinstance(interception, Mapping):
        lines.extend(_render_segment("interception (original request)", interception.get("stages")))
    return lines


def _render_segment(label: str, stages: object) -> list[str]:
    if not isinstance(stages, Mapping) or not stages:
        return []
    lines = [f"### {label}", ""]
    if isinstance(stages, Mapping) and "stages" in stages:
        stages = stages.get("stages")
        if not isinstance(stages, Mapping):
            return lines
    lines.extend(["| stage | status | message |", "|---|---|---|"])
    for name, stage in stages.items():
        if not isinstance(stage, Mapping):
            continue
        message = str(stage.get("message") or "").replace("|", "\\|")
        lines.append(f"| {name} | {stage.get('status')} | {message} |")
    lines.append("")
    diffs: list[Mapping[str, Any]] = []
    for stage in stages.values():
        if isinstance(stage, Mapping):
            diffs.extend(diff for diff in stage.get("diffs") or () if isinstance(diff, Mapping))
    if diffs:
        lines.extend(["| path | expected | actual |", "|---|---|---|"])
        for diff in diffs:
            lines.append(
                f"| `{diff.get('path')}` | `{_cell(diff.get('expected'))}` | "
                f"`{_cell(diff.get('actual'))}` |"
            )
        lines.append("")
    return lines


def _cell(value: object) -> str:
    """Render one diff value inside a Markdown table cell."""
    text = str("" if value is None else value)
    return text.replace("\n", "\\n").replace("|", "\\|")
