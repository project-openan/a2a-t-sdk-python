"""Tests of the in-memory run state: hop identities stay separate."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
)
from protocol.client_service.run_state import RunState
from protocol.transcript import NegotiationTranscript
from shared.models import NegotiationDecision
from shared.trace_propagation import reset_active_trace, set_active_trace


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


def test_the_run_started_frame_carries_the_trace_id(tmp_path: Path) -> None:
    run = _run(tmp_path)
    run.trace_id = "b" * 32

    frame = run.emit_run_started(
        description="d",
        user_request="r",
        language="en-US",
        model_mode="offline",
        model_label="",
        expected_sequence=[],
    )

    assert frame["traceId"] == "b" * 32
    started = next(span for span in run.traces if span["kind"] == "run_started")
    detail = started["detail"]
    assert isinstance(detail, dict)
    assert detail["traceId"] == "b" * 32


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


def _propose_event() -> dict[str, object]:
    """One inbound Negotiation-T proposal as a transport normalizes it."""
    return {
        "kind": "message",
        "text": "Please provide the following missing items: region.",
        "context_id": "ctx-propose",
        "metadata": {
            NEGOTIATION_T_EXTENSION_URI: "Please provide the following missing items: region.",
            TEMPLATE_URI_METADATA_KEY: "Negotiation-T/information-negotiation/propose/v1",
            NEGOTIATION_CONTEXT_METADATA_KEY: {
                "id": "ctx-propose",
                "round": 2,
                "maxRounds": 3,
                "performative": "PROPOSE",
            },
        },
    }


class _RecordingQueue(asyncio.Queue[dict[str, object]]):
    """Subscriber queue that remembers which thread fed it."""

    def __init__(self) -> None:
        super().__init__()
        self.fed_on: list[int] = []

    def put_nowait(self, item: dict[str, object]) -> None:
        self.fed_on.append(threading.get_ident())
        super().put_nowait(item)


def _subscribe_with(run: RunState) -> _RecordingQueue:
    """Attach one recording subscriber instead of the plain queue ``subscribe`` builds."""
    run.subscribe()
    queue = _RecordingQueue()
    run.subscribers.clear()
    run.subscribers.add(queue)
    return queue


async def test_a_worker_thread_hands_its_activity_frame_to_the_event_loop(tmp_path: Path) -> None:
    """The SDK call moved to a worker thread; its wait-state frame must still reach SSE."""
    run = _run(tmp_path)
    queue = _subscribe_with(run)
    loop_thread = threading.get_ident()

    await asyncio.to_thread(run.record_activity, _activity("running"))
    frame = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert frame["type"] == "activity"
    assert queue.fed_on == [loop_thread]


async def test_a_worker_thread_hands_its_trace_span_to_the_event_loop(tmp_path: Path) -> None:
    """Same rule for trace spans: an asyncio.Queue is only safe from its own loop thread."""
    run = _run(tmp_path)
    queue = _subscribe_with(run)
    loop_thread = threading.get_ident()

    await asyncio.to_thread(
        run.record_trace,
        {"spanId": "llm_client_test_0001", "kind": "llm", "phase": "dispatch", "title": "LLM call"},
    )
    frame = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert frame["type"] == "trace"
    assert queue.fed_on == [loop_thread]


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


def test_a_webhook_event_carries_the_run_trace_id(tmp_path: Path) -> None:
    """A pushed status is an independent inbound request; its span must still name this run.

    The event is consumed in the run task, where ``set_active_trace`` binds the vendor-facing trace
    id, so the envelope does not open an unrelated trace.
    """
    run = _run(tmp_path)
    run.trace_id = "0123456789abcdef0123456789abcdef"
    tokens = set_active_trace(run_id=run.run_id, downstream_trace_id=run.trace_id)
    try:
        entry = run.record_event(_waiting_event())
    finally:
        reset_active_trace(tokens)

    assert entry.get("spanId")
    spans = [span for span in run.traces if span.get("kind") == "status"]
    assert spans
    assert spans[-1]["traceId"] == run.trace_id


def test_a_proposal_is_traced_as_a_propose_span_with_its_kind_and_round(tmp_path: Path) -> None:
    """AJ: every inbound proposal carries a client-side "negotiation started" timestamp."""
    run = _run(tmp_path)

    run.record_event(_propose_event())

    spans = [span for span in run.traces if span.get("kind") == "propose"]
    assert len(spans) == 1
    span = spans[0]
    assert span["round"] == 2
    assert span["contextId"] == "ctx-propose"
    detail = span["detail"]
    assert isinstance(detail, dict)
    assert detail["negotiationType"] == "information"
    assert detail["templateUri"] == "Negotiation-T/information-negotiation/propose/v1"
    assert detail["content"] == "Please provide the following missing items: region."


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


def test_the_submitted_status_payload_is_kept_as_the_downstream_intent(tmp_path: Path) -> None:
    run = _run(tmp_path)
    payload = {"Operation type": "Create", "Energy saving region": "A2ATestRegion"}
    event = _submitted_event()
    event["text"] = f"Task creation succeeded{json.dumps(payload, separators=(',', ':'))}"

    run.record_event(event)

    assert run.server_intent == payload
    assert run.snapshot()["serverIntent"] == payload
    created = next(span for span in run.traces if span.get("kind") == "task_created")
    assert created["detail"]["params"] == payload


def test_a_status_that_is_not_submitted_never_populates_the_downstream_intent(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    payload = {"Operation type": "Create", "Energy saving region": "A2ATestRegion"}

    run.record_event(
        {
            "kind": "status",
            "state": "TASK_STATE_WORKING",
            "task_id": "task-1",
            "context_id": "ctx-1",
            "text": f"Task creation succeeded{json.dumps(payload, separators=(',', ':'))}",
        }
    )

    assert run.server_intent is None
    assert run.snapshot()["serverIntent"] is None


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


def test_the_decision_span_records_who_supplied_the_values(tmp_path: Path) -> None:
    """The trace must distinguish an AI suggestion the operator confirmed from a typed answer."""
    run = RunState(run_id="run-confirm", scenario="night", mode="confirm", transport="sse")
    run.transcript = NegotiationTranscript(
        scenario="night",
        language="en-US",
        model_mode="live",
        mode="confirm",
        transport="sse",
        runs_dir=tmp_path,
    )
    run.pending_answer_source = "ai_recommendation"

    entry = run.record_decision(
        NegotiationDecision(
            action="accept",
            rationale="Accepts the NMS question.",
            filled_params={"region": "Region A"},
        ),
        None,
    )

    assert entry["answerSource"] == "ai_recommendation"
    assert entry["filledParams"] == {"region": "Region A"}
    span = next(span for span in run.traces if span.get("kind") == "accept")
    assert span["detail"]["answerSource"] == "ai_recommendation"
    assert run.pending_answer_source is None


def test_task_created_span_reuses_the_identity_learned_from_a_poll(tmp_path: Path) -> None:
    """A bare SUBMITTED status may omit taskId; the transcript row must still name the Task."""
    run = _run(tmp_path)
    run.record_polled_task_state(task_id="task-9", context_id="ctx-9", state="TASK_STATE_WORKING")

    run.record_event({"kind": "status", "state": "TASK_STATE_SUBMITTED"})

    created = [span for span in run.traces if span.get("kind") == "task_created"]
    assert created[-1]["taskId"] == "task-9"
    assert created[-1]["contextId"] == "ctx-9"


def test_a_decision_records_who_made_it(tmp_path: Path) -> None:
    """A machine decision must not be recorded as the engineer's."""
    run = RunState(run_id="run-auto", scenario="night", mode="auto", transport="sse")
    run.transcript = NegotiationTranscript(
        scenario="night",
        language="en-US",
        model_mode="live",
        mode="auto",
        transport="sse",
        runs_dir=tmp_path,
    )

    entry = run.record_decision(
        NegotiationDecision(action="accept", rationale="automatic"),
        None,
        decided_by="agent",
    )

    span = next(span for span in run.traces if span.get("kind") == "accept")
    assert entry["decidedBy"] == "agent"
    assert span["actor"] == "client"
    assert span["detail"]["decidedBy"] == "agent"
    assert span["detail"]["confirmedBy"] is None

    human = run.record_decision(
        NegotiationDecision(action="reject", rationale="operator"),
        None,
        decided_by="human",
    )
    human_span = next(span for span in run.traces if span.get("kind") == "reject")
    assert human["decidedBy"] == "human"
    assert human_span["actor"] == "human"


def test_a_pure_question_span_carries_its_round_and_kind(tmp_path: Path) -> None:
    """The A2A-T propose span and the pure question span must export the same facts."""
    run = _run(tmp_path)
    run.record_event(
        {
            "kind": "status",
            "task_id": "task-1",
            "context_id": "ctx-1",
            "state": "TASK_STATE_INPUT_REQUIRED",
            "text": "Which target rate?",
            "metadata": {
                "hop": "oss",
                "pure": {
                    "hop": "oss",
                    "kind": "negotiation",
                    "phase": "question",
                    "negotiationKind": "target",
                    "round": 2,
                    "maxRounds": 3,
                },
            },
        }
    )

    span = next(span for span in run.traces if span.get("kind") == "propose")

    assert span["phase"] == "negotiation"
    assert span["round"] == 2
    detail = span["detail"]
    assert isinstance(detail, dict)
    assert detail["negotiationType"] == "target"
    assert detail["hop"] == "oss"
