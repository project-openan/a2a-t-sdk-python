"""Tests for the structured negotiation transcript."""

from __future__ import annotations

import json
from pathlib import Path

from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
)
from demo.scenario import FINAL_STATE_TASK_COMPLETED
from protocol.client_flow import ClientFlowResult
from protocol.transcript import (
    DIRECTION_CLIENT_TO_SERVER,
    DIRECTION_SERVER_TO_CLIENT,
    FINAL_STATE_FAILED,
    NegotiationTranscript,
)
from shared.models import NegotiationDecision, NegotiationProposeView

CONTEXT_ID = "ctx-transcript"


def _propose_event() -> dict[str, object]:
    return {
        "kind": "message",
        "text": "Negotiation proposal from the RAN energy saving service",
        "context_id": CONTEXT_ID,
        "message_id": "propose-1",
        "metadata": {
            NEGOTIATION_T_EXTENSION_URI: "Please provide the following missing items: region.",
            TEMPLATE_URI_METADATA_KEY: "Negotiation-T/information-negotiation/propose/v1",
            NEGOTIATION_CONTEXT_METADATA_KEY: {
                "id": CONTEXT_ID,
                "round": 1,
                "maxRounds": 3,
                "performative": "PROPOSE",
            },
        },
    }


def _status_event() -> dict[str, object]:
    return {
        "kind": "status",
        "task_id": "task-1",
        "context_id": CONTEXT_ID,
        "state": "TASK_STATE_COMPLETED",
    }


def _artifact_event() -> dict[str, object]:
    """Return the final report artifact; it echoes the agreeing negotiation context (design §6.4)."""
    return {
        "kind": "artifact",
        "task_id": "task-1",
        "context_id": CONTEXT_ID,
        "artifact_id": "artifact-1",
        "name": "energy saving intent report",
        "data": {"estimated_saving_kwh": 12.5},
        "metadata": {
            NEGOTIATION_CONTEXT_METADATA_KEY: {
                "id": CONTEXT_ID,
                "round": 1,
                "maxRounds": 3,
                "performative": "ACCEPT",
            },
            TEMPLATE_URI_METADATA_KEY: "Task-T/network-layer/ran-energy-saving/v1",
        },
    }


def _transcript() -> NegotiationTranscript:
    return NegotiationTranscript(
        scenario="information",
        language="en-US",
        model_mode="offline",
        mode="auto",
        transport="sse",
    )


def test_the_transcript_carries_the_run_trace_id() -> None:
    transcript = NegotiationTranscript(
        scenario="information",
        language="en-US",
        model_mode="offline",
        mode="auto",
        transport="sse",
        trace_id="b" * 32,
    )

    assert transcript.to_dict()["traceId"] == "b" * 32


def test_record_event_extracts_round_performative_and_kind() -> None:
    transcript = _transcript()

    entry = transcript.record_event(_propose_event())

    assert entry["direction"] == DIRECTION_SERVER_TO_CLIENT
    assert entry["round"] == 1
    assert entry["maxRounds"] == 3
    assert entry["performative"] == "PROPOSE"
    assert entry["kind"] == "propose"
    assert entry["eventKind"] == "message"
    assert entry["contextId"] == CONTEXT_ID
    assert entry["templateUri"] == "Negotiation-T/information-negotiation/propose/v1"
    assert "missing items" in str(entry["summary"])
    # The full content survives next to the truncated summary for an audience-facing view.
    assert entry["content"] == "Please provide the following missing items: region."
    assert entry["rationale"] is None
    assert transcript.context_id == CONTEXT_ID


def test_wire_metadata_keeps_the_negotiation_context() -> None:
    transcript = _transcript()

    entry = transcript.record_event(_propose_event())

    metadata = entry["metadata"]
    assert isinstance(metadata, dict)
    assert NEGOTIATION_CONTEXT_METADATA_KEY in metadata
    assert metadata[NEGOTIATION_CONTEXT_METADATA_KEY]["performative"] == "PROPOSE"


def test_plain_message_content_falls_back_to_the_event_text() -> None:
    """A metadata-less message still shows its full body instead of a truncated summary."""
    transcript = _transcript()
    event = {
        "kind": "message",
        "text": "## Information Negotiation\n\nThe energy saving region is missing.",
        "context_id": CONTEXT_ID,
    }

    entry = transcript.record_event(event)

    assert entry["content"] == event["text"]


def test_record_decision_carries_the_rationale_and_params() -> None:
    transcript = _transcript()
    propose = NegotiationProposeView(
        negotiation_id=CONTEXT_ID,
        kind="information",
        round=2,
        max_rounds=3,
        content="proposal",
        template_uri="Negotiation-T/information-negotiation/propose/v1",
        context_id=CONTEXT_ID,
    )
    decision = NegotiationDecision(
        action="accept",
        rationale="The region is now supplied.",
        filled_params={"region": "Region A"},
    )

    entry = transcript.record_decision(decision, propose=propose)

    assert entry["direction"] == DIRECTION_CLIENT_TO_SERVER
    assert entry["kind"] == "accept"
    assert entry["performative"] == "ACCEPT"
    assert entry["round"] == 2
    assert entry["rationale"] == "The region is now supplied."
    assert entry["filledParams"] == {"region": "Region A"}
    assert transcript.decisions == [entry]


def test_record_final_and_write_json_and_markdown(tmp_path: Path) -> None:
    transcript = _transcript()
    transcript.record_event(_propose_event())
    transcript.record_event(_status_event())
    transcript.record_event(_artifact_event())
    transcript.record_decision(
        NegotiationDecision(action="accept", rationale="Acceptable."),
    )
    result = ClientFlowResult(
        final_state=FINAL_STATE_TASK_COMPLETED,
        context_id=CONTEXT_ID,
        task_id="task-1",
        report={"summary": "Plan applied", "estimated_saving_kwh": 12.5},
    )
    transcript.record_final(result)

    paths = transcript.write(tmp_path)

    assert paths.json_path.exists()
    assert paths.markdown_path.exists()
    assert paths.json_path.name.endswith("-information.json")
    document = json.loads(paths.json_path.read_text(encoding="utf-8"))
    assert document["finalState"] == FINAL_STATE_TASK_COMPLETED
    assert document["report"]["summary"] == "Plan applied"
    assert document["contextId"] == CONTEXT_ID
    assert document["taskId"] == "task-1"
    assert len(document["events"]) == 3
    assert document["events"][0]["round"] == 1
    assert document["events"][1]["kind"] == "status"
    assert document["events"][2]["kind"] == "artifact"
    assert document["events"][2]["performative"] is None
    assert document["events"][2]["round"] == 1
    markdown = paths.markdown_path.read_text(encoding="utf-8")
    assert "# RAN energy-saving negotiation transcript" in markdown
    assert "| # | Seq | Phase | Step | Round | Direction | Kind |" in markdown
    assert "| 1 | 1 | negotiation | 1 | 1 | server->client | propose | PROPOSE |" in markdown
    assert "task_completed" in markdown
    assert "Plan applied" in markdown


def test_run_explorer_fields_share_one_seq_ordered_timeline() -> None:
    transcript = _transcript()

    event = transcript.record_event(
        _propose_event(),
        phase="negotiation",
        phase_step=2,
        seq=5,
        span_id="span-event",
        status="ok",
        duration_ms=12.5,
        detail={"metadataKeys": ["templateUri"]},
    )
    decision = transcript.record_decision(
        NegotiationDecision(action="accept", rationale="Acceptable."),
        phase="resolution",
        phase_step=1,
        seq=6,
        span_id="span-decision",
    )
    trace = transcript.record_trace(
        {
            "phase": "task_prompt",
            "phaseStep": 1,
            "spanId": "span-trace",
            "kind": "task_prompt_rendered",
            "title": "Task-T prompt rendered",
            "status": "ok",
            "detail": {"length": 12},
        },
        seq=7,
    )

    assert event["seq"] == 5
    assert event["phase"] == "negotiation"
    assert event["phaseStep"] == 2
    assert event["spanId"] == "span-event"
    assert event["status"] == "ok"
    assert event["durationMs"] == 12.5
    assert event["detail"] == {"metadataKeys": ["templateUri"]}
    assert decision["seq"] == 6
    assert decision["entryType"] == "decision"
    assert trace["seq"] == 7
    assert trace["entryType"] == "trace"
    document = transcript.to_dict()
    timeline = document["timeline"]
    assert isinstance(timeline, list)
    assert [entry["seq"] for entry in timeline] == [5, 6, 7]
    assert [entry["entryType"] for entry in timeline] == [
        "event",
        "decision",
        "trace",
    ]


def test_record_trace_allocates_a_fresh_phase_step_instead_of_trusting_the_producer() -> None:
    transcript = _transcript()

    event = transcript.record_event(_propose_event(), phase="negotiation")
    requested = transcript.record_trace(
        {
            "phase": "negotiation",
            "phaseStep": 1,
            "spanId": "flow-1",
            "kind": "decision_requested",
            "title": "Decision requested",
        }
    )
    decision = transcript.record_decision(
        NegotiationDecision(action="accept", rationale="Acceptable."),
        phase="resolution",
    )
    made = transcript.record_trace(
        {
            "phase": "resolution",
            "phaseStep": 1,
            "spanId": "flow-2",
            "kind": "decision_made",
            "title": "Decision made",
        }
    )

    assert event["phaseStep"] == 1
    assert requested["phaseStep"] == 2
    assert decision["phaseStep"] == 1
    assert made["phaseStep"] == 2
    assert [entry["phaseStep"] for entry in transcript.timeline()] == [1, 2, 1, 2]

    explicit = transcript.record_trace(
        {
            "phase": "resolution",
            "phaseStep": 1,
            "spanId": "ui-3",
            "kind": "message_sent",
            "title": "Accept sent",
        },
        phase_step=9,
    )
    assert explicit["phaseStep"] == 9


def test_record_event_infers_phase_and_seq_when_omitted() -> None:
    transcript = _transcript()

    status = transcript.record_event(_status_event())
    proposal = transcript.record_event(_propose_event())

    assert status["seq"] == 1
    assert status["phase"] == "report"
    assert status["phaseStep"] == 1
    assert proposal["seq"] == 2
    assert proposal["phase"] == "negotiation"
    assert proposal["phaseStep"] == 1


def test_record_event_carries_the_task_payload_and_the_extension_metadata() -> None:
    transcript = _transcript()
    task_payload = {
        "id": "task-1",
        "status": {"state": "TASK_STATE_SUBMITTED"},
        "history": [{"metadata": {TASK_T_EXTENSION_URI: "## Operation Type\n\nCreate"}}],
    }
    event = {
        "kind": "status",
        "task_id": "task-1",
        "context_id": CONTEXT_ID,
        "state": "TASK_STATE_SUBMITTED",
        "task": task_payload,
        "metadata": {
            TASK_T_EXTENSION_URI: "1. Basic information\n- Region: Region A",
            "unrelated": "not part of the wire payload",
        },
    }

    entry = transcript.record_event(event)

    assert entry["task"] == task_payload
    assert entry["metadata"] == {TASK_T_EXTENSION_URI: "1. Basic information\n- Region: Region A"}


def test_record_error_marks_the_run_failed() -> None:
    transcript = _transcript()

    transcript.record_error("RuntimeError: boom")

    document = transcript.to_dict()
    assert document["finalState"] == FINAL_STATE_FAILED
    assert document["error"] == "RuntimeError: boom"
    assert document["finishedAt"] is not None


def test_record_event_exposes_the_tool_of_a_tool_artifact() -> None:
    transcript = _transcript()

    entry = transcript.record_event(
        {
            "kind": "artifact",
            "task_id": "task-1",
            "context_id": CONTEXT_ID,
            "artifact_id": "artifact-apply",
            "name": "tool_result",
            "text": '{"ok": true, "plan_id": "plan-1"}',
            "metadata": {"toolName": "apply_energy_saving_plan"},
        }
    )

    assert entry["detail"] == {"toolName": "apply_energy_saving_plan"}
    assert entry["kind"] == "artifact"
