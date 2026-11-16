"""Tests of the review console's offline Langfuse snapshot reader."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import demo.web.review.traces as traces_module
import pytest
from demo.web.review.corpus import local_trace_points
from demo.web.review.traces import (
    EXPORT_STATE_EXPORTED,
    EXPORT_STATE_FAILED,
    EXPORT_STATE_NOT_EXPORTED,
    EXPORT_STATE_PARTIAL,
    InvalidTraceIdError,
    TraceStore,
    build_frames,
    normalize_rows,
)

TRACE_ID = "0123456789abcdef0123456789abcdef"


def _row(
    name: str,
    *,
    type: str = "SPAN",
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": f"span-{name}",
        "name": name,
        "type": type,
        "parentObservationId": None,
        "metadata": metadata or {},
    }


def _root_row(rounds: int | None = 1) -> dict[str, object]:
    return _row(
        "run · night · a2a-t",
        metadata={
            "runId": "run-1",
            "rounds": rounds,
            "spanLevel": "phase",
            "captureContent": True,
        },
    )


def _write_snapshot(
    runs_dir: Path,
    *,
    rows: list[dict[str, object]],
    stamp: str = "20261116-100000",
    exported_at: str = "2026-11-16T10:00:00+00:00",
    warnings: list[str] | None = None,
    failure: str | None = None,
) -> Path:
    directory = runs_dir / "review" / "langfuse" / f"{TRACE_ID}_{stamp}"
    (directory / "raw").mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps({"exportedAt": exported_at, "files": [], "warnings": warnings or []}),
        encoding="utf-8",
    )
    if failure is not None:
        (directory / "failure.json").write_text(json.dumps({"error": failure}), encoding="utf-8")
    else:
        (directory / "raw" / "observations.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
        )
    return directory


def test_load_reads_the_snapshot_and_projects_the_review_fields(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        rows=[
            _root_row(),
            _row(
                "Task-T sent",
                metadata={
                    "kind": "message_sent",
                    "runId": "run-1",
                    "detail": {"source": "interception", "verdict": "blocked"},
                },
            ),
            _row(
                "SendMessage",
                metadata={"side": "server", "scope.name": "a2a-t-server"},
            ),
        ],
    )
    store = TraceStore(tmp_path)

    snapshot = store.load(TRACE_ID)

    assert snapshot.export_state == EXPORT_STATE_EXPORTED
    assert snapshot.rounds == 1
    assert snapshot.span_level == "phase"
    assert snapshot.capture_content is True
    assert snapshot.observations[1].interception is True
    assert snapshot.observations[1].verdict == "blocked"
    assert snapshot.observations[2].side == "server"
    assert snapshot.observations[2].scope == "a2a-t-server"
    assert snapshot.manifest_sha256
    assert snapshot.export_dir_sha256


def test_missing_snapshot_is_not_exported(tmp_path: Path) -> None:
    snapshot = TraceStore(tmp_path).load(TRACE_ID)
    assert snapshot.export_state == EXPORT_STATE_NOT_EXPORTED
    assert snapshot.observations == ()


def test_failure_marker_wins_over_a_missing_detail_file(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, rows=[_root_row()], failure="AuthError: 401")
    snapshot = TraceStore(tmp_path).load(TRACE_ID)
    assert snapshot.export_state == EXPORT_STATE_FAILED
    assert "AuthError" in snapshot.warnings[0]


def test_a_snapshot_without_observations_is_partial(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, rows=[_root_row()], warnings=["server-side observations are empty"])
    snapshot = TraceStore(tmp_path).load(TRACE_ID)
    assert snapshot.export_state == EXPORT_STATE_PARTIAL
    assert snapshot.warnings


def test_invalid_trace_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidTraceIdError):
        TraceStore(tmp_path).load("../../etc")
    with pytest.raises(InvalidTraceIdError):
        TraceStore(tmp_path).load("ABC")


def test_frames_gate_the_nms_hop_and_keep_other_hops_for_display(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        rows=[
            _root_row(rounds=1),
            _row("Task-T sent", metadata={"kind": "message_sent", "runId": "run-1"}),
            _row("Accept sent", metadata={"kind": "message_sent", "runId": "run-1"}),
            _row("Pure A2A message sent", metadata={"kind": "message_sent", "runId": "oss-hop"}),
            _row("SendMessage", metadata={"side": "server", "scope.name": "a2a-t-server"}),
            _row("SendMessage", metadata={"side": "server", "scope.name": "a2a-t-server"}),
            _row("SendMessage-negotiation", metadata={"side": "server", "scope.name": "a2a-t-server"}),
        ],
    )
    store = TraceStore(tmp_path)
    snapshot = store.load(TRACE_ID)
    frames = build_frames(
        snapshot.observations,
        run_id="run-1",
        nms_kind="real",
        rounds=snapshot.rounds,
    )

    assert frames["nms"] == {
        "expected": 2,
        "observed": 2,
        "scope": "a2a-t-server",
    }
    assert frames["client"] == {"runId": "run-1", "observed": 2}
    assert frames["otherHops"] == [{"runId": "oss-hop", "observed": 1}]
    assert frames["serverSendCount"]["total"] == 2
    assert frames["serverSendCount"]["byScope"] == [{"scope": "a2a-t-server", "count": 2, "sideSource": "metadata"}]


def test_normalize_rows_reads_flattened_detail_and_generation_output() -> None:
    """Langfuse v4 flattens ``detail`` into dotted keys; the review must read both shapes."""
    rows = [
        _row(
            "Task-T prompt rendered",
            metadata={
                "kind": "task_prompt_rendered",
                "runId": "run-1",
                "detail.userRequest": "create a task",
                "detail.promptText": "TASK-T",
            },
        ),
        _row(
            "Task-T sent",
            metadata={
                "kind": "message_sent",
                "runId": "run-1",
                "detail.taskT": "TASK-T",
                "detail.performative": "TASK_T",
            },
        ),
        {
            "id": "gen-1",
            "name": "LLM call · task_t_slot_extraction",
            "type": "GENERATION",
            "metadata": {"kind": "llm", "stage": "task_t_slot_extraction", "runId": "run-1"},
            "output": '{"slots": {}}',
        },
        _row(
            "Task created",
            metadata={
                "kind": "task_created",
                "runId": "run-1",
                "taskId": "task-1",
                "contextId": "ctx-1",
                "detail.operation": "create",
                "detail.state": "TASK_STATE_SUBMITTED",
                "detail.params.Operation type": "Create",
            },
        ),
        _row(
            "run · night · a2a-t",
            metadata={"runId": "run-1", "userRequest": "create a task"},
        ),
    ]

    by_kind = {observation.kind: observation for observation in normalize_rows(rows)}

    assert by_kind["task_prompt_rendered"].prompt_text == "TASK-T"
    assert by_kind["task_prompt_rendered"].user_request == "create a task"
    assert by_kind["message_sent"].task_t == "TASK-T"
    assert by_kind["message_sent"].performative == "TASK_T"
    assert by_kind["llm"].response == '{"slots": {}}'
    assert by_kind["task_created"].task_id == "task-1"
    assert by_kind["task_created"].context_id == "ctx-1"
    assert by_kind["task_created"].operation == "create"
    assert by_kind["task_created"].state == "TASK_STATE_SUBMITTED"
    assert by_kind["task_created"].params == {"Operation type": "Create"}
    root = next(observation for observation in normalize_rows(rows) if observation.name.startswith("run ·"))
    assert root.user_request == "create a task"


def test_frames_pick_the_unique_vendor_scope() -> None:
    rows = [
        _row("SendMessage", metadata={"side": "server", "scope.name": "a2a-t-server"}),
        _row("SendMessage", metadata={"side": "server", "scope.name": "ran-energy-saving"}),
    ]
    frames = build_frames(normalize_rows(rows), run_id="run-1", nms_kind="real", rounds=0)
    assert frames["nms"] == {"expected": 1, "observed": 1, "scope": "a2a-t-server"}
    assert frames["serverSendCount"]["total"] == 2


def test_frames_find_the_nms_hop_even_when_the_downstream_is_undeclared() -> None:
    """A legacy batch has no nms key; its vendor spans are still the vendor spans."""
    rows = [_row("SendMessage", metadata={"side": "server", "scope.name": "a2a-t-server"})]
    frames = build_frames(normalize_rows(rows), run_id="run-1", nms_kind="unknown", rounds=0)
    assert frames["nms"] == {"expected": 1, "observed": 1, "scope": "a2a-t-server"}


def test_frames_prefer_the_exact_vendor_scope_over_a_lookalike() -> None:
    rows = [
        _row("SendMessage", metadata={"side": "server", "scope.name": "a2a-t-server"}),
        _row("SendMessage", metadata={"side": "server", "scope.name": "a2a-t-server-2"}),
    ]
    frames = build_frames(normalize_rows(rows), run_id="run-1", nms_kind="real", rounds=0)
    assert frames["nms"] == {"expected": 1, "observed": 1, "scope": "a2a-t-server"}
    assert frames["serverSendCount"]["total"] == 2


def test_frames_read_a_nested_scope_metadata() -> None:
    rows = [_row("SendMessage", metadata={"scope": {"name": "a2a-t-server"}})]
    frames = build_frames(normalize_rows(rows), run_id="run-1", nms_kind="unknown", rounds=0)
    assert frames["nms"] == {"expected": 1, "observed": 1, "scope": "a2a-t-server"}


def test_frames_find_the_hop_when_the_side_metadata_is_missing() -> None:
    """A remote SendMessage may carry only its scope; the scope is enough to place the hop."""
    rows = [_row("SendMessage", metadata={"scope.name": "a2a-t-server"})]
    frames = build_frames(normalize_rows(rows), run_id="run-1", nms_kind="unknown", rounds=0)
    assert frames["nms"] == {"expected": 1, "observed": 1, "scope": "a2a-t-server"}


def test_frames_derive_the_rounds_when_the_root_metadata_is_missing() -> None:
    rows = [
        _root_row(rounds=None),
        _row("Task-T sent", metadata={"kind": "message_sent", "runId": "run-1"}),
        _row("Accept sent", metadata={"kind": "message_sent", "runId": "run-1"}),
        _row("SendMessage", metadata={"side": "server", "scope.name": "a2a-t-server"}),
        _row("SendMessage", metadata={"side": "server", "scope.name": "a2a-t-server"}),
    ]
    frames = build_frames(normalize_rows(rows), run_id="run-1", nms_kind="real", rounds=None)
    assert frames["rounds"] == 1
    assert frames["nms"] == {"expected": 2, "observed": 2, "scope": "a2a-t-server"}


def test_cached_payload_serves_a_snapshot_without_a_langfuse_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_snapshot(
        tmp_path,
        rows=[_root_row(rounds=0)],
        exported_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("an existing snapshot must not be fetched again")

    monkeypatch.setattr(traces_module, "export_full", explode)
    payload = TraceStore(tmp_path).cached_payload(
        TRACE_ID,
        run_id="run-1",
        nms_kind="real",
        config=object(),  # type: ignore[arg-type]
    )

    assert payload["exportState"] == EXPORT_STATE_EXPORTED


def test_cached_payload_fetches_lazily_and_caches_the_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_export_full(
        config: object,
        trace_id: str,
        out_root: object,
        **kwargs: object,
    ) -> dict[str, object]:
        calls.append(trace_id)
        directory = Path(out_root) / f"{trace_id}_20261116-110000"
        (directory / "raw").mkdir(parents=True, exist_ok=True)
        (directory / "manifest.json").write_text(
            json.dumps({"exportedAt": datetime.now(UTC).isoformat(timespec="seconds")}),
            encoding="utf-8",
        )
        (directory / "raw" / "observations.jsonl").write_text(json.dumps(_root_row(rounds=0)), encoding="utf-8")
        return {"exportDir": str(directory), "observations": 1}

    monkeypatch.setattr(traces_module, "export_full", fake_export_full)
    store = TraceStore(tmp_path)

    payload = store.cached_payload(
        TRACE_ID,
        run_id="run-1",
        nms_kind="real",
        config=object(),  # type: ignore[arg-type]
    )
    assert calls == [TRACE_ID]
    assert payload["exportState"] == EXPORT_STATE_EXPORTED

    # A second read hits the permanent cache; an old snapshot is never re-fetched.
    store.cached_payload(
        TRACE_ID,
        run_id="run-1",
        nms_kind="real",
        config=object(),  # type: ignore[arg-type]
    )
    assert calls == [TRACE_ID]


def test_an_old_snapshot_is_still_the_permanent_cache(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, rows=[_root_row(rounds=0)], exported_at="2020-01-01T00:00:00+00:00")
    payload = TraceStore(tmp_path).cached_payload(
        TRACE_ID,
        run_id="run-1",
        nms_kind="real",
        config=None,
    )
    assert payload["exportState"] == EXPORT_STATE_EXPORTED
    assert payload["warnings"] == []


def test_local_trace_points_only_claim_what_the_snapshot_proves() -> None:
    item = {
        "answer": {
            "taskId": "task-1",
            "exchanges": [
                {"direction": "OUT", "messageType": "Task-T", "round": 1},
                {"direction": "OUT", "messageType": "ACCEPT", "round": 1},
                {"direction": "IN", "messageType": "status", "round": None},
            ],
        },
        "stages": {"send": {"stage": "send", "status": "passed"}},
    }
    emitted, known = local_trace_points(item)

    assert {"message_sent", "decision:R1", "task_created"} <= set(emitted)
    assert {"message_sent", "decision:R1", "task_created"} <= set(known)
    # Task-T rendering / LLM validation are preflight calls, not provable run spans.
    assert "task_prompt_rendered" not in known
    assert "task_validation" not in known


def test_local_trace_points_are_empty_without_a_tester_answer() -> None:
    assert local_trace_points({}) == ((), ())


def test_payload_is_the_compact_block_the_review_store_keeps(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        rows=[
            _root_row(rounds=0),
            _row("Task-T sent", metadata={"kind": "message_sent", "runId": "run-1"}),
            _row("SendMessage", metadata={"side": "server", "scope.name": "a2a-t-server"}),
        ],
    )
    payload = TraceStore(tmp_path).payload(
        TRACE_ID,
        run_id="run-1",
        nms_kind="real",
        local_points=["task_prompt_rendered"],
    )

    assert payload["traceId"] == TRACE_ID
    assert payload["exportState"] == EXPORT_STATE_EXPORTED
    assert payload["nmsKind"] == "real"
    assert payload["related"] == []
    assert payload["localPoints"] == ["task_prompt_rendered"]
    assert payload["frames"]["nms"]["expected"] == 1
    assert payload["frames"]["client"]["observed"] == 1
    assert payload["observations"][0]["name"] == "run · night · a2a-t"
