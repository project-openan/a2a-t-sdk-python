"""Candidate collection for the expectation annotation console.

An annotation starts from evidence: the tester batches this environment already ran. A batch file
(``runs/tester/<batchId>.json``) carries the per-item stages, including the slot errors of an
interception, while the server's negotiation messages live in the run store
(``negotiation_events``), joined by ``runId``.

Two readings of the evidence are possible and only one is useful:

* **consistent** — the observation agrees with what the case already expects (a blocked case really
  blocked; the negotiation has the expected round count and kinds). Only those may become an
  expectation, because promoting a divergence would freeze a defect as the oracle.
* **inconsistent** — shown as evidence and offered as a ``known_issue`` candidate instead.

Batches are listed from the directory, not from the tester's owner-scoped API: annotation is
cross-operator by nature, and the browser cookie that owns a batch is meaningless here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from eval.osp.expectations import MESSAGE_DIMENSION, SLOT_DIMENSION
from protocol.client_store import read_inbound_negotiation_events

_LOGGER = logging.getLogger("ran_energy_saving.annotate")

#: Protocol of the tester item versus the protocol recorded by the run store.
_RUNTIME_PROTOCOL: Final[dict[str, str]] = {"a2a-t": "a2a-t", "a2a": "pure"}

#: Slot errors need no round index; the store uses 0 for the case-level dimension.
CASE_ROUND: Final[int] = 0

#: How many batch files one scan reads unless the caller asks for more.
DEFAULT_MAX_BATCHES: Final[int] = 500


@dataclass(frozen=True)
class CorpusScan:
    """Result of one scan of the tester history."""

    observations: tuple[dict[str, object], ...]
    batches: tuple[dict[str, object], ...]
    window_start: str
    window_end: str
    #: Files not read (outside the window, unreadable, unknown schema) and batches past the limit.
    skipped: Mapping[str, int] = field(default_factory=dict)
    #: True when the limit cut the scan short; the console must say so instead of implying coverage.
    truncated: bool = False
    #: traceId/runId of the items a trace scan selected; empty for a time-window scan.
    matched_ids: frozenset[str] = frozenset()


def scan_corpus(
    *,
    runs_dir: Path,
    client_db_path: Path | None,
    cases: Mapping[int, Mapping[str, Any]],
    days: float = 1.0,
    max_batches: int = DEFAULT_MAX_BATCHES,
    now: datetime | None = None,
    traces: Sequence[str] | None = None,
) -> CorpusScan:
    """Collect the slot-error and negotiation-message candidates of the recent tester batches.

    Args:
        runs_dir: sample runs directory (the batches live in ``<runs_dir>/tester``).
        client_db_path: client store holding ``negotiation_events``; ``None`` when the process runs
            without persistence, in which case only the slot-error candidates are collected.
        cases: case documents keyed by id, used for the expectation each observation is screened
            against.
        days: size of the window, ending now.
        max_batches: limit on the batch files read, newest first.
        now: current time, injectable for tests.
        traces: explicit traceId/runId selection. When given, the time window is ignored and only
            the items carrying one of those identifiers are read: the supplied list *is* the
            operator's selection, and silently requiring it to fall inside the default window
            would report an explicitly named trace as unknown.
    """
    moment = now or datetime.now(UTC)
    selected_traces = frozenset(
        token.strip().lower() for token in traces or () if token.strip()
    )
    since = None if selected_traces else moment - timedelta(days=max(0.0, days))
    skipped: dict[str, int] = {}
    batches = _recent_batches(runs_dir / "tester", since=since, skipped=skipped)
    truncated = len(batches) > max_batches
    selected = batches[: max(0, max_batches)]
    if truncated:
        skipped["limit"] = len(batches) - len(selected)

    items: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    matched_ids: set[str] = set()
    for batch in selected:
        for item in batch.get("items") or ():
            if not isinstance(item, Mapping):
                continue
            identifiers = _item_ids(item)
            if selected_traces:
                hits = identifiers & selected_traces
                if not hits:
                    continue
                matched_ids |= hits
            items.append((batch, dict(item), dict(batch)))
    run_ids = [
        str(item["runId"])
        for _batch, item, _ in items
        if isinstance(item.get("runId"), str) and item.get("runId")
    ]
    events = (
        read_inbound_negotiation_events(client_db_path, run_ids)
        if client_db_path is not None and run_ids
        else {}
    )
    observations: list[dict[str, object]] = []
    for batch, item, _ in items:
        case_id = _int_value(item.get("caseId"))
        if case_id is None:
            continue
        case = cases.get(case_id)
        protocol = str(item.get("protocol") or "a2a-t")
        observations.extend(
            _slot_observations(batch, item, case, case_id, protocol, moment)
        )
        observations.extend(
            _message_observations(batch, item, case, case_id, protocol, events, moment)
        )
    observations.sort(
        key=lambda row: (str(row.get("observedAt") or ""), str(row.get("sourceBatch") or "")),
        reverse=True,
    )
    summary = tuple(
        {
            "batchId": str(batch.get("batchId") or ""),
            "createdAt": batch.get("createdAt"),
            "finishedAt": batch.get("finishedAt"),
            "decisionMode": batch.get("decisionMode"),
            "model": batch.get("modelLabel"),
            "nms": _batch_nms(batch),
            "nmsSim": bool(batch.get("nmsSim")),
            "rounds": batch.get("rounds"),
            "items": len(batch.get("items") or ()),
        }
        for batch in selected
    )
    return CorpusScan(
        observations=tuple(observations),
        batches=summary,
        window_start=(since or moment - timedelta(days=max(0.0, days))).isoformat(
            timespec="seconds"
        ),
        window_end=moment.isoformat(timespec="seconds"),
        skipped=skipped,
        truncated=truncated,
        matched_ids=frozenset(matched_ids),
    )


def _batch_nms(batch: Mapping[str, Any]) -> bool:
    """Return whether the batch ran against the real vendor NMS.

    The tester records the downstream it talked to as ``discovery: nms``; batches written before
    that key existed (and the offline simulator) used ``nms`` / ``nmsSim``. Reading only the old
    keys made every live batch look local, so the page's "只看真机" filter hid every candidate.
    """
    if batch.get("nms") or batch.get("nmsSim"):
        return True
    return str(batch.get("discovery") or "") == "nms"


def _item_ids(item: Mapping[str, Any]) -> set[str]:
    """Return the traceId and runId of one tester item, normalized for matching."""
    identifiers: set[str] = set()
    for key in ("traceId", "runId"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            identifiers.add(value.strip().lower())
    return identifiers


def _trace_id(item: Mapping[str, Any]) -> str:
    """Return the normalized traceId of one tester item, or an empty string."""
    value = item.get("traceId")
    return value.strip().lower() if isinstance(value, str) else ""


def _recent_batches(
    directory: Path,
    *,
    since: datetime | None,
    skipped: dict[str, int],
) -> list[dict[str, Any]]:
    """Return the batch documents written inside the window, newest first."""
    if not directory.is_dir():
        skipped["missing"] = 1
        return []
    since_ts = since.timestamp() if since is not None else None
    documents: list[tuple[float, dict[str, Any]]] = []
    for path in directory.glob("*.json"):
        try:
            modified = path.stat().st_mtime
        except OSError:
            skipped["unreadable"] = skipped.get("unreadable", 0) + 1
            continue
        if since_ts is not None and modified < since_ts:
            skipped["outside_window"] = skipped.get("outside_window", 0) + 1
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            skipped["unreadable"] = skipped.get("unreadable", 0) + 1
            continue
        if not isinstance(document, Mapping):
            skipped["unreadable"] = skipped.get("unreadable", 0) + 1
            continue
        if not isinstance(document.get("items"), list):
            skipped["unknown_schema"] = skipped.get("unknown_schema", 0) + 1
            continue
        document.setdefault("batchId", path.stem)
        documents.append((_batch_time(document), dict(document)))
    documents.sort(key=lambda pair: pair[0], reverse=True)
    return [document for _moment, document in documents]


def _batch_time(document: Mapping[str, Any]) -> float:
    """Return the timestamp a batch is ordered by (its finish time, else its creation time)."""
    for key in ("finishedAt", "createdAt", "startedAt"):
        moment = _parse_iso(document.get(key))
        if moment is not None:
            return moment.timestamp()
    return 0.0


def _slot_observations(
    batch: Mapping[str, Any],
    item: Mapping[str, Any],
    case: Mapping[str, Any] | None,
    case_id: int,
    protocol: str,
    moment: datetime,
) -> list[dict[str, object]]:
    """Return the slot-error observations of one item.

    The interception of a blocked case with a replacement request is recorded on its own segment,
    so that segment wins over the follow-up's validation: the case's ``slot_errors`` block
    describes the original request.
    """
    validate = _validate_stage(item)
    if validate is None:
        return []
    detail = validate.get("detail")
    if not isinstance(detail, Mapping):
        return []
    errors = detail.get("slotErrors")
    if not isinstance(errors, list) or not errors:
        return []
    expected = _expected_validate(case)
    verdict = str(detail.get("verdict") or validate.get("actual") or "")
    consistent = expected == "blocked" and verdict == "blocked"
    observed_at = str(batch.get("finishedAt") or batch.get("createdAt") or "")
    rows: list[dict[str, object]] = []
    for error in errors:
        if not isinstance(error, Mapping):
            continue
        rows.append(
            {
                "dimension": SLOT_DIMENSION,
                "caseId": case_id,
                "protocol": protocol,
                "round": CASE_ROUND,
                "kind": None,
                "code": str(error.get("code") or ""),
                "slotName": str(error.get("slotName") or ""),
                "facts": dict(error.get("facts") or {}),
                "message": str(error.get("message") or ""),
                "content": None,
                "sourceBatch": str(batch.get("batchId") or ""),
                "sourceRound": _int_value(item.get("round")) or 1,
                "runId": item.get("runId"),
                "traceId": _trace_id(item),
                "observedAt": observed_at,
                "model": batch.get("modelLabel"),
                "decisionMode": str(batch.get("decisionMode") or ""),
                "nms": _batch_nms(batch),
                "consistent": consistent,
                "observedVerdict": verdict,
                "expectedVerdict": expected,
                "collectedAt": moment.isoformat(timespec="seconds"),
            }
        )
    return rows


def _message_observations(
    batch: Mapping[str, Any],
    item: Mapping[str, Any],
    case: Mapping[str, Any] | None,
    case_id: int,
    protocol: str,
    events: Mapping[str, Sequence[Mapping[str, object]]],
    moment: datetime,
) -> list[dict[str, object]]:
    """Return the server-negotiation-message observations of one item."""
    run_id = item.get("runId")
    if not isinstance(run_id, str) or not run_id:
        return []
    runtime_protocol = _RUNTIME_PROTOCOL.get(protocol, protocol)
    rounds: list[Mapping[str, object]] = []
    for event in events.get(run_id, ()):
        if str(event.get("protocol") or "") != runtime_protocol:
            continue
        content = event.get("content")
        text = content.get("content") if isinstance(content, Mapping) else None
        if not isinstance(text, str) or not text.strip():
            continue
        rounds.append(event)
    if not rounds:
        return []
    expected_rounds, expected_kinds = _expected_negotiation(case, protocol)
    consistent = expected_rounds == len(rounds) and (
        expected_kinds is None
        or all(
            kind in {str(event.get("kind") or "") for event in rounds}
            for kind in expected_kinds
        )
    )
    observed_at = str(batch.get("finishedAt") or batch.get("createdAt") or "")
    rows: list[dict[str, object]] = []
    for index, event in enumerate(rounds):
        content = event.get("content")
        text = content.get("content") if isinstance(content, Mapping) else ""
        rows.append(
            {
                "dimension": MESSAGE_DIMENSION,
                "caseId": case_id,
                "protocol": protocol,
                "round": _int_value(event.get("round")) or index + 1,
                "kind": str(event.get("kind") or ""),
                "code": None,
                "slotName": None,
                "facts": {},
                "message": None,
                "content": str(text or ""),
                "sourceBatch": str(batch.get("batchId") or ""),
                "sourceRound": _int_value(item.get("round")) or 1,
                "runId": run_id,
                "traceId": _trace_id(item),
                "observedAt": observed_at,
                "model": batch.get("modelLabel"),
                "decisionMode": str(batch.get("decisionMode") or ""),
                "nms": _batch_nms(batch),
                "consistent": consistent,
                "observedRounds": len(rounds),
                "expectedRounds": expected_rounds,
                "collectedAt": moment.isoformat(timespec="seconds"),
            }
        )
    return rows


def _validate_stage(item: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the validation stage to read the slot errors from.

    The interception segment is preferred: for a blocked case with a replacement request the
    top-level stage judges the replacement, while the case's slot-error block describes the
    original request the interception blocked.
    """
    interception = item.get("interception")
    if isinstance(interception, Mapping):
        stages = interception.get("stages")
        if isinstance(stages, Mapping):
            stage = stages.get("validate")
            if isinstance(stage, Mapping):
                return stage
    stages = item.get("stages")
    if isinstance(stages, Mapping):
        stage = stages.get("validate")
        if isinstance(stage, Mapping):
            return stage
    return None


def _expected_validate(case: Mapping[str, Any] | None) -> str | None:
    """Return the case's expected A2A-T validation verdict."""
    block = _protocol_block(case, "a2a-t")
    value = block.get("validate")
    return value if isinstance(value, str) else None


def _expected_negotiation(
    case: Mapping[str, Any] | None,
    protocol: str,
) -> tuple[int, list[str] | None]:
    """Return the expected round count and kinds of one protocol."""
    block = _protocol_block(case, protocol)
    negotiation = block.get("negotiation")
    if not isinstance(negotiation, Mapping):
        return 0, None
    kinds = negotiation.get("kinds")
    return (
        _int_value(negotiation.get("expected_rounds")) or 0,
        [str(kind) for kind in kinds] if isinstance(kinds, list) else None,
    )


def _protocol_block(case: Mapping[str, Any] | None, protocol: str) -> Mapping[str, Any]:
    """Return one protocol's expectation block of one case."""
    if case is None:
        return {}
    expect = case.get("expect")
    protocols = expect.get("protocols") if isinstance(expect, Mapping) else None
    block = protocols.get(protocol) if isinstance(protocols, Mapping) else None
    return block if isinstance(block, Mapping) else {}


def _int_value(value: object) -> int | None:
    """Return an integer value, or ``None`` when it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _parse_iso(value: object) -> datetime | None:
    """Parse one ISO 8601 timestamp of the artifacts, or ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def case_documents(cases: Iterable[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    """Index case documents by id."""
    return {int(case["id"]): case for case in cases if isinstance(case.get("id"), int)}


__all__ = ["CASE_ROUND", "CorpusScan", "case_documents", "scan_corpus"]
