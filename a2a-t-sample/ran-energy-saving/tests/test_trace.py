"""Tests for the Run Explorer trace foundation (``shared.trace``)."""

from __future__ import annotations

import asyncio

from a2a_t.core.metadata import TASK_T_EXTENSION_URI
from shared.trace import (
    MAX_PAYLOAD_CHARS,
    PHASE_DISPATCH,
    PHASE_EXECUTION,
    PHASE_NEGOTIATION,
    PHASE_REPORT,
    PHASE_RESOLUTION,
    PHASE_TASK_PROMPT,
    PHASE_TASK_VALIDATION,
    TRUNCATED_MARKER,
    Span,
    TraceRecorder,
    current_trace,
    emit_span,
    infer_decision_phase,
    infer_event_phase,
    is_report_artifact,
    phase_for_stage,
    reset_task_sink,
    set_global_sink,
    set_task_sink,
    trace_scope,
    trace_stage,
)


def test_recorder_assigns_seq_phase_step_and_span_id() -> None:
    recorder = TraceRecorder("run-1")

    first = recorder.emit(kind="run_started", phase=PHASE_DISPATCH)
    second = recorder.emit(kind="runtime_ready", phase=PHASE_DISPATCH)
    third = recorder.emit(kind="propose", phase=PHASE_NEGOTIATION)

    assert (first.seq, second.seq, third.seq) == (1, 2, 3)
    assert (first.phase_step, second.phase_step, third.phase_step) == (1, 2, 1)
    assert first.span_id and second.span_id and first.span_id != second.span_id
    assert first.run_id == "run-1"


def test_recorders_with_distinct_prefixes_never_share_generated_ids() -> None:
    ui = TraceRecorder("run-1", id_prefix="ui")
    flow = TraceRecorder("run-1", id_prefix="flow")

    ui_span = ui.emit(kind="run_started", phase=PHASE_DISPATCH)
    flow_span = flow.emit(kind="task_prompt_rendered", phase=PHASE_DISPATCH)
    second_flow = flow.emit(kind="message_sent", phase=PHASE_DISPATCH)

    assert ui_span.span_id == "ui_0001"
    assert flow_span.span_id == "flow_0001"
    assert second_flow.span_id == "flow_0002"
    assert ui_span.span_id != flow_span.span_id


def test_recorder_respects_explicit_seq_step_and_span_id() -> None:
    recorder = TraceRecorder("run-1")

    recorder.emit(kind="a", phase=PHASE_DISPATCH)
    explicit = recorder.emit(
        kind="b",
        phase=PHASE_DISPATCH,
        seq=10,
        phase_step=7,
        span_id="custom-span",
    )

    assert explicit.seq == 10
    assert explicit.phase_step == 7
    assert explicit.span_id == "custom-span"
    assert recorder.get("custom-span") is explicit
    assert recorder.emit(kind="c", phase=PHASE_DISPATCH).seq == 11


def test_recorder_redacts_secrets_and_truncates_large_payloads() -> None:
    recorder = TraceRecorder("run-1")

    span = recorder.emit(
        kind="llm",
        phase=PHASE_NEGOTIATION,
        detail={
            "headers": {
                "Authorization": "Bearer secret",
                "X-API-Key": "key",
                "api_key": "key-2",
                "safe": "value",
            },
            "prompt": "a" * (MAX_PAYLOAD_CHARS + 1000),
        },
    )

    headers = span.detail["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "[redacted]"
    assert headers["X-API-Key"] == "[redacted]"
    assert headers["api_key"] == "[redacted]"
    assert headers["safe"] == "value"
    prompt = span.detail["prompt"]
    assert isinstance(prompt, str)
    assert "truncated" in prompt
    assert len(prompt) < MAX_PAYLOAD_CHARS
    assert span.detail[TRUNCATED_MARKER] is True


def test_span_wire_form_uses_the_envelope_keys() -> None:
    span = Span(run_id="r", seq=4, span_id="s", phase=PHASE_NEGOTIATION, phase_step=2, kind="llm")
    payload = span.to_dict()

    for key in (
        "schemaVersion",
        "runId",
        "seq",
        "spanId",
        "parentSpanId",
        "contextId",
        "taskId",
        "phase",
        "phaseStep",
        "round",
        "actor",
        "side",
        "kind",
        "stage",
        "title",
        "purpose",
        "status",
        "startedAt",
        "endedAt",
        "durationMs",
        "direction",
        "model",
        "tokens",
        "error",
        "detail",
    ):
        assert key in payload
    assert payload["schemaVersion"] == 1
    assert Span.from_dict(payload).span_id == "s"


def test_trace_context_scope_and_stage_nest_and_reset() -> None:
    assert current_trace().run_id is None

    with trace_scope(run_id="run-9", role="client", context_id="ctx-1") as scope:
        assert scope.run_id == "run-9"
        assert current_trace().role == "client"
        assert current_trace().context_id == "ctx-1"
        with trace_stage("client_decision_agent"):
            assert current_trace().stage == "client_decision_agent"
            assert current_trace().run_id == "run-9"
        assert current_trace().stage is None
        assert current_trace().run_id == "run-9"

    assert current_trace().run_id is None
    assert current_trace().role is None


def test_global_sink_receives_spans_and_is_noop_when_unset() -> None:
    seen: list[dict[str, object]] = []
    set_global_sink(seen.append)
    try:
        emit_span(Span(run_id="run-1", seq=1, span_id="s1", kind="llm"))
        emit_span({"runId": "run-1", "seq": 2, "spanId": "s2", "kind": "tool"})
    finally:
        set_global_sink(None)

    assert [payload["spanId"] for payload in seen] == ["s1", "s2"]

    # With no sink attached the producer is a no-op and never raises.
    emit_span(Span(run_id="run-1", seq=3, span_id="s3"))

    def failing_sink(span: dict[str, object]) -> None:
        del span
        raise RuntimeError("sink down")

    set_global_sink(failing_sink)
    try:
        emit_span(Span(run_id="run-1", seq=4, span_id="s4"))
    finally:
        set_global_sink(None)


def test_report_artifact_predicate_matches_only_the_final_report() -> None:
    """Only the named/stamped report artifact ends the run (ADK tool artifacts do not)."""
    report = {"kind": "artifact", "name": "report", "artifact_id": "a1"}
    stamped = {
        "kind": "artifact",
        "name": "",
        "metadata": {TASK_T_EXTENSION_URI: "Task-T report text"},
    }
    tool_artifact = {"kind": "artifact", "name": "apply_energy_saving_plan", "artifact_id": "a2"}
    unnamed = {"kind": "artifact", "name": None, "artifact_id": "a3"}

    assert is_report_artifact(report) is True
    assert is_report_artifact(stamped) is True
    assert is_report_artifact(tool_artifact) is False
    assert is_report_artifact(unnamed) is False
    assert is_report_artifact({"kind": "status", "name": "report"}) is False


def test_artifact_events_map_to_report_only_when_they_are_the_report() -> None:
    """Intermediate agent artifacts are execution rows, not report rows."""
    report = {"kind": "artifact", "name": "report"}
    stamped = {"kind": "artifact", "metadata": {TASK_T_EXTENSION_URI: "report body"}}
    intermediate = {"kind": "artifact", "name": "get_cell_energy_profile", "artifact_id": "a2"}

    assert infer_event_phase(report) == PHASE_REPORT
    assert infer_event_phase(stamped) == PHASE_REPORT
    assert infer_event_phase(intermediate) == PHASE_EXECUTION
    assert infer_event_phase({"kind": "status", "state": "TASK_STATE_COMPLETED"}) == PHASE_REPORT
    assert infer_event_phase({"kind": "status", "state": "TASK_STATE_WORKING"}) == PHASE_EXECUTION


def test_accept_time_task_t_stages_belong_to_the_resolution_phase() -> None:
    """The refill and its revalidation run after the decision, so Story puts them in Resolution."""
    assert phase_for_stage("task_t_slot_extraction_refill") == PHASE_RESOLUTION
    assert phase_for_stage("task_t_accept_validation") == PHASE_RESOLUTION
    # The initial generation/validation keep their own phases.
    assert phase_for_stage("task_t_slot_extraction") == PHASE_TASK_PROMPT
    assert phase_for_stage("task_t_server_validation") == PHASE_TASK_VALIDATION
    assert infer_decision_phase("accept") == PHASE_RESOLUTION
    assert infer_decision_phase("counter") == PHASE_NEGOTIATION


async def test_task_sinks_are_isolated_between_concurrent_runs() -> None:
    """Two runs on one UI loop must not receive each other's spans."""
    first: list[dict[str, object]] = []
    second: list[dict[str, object]] = []

    async def run(sink: list[dict[str, object]], span_id: str) -> None:
        token = set_task_sink(sink.append)
        try:
            await asyncio.sleep(0)
            emit_span(Span(run_id=span_id, span_id=span_id, kind="llm"))
        finally:
            reset_task_sink(token)

    await asyncio.gather(run(first, "run-a"), run(second, "run-b"))

    assert [span["runId"] for span in first] == ["run-a"]
    assert [span["runId"] for span in second] == ["run-b"]
