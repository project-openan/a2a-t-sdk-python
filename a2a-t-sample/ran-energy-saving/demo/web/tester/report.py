"""XLSX export of one tester batch.

Excel is no longer an input; it remains a convenient report format for the test team. The first
sheet is the arbitration view the test team compares with the reference workbook; the original
result / stage-difference / summary sheets follow it and stay the troubleshooting view.

The arbitration sheet is built from the data collected while the batch ran: the SDK intent, the
frozen case reference, the run answer and the trace id. Its interaction column is the one exception:
the raw wire frames are read from the run directory at export time, because copying every frame
into the batch snapshot would multiply the one-second polling payload and the history file.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any, Final

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from demo.web.tester.export_view import (
    intent_structure,
    intent_structure_label,
    render_intent_diff,
    render_trajectory,
    render_wire_frame_json,
    render_wire_json,
    render_wire_trajectory,
    result_label,
    score_for,
    verdict_reason,
)
from demo.web.tester.langfuse_columns import (
    TRACE_COLUMN_WIDTHS,
    trace_column_headers,
    trace_column_notes,
)
from demo.web.tester.messages import TesterMessages
from demo.web.wire_records import read_wire_records

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

#: Message-catalog keys of the arbitration worksheet's fixed columns.
#:
#: The reference workbook's authoring bookmarks (QA_ID / UID / 设计人), the duplicate score
#: columns (最终得分 is 自动评分; 仲裁得分 / 人工标记结果 stay empty without a manual arbitration)
#: and the four Langfuse duplicates (输出token / 总token / 协商轮次 / 端到端时延) are gone: the
#: test team does not use them, and they made the live-run columns hard to read.
_REFERENCE_HEADER_KEYS: Final[tuple[str, ...]] = (
    "export_header_trace_id",
    "export_header_protocol",
    "export_header_case_id",
    "export_header_case_name",
    "export_header_task_type",
    "export_header_expected_task_t",
    "export_header_raw_request",
    "export_header_normalized_request",
    "export_header_client_blocked",
    "export_header_interception_compare",
    "export_header_sdk_errors",
    "export_header_server_intent",
    "export_header_sdk_intent",
    "export_header_intent_source",
    "export_header_expected_intent",
    "export_header_intent_structure",
    "export_header_intent_diff",
    "export_header_model_answer",
    "export_header_interaction",
    "export_header_trajectory",
    "export_header_task_state",
    "export_header_negotiation_triggered",
    "export_header_negotiation_succeeded",
    "export_header_execution_status",
    "export_header_auto_score",
    "export_header_summary_result",
    "export_header_verdict_reason",
    "export_header_notes",
)

#: Excel's hard limit of one cell's text length.
_MAX_CELL_CHARS: Final[int] = 32000


def _reference_headers(messages: TesterMessages) -> tuple[str, ...]:
    """Return the arbitration column order in ``messages``' language."""
    return tuple(messages.get(key) for key in _REFERENCE_HEADER_KEYS) + trace_column_headers(
        messages
    )


def _resolve_messages(
    batch: Mapping[str, Any], messages: TesterMessages | None
) -> TesterMessages:
    """Use the given messages, else the batch's generation language (D9), else Chinese."""
    if messages is not None:
        return messages
    language = batch.get("language")
    return TesterMessages(language if isinstance(language, str) and language else "")


#: Per-run Langfuse cells (``runId -> (values, note)``). The route fills it before calling
#: ``write_report``; keeping it outside this module keeps the report a pure function of its input.
TraceColumns = Mapping[str, tuple[Sequence[object], str]]


def write_report(
    batch: Mapping[str, Any],
    *,
    runs_dir: Path | None = None,
    trace_columns: TraceColumns | None = None,
    messages: TesterMessages | None = None,
) -> bytes:
    """Build the XLSX report of one batch snapshot.

    Args:
        batch: The batch snapshot served to the UI.
        runs_dir: Directory holding ``<runId>/wire*.jsonl``, used for the interaction column.
            ``None`` keeps the report exportable when only the snapshot is available.
        trace_columns: Langfuse metrics per run id. ``None`` leaves the Langfuse columns blank, which
            keeps the workbook structure stable when observability is off or not requested.
        messages: The batch's message set. ``None`` derives it from the batch's ``language``
            (defaulting to Chinese), so a historical batch exports in the language it was created
            in even under another process language (D9).
    """
    messages = _resolve_messages(batch, messages)
    rounds = _rounds_of(batch)
    multi = rounds > 1
    workbook = Workbook()
    reference = workbook.active
    reference.title = messages.get("export_sheet_arbitration")
    overflow: list[tuple[Mapping[str, Any], list[str]]] = []
    _write_reference_sheet(
        reference,
        batch,
        multi=multi,
        runs_dir=runs_dir,
        overflow=overflow,
        trace_columns=trace_columns,
        messages=messages,
    )
    results = workbook.create_sheet(messages.get("export_sheet_results"))
    headers: list[str] = [
        messages.get("export_header_case_id"),
        messages.get("export_header_case_name"),
        messages.get("export_header_protocol"),
        messages.get("export_header_execution_status"),
        messages.get("export_header_verdict_reason"),
        messages.get("export_header_duration_ms"),
        messages.get("export_header_total_token_count"),
        messages.get("export_header_run_id"),
        messages.get("export_header_baseline_status"),
        messages.get("export_header_baseline_run_id"),
        messages.get("export_header_baseline_task_id"),
        messages.get("export_header_baseline_state"),
        messages.get("export_header_baseline_error"),
        messages.get("export_header_transport"),
        messages.get("export_header_discovery"),
        messages.get("export_header_interception_validation"),
        messages.get("export_header_interception_followup"),
        messages.get("export_header_client_blocked"),
        messages.get("export_header_interception_compare"),
        messages.get("export_header_sdk_errors"),
        *[messages.get("export_header_stage_status", stage=stage) for stage in _STAGE_ORDER],
        messages.get("export_header_error"),
    ]
    if multi:
        headers.insert(3, messages.get("export_header_round"))
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
        baseline = item.get("baseline")
        baseline_map = baseline if isinstance(baseline, Mapping) else {}
        interception = _client_interception(item)
        row: list[object] = [
            item.get("caseId", ""),
            item.get("name", ""),
            item.get("protocol", ""),
            item.get("status", ""),
            verdict_reason(item, messages),
            item.get("durationMs", ""),
            token_map.get("total", ""),
            item.get("runId", ""),
            baseline_map.get("status", ""),
            baseline_map.get("runId", ""),
            baseline_map.get("taskId", ""),
            baseline_map.get("state", ""),
            baseline_map.get("error", ""),
            batch.get("transport", ""),
            batch.get("discovery", ""),
            _interception_verdict(item),
            _interception_followup(item, messages),
            _interception_observed_cell(interception, messages),
            _interception_compare_cell(interception, messages),
            _sdk_interception_errors(item, messages),
            *[
                (stage_map.get(stage, {}).get("status", "") if isinstance(stage_map.get(stage), Mapping) else "")
                for stage in _STAGE_ORDER
            ],
            item.get("error", ""),
        ]
        if multi:
            row.insert(3, item.get("round", 1))
        results.append(row)
    widths = [
        12, 42, 10, 10, 40, 12, 12, 34,
        10, 34, 34, 18, 40,
        10, 10, 10, 14, 48, 14, 14, 60,
        12, 12, 12, 12, 14, 12, 12, 12, 40,
    ]
    if multi:
        widths.insert(3, 6)
    _set_widths(results, tuple(widths))

    diffs = workbook.create_sheet(messages.get("export_sheet_stage_diffs"))
    diff_headers = [
        messages.get("export_header_case_id"),
        messages.get("export_header_protocol"),
        messages.get("export_header_stage"),
        messages.get("export_header_status"),
        messages.get("export_header_field"),
        messages.get("export_header_expected"),
        messages.get("export_header_actual"),
        messages.get("export_header_detail"),
    ]
    if multi:
        diff_headers.insert(2, messages.get("export_header_round"))
    diffs.append(diff_headers)
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
            sources.append((messages.get("export_interception_stage_prefix"), blocked))
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
                        row = [
                            item.get("caseId", ""),
                            item.get("protocol", ""),
                            label,
                            stage.get("status", ""),
                            diff.get("path", ""),
                            _text(diff.get("expected")),
                            _text(diff.get("actual")),
                            diff.get("reason", ""),
                        ]
                        if multi:
                            row.insert(2, item.get("round", 1))
                        diffs.append(row)
                elif stage.get("message"):
                    row = [
                        item.get("caseId", ""),
                        item.get("protocol", ""),
                        label,
                        stage.get("status", ""),
                        "",
                        _text(stage.get("expected")),
                        _text(stage.get("actual")),
                        stage.get("message", ""),
                    ]
                    if multi:
                        row.insert(2, item.get("round", 1))
                    diffs.append(row)
    diff_widths = [12, 10, 12, 10, 30, 48, 48, 34]
    if multi:
        diff_widths.insert(2, 6)
    _set_widths(diffs, tuple(diff_widths))

    summary = workbook.create_sheet(messages.get("export_sheet_summary"))
    summary.append(
        [messages.get("export_header_metric"), messages.get("export_header_value")]
    )
    _style_header(summary)
    raw_summary = batch.get("summary")
    summary_map = raw_summary if isinstance(raw_summary, Mapping) else {}
    for key, value in summary_map.items():
        summary.append([key, value])
    summary.append(["stopAfter", batch.get("stopAfter", "")])
    summary.append(["keepTasks", batch.get("keepTasks", "")])
    summary.append(["concurrency", batch.get("concurrency", 1)])
    if multi:
        summary.append(["rounds", rounds])
    summary.append(["transport", batch.get("transport", "")])
    summary.append(["discovery", batch.get("discovery", "")])
    summary.append(["batchId", batch.get("batchId", "")])
    _set_widths(summary, (28, 36))

    # An interaction transcript that does not fit one Excel cell keeps every frame here instead
    # of losing its tail: one row per wire frame, chunked only when a single frame is oversized.
    if overflow:
        _write_wire_sheet(
            workbook.create_sheet(messages.get("export_sheet_wire")), overflow, messages
        )

    if multi:
        _write_round_sheet(
            workbook.create_sheet(messages.get("export_sheet_rounds")),
            batch,
            rounds,
            messages,
        )

    plan = batch.get("plan")
    if isinstance(plan, Mapping):
        _write_plan_sheet(
            workbook.create_sheet(messages.get("export_sheet_plan")), plan, messages
        )

    # The decision-quality appendix is only meaningful when at least one agent-authored negotiation
    # produced it; a batch without it keeps exactly the four historical sheets.
    if _has_client_quality(batch):
        _write_client_quality_sheet(
            workbook.create_sheet(messages.get("export_sheet_client_quality")), batch, messages
        )

    # The recorded A2A reply is reference text, not replayed; export it next to the case so a
    # reviewer can compare it with the agent's own decision without opening the YAML.
    if _has_client_replies(batch):
        _write_client_replies_sheet(
            workbook.create_sheet(messages.get("export_sheet_client_replies")), batch, messages
        )

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_plan_sheet(
    sheet: Worksheet, plan: Mapping[str, Any], messages: TesterMessages
) -> None:
    """Write the batch plan as key/value rows so a report names its own reproducibility context."""
    sheet.append([messages.get("export_header_item"), messages.get("export_header_value")])
    _style_header(sheet)
    environment = plan.get("environment")
    environment_text = ""
    if isinstance(environment, Mapping):
        environment_text = " · ".join(
            f"{key}={value}" for key, value in environment.items()
        )
    protocols = plan.get("protocols")
    rows = (
        (
            messages.get("export_header_protocol"),
            " / ".join(str(value) for value in protocols)
            if isinstance(protocols, list)
            else protocols,
        ),
        (messages.get("export_plan_environment"), environment_text),
        (messages.get("export_plan_negotiation_model"), plan.get("negotiationModel", "")),
        (messages.get("export_plan_judge_model"), plan.get("judgeModel", "")),
        (messages.get("export_plan_principles_version"), plan.get("principlesVersion", "")),
        (messages.get("export_plan_nms_version"), plan.get("nmsVersion", "")),
        (messages.get("export_plan_stop_after"), plan.get("stopAfter", "")),
        (messages.get("export_plan_rounds"), plan.get("rounds", "")),
    )
    for key, value in rows:
        sheet.append([key, value])
    _set_widths(sheet, (20, 64))


def _has_client_replies(batch: Mapping[str, Any]) -> bool:
    """Return whether a batch recorded A2A replies worth showing as reference text."""
    items = batch.get("items")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        refs = item.get("refs")
        replies = refs.get("clientReplies") if isinstance(refs, Mapping) else None
        if isinstance(replies, list) and replies:
            return True
    return False


def _write_client_replies_sheet(
    sheet: Worksheet, batch: Mapping[str, Any], messages: TesterMessages
) -> None:
    """Write the recorded A2A (P-column) replies as reference text."""
    sheet.append(
        [
            messages.get("export_header_case_id"),
            messages.get("export_header_protocol"),
            messages.get("export_header_round"),
            messages.get("export_header_recorded_replies"),
        ]
    )
    _style_header(sheet)
    items = batch.get("items")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        refs = item.get("refs")
        replies = refs.get("clientReplies") if isinstance(refs, Mapping) else None
        for index, reply in enumerate(replies if isinstance(replies, list) else [], start=1):
            sheet.append(
                [
                    item.get("caseId", ""),
                    item.get("protocol", ""),
                    index,
                    _cell(reply, messages),
                ]
            )
    _set_widths(sheet, (12, 10, 8, 96))


def _has_client_quality(batch: Mapping[str, Any]) -> bool:
    """Return whether any item carries a decision-quality appendix."""
    items = batch.get("items")
    for item in items if isinstance(items, list) else []:
        if isinstance(item, Mapping) and isinstance(item.get("clientQuality"), Mapping):
            return True
    return False


def _write_client_quality_sheet(
    sheet: Worksheet, batch: Mapping[str, Any], messages: TesterMessages
) -> None:
    """Write one row per ``(case, protocol, round)`` of the client decision-quality appendix."""
    sheet.append(
        [
            messages.get("export_header_case_id"),
            messages.get("export_header_protocol"),
            messages.get("export_header_negotiation_round"),
            messages.get("export_header_decision"),
            messages.get("export_header_proposal"),
            messages.get("export_header_quality_expected"),
            messages.get("export_header_action"),
            messages.get("export_header_value"),
            messages.get("export_header_structure"),
            messages.get("export_header_review"),
            messages.get("export_header_coverage"),
            messages.get("export_header_quality"),
            messages.get("export_header_human_review"),
            messages.get("export_header_method"),
            messages.get("export_header_reason"),
        ]
    )
    _style_header(sheet)
    items = batch.get("items")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        quality = item.get("clientQuality")
        if not isinstance(quality, Mapping):
            continue
        rounds = quality.get("rounds")
        for row in rounds if isinstance(rounds, list) else []:
            if not isinstance(row, Mapping):
                continue
            sheet.append(
                [
                    item.get("caseId", ""),
                    item.get("protocol", ""),
                    row.get("round", ""),
                    row.get("decision", ""),
                    _cell(row.get("proposal", ""), messages),
                    _cell(row.get("expected", ""), messages),
                    row.get("action", ""),
                    row.get("value", ""),
                    row.get("structure", ""),
                    row.get("review", ""),
                    row.get("coverage", ""),
                    row.get("quality", ""),
                    messages.get("export_yes")
                    if quality.get("needsHumanReview") is True
                    else messages.get("export_no"),
                    row.get("method", ""),
                    _cell(row.get("reason", ""), messages),
                ]
            )
    _set_widths(sheet, (12, 10, 10, 10, 48, 48, 10, 10, 10, 10, 10, 10, 10, 12, 48))


def _write_reference_sheet(
    sheet: Worksheet,
    batch: Mapping[str, Any],
    *,
    multi: bool,
    runs_dir: Path | None,
    overflow: list[tuple[Mapping[str, Any], list[str]]],
    trace_columns: TraceColumns | None,
    messages: TesterMessages,
) -> None:
    """Write the arbitration worksheet: one row per batch item, reference column order.

    An item whose interaction transcript exceeds one Excel cell is also appended to ``overflow``
    so the caller can give every frame its own row on the fallback sheet.
    """
    headers = list(_reference_headers(messages))
    if multi:
        headers.insert(2, messages.get("export_header_round"))
    sheet.append(headers)
    _style_header(sheet)
    _annotate_trace_headers(sheet, headers, messages)
    items = batch.get("items")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        wire_text, frames, records = _wire_text(item, runs_dir, messages)
        oversized = len(wire_text) > _MAX_CELL_CHARS
        shown = _oversized_wire_cell(item, len(frames), messages) if oversized else wire_text
        trajectory = render_wire_trajectory(records)
        if len(trajectory) > _MAX_CELL_CHARS:
            note = messages.get("export_trajectory_truncated_note")
            keep = _MAX_CELL_CHARS - len(note)
            trajectory = trajectory[:keep] + note
        row = _reference_row(
            item, wire_text=shown, wire_trajectory=trajectory, messages=messages
        )
        _append_trace_cells(row, item, trace_columns, messages)
        if multi:
            row.insert(2, item.get("round", 1))
        sheet.append([_cell(value, messages) for value in row])
        if oversized:
            overflow.append((item, frames))
    widths = [
        34, 8, 10, 42, 10, 60, 48, 48, 14, 14, 60, 60, 60, 10, 60, 20, 60, 90, 50,
        50, 16, 14, 10, 10, 10, 12, 40, 40,
        *TRACE_COLUMN_WIDTHS,
    ]
    if multi:
        widths.insert(2, 6)
    _set_widths(sheet, tuple(widths))


def _append_trace_cells(
    row: list[object],
    item: Mapping[str, Any],
    trace_columns: TraceColumns | None,
    messages: TesterMessages,
) -> None:
    """Append the Langfuse cells after ``备注`` and merge their note into it.

    ``_reference_row`` always ends with ``备注``; the trace block stays after it so a change to
    the trace metrics never shifts the columns the review already references. A per-run failure
    note joins the same cell.
    """
    values: Sequence[object] = ("",) * len(trace_column_headers(messages))
    note = ""
    if trace_columns is not None:
        run_id = str(item.get("runId") or "")
        entry = trace_columns.get(run_id)
        if entry is not None:
            values, note = entry
    note_index = len(row) - 1
    if note:
        existing = str(row[note_index] or "")
        separator = messages.get("export_note_separator")
        row[note_index] = f"{existing}{separator}{note}" if existing else note
    row.extend(values)


def _annotate_trace_headers(
    sheet: Worksheet, headers: list[str], messages: TesterMessages
) -> None:
    """Explain the Langfuse-specific number definitions where they differ from the sheet."""
    start = headers.index(trace_column_headers(messages)[0])
    for offset, note in enumerate(trace_column_notes(messages)):
        if not note:
            continue
        comment = Comment(note, "tester")
        comment.width = 320
        comment.height = 90
        sheet.cell(row=1, column=start + offset + 1).comment = comment


def _rounds_of(batch: Mapping[str, Any]) -> int:
    """Return the batch's round count; an old document without the field is one round."""
    value = batch.get("rounds")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return value


def _write_round_sheet(
    sheet: Worksheet,
    batch: Mapping[str, Any],
    rounds: int,
    messages: TesterMessages,
) -> None:
    """Write the per-round counts and the case × round status matrix."""
    sheet.append(
        [
            messages.get("export_header_round"),
            messages.get("export_header_total"),
            messages.get("export_header_passed"),
            messages.get("export_header_pending"),
            messages.get("export_header_failed"),
            messages.get("export_header_error_count"),
            messages.get("export_header_cancelled"),
            messages.get("export_header_duration_ms"),
            messages.get("export_header_stable_passes"),
            messages.get("export_header_comparable_cases"),
        ]
    )
    _style_header(sheet)
    cross = _map_of(batch.get("crossRound"))
    stable = cross.get("stable", 0)
    total = cross.get("total", 0)
    for entry in _round_entries(batch, rounds):
        summary = _map_of(entry.get("summary"))
        sheet.append(
            [
                entry.get("round", ""),
                summary.get("total", 0),
                summary.get("passed", 0),
                summary.get("partial", 0),
                summary.get("failed", 0),
                summary.get("error", 0),
                summary.get("cancelled", 0),
                summary.get("durationMs", 0),
                stable,
                total,
            ]
        )
    sheet.append([])
    sheet.append(
        [
            messages.get("export_header_case_id"),
            messages.get("export_header_case_name"),
            messages.get("export_header_protocol"),
            *[
                messages.get("export_round_column", round=round_index)
                for round_index in range(1, rounds + 1)
            ],
        ]
    )
    for row in _round_matrix(batch, rounds):
        sheet.append(row)
    _set_widths(sheet, (10, 10, 10, 10, 10, 10, 10, 12, 12, 12))


def _round_entries(
    batch: Mapping[str, Any], rounds: int
) -> list[Mapping[str, object]]:
    """Return the snapshot's per-round summaries, rebuilding them from items when absent."""
    raw = batch.get("roundSummaries")
    entries = [entry for entry in raw if isinstance(entry, Mapping)] if isinstance(raw, list) else []
    if entries:
        return entries
    items = batch.get("items")
    rows: list[Mapping[str, object]] = []
    for round_index in range(1, rounds + 1):
        group = [
            item
            for item in (items if isinstance(items, list) else [])
            if isinstance(item, Mapping) and item.get("round", 1) == round_index
        ]
        rows.append(
            {
                "round": round_index,
                "summary": {
                    "total": len(group),
                    "passed": sum(1 for item in group if item.get("status") == "passed"),
                    "partial": sum(1 for item in group if item.get("status") == "partial"),
                    "failed": sum(1 for item in group if item.get("status") == "failed"),
                    "error": sum(1 for item in group if item.get("status") == "error"),
                    "cancelled": sum(1 for item in group if item.get("status") == "cancelled"),
                    "durationMs": sum(
                        int(item.get("durationMs") or 0) for item in group
                    ),
                },
            }
        )
    return rows


def _round_matrix(batch: Mapping[str, Any], rounds: int) -> list[list[object]]:
    """Return one row per ``(case, protocol)`` with its status in every round."""
    items = batch.get("items")
    statuses: dict[tuple[object, object], dict[int, str]] = {}
    names: dict[tuple[object, object], object] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        key = (item.get("caseId"), item.get("protocol"))
        statuses.setdefault(key, {})[int(item.get("round") or 1)] = str(
            item.get("status") or ""
        )
        names[key] = item.get("name", "")
    rows: list[list[object]] = []
    for key in sorted(statuses, key=lambda pair: (str(pair[0]), str(pair[1]))):
        case_id, protocol = key
        rows.append(
            [
                case_id,
                names[key],
                protocol,
                *[
                    statuses[key].get(round_index, "")
                    for round_index in range(1, rounds + 1)
                ],
            ]
        )
    return rows


def _reference_row(
    item: Mapping[str, Any],
    *,
    wire_text: str,
    wire_trajectory: str = "",
    messages: TesterMessages,
) -> list[object]:
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
    status = str(item.get("status") or "")
    raw_request = intent_meta.get("rawRequest") or refs.get("userInput") or ""
    case_id = item.get("caseId")
    structure = _intent_structure(item, expected_intent, server_intent, messages)
    interception = _client_interception(item)
    return [
        str(item.get("traceId") or ""),
        str(item.get("protocol") or ""),
        case_id if case_id != 0 else "",
        refs.get("caseName") or item.get("name", ""),
        refs.get("operation", ""),
        refs.get("taskT", ""),
        raw_request,
        intent_meta.get("normalizedText") or raw_request,
        _interception_observed_cell(interception, messages),
        _interception_compare_cell(interception, messages),
        _sdk_interception_errors(item, messages),
        _json_cell(server_intent, indent=2),
        _json_cell(sdk_intent, indent=2),
        intent_meta.get("source", ""),
        _json_cell(expected_intent, indent=2),
        intent_structure_label(structure, messages),
        render_intent_diff(
            expected_intent if isinstance(expected_intent, Mapping) else None,
            server_intent,
            messages,
        ),
        _json_cell(_slim_answer(answer), indent=2),
        wire_text,
        wire_trajectory
        or render_trajectory(exchanges if isinstance(exchanges, list) else []),
        _task_state(item, answer),
        _bool_cell(refs.get("negotiationExpected")),
        _negotiation_success(item, refs, answer),
        status,
        _score_cell(status),
        result_label(status, messages),
        verdict_reason(item, messages),
        _reference_note(
            item, refs, intent_meta, answer, server_intent, sdk_intent, messages
        ),
    ]


def _reference_note(
    item: Mapping[str, Any],
    refs: Mapping[str, Any],
    intent_meta: Mapping[str, Any],
    answer: Mapping[str, Any],
    server_intent: Mapping[str, Any] | None,
    sdk_intent: object,
    messages: TesterMessages,
) -> str:
    """Explain the empty arbitration cells of one item."""
    notes: list[str] = []
    baseline = item.get("baseline")
    if isinstance(baseline, Mapping) and baseline.get("status") == "failed":
        detail = " ".join(
            str(part) for part in (baseline.get("state"), baseline.get("error")) if part
        )
        notes.append(
            messages.get(
                "export_baseline_failed",
                detail=detail or messages.get("export_baseline_failed_no_reason"),
            )
        )
    if item.get("scratch") is not None:
        notes.append(messages.get("export_scratch_item"))
    elif not refs:
        notes.append(messages.get("export_no_case_metadata"))
    if item.get("scratch") is None:
        if not refs:
            notes.append(messages.get("export_no_intent_note"))
        elif server_intent is None and answer:
            # A validate-only batch never sent anything; the no-answer note says that instead of
            # blaming a downstream that was never asked.
            if str(item.get("protocol") or "") == "a2a":
                notes.append(messages.get("export_no_server_intent_pure_note"))
            else:
                notes.append(messages.get("export_no_server_intent_note"))
        if not isinstance(sdk_intent, Mapping):
            notes.append(messages.get("export_sdk_intent_missing"))
    if not answer:
        notes.append(messages.get("export_no_answer_note"))
    if intent_meta.get("traceparentSource") == "reconstructed":
        notes.append(messages.get("export_rebuilt_trace_note"))
    langfuse_warning = item.get("langfuseWarning")
    if isinstance(langfuse_warning, str) and langfuse_warning:
        notes.append(messages.get("export_langfuse_warning", warning=langfuse_warning))
    return messages.get("export_note_separator").join(notes)


def _wire_text(
    item: Mapping[str, Any], runs_dir: Path | None, messages: TesterMessages
) -> tuple[str, list[str], list[dict[str, object]]]:
    """Return the interaction JSON, its per-frame JSON documents and the raw records."""
    run_id = str(item.get("runId") or "")
    if not run_id:
        note = messages.get("export_no_run_note")
        return note, [note], []
    if runs_dir is None:
        note = messages.get("export_no_runs_dir_note")
        return note, [note], []
    records = read_wire_records(runs_dir, run_id, include_errors=True)
    if not records:
        note = messages.get("export_no_wire_note", run_id=run_id)
        return note, [note], []
    frames = [
        render_wire_frame_json(record, index)
        for index, record in enumerate(records, start=1)
    ]
    return render_wire_json(records, run_id=run_id), frames, records


def _oversized_wire_cell(
    item: Mapping[str, Any], frame_count: int, messages: TesterMessages
) -> str:
    """Return a valid-JSON pointer for a cell that cannot hold the whole transcript."""
    return json.dumps(
        {
            "runId": str(item.get("runId") or ""),
            "frameCount": frame_count,
            "note": messages.get("export_wire_overflow_note"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _write_wire_sheet(
    sheet: Worksheet,
    overflow: Sequence[tuple[Mapping[str, Any], list[str]]],
    messages: TesterMessages,
) -> None:
    """Write every frame of an oversized interaction transcript, one row per frame."""
    sheet.append(
        [
            messages.get("export_header_case_id"),
            messages.get("export_header_protocol"),
            messages.get("export_header_round"),
            messages.get("export_header_run_id"),
            messages.get("export_header_frame"),
            messages.get("export_header_segment"),
            messages.get("export_header_interaction"),
        ]
    )
    _style_header(sheet)
    for item, frames in overflow:
        for number, frame in enumerate(frames, start=1):
            chunks = _split_cell(frame)
            for part, chunk in enumerate(chunks, start=1):
                sheet.append(
                    [
                        item.get("caseId", ""),
                        item.get("protocol", ""),
                        item.get("round", 1),
                        item.get("runId", ""),
                        number,
                        f"{part}/{len(chunks)}" if len(chunks) > 1 else "",
                        chunk,
                    ]
                )
    _set_widths(sheet, (12, 10, 6, 34, 8, 8, 120))


def _split_cell(text: str) -> list[str]:
    """Split one oversized cell into Excel-sized chunks, keeping every character."""
    if len(text) <= _MAX_CELL_CHARS:
        return [text]
    return [
        text[index : index + _MAX_CELL_CHARS]
        for index in range(0, len(text), _MAX_CELL_CHARS)
    ]


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


def _cell(value: object, messages: TesterMessages) -> object:
    """Truncate one cell to Excel's limit so an oversized origin_answer never fails the export."""
    if isinstance(value, str) and len(value) > _MAX_CELL_CHARS:
        return value[:_MAX_CELL_CHARS] + messages.get("export_cell_truncated")
    return value


def _intent_structure(
    item: Mapping[str, Any],
    expected: object,
    actual: Mapping[str, Any] | None,
    messages: TesterMessages,
) -> dict[str, object]:
    """Return the item's intent-structure verdict, recomputed for older batch snapshots."""
    document = item.get("intentStructure")
    if isinstance(document, Mapping):
        return dict(document)
    return intent_structure(
        expected if isinstance(expected, Mapping) else None,
        actual,
        messages,
    )


#: Message-catalog keys of the observed client gate.
_INTERCEPTION_OBSERVED_KEYS: Final[dict[str, str]] = {
    "blocked": "export_yes",
    "passed": "export_no",
    "pass": "export_no",
    "not_executed": "export_interception_not_executed",
    "not_applicable": "export_interception_not_applicable",
}
#: Message-catalog keys of the observed-vs-expected interception comparison.
_INTERCEPTION_COMPARE_KEYS: Final[dict[str, str]] = {
    "hit": "export_interception_hit",
    "miss": "export_interception_miss",
    "false_block": "export_interception_false_block",
    "undefined": "export_interception_undefined",
    "not_executed": "export_interception_not_executed",
    "not_applicable": "export_interception_not_applicable",
}


def _client_interception(item: Mapping[str, Any]) -> dict[str, object]:
    """Return the item's client-interception record, derived for older snapshots."""
    record = item.get("clientInterception")
    if isinstance(record, Mapping):
        return dict(record)
    if str(item.get("protocol") or "") == "a2a":
        return {"observed": "not_applicable", "compare": "not_applicable"}
    observed = ""
    interception = item.get("interception")
    stages = interception.get("stages") if isinstance(interception, Mapping) else None
    for candidate in (
        stages.get("validate") if isinstance(stages, Mapping) else None,
        _map_of(item.get("stages")).get("validate"),
    ):
        if not isinstance(candidate, Mapping):
            continue
        detail = candidate.get("detail")
        verdict = detail.get("verdict") if isinstance(detail, Mapping) else candidate.get("actual")
        if isinstance(verdict, str) and verdict:
            observed = verdict
            break
    if not observed:
        return {"observed": "not_executed", "compare": "not_executed"}
    blocked = observed == "blocked"
    expected = _map_of(item.get("refs")).get("validateExpected")
    if expected is None:
        compare = "undefined"
    elif expected == "blocked":
        compare = "hit" if blocked else "miss"
    else:
        compare = "false_block" if blocked else "hit"
    return {"observed": "blocked" if blocked else "passed", "compare": compare}


def _interception_observed_cell(
    record: Mapping[str, object], messages: TesterMessages
) -> str:
    """Return 是/否/未执行/不适用 for the observed client gate."""
    key = _INTERCEPTION_OBSERVED_KEYS.get(str(record.get("observed") or ""))
    return messages.get(key) if key is not None else ""


def _interception_compare_cell(
    record: Mapping[str, object], messages: TesterMessages
) -> str:
    """Return 命中/漏拦/误拦/未定义期望 for the observed-vs-expected gate."""
    key = _INTERCEPTION_COMPARE_KEYS.get(str(record.get("compare") or ""))
    return messages.get(key) if key is not None else ""


def _sdk_interception_errors(item: Mapping[str, Any], messages: TesterMessages) -> str:
    """Return the SDK's own validation errors, one block per gate attempt.

    A blocked case has two attempts: the original request the case expects to be blocked, and
    the replacement it dispatches. Both are printed so the error can be read against the request
    rewrite instead of trusting the stage verdict alone. A clean pass leaves the cell empty.
    """
    entries: list[tuple[str, Mapping[str, object]]] = []
    interception = item.get("interception")
    stages = interception.get("stages") if isinstance(interception, Mapping) else None
    original = stages.get("validate") if isinstance(stages, Mapping) else None
    if isinstance(original, Mapping):
        entries.append((messages.get("export_gate_original"), original))
    validate = _map_of(item.get("stages")).get("validate")
    if isinstance(validate, Mapping):
        entries.append(
            (
                messages.get("export_gate_dispatched" if entries else "export_gate_validation"),
                validate,
            )
        )
    texts = [text for label, stage in entries if (text := _gate_error_text(label, stage, messages))]
    return "\n".join(texts)


def _gate_error_text(
    label: str, stage: Mapping[str, object], messages: TesterMessages
) -> str:
    """Render one gate attempt's verdict, code and per-slot errors."""
    detail = _map_of(stage.get("detail"))
    verdict = str(detail.get("verdict") or stage.get("actual") or "")
    if not verdict:
        return ""
    if verdict == "pass":
        return ""
    code = str(detail.get("code") or "")
    if code:
        lines = [messages.get("export_gate_head_code", label=label, verdict=verdict, code=code)]
    else:
        lines = [messages.get("export_gate_head", label=label, verdict=verdict)]
    errors = detail.get("slotErrors")
    for error in errors if isinstance(errors, list) else []:
        if not isinstance(error, Mapping):
            continue
        slot = str(error.get("slotName") or "")
        error_code = str(error.get("code") or "")
        message = str(error.get("message") or "")
        head = messages.get("export_gate_slot_separator").join(
            part for part in (slot, error_code) if part
        )
        if head and message:
            lines.append(messages.get("export_gate_slot_line", head=head, message=message))
        else:
            lines.append(f"- {head or message}")
    if len(lines) == 1 and stage.get("message"):
        lines.append(f"- {stage['message']}")
    return "\n".join(lines)


#: Keys of the model answer kept in the arbitration cell. ``exchanges`` is dropped because the
#: interaction column and the trajectory already carry every frame.
_SLIM_ANSWER_KEYS: Final[tuple[str, ...]] = (
    "code",
    "message",
    "finalResult",
    "negotiationSummary",
)


def _slim_answer(answer: Mapping[str, Any]) -> dict[str, object]:
    """Return the answer object without the exchange list the other columns already carry."""
    return {key: answer[key] for key in _SLIM_ANSWER_KEYS if key in answer}


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


def _interception_followup(
    item: Mapping[str, Any], messages: TesterMessages
) -> str:
    """Return the replacement request a blocked case was continued with, if any."""
    record = item.get("interception")
    if not isinstance(record, Mapping):
        return ""
    text = str(record.get("followupInput") or "")
    # A failed interception never dispatches the replacement: name it without implying it ran.
    if record.get("followupSkipped") is True and text:
        return messages.get("export_followup_skipped", text=text)
    return text


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
