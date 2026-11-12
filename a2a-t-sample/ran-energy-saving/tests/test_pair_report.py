"""Tests of the paired-batch acceptance report."""

from __future__ import annotations

import json
from pathlib import Path

from eval.osp.pair_report import build_report


def _wire(runs_dir: Path, run_id: str, *, extensions: bool, metadata: bool) -> None:
    """Write one outbound request frame for a run."""
    directory = runs_dir / run_id
    directory.mkdir(parents=True, exist_ok=True)
    headers = {"A2A-Version": "1.0"}
    if extensions:
        headers["A2A-Extensions"] = "https://example/Task-T/v1,https://example/Negotiation-T/v1"
    message: dict[str, object] = {
        "messageId": "m-1",
        "role": "ROLE_USER",
        "parts": [{"text": "operator text"}],
    }
    if metadata:
        message["metadata"] = {
            "https://example/Task-T/v1": "## Task Target\n...",
            "https://example/Negotiation-T/v1": "...",
        }
    frame = {
        "dir": "out",
        "method": "POST",
        "url": "https://nms.example/a2a/json/message:send",
        "headers": headers,
        "body": {"message": message},
    }
    (directory / "wire.jsonl").write_text(json.dumps(frame) + "\n", encoding="utf-8")


def _batch() -> dict[str, object]:
    """Return a minimal paired batch snapshot."""
    item = lambda run_id, protocol, status, rounds: {  # noqa: E731 - terse fixture
        "caseId": 7,
        "protocol": protocol,
        "status": status,
        "runId": run_id,
        "durationMs": 1000 if protocol == "a2a-t" else 3000,
        "tokens": {"total": 900 if protocol == "a2a-t" else 300},
        "stages": {"negotiate": {"actual": {"rounds": rounds}}},
        "error": None,
    }
    return {
        "batchId": "batch-test",
        "protocols": ["a2a-t", "a2a"],
        "stopAfter": "negotiate",
        "transport": "webhook",
        "discovery": "nms",
        "decisionMode": "live",
        "excludedCaseIds": [22],
        "items": [
            item("run-a2at", "a2a-t", "passed", 1),
            item("run-a2a", "a2a", "failed", 3),
            {
                "caseId": 9,
                "protocol": "a2a",
                "status": "error",
                "runId": None,
                "error": "The vendor returned 429 quota exceeded",
                "stages": {},
            },
        ],
        "pairs": [
            {
                "caseId": 7,
                "comparable": True,
                "roundsDelta": -2.0,
                "durationDeltaMs": -2000.0,
                "tokensDelta": 600.0,
                "protocols": {
                    "a2a-t": {"status": "passed"},
                    "a2a": {"status": "failed"},
                },
            }
        ],
    }


def test_report_renders_the_fingerprint_from_the_wire(tmp_path: Path) -> None:
    _wire(tmp_path, "run-a2at", extensions=True, metadata=True)
    _wire(tmp_path, "run-a2a", extensions=False, metadata=False)

    report = build_report(_batch(), runs_dir=tmp_path)

    assert "A2A-Extensions" in report
    a2at_row = next(line for line in report.splitlines() if "| 7 | a2a-t |" in line)
    pure_row = next(line for line in report.splitlines() if "| 7 | a2a |" in line)
    assert a2at_row.count("是") >= 3
    assert pure_row.count("否") >= 3


def test_report_states_the_excluded_cases_and_the_deltas(tmp_path: Path) -> None:
    _wire(tmp_path, "run-a2at", extensions=True, metadata=True)
    _wire(tmp_path, "run-a2a", extensions=False, metadata=False)

    report = build_report(_batch(), runs_dir=tmp_path)

    assert "排除用例" in report and "22" in report
    assert "-2" in report and "-2000" in report and "+600" in report


def test_report_buckets_vendor_failures_apart_from_protocol_ones(tmp_path: Path) -> None:
    _wire(tmp_path, "run-a2at", extensions=True, metadata=True)
    _wire(tmp_path, "run-a2a", extensions=False, metadata=False)

    report = build_report(_batch(), runs_dir=tmp_path)

    assert "| vendor | 1 | 9/a2a |" in report
    assert "| protocol | 1 | 7/a2a |" in report


def test_report_without_a_runs_dir_skips_the_fingerprint(tmp_path: Path) -> None:
    report = build_report(_batch(), runs_dir=None)

    assert "跳过 wire 指纹" in report
    assert "## 2. 差异矩阵" in report


def test_report_buckets_llm_failures_as_infra(tmp_path: Path) -> None:
    """An extraction failure before the wire is not a protocol result."""
    batch = _batch()
    batch["items"] = [
        {
            "caseId": 35,
            "protocol": "a2a-t",
            "status": "error",
            "runId": None,
            "error": "PromptGenerationError: The LLM response is invalid (step: slot extraction)",
            "stages": {},
        }
    ]
    batch["pairs"] = []

    report = build_report(batch, runs_dir=tmp_path)

    assert "| infra | 1 | 35/a2a-t |" in report
