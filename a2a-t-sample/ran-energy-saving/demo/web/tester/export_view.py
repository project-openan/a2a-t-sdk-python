"""Renderers of the arbitration export (reference-workbook shapes).

The functions here are pure: they take the collected batch item data and return cell strings.
Nothing reads the run directory or a transcript file, so the XLSX report stays deterministic.

The reference worksheet the test team arbitrates with uses:

* a nested JSON path with array indices for the intent difference (``A[0].B``);
* three difference classes in the order missing / added / changed;
* a milestone trajectory such as ``### Round 1 | Client | Task-T``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from eval.osp.compare import (
    INTENT_ROW_ADDED,
    INTENT_ROW_CHANGED,
    INTENT_ROW_MISSING,
    INTENT_ROW_SAME,
    CompareRow,
    flatten_intent,
)
from eval.osp.intent_schema import OSP_INTENT_KEYS
from protocol.client_service import RunState

#: Rendered diff of an intent equal to its reference.
DIFF_IDENTICAL: str = "完全一致"

#: Direction label of the client side in the trajectory.
_SIDE_CLIENT: Final = "Client"
#: Direction label of the server side in the trajectory.
_SIDE_SERVER: Final = "Server"

#: Inbound message labels kept in the milestone trajectory.
_MILESTONE_TYPES: frozenset[str] = frozenset(
    {
        "PROPOSE",
        "ACCEPT",
        "REJECT",
        "ABORT",
        "TARGET",
        "INFORMATION",
        "FEASIBILITY",
    }
)

#: Score and label of one item status in the arbitration worksheet.
_SCORE_BY_STATUS: dict[str, int | None] = {
    "passed": 10,
    "failed": 0,
    "partial": None,
    "error": 0,
    "cancelled": 0,
}
_LABEL_BY_STATUS: dict[str, str] = {
    "passed": "通过",
    "failed": "失败",
    "partial": "待定",
    "error": "错误",
    "cancelled": "取消",
}


#: Terminal states that mean the request succeeded, for the workbook's ``code`` field.
_CODE_BY_FINAL_STATE: dict[str, str] = {
    "task_completed": "SUCCESS",
    "task_failed": "FAILED",
    "failed": "FAILED",
    "incomplete": "INCOMPLETE",
}


def read_traceparent(
    runs_dir: Path | None,
    run_id: str,
    *,
    trace_id: str,
) -> tuple[str, str]:
    """Return the W3C ``traceparent`` of the run's first outbound call and where it came from.

    The wire log is the authority: the vendor NMS client and the a2a-sdk transport both record the
    headers they actually sent. Without one, a traceparent is rebuilt from the run's stable trace
    id; the second value is then ``reconstructed`` so the export can label it as such.
    """
    if runs_dir is not None and run_id:
        try:
            lines = (Path(runs_dir) / run_id / "wire.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, Mapping) or record.get("dir") != "out":
                continue
            headers = record.get("headers")
            if not isinstance(headers, Mapping):
                continue
            for key, value in headers.items():
                if str(key).lower() == "traceparent" and isinstance(value, str) and value:
                    return value, "wire"
    span_id = hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:16]
    return f"00-{trace_id}-{span_id}-01", "reconstructed"


def build_answer(
    run: RunState,
    *,
    refs: Mapping[str, object],
    traceparent: str,
) -> dict[str, object]:
    """Build the reference-workbook answer object of one run.

    The object carries the same seven keys as the reference ``被测模型生成答案`` and
    ``origin_answer`` columns. The exchanges are a normalized reconstruction from the trace spans
    and the transcript events, not a raw packet capture.
    """
    del refs  # The Task-T is already carried by the outbound message_sent span.
    result = run.result
    report = result.report if result is not None and isinstance(result.report, Mapping) else {}
    final_state = result.final_state if result is not None else ""
    code = report.get("code") or _CODE_BY_FINAL_STATE.get(str(final_state), str(final_state).upper())
    message = report.get("message") or (result.error if result is not None and result.error else "")
    final_result = report.get("finalResult")
    if not isinstance(final_result, str):
        final_result = json.dumps(report, ensure_ascii=False) if report else ""
    return {
        "code": str(code),
        "message": str(message),
        "finalResult": final_result,
        "negotiationSummary": _negotiation_summary(run),
        "exchanges": _collect_exchanges(run),
        "traceparent": traceparent,
        "taskId": run.remote_task_id or (result.task_id if result is not None else None) or "",
    }


def _negotiation_summary(run: RunState) -> dict[str, object]:
    """Summarize the negotiation rounds, missing items and filled parameters of one run."""
    rounds = max((proposal.round for proposal in run.proposals), default=0)
    missing: list[object] = []
    if run.proposals:
        parameters = run.proposals[-1].parameters
        if isinstance(parameters, Mapping):
            raw_missing = parameters.get("missingItems")
            if isinstance(raw_missing, list):
                missing = [*raw_missing]
    filled: dict[str, object] = {}
    if run.result is not None and run.result.decisions:
        params = run.result.decisions[-1].filled_params
        filled = dict(params) if isinstance(params, Mapping) else {}
    return {"missingItems": missing, "filledParams": filled, "negotiationRounds": rounds}


def _collect_exchanges(run: RunState) -> list[dict[str, object]]:
    """Return the outbound and inbound exchanges of one run in sequence order."""
    pending: list[tuple[int, int, dict[str, object]]] = []
    position = 0
    first_outbound = True
    for span in run.traces:
        if span.get("kind") != "message_sent":
            continue
        detail = span.get("detail")
        detail_map = detail if isinstance(detail, Mapping) else {}
        performative = str(detail_map.get("performative") or "")
        message_type = "Task-T" if performative == "TASK_T" else (performative or "Task-T")
        round_number = span.get("round")
        if not isinstance(round_number, int):
            round_number = 1 if first_outbound else 0
        first_outbound = False
        pending.append(
            (
                _seq_of(span, position),
                position,
                {
                    "round": round_number,
                    "step": f"发送{message_type}请求",
                    "direction": "OUT",
                    "messageType": message_type,
                    "templateUri": detail_map.get("templateUri"),
                    "payload": _outbound_payload(detail_map),
                    "durationMs": _number(span.get("durationMs")),
                    "timestamp": str(span.get("startedAt") or ""),
                },
            )
        )
        position += 1
    events = run.transcript.events if run.transcript is not None else []
    for entry in events:
        if entry.get("entryType") != "event":
            continue
        if str(entry.get("direction") or "server->client") != "server->client":
            continue
        message_type = _inbound_type(entry)
        pending.append(
            (
                _seq_of(entry, position),
                position,
                {
                    # A status/artifact row has no negotiation round. ``None`` keeps it out of the
                    # milestone trajectory instead of pretending it is round 0.
                    "round": entry.get("round") if isinstance(entry.get("round"), int) else None,
                    "step": f"接收{message_type}",
                    "direction": "IN",
                    "messageType": message_type,
                    "templateUri": entry.get("templateUri"),
                    "payload": _inbound_payload(entry),
                    "durationMs": _number(entry.get("durationMs")),
                    "timestamp": str(entry.get("ts") or ""),
                },
            )
        )
        position += 1
    pending.sort(key=lambda item: (item[0], item[1]))
    return [exchange for _seq, _position, exchange in pending]


def _outbound_payload(detail: Mapping[str, object]) -> dict[str, object]:
    """Reconstruct the outbound A2A message body from one ``message_sent`` span detail."""
    text = detail.get("taskT") or detail.get("summary") or ""
    metadata: dict[str, object] = {}
    if detail.get("templateUri"):
        metadata["templateUri"] = detail["templateUri"]
    if detail.get("extensionUri") and detail.get("taskT"):
        metadata[str(detail["extensionUri"])] = detail["taskT"]
    message: dict[str, object] = {
        "role": "ROLE_USER",
        "parts": [{"text": str(text)}],
        "metadata": metadata,
    }
    if detail.get("messageId"):
        message["messageId"] = detail["messageId"]
    return {"message": message}


def _inbound_payload(entry: Mapping[str, object]) -> dict[str, object]:
    """Reconstruct the inbound A2A body from one normalized transcript event."""
    raw_metadata = entry.get("metadata")
    metadata: dict[str, object] = (
        {str(key): value for key, value in raw_metadata.items()}
        if isinstance(raw_metadata, Mapping)
        else {}
    )
    if entry.get("state"):
        metadata["taskState"] = entry["state"]
    payload: dict[str, object] = {
        "role": "ROLE_AGENT",
        "parts": [{"text": str(entry.get("content") or entry.get("summary") or "")}],
        "metadata": metadata,
    }
    for key, source in (("contextId", "contextId"), ("taskId", "taskId")):
        if entry.get(source):
            payload[key] = entry[source]
    return payload


def _inbound_type(entry: Mapping[str, object]) -> str:
    """Return the reference message type of one inbound event."""
    kind = str(entry.get("kind") or "")
    if kind == "status":
        return str(entry.get("state") or "STATUS")
    if kind == "artifact":
        return "ARTIFACT"
    performative = entry.get("performative")
    return str(performative).upper() if performative else "RESULT"


def _seq_of(entry: Mapping[str, object], fallback: int) -> int:
    """Return the shared run sequence number of one entry."""
    value = entry.get("seq")
    return value if isinstance(value, int) else fallback


def _number(value: object) -> float:
    """Return a numeric duration, zero when absent."""
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def render_intent_diff(
    expected: Mapping[str, object] | None,
    actual: Mapping[str, object] | None,
) -> str:
    """Render the difference between the reference and the answer intent.

    An OSP intent is compared with the same normalization the pass/fail decision uses, so a
    notation-only difference is reported as an expression difference instead of a value change.
    A document outside the OSP schema falls back to a structural walk. ``完全一致`` when both
    documents agree; ``""`` when the answer intent was not collected (pure A2A or a batch that
    stopped before the SDK stages).
    """
    if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
        return ""
    if _is_osp_intent(expected) and _is_osp_intent(actual):
        lines = [
            line
            for row in flatten_intent(expected, actual)
            if (line := _flattened_diff_line(row))
        ]
        return "\n".join(lines) if lines else DIFF_IDENTICAL
    missing: list[str] = []
    added: list[str] = []
    changed: list[str] = []
    _walk(expected, actual, "", missing, added, changed)
    lines = [*missing, *added, *changed]
    return "\n".join(lines) if lines else DIFF_IDENTICAL


def _is_osp_intent(document: Mapping[str, object]) -> bool:
    """Return whether every key of one document belongs to the OSP intent schema."""
    return set(document) <= set(OSP_INTENT_KEYS)


def _flattened_diff_line(row: CompareRow) -> str:
    """Render one normalized intent row in the workbook's diff notation."""
    path = row.path.removeprefix("intent.")
    if row.verdict == INTENT_ROW_MISSING:
        return f"缺失: {path} (ref值为 {_json(row.expected)})"
    if row.verdict == INTENT_ROW_ADDED:
        return f"新增: {path} (ans值为 {_json(row.actual)})"
    if row.verdict == INTENT_ROW_CHANGED:
        return f"值变更: {path} ({_plain(row.expected)} → {_plain(row.actual)})"
    if row.status != INTENT_ROW_SAME:
        return (
            f"表达差异（归一化后一致）: {path} "
            f"({_plain(row.expected)} → {_plain(row.actual)})"
        )
    return ""


def _walk(
    expected: object,
    actual: object,
    path: str,
    missing: list[str],
    added: list[str],
    changed: list[str],
) -> None:
    """Collect missing, added and changed leaves below ``path``."""
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        for key, value in expected.items():
            child = _child_path(path, str(key))
            if key not in actual:
                missing.append(f"缺失: {child} (ref值为 {_json(value)})")
            else:
                _walk(value, actual[key], child, missing, added, changed)
        for key, value in actual.items():
            if key not in expected:
                added.append(f"新增: {_child_path(path, str(key))} (ans值为 {_json(value)})")
        return
    if isinstance(expected, list) and isinstance(actual, list):
        for index, value in enumerate(expected):
            child = f"{path}[{index}]"
            if index >= len(actual):
                missing.append(f"缺失: {child} (ref值为 {_json(value)})")
            else:
                _walk(value, actual[index], child, missing, added, changed)
        for index in range(len(expected), len(actual)):
            added.append(f"新增: {path}[{index}] (ans值为 {_json(actual[index])})")
        return
    if expected != actual:
        changed.append(f"值变更: {path or '<root>'} ({_plain(expected)} → {_plain(actual)})")


def _child_path(path: str, key: str) -> str:
    """Append one mapping key to a JSON path."""
    return f"{path}.{key}" if path else key


def _json(value: object) -> str:
    """Render one value as compact JSON (strings quoted, Unicode kept)."""
    return json.dumps(value, ensure_ascii=False)


def _plain(value: object) -> str:
    """Render one changed scalar for the ``ref → ans`` clause."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def render_trajectory(exchanges: Sequence[Mapping[str, object]]) -> str:
    """Render the milestone trajectory column of one item."""
    lines: list[str] = []
    for exchange in exchanges:
        direction = str(exchange.get("direction") or "").upper()
        label = str(exchange.get("messageType") or "").strip()
        if direction == "OUT":
            side = _SIDE_CLIENT
            label = label or "Task-T"
        elif direction == "IN":
            side = _SIDE_SERVER
            # Only a Task state or a negotiation performative is a milestone; a plain RESULT
            # message repeats what the Task-T exchange already showed.
            if not (label.startswith("TASK_STATE_") or label in _MILESTONE_TYPES):
                continue
        else:
            continue
        round_number = exchange.get("round")
        # A Task state is an observation, not a negotiation round; printing ``Round 0`` for it
        # would read as a zero-th round in the arbitration workbook.
        prefix = f"Round {round_number} | " if isinstance(round_number, int) else ""
        lines.append(f"### {prefix}{side} | {label}")
    return "\n\n".join(lines)


#: Label of a blank SSE line in the interaction transcript (the frame separator).
_SSE_BLANK_LINE: Final = "(空行)"


def render_wire_frames(records: Sequence[Mapping[str, object]]) -> list[str]:
    """Render the raw wire records of one run as one text block per frame.

    This is the evidence the arbitration sheet's interaction column shows: the request line, its
    headers and body, every SSE ``data`` line, every webhook push (including a dropped duplicate)
    and every frame the SDK rejected. A record is printed verbatim, one frame per block, so a
    reader (human or model) can follow the exchange in order; nothing is summarized.
    """
    return [_wire_frame(index, record) for index, record in enumerate(records, start=1)]


def _wire_frame(index: int, record: Mapping[str, object]) -> str:
    """Render one wire record as a headed block."""
    ts = str(record.get("ts") or "")
    if _is_wire_error(record):
        return _wire_error_frame(index, ts, record)
    if "line" in record:
        line = str(record.get("line") or "")
        return f"[{index}] {ts} IN SSE\nline: {line or _SSE_BLANK_LINE}"
    source = record.get("source")
    if isinstance(source, str) and source:
        dropped = str(record.get("dropped") or "")
        suffix = f" (dropped: {dropped})" if dropped else ""
        lines = [f"[{index}] {ts} IN {source}{suffix}"]
        _append_wire_value(lines, "body", record.get("body"))
        return "\n".join(lines)
    lines = [f"[{index}] {ts} {_wire_heading(record)}"]
    _append_wire_value(lines, "headers", record.get("headers"))
    _append_wire_value(lines, "body", record.get("body"))
    return "\n".join(lines)


def _wire_heading(record: Mapping[str, object]) -> str:
    """Return the direction line of one HTTP wire record."""
    parts: list[str] = [str(record.get("dir") or "").upper() or "?"]
    status = record.get("status")
    if status is not None:
        parts.append(str(status))
    method = str(record.get("method") or "")
    if method:
        parts.append(method)
    url = str(record.get("url") or "")
    if url:
        parts.append(url)
    elapsed = record.get("elapsedMs")
    if isinstance(elapsed, int | float) and not isinstance(elapsed, bool):
        parts.append(f"{elapsed}ms")
    if record.get("stream") is True:
        parts.append("stream")
    return " ".join(parts)


def _is_wire_error(record: Mapping[str, object]) -> bool:
    """Return whether one record comes from ``wire-error.jsonl``."""
    return "type" in record and "message" in record and "dir" not in record


def _wire_error_frame(index: int, ts: str, record: Mapping[str, object]) -> str:
    """Render one rejected frame kept in ``wire-error.jsonl``."""
    label = str(record.get("type") or "")
    message = str(record.get("message") or "")
    detail = f"{label}: {message}" if label and message else (label or message)
    head = " ".join(part for part in (f"[{index}]", ts, "ERROR", detail) if part)
    lines = [head]
    status = record.get("status")
    if status is not None:
        lines.append(f"status: {status}")
    _append_wire_value(lines, "headers", record.get("headers"))
    _append_wire_value(lines, "frame", record.get("frame"))
    return "\n".join(lines)


def _append_wire_value(lines: list[str], key: str, value: object) -> None:
    """Append one ``key: value`` line, JSON-encoding structured values on a single line."""
    if value is None:
        return
    if isinstance(value, str):
        if value:
            lines.append(f"{key}: {value}")
        return
    if isinstance(value, Mapping | list) and not value:
        return
    lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}")


#: Stage names in execution order, used to name the first unexplained stage of a row.
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

#: Stage statuses whose message explains why the row is not a clean pass.
_EXPLAINING_STAGE_STATUSES: Final[frozenset[str]] = frozenset(
    {"waiting", "warning", "error", "failed"}
)

#: Record-only stages whose message still explains a non-green row.
_RECORDED_REASON_STAGES: Final[frozenset[str]] = frozenset({"intent", "final"})


def verdict_reason(item: Mapping[str, object]) -> str:
    """Explain why one row is not ``passed``.

    The arbitration workbook used to leave the whole column empty for a ``partial`` row: the
    evidence was spread across the stage-difference sheet and the run console. The reason is built
    from the same snapshot the UI shows, in the order a reviewer needs it: the run's own error, the
    first non-green stage message, then the human-review trigger of an undecided intent.
    """
    status = str(item.get("status") or "")
    if status in {"", "passed"}:
        return ""
    reasons: list[str] = []
    error = item.get("error")
    if isinstance(error, str) and error.strip():
        reasons.append(error.strip())
    stages = item.get("stages")
    if isinstance(stages, Mapping):
        for stage_name in reversed(_STAGE_ORDER):
            stage = stages.get(stage_name)
            if not isinstance(stage, Mapping):
                continue
            stage_status = str(stage.get("status") or "")
            message = str(stage.get("message") or "").strip()
            if message and any(message in reason for reason in reasons):
                # The run error and the stage message often carry the same sentence; one copy is
                # enough.
                continue
            if stage_status in _EXPLAINING_STAGE_STATUSES:
                reasons.append(f"{stage_name}: {message}" if message else stage_name)
            elif stage_status == "recorded" and stage_name in _RECORDED_REASON_STAGES:
                reasons.append(
                    f"{stage_name}: {message}" if message else f"{stage_name}: 只记录，未判定"
                )
    if item.get("needsReview") and not any("转人工复核" in reason for reason in reasons):
        paths = _residual_paths(item.get("intentVerdict"))
        suffix = f"（{', '.join(paths[:3])}）" if paths else ""
        reasons.append(f"转人工复核{suffix}")
    return "；".join(_unique_reasons(reasons))


def _residual_paths(verdict: object) -> list[str]:
    """Return the field paths of one persisted intent verdict's residual rows."""
    if not isinstance(verdict, Mapping):
        return []
    residual = verdict.get("residual")
    if not isinstance(residual, list):
        return []
    return [
        str(row.get("path") or "")
        for row in residual
        if isinstance(row, Mapping) and row.get("path")
    ]


def _unique_reasons(reasons: Sequence[str]) -> list[str]:
    """Drop empty and repeated reasons while keeping their priority order."""
    unique: list[str] = []
    for reason in reasons:
        if reason and reason not in unique:
            unique.append(reason)
    return unique


def score_for(status: str) -> int | None:
    """Return the automatic score of one item status, ``None`` when not judged."""
    return _SCORE_BY_STATUS.get(status)


def result_label(status: str) -> str:
    """Return the summary label of one item status."""
    return _LABEL_BY_STATUS.get(status, "")


__all__ = [
    "DIFF_IDENTICAL",
    "render_intent_diff",
    "render_trajectory",
    "render_wire_frames",
    "result_label",
    "score_for",
    "verdict_reason",
]
