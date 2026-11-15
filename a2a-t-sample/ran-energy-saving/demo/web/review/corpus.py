"""Evidence collection for the acceptance review console.

The review page reads the same artifacts as the tester and the annotation console:

* ``runs/tester/<batchId>.json`` — one tester batch snapshot, with the per-item stages, the SDK
  extraction (``intent``), the downstream structured reading (``serverIntent``) and the frozen
  ``refs``;
* ``runs/client-state.sqlite3`` — the server messages of each round, joined by ``runId``. The tester
  only stored them when the case already expected a message, so the join is what makes older
  batches usable without a re-run.

One observation is one tester item. The evidence is trimmed to what judging and display need; the
expectations are deliberately *not* copied, because the review re-judges against the current corpus
rather than the corpus that was current when the run started.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from eval.osp.review import evidence_input_signature
from protocol.client_store import read_inbound_negotiation_events

_LOGGER = logging.getLogger("ran_energy_saving.review")

#: Protocol of the tester item versus the protocol recorded by the run store.
_RUNTIME_PROTOCOL: Final[dict[str, str]] = {"a2a-t": "a2a-t", "a2a": "pure"}

#: How many batch files one scan reads unless the caller asks for more.
DEFAULT_MAX_BATCHES: Final[int] = 500

#: Stage keys whose actual value is useful for re-judging; everything else is display noise.
_STAGE_KEYS: Final[tuple[str, ...]] = (
    "stage",
    "status",
    "message",
    "actual",
    "diffs",
    "detail",
    "durationMs",
)


@dataclass(frozen=True)
class ReviewScan:
    """Result of one scan of the tester history."""

    observations: tuple[dict[str, object], ...]
    batches: tuple[dict[str, object], ...]
    window_start: str
    window_end: str
    skipped: Mapping[str, int] = field(default_factory=dict)
    truncated: bool = False


def scan_evidence(
    *,
    runs_dir: Path,
    client_db_path: Path | None,
    cases: Mapping[int, Mapping[str, Any]],
    days: float = 7.0,
    max_batches: int = DEFAULT_MAX_BATCHES,
    now: datetime | None = None,
) -> ReviewScan:
    """Collect the reviewable items of the recent tester batches.

    Args:
        runs_dir: sample runs directory (the batches live in ``<runs_dir>/tester``).
        client_db_path: client store holding ``negotiation_events``; ``None`` leaves the stored
            stage messages as the only message evidence.
        cases: case documents keyed by id; an item whose case left the corpus is skipped.
        days: size of the window, ending now.
        max_batches: limit on the batch files read, newest first.
        now: current time, injectable for tests.
    """
    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=max(0.0, days))
    skipped: dict[str, int] = {}
    batches = _recent_batches(runs_dir / "tester", since=since, skipped=skipped)
    truncated = len(batches) > max_batches
    selected = batches[: max(0, max_batches)]
    if truncated:
        skipped["limit"] = len(batches) - len(selected)

    items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for batch in selected:
        for item in batch.get("items") or ():
            if not isinstance(item, Mapping):
                continue
            if not isinstance(item.get("stages"), Mapping):
                continue
            items.append((batch, dict(item)))
    run_ids = [
        str(item["runId"])
        for _batch, item in items
        if isinstance(item.get("runId"), str) and item.get("runId")
    ]
    events = (
        read_inbound_negotiation_events(client_db_path, run_ids)
        if client_db_path is not None and run_ids
        else {}
    )
    observations: list[dict[str, object]] = []
    for batch, item in items:
        case_id = _int_value(item.get("caseId"))
        if case_id is None or case_id not in cases:
            continue
        protocol = str(item.get("protocol") or "a2a-t")
        observations.append(_observation(batch, item, case_id, protocol, events))
    observations.sort(
        key=lambda row: (str(row.get("observedAt") or ""), str(row.get("batchId") or "")),
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
            "discovery": str(batch.get("discovery") or ""),
            "transport": str(batch.get("transport") or ""),
            "rounds": batch.get("rounds"),
            "items": len(batch.get("items") or ()),
        }
        for batch in selected
    )
    return ReviewScan(
        observations=tuple(observations),
        batches=summary,
        window_start=since.isoformat(timespec="seconds"),
        window_end=moment.isoformat(timespec="seconds"),
        skipped=skipped,
        truncated=truncated,
    )


def _observation(
    batch: Mapping[str, Any],
    item: Mapping[str, Any],
    case_id: int,
    protocol: str,
    events: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, object]:
    """Build one review observation from a stored tester item."""
    observed_at = str(batch.get("finishedAt") or batch.get("createdAt") or "")
    messages = _messages(events, item.get("runId"), protocol)
    evidence: dict[str, object] = {
        "caseId": case_id,
        "protocol": protocol,
        "round": _int_value(item.get("round")) or 1,
        "status": str(item.get("status") or ""),
        "runId": item.get("runId"),
        "traceId": item.get("traceId"),
        "stages": _stages(item.get("stages")),
        "intent": _mapping_or_none(item.get("intent")),
        "serverIntent": _mapping_or_none(item.get("serverIntent")),
        "intentMeta": _mapping(item.get("intentMeta")),
        "refs": _mapping(item.get("refs")),
        "interception": _interception(item.get("interception")),
        "messages": messages,
        "durationMs": item.get("durationMs"),
        "tokens": _mapping(item.get("tokens")),
        "langfuseUrl": item.get("langfuseUrl"),
    }
    return {
        "caseId": case_id,
        "protocol": protocol,
        "batchId": str(batch.get("batchId") or ""),
        "sourceRound": _int_value(item.get("round")) or 1,
        "runId": item.get("runId") if isinstance(item.get("runId"), str) else None,
        "traceId": item.get("traceId") if isinstance(item.get("traceId"), str) else None,
        "observedAt": observed_at,
        "model": batch.get("modelLabel"),
        "decisionMode": str(batch.get("decisionMode") or ""),
        "nms": _batch_nms(batch),
        "nmsSim": bool(batch.get("nmsSim")),
        "discovery": str(batch.get("discovery") or ""),
        "transport": str(batch.get("transport") or ""),
        "status": str(item.get("status") or ""),
        "inputSignature": evidence_input_signature(evidence),
        "evidence": evidence,
    }


def _stages(raw: object) -> dict[str, object]:
    """Return the stage map trimmed to the keys judging needs."""
    stages = raw if isinstance(raw, Mapping) else {}
    trimmed: dict[str, object] = {}
    for name, stage in stages.items():
        if not isinstance(name, str) or not isinstance(stage, Mapping):
            continue
        trimmed[name] = {
            key: stage.get(key) for key in _STAGE_KEYS if key in stage
        }
    return trimmed


def _interception(raw: object) -> dict[str, object] | None:
    """Return the interception segment trimmed to the stages it carries."""
    if not isinstance(raw, Mapping):
        return None
    return {
        # The intercepted and the replacement request are evidence in their own right: the review
        # compares what the operator wrote, what was blocked and what the run actually dispatched.
        "input": raw.get("input"),
        "followupInput": raw.get("followupInput"),
        "stages": _stages(raw.get("stages")),
        "verdict": raw.get("verdict"),
        "reason": raw.get("reason"),
        "error": raw.get("error"),
    }


def _messages(
    events: Mapping[str, Sequence[Mapping[str, object]]],
    run_id: object,
    protocol: str,
) -> list[dict[str, object]]:
    """Return the server messages of one run for one protocol."""
    if not isinstance(run_id, str) or not run_id:
        return []
    runtime = _RUNTIME_PROTOCOL.get(protocol, protocol)
    rows: list[dict[str, object]] = []
    for event in events.get(run_id, ()):
        if str(event.get("protocol") or "") != runtime:
            continue
        content = event.get("content")
        text = content.get("content") if isinstance(content, Mapping) else None
        if not isinstance(text, str) or not text.strip():
            continue
        rows.append(
            {
                "round": _int_value(event.get("round")) or len(rows) + 1,
                "kind": str(event.get("kind") or ""),
                "content": text,
            }
        )
    return rows


def _recent_batches(
    directory: Path,
    *,
    since: datetime,
    skipped: dict[str, int],
) -> list[dict[str, Any]]:
    """Return the batch documents written inside the window, newest first."""
    if not directory.is_dir():
        skipped["missing"] = 1
        return []
    since_ts = since.timestamp()
    documents: list[tuple[float, dict[str, Any]]] = []
    for path in directory.glob("*.json"):
        try:
            modified = path.stat().st_mtime
        except OSError:
            skipped["unreadable"] = skipped.get("unreadable", 0) + 1
            continue
        if modified < since_ts:
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


def _batch_nms(batch: Mapping[str, Any]) -> bool:
    """Return whether the batch ran against the real vendor NMS.

    Mirrors the annotation console: live batches record the downstream as ``discovery: nms``;
    older batches used ``nms`` / ``nmsSim``.
    """
    if batch.get("nms") or batch.get("nmsSim"):
        return True
    return str(batch.get("discovery") or "") == "nms"


def _int_value(value: object) -> int | None:
    """Return an integer value, or ``None`` when it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _mapping(value: object) -> dict[str, Any]:
    """Return a plain mapping, or an empty one."""
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_or_none(value: object) -> dict[str, Any] | None:
    """Return a plain mapping, or ``None``."""
    return dict(value) if isinstance(value, Mapping) else None


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


__all__ = ["DEFAULT_MAX_BATCHES", "ReviewScan", "case_documents", "scan_evidence"]
