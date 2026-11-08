"""Tests of the in-memory run state: hop identities stay separate."""

from __future__ import annotations

from pathlib import Path

from protocol.client_service.run_state import RunState
from protocol.transcript import NegotiationTranscript
from shared.models import NegotiationDecision


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


def _activity(status: str, *, activity_id: str = "act-1", **detail: object) -> dict[str, object]:
    """One wait-state detail as a producer publishes it."""
    return {
        "activityId": activity_id,
        "activityKind": "model",
        "labelKey": "waitingModel",
        "status": status,
        "startedAt": "2026-11-08T02:33:32.759Z",
        **detail,
    }


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


def test_an_activity_is_broadcast_but_not_replayed_as_a_timeline_message(tmp_path: Path) -> None:
    """A wait-state signal must not accumulate in the replay buffer of a days-long run."""
    run = _run(tmp_path)
    snapshot, queue = run.subscribe()
    assert snapshot == []

    run.record_activity(_activity("running"))

    frame = queue.get_nowait()
    assert frame["type"] == "activity"
    assert frame["activity"]["labelKey"] == "waitingModel"
    assert frame["activity"]["kind"] == "model"
    assert run.messages == []
    assert run.traces == []
    assert run.transcript is not None and run.transcript.events == []

    # A page that opens mid-wait still sees the current state without a replay backlog.
    late_snapshot, _ = run.subscribe()
    assert [item["type"] for item in late_snapshot] == ["activity"]


def test_an_activity_keeps_the_stage_the_page_uses_for_its_label(tmp_path: Path) -> None:
    """The page names the wait from the stage (``task_t_slot_extraction``), not a generic line."""
    run = _run(tmp_path)

    run.record_activity(_activity("running", stage="task_t_slot_extraction"))

    assert run.activity is not None
    assert run.activity["stage"] == "task_t_slot_extraction"
    detail = run.activity["detail"]
    assert isinstance(detail, dict)
    assert "stage" not in detail


def test_a_finished_activity_clears_the_wait_state(tmp_path: Path) -> None:
    run = _run(tmp_path)
    run.record_activity(_activity("running"))
    assert run.activity is not None

    run.record_activity(_activity("done"))

    assert run.activity is None
    snapshot, _ = run.subscribe()
    assert snapshot == []


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


def _submitted_event() -> dict[str, object]:
    """The A2A status that proves the Task was accepted (OSP `AN`)."""
    return {
        "kind": "status",
        "state": "TASK_STATE_SUBMITTED",
        "task_id": "task-1",
        "context_id": "ctx-1",
    }


def test_first_submitted_status_emits_one_task_created_span(tmp_path: Path) -> None:
    run = _run(tmp_path)

    run.record_event(_submitted_event())
    run.record_event(_submitted_event())

    created = [span for span in run.traces if span.get("kind") == "task_created"]
    assert len(created) == 1
    assert created[0]["title"] == "Task created"
    assert created[0]["taskId"] == "task-1"
    assert created[0]["detail"]["operation"] == "create"
    # ``Task created`` is derived locally from a real status; it must not claim the peer spoke.
    assert (created[0]["actor"], created[0]["side"], created[0]["direction"]) == (
        "client",
        "client",
        "internal",
    )


def test_a_polled_task_state_is_recorded_as_a_local_snapshot(tmp_path: Path) -> None:
    run = _run(tmp_path)

    span = run.record_polled_task_state(
        task_id="task-1",
        context_id="ctx-1",
        state="TASK_STATE_WORKING",
    )

    assert span["kind"] == "task_poll"
    assert span["title"] == "Polled task state: TASK_STATE_WORKING"
    assert (span["actor"], span["side"], span["direction"]) == ("client", "client", "poll")
    assert span["detail"]["state"] == "TASK_STATE_WORKING"
    # The export carries the local origin instead of a fabricated status update.
    assert run.transcript is not None
    entry = run.transcript.events[-1]
    assert (entry["entryType"], entry["kind"], entry["direction"]) == ("trace", "task_poll", "poll")
    assert not [row for row in run.transcript.events if row.get("eventKind") == "status"]


def test_a_modify_run_marks_the_task_modified(tmp_path: Path) -> None:
    run = RunState(
        run_id="run-modify",
        scenario="night",
        mode="auto",
        transport="webhook",
        run_mode="modify",
        target_task_id="task-0",
        target_context_id="ctx-0",
    )
    run.transcript = NegotiationTranscript(
        scenario="night",
        language="en-US",
        model_mode="offline",
        mode="auto",
        transport="webhook",
        runs_dir=tmp_path,
    )

    run.record_event(_submitted_event())

    created = [span for span in run.traces if span.get("kind") == "task_created"]
    assert len(created) == 1
    assert created[0]["title"] == "Task modified"
    assert created[0]["detail"]["operation"] == "modify"


def test_pure_intent_is_promoted_to_one_intent_span(tmp_path: Path) -> None:
    run = _run(tmp_path)

    for _ in range(2):
        run.record_event(
            {
                "kind": "status",
                "state": "TASK_STATE_WORKING",
                "task_id": "task-1",
                "context_id": "ctx-1",
                "metadata": {
                    "pure": {"hop": "oss", "phase": "intent"},
                    "intentProcessed": {"rawRequest": "save energy", "normalizedText": "save energy now"},
                },
            }
        )

    promoted = [span for span in run.traces if span.get("kind") == "intent_processed"]
    assert len(promoted) == 1
    assert promoted[0]["detail"]["rawRequest"] == "save energy"


def test_the_decision_span_starts_at_the_engineer_answer(tmp_path: Path) -> None:
    run = RunState(run_id="run-manual", scenario="night", mode="manual", transport="sse")
    run.transcript = NegotiationTranscript(
        scenario="night",
        language="en-US",
        model_mode="live",
        mode="manual",
        transport="sse",
        runs_dir=tmp_path,
    )

    run.mark_decision_answered()
    answered_at = run._decision_answered_at[0]
    run.record_decision(NegotiationDecision(action="accept", rationale="ok"), None)

    decision = next(span for span in run.traces if span.get("kind") == "accept")
    assert decision["startedAt"] == answered_at
    assert decision["durationMs"] >= 0
    assert run._decision_answered_at is None
