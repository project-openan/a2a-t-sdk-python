"""Render one paired tester batch as the A2A-T vs A2A acceptance tables.

The batch snapshot already carries ``pairs`` (the per-case join) and per-item evidence. This
module turns it into the three tables an acceptance report needs, and reads each run's
``wire.jsonl`` for the protocol fingerprint so every row points back to a frame:

1. the protocol fingerprint (extension header, Task-T/Negotiation-T metadata, Task identity),
2. the difference matrix (the ``pairs`` rows with their A2A-T − A2A deltas),
3. the cost-effect table (per protocol runs/passed/tokens/duration/rounds),

plus a failure bucket so vendor and network noise is not reported as a protocol result.

Usage::

    python -m eval.osp.pair_report runs/tester/batch-xxxx.json
    python -m eval.osp.pair_report runs/tester/batch-xxxx.json --out report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

#: Header the A2A-T client sets; a pure A2A request does not carry it.
EXTENSION_HEADER: Final[str] = "a2a-extensions"
#: Substring of the extension URIs, matched case-insensitively inside the metadata keys.
TASK_T_MARKER: Final[str] = "task-t"
NEGOTIATION_T_MARKER: Final[str] = "negotiation-t"

#: Failure buckets. A protocol result must not absorb the other two.
BUCKET_PROTOCOL: Final[str] = "protocol"
BUCKET_VENDOR: Final[str] = "vendor"
BUCKET_NETWORK: Final[str] = "network"
BUCKET_INFRA: Final[str] = "infra"
BUCKET_OTHER: Final[str] = "other"

_INFRA_HINTS: Final[tuple[str, ...]] = (
    "promptgenerationerror",
    "the llm response is invalid",
    "decisionprovidererror",
    "model",
)

_VENDOR_HINTS: Final[tuple[str, ...]] = (
    "429",
    "500",
    "503",
    "conflict",
    "submitted",
    "not available",
    "task state",
)
_NETWORK_HINTS: Final[tuple[str, ...]] = (
    "timeout",
    "timed out",
    "connection",
    "refused",
    "unreachable",
    "resolve",
    "certificate",
)


def load_batch(path: Path) -> dict[str, Any]:
    """Load one persisted batch snapshot."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path} is not a batch document")
    return document


def build_report(
    batch: Mapping[str, Any],
    *,
    runs_dir: Path | None = None,
    title: str | None = None,
) -> str:
    """Return the markdown report of one batch."""
    items = [item for item in batch.get("items") or [] if isinstance(item, Mapping)]
    pairs = [pair for pair in batch.get("pairs") or [] if isinstance(pair, Mapping)]
    lines: list[str] = [f"# {title or str(batch.get('batchId') or 'batch')}", ""]
    lines += _context(batch)
    lines += _fingerprint_table(items, runs_dir)
    lines += _difference_table(pairs)
    lines += _cost_table(items)
    lines += _failure_table(items)
    return "\n".join(lines).rstrip() + "\n"


def _context(batch: Mapping[str, Any]) -> list[str]:
    """Return the batch-level facts a reader needs before the numbers."""
    excluded = [value for value in batch.get("excludedCaseIds") or [] if isinstance(value, int)]
    rows = [
        "| 项 | 值 |",
        "|---|---|",
        f"| 协议 | {', '.join(str(p) for p in batch.get('protocols') or [])} |",
        f"| 执行到 | {batch.get('stopAfter')} |",
        f"| transport / discovery | {batch.get('transport')} / {batch.get('discovery')} |",
        f"| decisionMode | {batch.get('decisionMode')} |",
        f"| 用例数 | {len(batch.get('items') or [])} 项 / {len(batch.get('pairs') or [])} 对 |",
        f"| 排除用例（blocked，两臂文本不同） | {', '.join(str(v) for v in excluded) or '无'} |",
    ]
    return rows + [""]


def _fingerprint_table(
    items: Sequence[Mapping[str, Any]], runs_dir: Path | None
) -> list[str]:
    """Return the per-item wire fingerprint."""
    lines = ["## 1. 协议指纹对照", ""]
    if runs_dir is None:
        lines += ["未提供 runs 目录，跳过 wire 指纹。", ""]
        return lines
    lines += [
        "| case | 协议 | A2A-Extensions | Task-T | Negotiation-T | 请求带 taskId | 协商轮次 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in items:
        run_id = item.get("runId")
        fingerprint = (
            _request_fingerprint(_request_frame(runs_dir, str(run_id)))
            if isinstance(run_id, str) and run_id
            else _empty_fingerprint()
        )
        rounds = _rounds(item)
        lines.append(
            "| {case} | {protocol} | {extensions} | {task_t} | {negotiation_t} | "
            "{task_id} | {rounds} |".format(
                case=item.get("caseId"),
                protocol=item.get("protocol"),
                extensions="是" if fingerprint["extensions"] else "否",
                task_t="是" if fingerprint["taskT"] else "否",
                negotiation_t="是" if fingerprint["negotiationT"] else "否",
                task_id="是" if fingerprint["taskId"] else "否",
                rounds="—" if rounds is None else rounds,
            )
        )
    return lines + [""]


def _difference_table(pairs: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return the paired difference matrix."""
    lines = [
        "## 2. 差异矩阵（差值 = A2A-T − A2A）",
        "",
        "| case | 可比 | A2A-T | A2A | 轮次 Δ | 时延 Δ(ms) | token Δ |",
        "|---|---|---|---|---|---|---|",
    ]
    for pair in pairs:
        protocols = pair.get("protocols")
        entries = protocols if isinstance(protocols, Mapping) else {}
        a2at_entry, pure_entry = entries.get("a2a-t"), entries.get("a2a")
        a2at = a2at_entry if isinstance(a2at_entry, Mapping) else {}
        pure = pure_entry if isinstance(pure_entry, Mapping) else {}
        lines.append(
            "| {case} | {comparable} | {a2at} | {pure} | {rounds} | {duration} | {tokens} |".format(
                case=pair.get("caseId"),
                comparable="是" if pair.get("comparable") else "**否**",
                a2at=a2at.get("status"),
                pure=pure.get("status"),
                rounds=_format_delta(pair.get("roundsDelta")),
                duration=_format_delta(pair.get("durationDeltaMs")),
                tokens=_format_delta(pair.get("tokensDelta")),
            )
        )
    if not pairs:
        lines.append("| — | — | — | — | — | — | — |")
    return lines + [""]


def _cost_table(items: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return the per-protocol cost-effect table."""
    lines = [
        "## 3. 成本-效果",
        "",
        "| 协议 | 项数 | passed | 均值时延(ms) | 均值轮次 | token 合计 | token 上报项 |",
        "|---|---|---|---|---|---|---|",
    ]
    for protocol in sorted({str(item.get("protocol")) for item in items}):
        rows = [item for item in items if str(item.get("protocol")) == protocol]
        passed = sum(1 for row in rows if row.get("status") == "passed")
        durations = [
            row["durationMs"]
            for row in rows
            if isinstance(row.get("durationMs"), (int, float))
            and not isinstance(row.get("durationMs"), bool)
        ]
        rounds = [value for value in (_rounds(row) for row in rows) if value is not None]
        tokens = [
            row["tokens"]["total"]
            for row in rows
            if isinstance(row.get("tokens"), Mapping)
            and isinstance(row["tokens"].get("total"), (int, float))
            and not isinstance(row["tokens"].get("total"), bool)
        ]
        lines.append(
            "| {protocol} | {count} | {passed} | {duration} | {rounds} | {tokens} | {reported} |".format(
                protocol=protocol,
                count=len(rows),
                passed=passed,
                duration=_mean(durations),
                rounds=_mean(rounds),
                tokens=int(sum(tokens)) if tokens else "—",
                reported=len(tokens),
            )
        )
    return lines + [""]


def _failure_table(items: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return the failure bucket table."""
    buckets: dict[str, list[str]] = {}
    for item in items:
        if item.get("status") not in {"failed", "error"}:
            continue
        error = item.get("error")
        # A failed stage without a thrown error is an assertion mismatch, which is the only
        # bucket that belongs to the protocol itself.
        bucket = _classify(str(error)) if isinstance(error, str) and error else BUCKET_PROTOCOL
        buckets.setdefault(bucket, []).append(
            f"{item.get('caseId')}/{item.get('protocol')}"
        )
    lines = [
        "## 4. 失败分类",
        "",
        "| 归属 | 数量 | 项 |",
        "|---|---|---|",
    ]
    for bucket in (BUCKET_PROTOCOL, BUCKET_INFRA, BUCKET_VENDOR, BUCKET_NETWORK, BUCKET_OTHER):
        rows = buckets.get(bucket) or []
        if not rows:
            continue
        lines.append(f"| {bucket} | {len(rows)} | {', '.join(rows)} |")
    if len(lines) == 4:
        lines.append("| — | 0 | 无失败项 |")
    return lines + [""]


def _classify(error: str) -> str:
    """Bucket one failure message by its most likely owner."""
    lowered = error.lower()
    if any(hint in lowered for hint in _NETWORK_HINTS):
        return BUCKET_NETWORK
    if any(hint in lowered for hint in _VENDOR_HINTS):
        return BUCKET_VENDOR
    if any(hint in lowered for hint in _INFRA_HINTS):
        return BUCKET_INFRA
    return BUCKET_PROTOCOL


def _request_frame(runs_dir: Path, run_id: str) -> Mapping[str, Any] | None:
    """Return the first outbound frame of one run."""
    path = runs_dir / run_id / "wire.jsonl"
    try:
        handle = path.open(encoding="utf-8")
    except OSError:
        return None
    with handle:
        for line in handle:
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            if isinstance(frame, Mapping) and frame.get("dir") == "out":
                return frame
    return None


def _empty_fingerprint() -> dict[str, bool]:
    """Return a fingerprint for an item without a run."""
    return {"extensions": False, "taskT": False, "negotiationT": False, "taskId": False}


def _request_fingerprint(frame: Mapping[str, Any] | None) -> dict[str, bool]:
    """Return the protocol markers of one outbound A2A request."""
    if frame is None:
        return _empty_fingerprint()
    headers = frame.get("headers")
    header_names = (
        {str(name).lower() for name in headers} if isinstance(headers, Mapping) else set()
    )
    body = frame.get("body")
    message = body.get("message") if isinstance(body, Mapping) else None
    if not isinstance(message, Mapping):
        message = {}
    metadata = message.get("metadata")
    keys = {str(key).lower() for key in metadata} if isinstance(metadata, Mapping) else set()
    return {
        "extensions": EXTENSION_HEADER in header_names,
        "taskT": any(TASK_T_MARKER in key for key in keys),
        "negotiationT": any(NEGOTIATION_T_MARKER in key for key in keys),
        "taskId": bool(message.get("taskId")),
    }


def _rounds(item: Mapping[str, Any]) -> int | None:
    """Return the rounds recorded by the item's ``negotiate`` stage."""
    stages = item.get("stages")
    if not isinstance(stages, Mapping):
        return None
    stage = stages.get("negotiate")
    if not isinstance(stage, Mapping):
        return None
    actual = stage.get("actual")
    if not isinstance(actual, Mapping):
        return None
    rounds = actual.get("rounds")
    if isinstance(rounds, bool) or not isinstance(rounds, int):
        return None
    return rounds


def _format_delta(value: object) -> str:
    """Format one numeric delta, marking a missing side instead of printing a zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{value:+.0f}"


def _mean(values: Sequence[float]) -> str:
    """Return the mean of the values, ``—`` when empty."""
    if not values:
        return "—"
    return f"{sum(values) / len(values):.0f}"


def main(argv: Iterable[str] | None = None) -> int:
    """Render one batch file to stdout or a file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path, help="persisted batch JSON (runs/tester/batch-*.json)")
    parser.add_argument("--out", type=Path, default=None, help="write the markdown here")
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="directory holding <runId>/wire.jsonl (default: the batch file's grandparent)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    batch_path: Path = args.batch
    runs_dir: Path = args.runs_dir or batch_path.parent.parent
    try:
        batch = load_batch(batch_path)
    except (OSError, ValueError) as exc:
        print(f"[pair_report] {exc}", file=sys.stderr)
        return 2
    report = build_report(batch, runs_dir=runs_dir)
    if args.out is None:
        sys.stdout.write(report)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"[pair_report] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
