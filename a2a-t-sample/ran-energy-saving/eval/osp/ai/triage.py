"""Deterministic triage of one persisted tester batch.

The input is the batch document the tester writes under ``RUNS_DIR/tester/<batchId>.json``. The
output is one :class:`FailureRecord` per failing stage, plus the run/baseline identities kept
apart (a Modify item owns two runs) and a ``signals`` block that answers "which layer failed
first" from the batch JSON alone. No database, no network, no clock: the same document always
produces byte-identical ``failures.jsonl``. Root cause and ownership stay with the agent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from eval.osp.ai.paths import run_artifacts
from eval.osp.ai.types import FailureRecord, RunIdentity

#: Stage statuses that make a stage a failure. ``waiting`` is not green: the stage is unproven.
FAILING_STAGE_STATUSES: Final[frozenset[str]] = frozenset({"failed", "error", "waiting"})

#: Item statuses that produce an item-level record when no stage of that item failed.
FAILING_ITEM_STATUSES: Final[frozenset[str]] = frozenset({"failed", "error"})

#: Canonical stage order of the tester; record order must not depend on the JSON key order.
STAGE_ORDER: Final[tuple[str, ...]] = (
    "intent",
    "task_t",
    "validate",
    "send",
    "negotiate",
    "accepted",
    "working",
    "final",
)

#: The SDK runner writes ``blocked by <code>`` for a blocking validation error.
_BLOCKED_RE: Final[re.Pattern[str]] = re.compile(r"blocked by (\S+)")
#: The SDK runner writes ``negotiable (<code>)`` for an error the request is still sent with.
_NEGOTIABLE_RE: Final[re.Pattern[str]] = re.compile(r"negotiable \((\S+?)\)")
#: Markers of "the run ended before the request was dispatched" (client flow, both languages).
_ENDS_BEFORE_SEND_MARKERS: Final[tuple[str, ...]] = ("发送前结束", "ended before send")


def load_batch(path: Path) -> Mapping[str, object]:
    """Read and validate the tester batch document persisted at ``path``.

    Raises ``FileNotFoundError`` when the file is absent, ``json.JSONDecodeError`` when it is not
    JSON, and ``ValueError`` when it is not a batch snapshot (no ``batchId``, no ``items`` list).
    """
    document: object = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ValueError(f"batch document is not a JSON object: {path}")
    batch_id(document)
    items = document.get("items")
    if not isinstance(items, list):
        raise ValueError(f"batch document has no items list: {path}")
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise ValueError(f"batch item {index} is not a JSON object: {path}")
    return document


def batch_id(document: Mapping[str, object]) -> str:
    """Return the batch id of one batch document, raising ``ValueError`` when it is missing."""
    value = document.get("batchId")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("batch document has no batchId")
    return value


def failure_records(
    document: Mapping[str, object],
    *,
    batch_path: Path,
    runs_dir: Path,
) -> list[FailureRecord]:
    """Return one record per failing stage, in item order then canonical stage order.

    An item whose own status is ``error``/``failed`` but which has no failing stage contributes a
    single ``stage="item"`` record, so an infrastructure failure is never silently dropped.
    ``passed``, ``recorded`` and ``skipped`` stages are never reported.
    """
    records: list[FailureRecord] = []
    for item in _items(document):
        records.extend(_item_records(item, batch_path=batch_path, runs_dir=runs_dir))
    return records


def write_failures(records: Sequence[FailureRecord], path: Path) -> None:
    """Write ``records`` as JSONL (UTF-8, sorted keys, ``\\n`` endings), creating parent dirs."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for record in records]
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(lines)


def summarize(records: Sequence[FailureRecord]) -> dict[str, int]:
    """Count the records by ``stage``, ``protocol``, ``itemStatus`` and ``validationCode``.

    Keys are flat and prefixed (``stage.send``, ``protocol.a2a-t``, ``itemStatus.failed``,
    ``validationCode.content.param_missing``, ``validationCode.none``); ``total`` is always
    present and the result is sorted so two runs compare equal.
    """
    counts: dict[str, int] = {"total": len(records)}
    for record in records:
        _bump(counts, f"stage.{record.stage}")
        _bump(counts, f"protocol.{record.protocol or 'unknown'}")
        _bump(counts, f"itemStatus.{record.item_status or 'unknown'}")
        code = record.signals.get("validationCode")
        _bump(counts, "validationCode.none" if code is None else f"validationCode.{code}")
    return dict(sorted(counts.items()))


def first_diff(stage: Mapping[str, object]) -> dict[str, object] | None:
    """Return the first diff of one stage normalized to ``path/expected/actual/reason``."""
    diffs = stage.get("diffs")
    if not isinstance(diffs, Sequence) or isinstance(diffs, (str, bytes)):
        return None
    for diff in diffs:
        if isinstance(diff, Mapping):
            return {
                "path": _text(diff.get("path")),
                "expected": diff.get("expected"),
                "actual": diff.get("actual"),
                "reason": _text(diff.get("reason")),
            }
    return None


def signals_for(item: Mapping[str, object], stage: Mapping[str, object]) -> dict[str, object]:
    """Return the deterministic pre-classification of one failing stage.

    Only the batch JSON is read, so the same item always yields the same signals. The values say
    which layer is implicated, never who is to blame.
    """
    message = _text(stage.get("message"))
    code = _validation_code(message)
    run_ended = _ends_before_send(message)
    baseline_failed = _baseline_failed(item.get("baseline"))
    return {
        "category": _category(stage, code=code, run_ended=run_ended, baseline_failed=baseline_failed),
        "validationCode": code,
        # The SDK validator and the sample's own required-field gate both report ``content.*``;
        # only the transcript can tell them apart, so a pre-send block stays ``unknown``.
        "validationOrigin": "unknown" if run_ended else ("sdk" if _text(stage.get("stage")) == "validate" else None),
        "runEndedBeforeSend": run_ended,
        "negotiationRounds": _negotiation_rounds(item),
        "baselineFailed": baseline_failed,
        "itemStatus": _text(item.get("status")),
        "stageStatus": _text(stage.get("status")),
    }


def _category(
    stage: Mapping[str, object],
    *,
    code: str | None,
    run_ended: bool,
    baseline_failed: bool,
) -> str:
    """Return the first layer the evidence implicates, never the owner."""
    name = _text(stage.get("stage"))
    if name == "intent" or (code is not None and code.startswith("slot.")):
        return "sdk.slot"
    if name == "validate":
        return "sdk.validation"
    if run_ended:
        return "runtime.send"
    if name == "negotiate":
        return "runtime.negotiation"
    if baseline_failed:
        return "runtime.baseline"
    if name in {"accepted", "working", "final", "item"}:
        return "runtime.task"
    return "unknown"


def _item_records(
    item: Mapping[str, object],
    *,
    batch_path: Path,
    runs_dir: Path,
) -> list[FailureRecord]:
    """Return the failure records of one item: its failing stages, or one item-level record."""
    stages = item.get("stages")
    stage_map = stages if isinstance(stages, Mapping) else {}
    records: list[FailureRecord] = []
    for name in _ordered_stage_names(stage_map):
        stage = stage_map[name]
        if not isinstance(stage, Mapping):
            continue
        status = _text(stage.get("status"))
        if status not in FAILING_STAGE_STATUSES:
            continue
        records.append(
            _record(
                item,
                stage=name,
                stage_status=status,
                message=_text(stage.get("message")),
                expected=stage.get("expected"),
                actual=stage.get("actual"),
                first_diff=first_diff(stage),
                signals=signals_for(item, stage),
                batch_path=batch_path,
                runs_dir=runs_dir,
            )
        )
    status = _text(item.get("status"))
    if records or status not in FAILING_ITEM_STATUSES:
        return records
    message = _text(item.get("error"))
    item_stage: dict[str, object] = {"stage": "item", "status": status, "message": message}
    records.append(
        _record(
            item,
            stage="item",
            stage_status=status,
            message=message,
            expected=None,
            actual=None,
            first_diff=None,
            signals=signals_for(item, item_stage),
            batch_path=batch_path,
            runs_dir=runs_dir,
        )
    )
    return records


def _record(
    item: Mapping[str, object],
    *,
    stage: str,
    stage_status: str,
    message: str,
    expected: object,
    actual: object,
    first_diff: Mapping[str, object] | None,
    signals: Mapping[str, object],
    batch_path: Path,
    runs_dir: Path,
) -> FailureRecord:
    """Build one record from an item and the values already resolved for its stage."""
    baseline = item.get("baseline")
    run = _run_identity(item)
    return FailureRecord(
        case_id=_as_int(item.get("caseId")),
        name=_text(item.get("name")),
        protocol=_text(item.get("protocol")),
        operation=_operation(item),
        item_status=_text(item.get("status")),
        stage=stage,
        stage_status=stage_status,
        message=message,
        expected=expected,
        actual=actual,
        first_diff=first_diff,
        signals=signals,
        run=run,
        baseline=dict(baseline) if isinstance(baseline, Mapping) else None,
        artifacts=run_artifacts(runs_dir, run.run_id, batch=batch_path),
    )


def _run_identity(item: Mapping[str, object]) -> RunIdentity:
    """Return the four identities of one item, leaving each absent one as ``None``."""
    answer = item.get("answer")
    baseline = item.get("baseline")
    task_id = answer.get("taskId") if isinstance(answer, Mapping) else None
    context_id = baseline.get("contextId") if isinstance(baseline, Mapping) else None
    return RunIdentity(
        run_id=_optional_text(item.get("runId")),
        trace_id=_optional_text(item.get("traceId")),
        task_id=_optional_text(task_id),
        context_id=_optional_text(context_id),
    )


def _operation(item: Mapping[str, object]) -> str:
    """Return the case operation (``Create`` / ``Modify``) frozen in the item refs."""
    refs = item.get("refs")
    if not isinstance(refs, Mapping):
        return ""
    return _text(refs.get("operation"))


def _validation_code(message: str) -> str | None:
    """Return the SDK validation code carried by a ``validate`` stage message, else ``None``."""
    match = _BLOCKED_RE.search(message) or _NEGOTIABLE_RE.search(message)
    return match.group(1) if match is not None else None


def _ends_before_send(message: str) -> bool:
    """Return whether the message says the run ended before the request was dispatched."""
    lowered = message.lower()
    return any(marker in lowered for marker in _ENDS_BEFORE_SEND_MARKERS)


def _negotiation_rounds(item: Mapping[str, object]) -> int:
    """Return the observed negotiation rounds of one item, ``0`` when the stage did not run.

    The tester records ``actual={"rounds": n, "kinds": [...]}``; a bare integer actual is accepted
    too so a hand-written or older batch document still classifies.
    """
    stages = item.get("stages")
    if not isinstance(stages, Mapping):
        return 0
    stage = stages.get("negotiate")
    if not isinstance(stage, Mapping):
        return 0
    actual = stage.get("actual")
    if _is_int(actual):
        return actual
    if isinstance(actual, Mapping):
        rounds = actual.get("rounds")
        if _is_int(rounds):
            return rounds
    return 0


def _baseline_failed(baseline: object) -> bool:
    """Return whether the item's Modify baseline is recorded as failed."""
    if not isinstance(baseline, Mapping):
        return False
    error = baseline.get("error")
    if isinstance(error, str):
        if error.strip():
            return True
    elif error:
        return True
    return bool(baseline.get("failed"))


def _items(document: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Return the mapping items of a batch document, skipping non-mapping entries."""
    items = document.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, Mapping)]


def _ordered_stage_names(stages: Mapping[str, object]) -> list[str]:
    """Return the stage keys in the tester's execution order, unknown keys last and sorted."""
    known = [name for name in STAGE_ORDER if name in stages]
    return known + sorted(name for name in stages if name not in STAGE_ORDER)


def _bump(counts: dict[str, int], key: str) -> None:
    """Increment ``key`` in ``counts``, starting from zero."""
    counts[key] = counts.get(key, 0) + 1


def _is_int(value: object) -> bool:
    """Return whether ``value`` is an integer that is not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool)


def _as_int(value: object) -> int:
    """Return ``value`` as an integer, or ``0`` when it is not one."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def _text(value: object) -> str:
    """Return ``value`` when it is a string, else ``""``."""
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    """Return a non-empty string, else ``None``: an absent identity is never guessed."""
    return value if isinstance(value, str) and value else None
