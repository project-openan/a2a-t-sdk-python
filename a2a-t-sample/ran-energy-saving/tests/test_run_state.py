"""Tests of the in-memory run state: hop identities stay separate."""

from __future__ import annotations

from pathlib import Path

from protocol.client_service.run_state import RunState
from protocol.transcript import NegotiationTranscript


def _run(tmp_path: Path) -> RunState:
    run = RunState(run_id="run-1", scenario="night", mode="auto", transport="webhook")
    run.transcript = NegotiationTranscript(
        scenario="night",
        language="en-US",
        model_mode="offline",
        mode="auto",
        transport="webhook",
        runs_dir=tmp_path,
    )
    return run


def _waiting_event() -> dict[str, object]:
    """One OSS event that names the Task the OSS Agent continued on the NMS side."""
    return {
        "kind": "status",
        "state": "TASK_STATE_WORKING",
        "task_id": "outer-task",
        "context_id": "outer-ctx",
        "metadata": {
            "hop": "nms",
            "pure": {
                "hop": "nms",
                "phase": "waiting",
                "downstreamTaskId": "nms-task",
                "downstreamContextId": "nms-ctx",
                "downstreamHop": "nms",
            },
        },
    }


def test_record_event_keeps_the_downstream_identity_beside_the_run_identity(tmp_path: Path) -> None:
    run = _run(tmp_path)

    entry = run.record_event(_waiting_event())

    # The event still belongs to the hop this run owns; the far-side Task is recorded separately.
    assert entry["contextId"] == "outer-ctx"
    assert run.downstream_task_id == "nms-task"
    assert run.downstream_context_id == "nms-ctx"
    assert run.downstream_hop == "nms"

    snapshot = run.snapshot()
    assert snapshot["downstreamTaskId"] == "nms-task"
    assert snapshot["downstreamContextId"] == "nms-ctx"
    assert snapshot["downstreamHop"] == "nms"


def test_events_without_a_downstream_identity_leave_the_fields_unset(tmp_path: Path) -> None:
    run = _run(tmp_path)

    run.record_event(
        {
            "kind": "status",
            "state": "TASK_STATE_WORKING",
            "task_id": "nms-task",
            "context_id": "nms-ctx",
            "metadata": {"pure": {"hop": "oss", "phase": "receive"}},
        }
    )

    assert run.downstream_task_id is None
    assert run.downstream_context_id is None
    assert run.downstream_hop == ""
    snapshot = run.snapshot()
    assert snapshot["downstreamTaskId"] is None
    assert snapshot["downstreamContextId"] is None
