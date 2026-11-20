"""Tests of the deterministic batch triage (failure records, signals, JSONL output)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from eval.osp.ai.triage import (
    batch_id,
    failure_records,
    first_diff,
    load_batch,
    signals_for,
    summarize,
    write_failures,
)
from eval.osp.ai.types import FailureRecord

BATCH_ID = "20261110-120000-osp-ai"
TRACE = "0123456789abcdef0123456789abcdef"


def _stage(
    name: str,
    status: str,
    *,
    message: str = "",
    expected: object = None,
    actual: object = None,
    diffs: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "stage": name,
        "status": status,
        "message": message,
        "expected": expected,
        "actual": actual,
        "diffs": diffs or [],
        "durationMs": 7,
    }


def _item(
    case_id: int,
    name: str,
    protocol: str,
    status: str,
    stages: dict[str, object],
    **extra: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "caseId": case_id,
        "name": name,
        "protocol": protocol,
        "status": status,
        "stages": stages,
        "runId": None,
        "durationMs": 11,
        "tokens": {},
        "error": None,
        "baseline": None,
        "scratch": None,
        "attempts": [],
        "traceId": "",
        "intent": None,
        "serverIntent": None,
        "intentMeta": {},
        "refs": {"operation": "Create"},
        "answer": None,
        "interception": None,
    }
    item.update(extra)
    return item


def _batch_document() -> dict[str, Any]:
    """A synthetic batch with one item per triage branch."""
    return {
        "schema": "osp-tester/v3",
        "batchId": BATCH_ID,
        "status": "finished",
        "caseIds": [1, 2, 3, 20, 34, 40, 50],
        "protocols": ["a2a-t", "a2a"],
        "stopAfter": "final",
        "approval": None,
        "timeoutSeconds": 60.0,
        "workingTimeoutSeconds": 30.0,
        "decisionMode": "scripted",
        "keepTasks": False,
        "transport": "sse",
        "discovery": "direct",
        "ownerId": "osp-ai",
        "createdAt": "2026-11-10T04:00:00.000Z",
        "startedAt": "2026-11-10T04:00:01.000Z",
        "finishedAt": "2026-11-10T04:05:00.000Z",
        "currentIndex": None,
        "error": None,
        "summary": {"total": 7, "passed": 3},
        "items": [
            # Green item: nothing to report, and a ``recorded`` stage is not a failure.
            _item(
                1,
                "速率归位",
                "a2a-t",
                "passed",
                {
                    "intent": _stage("intent", "passed", actual={"Operation type": "Create"}),
                    "send": _stage("send", "passed"),
                    "working": _stage("working", "recorded", message="workbook 不判定该阶段"),
                },
                runId="run-pass-1",
                traceId=TRACE,
                answer={"taskId": "task-pass-1"},
            ),
            # Failed intent stage carrying diffs; the extra diff key must be dropped.
            _item(
                2,
                "时间窗归位",
                "a2a-t",
                "failed",
                {
                    "intent": _stage(
                        "intent",
                        "failed",
                        message="意图不一致。",
                        expected={"time": "08:00-12:00", "rate": "2Mbps"},
                        actual={"time": "09:00-12:00", "rate": "2Mbps"},
                        diffs=[
                            {
                                "path": "Task Context.time",
                                "expected": "08:00-12:00",
                                "actual": "09:00-12:00",
                                "reason": "window shifted",
                                "debug": "dropped",
                            },
                            {"path": "Task Context.rate", "expected": "2Mbps", "actual": "2Mbps"},
                        ],
                    ),
                    "send": _stage("send", "passed"),
                },
                runId="run-2",
                traceId=TRACE,
                answer={"taskId": "task-2"},
            ),
            # Blocked validation: the SDK code is the whole deterministic signal.
            _item(
                3,
                "缺速率拦截",
                "a2a-t",
                "failed",
                {
                    "validate": _stage(
                        "validate",
                        "failed",
                        message="blocked by content.param_missing",
                        expected="blocked",
                        actual="blocked",
                    )
                },
            ),
            # P1: a Task still working (waiting, not failed) and two negotiation rounds.
            _item(
                20,
                "时间重叠",
                "a2a-t",
                "partial",
                {
                    "send": _stage("send", "passed"),
                    "negotiate": _stage(
                        "negotiate",
                        "passed",
                        expected={"rounds": 2, "kinds": ["TARGET"]},
                        actual={"rounds": 2, "kinds": ["TARGET"]},
                    ),
                    "accepted": _stage("accepted", "passed"),
                    "working": _stage(
                        "working",
                        "waiting",
                        message="未观察到 Task 越过 SUBMITTED。",
                        actual={"state": "TASK_STATE_SUBMITTED"},
                    ),
                },
                runId="run-20",
                traceId=TRACE,
                answer={"taskId": "task-20"},
            ),
            # Modify whose baseline Create never reached a continuable state.
            _item(
                34,
                "时间重叠+可行性协商",
                "a2a-t",
                "error",
                {"send": _stage("send", "error", message="Modify 基线任务创建失败。")},
                runId="run-34",
                traceId=TRACE,
                answer={"taskId": "task-34"},
                baseline={
                    "runId": "run-34-base",
                    "protocol": "a2a-t",
                    "status": "failed",
                    "state": "TASK_STATE_FAILED",
                    "contextId": "ctx-34",
                    "error": "基线 Task 未被受理。",
                    "request": "把 A2ATestRegion 的速率改为 10Mbps",
                },
                refs={"operation": "Modify"},
                error="基线 Task 未被受理。",
            ),
            # Item-level error with no failing stage: recorded as ``stage="item"``.
            _item(
                40,
                "审批拒绝",
                "a2a",
                "error",
                {
                    "send": _stage("send", "passed"),
                    "negotiate": _stage("negotiate", "recorded"),
                    "final": _stage("final", "skipped", message="用例期望不发送"),
                },
                runId="run-40",
                traceId=TRACE,
                error="RuntimeError: vendor returned 500",
            ),
            # Nothing ran beyond a skipped send: excluded.
            _item(
                50,
                "不发送用例",
                "a2a",
                "passed",
                {
                    "intent": _stage("intent", "passed"),
                    "send": _stage("send", "skipped", message="用例期望不发送"),
                    "final": _stage("final", "recorded"),
                },
            ),
        ],
    }


def _write_batch(tmp_path: Path, document: dict[str, Any] | None = None) -> Path:
    path = tmp_path / "runs-ai" / "tester" / f"{BATCH_ID}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = document if document is not None else _batch_document()
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _records(tmp_path: Path) -> list[FailureRecord]:
    path = _write_batch(tmp_path)
    document = load_batch(path)
    return failure_records(document, batch_path=path, runs_dir=tmp_path / "runs-ai")


def _by_case(records: list[FailureRecord], case_id: int, stage: str) -> FailureRecord:
    for record in records:
        if record.case_id == case_id and record.stage == stage:
            return record
    raise AssertionError(f"no record for case {case_id} stage {stage}")


def test_load_batch_accepts_a_snapshot(tmp_path: Path) -> None:
    path = _write_batch(tmp_path)
    document = load_batch(path)
    assert batch_id(document) == BATCH_ID
    assert isinstance(document["items"], list)


def test_load_batch_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_batch(tmp_path / "runs-ai" / "tester" / "absent.json")


def test_load_batch_rejects_a_document_without_batch_id(tmp_path: Path) -> None:
    document = _batch_document()
    document.pop("batchId")
    with pytest.raises(ValueError, match="batchId"):
        load_batch(_write_batch(tmp_path, document))


def test_load_batch_rejects_items_that_are_not_a_list(tmp_path: Path) -> None:
    document = _batch_document()
    document["items"] = {"0": {}}
    with pytest.raises(ValueError, match="items"):
        load_batch(_write_batch(tmp_path, document))


def test_load_batch_rejects_a_non_object_root(tmp_path: Path) -> None:
    path = tmp_path / "runs-ai" / "tester" / f"{BATCH_ID}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_batch(path)


def test_load_batch_rejects_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "runs-ai" / "tester" / f"{BATCH_ID}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"batchId": ', encoding="utf-8")
    with pytest.raises(ValueError):
        load_batch(path)


def test_batch_id_requires_a_value() -> None:
    with pytest.raises(ValueError, match="batchId"):
        batch_id({})


def test_one_record_per_failing_stage_only(tmp_path: Path) -> None:
    records = _records(tmp_path)
    keys = [(record.case_id, record.stage) for record in records]
    assert keys == [(2, "intent"), (3, "validate"), (20, "working"), (34, "send"), (40, "item")]
    assert {record.stage_status for record in records} == {"failed", "waiting", "error"}


def test_record_identity_and_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs-ai" / "run-2"
    run_dir.mkdir(parents=True)
    (run_dir / "wire.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "runs-ai" / "client-state.sqlite3").write_text("", encoding="utf-8")
    records = _records(tmp_path)

    record = _by_case(records, 2, "intent")
    assert record.name == "时间窗归位"
    assert record.protocol == "a2a-t"
    assert record.operation == "Create"
    assert record.item_status == "failed"
    assert record.message == "意图不一致。"
    assert record.expected == {"time": "08:00-12:00", "rate": "2Mbps"}
    assert record.actual == {"time": "09:00-12:00", "rate": "2Mbps"}
    assert record.run.to_dict() == {
        "runId": "run-2",
        "traceId": TRACE,
        "taskId": "task-2",
        "contextId": None,
    }
    assert record.baseline is None
    assert record.artifacts.wire == run_dir / "wire.jsonl"
    assert record.artifacts.server_trace is None
    assert record.artifacts.client_state == tmp_path / "runs-ai" / "client-state.sqlite3"
    assert record.artifacts.batch == tmp_path / "runs-ai" / "tester" / f"{BATCH_ID}.json"


def test_record_without_run_keeps_identity_empty(tmp_path: Path) -> None:
    record = _by_case(_records(tmp_path), 3, "validate")
    assert record.run.to_dict() == {"runId": None, "traceId": None, "taskId": None, "contextId": None}
    assert record.artifacts.wire is None


def test_first_diff_is_normalized(tmp_path: Path) -> None:
    record = _by_case(_records(tmp_path), 2, "intent")
    assert record.first_diff == {
        "path": "Task Context.time",
        "expected": "08:00-12:00",
        "actual": "09:00-12:00",
        "reason": "window shifted",
    }
    assert first_diff({"diffs": []}) is None
    assert first_diff({}) is None
    assert first_diff({"diffs": {"path": "x"}}) is None


def test_signals_of_a_blocked_validation(tmp_path: Path) -> None:
    record = _by_case(_records(tmp_path), 3, "validate")
    assert record.signals == {
        "category": "sdk.validation",
        "validationCode": "content.param_missing",
        "validationOrigin": "sdk",
        "runEndedBeforeSend": False,
        "negotiationRounds": 0,
        "baselineFailed": False,
        "itemStatus": "failed",
        "stageStatus": "failed",
    }


def test_signals_of_a_negotiable_validation(tmp_path: Path) -> None:
    item = _item(60, "缺速率可协商", "a2a-t", "passed", {})
    stage = _stage(
        "validate",
        "passed",
        message="negotiable (content.param_missing); the request is still sent",
    )
    assert signals_for(item, stage)["validationCode"] == "content.param_missing"


def test_signals_of_a_run_that_ended_before_send(tmp_path: Path) -> None:
    item = _item(61, "发送前结束", "a2a-t", "failed", {})
    stage = _stage("accepted", "failed", message="run 在发送前结束：input_invalid")
    signals = signals_for(item, stage)
    assert signals["runEndedBeforeSend"] is True
    assert signals["validationCode"] is None

    english = _stage("send", "failed", message="run ended before send: blocked")
    assert signals_for(item, english)["runEndedBeforeSend"] is True
    assert signals_for(item, _stage("send", "failed"))["runEndedBeforeSend"] is False


def test_signals_of_a_waiting_stage(tmp_path: Path) -> None:
    record = _by_case(_records(tmp_path), 20, "working")
    assert record.signals["stageStatus"] == "waiting"
    assert record.signals["itemStatus"] == "partial"
    assert record.signals["negotiationRounds"] == 2


def test_signals_of_a_modify_baseline(tmp_path: Path) -> None:
    record = _by_case(_records(tmp_path), 34, "send")
    assert record.operation == "Modify"
    assert record.signals["baselineFailed"] is True
    assert record.run.task_id == "task-34"
    assert record.run.context_id == "ctx-34"
    assert record.baseline is not None
    assert record.baseline["runId"] == "run-34-base"
    assert record.baseline["error"] == "基线 Task 未被受理。"
    # The baseline is a copy: mutating it must not touch the batch document.
    mutated = dict(record.baseline or {})
    mutated["runId"] = "mutated"
    fresh = _by_case(_records(tmp_path), 34, "send")
    assert fresh.baseline is not None
    assert fresh.baseline["runId"] == "run-34-base"


def test_item_level_record_when_no_stage_failed(tmp_path: Path) -> None:
    record = _by_case(_records(tmp_path), 40, "item")
    assert record.stage_status == "error"
    assert record.message == "RuntimeError: vendor returned 500"
    assert record.expected is None
    assert record.actual is None
    assert record.first_diff is None
    assert record.run.run_id == "run-40"
    assert record.run.task_id is None


def test_write_failures_is_deterministic(tmp_path: Path) -> None:
    records = _records(tmp_path)
    first = tmp_path / "out" / "a" / "failures.jsonl"
    second = tmp_path / "out" / "b" / "failures.jsonl"
    write_failures(records, first)
    write_failures(records, second)

    payload = first.read_bytes()
    assert payload == second.read_bytes()
    assert payload.decode("utf-8").endswith("\n")
    lines = payload.decode("utf-8").splitlines()
    assert len(lines) == len(records)
    # UTF-8, not escaped: the analyst reads the file directly.
    assert "时间窗归位" in payload.decode("utf-8")
    assert "\\u" not in payload.decode("utf-8")
    parsed = [json.loads(line) for line in lines]
    assert [entry["caseId"] for entry in parsed] == [2, 3, 20, 34, 40]
    assert list(parsed[0]) == sorted(parsed[0])
    assert parsed[0]["firstDiff"]["path"] == "Task Context.time"
    assert parsed[0]["artifacts"]["batch"].endswith(f"{BATCH_ID}.json")


def test_write_failures_creates_an_empty_file_for_no_records(tmp_path: Path) -> None:
    target = tmp_path / "out" / "empty.jsonl"
    write_failures([], target)
    assert target.read_bytes() == b""


def test_summarize_counts(tmp_path: Path) -> None:
    summary = summarize(_records(tmp_path))
    assert summary == {
        "itemStatus.error": 2,
        "itemStatus.failed": 2,
        "itemStatus.partial": 1,
        "protocol.a2a": 1,
        "protocol.a2a-t": 4,
        "stage.intent": 1,
        "stage.item": 1,
        "stage.send": 1,
        "stage.validate": 1,
        "stage.working": 1,
        "total": 5,
        "validationCode.content.param_missing": 1,
        "validationCode.none": 4,
    }
    assert summarize([]) == {"total": 0}
