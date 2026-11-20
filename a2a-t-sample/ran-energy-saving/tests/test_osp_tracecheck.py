"""Tests of the pure trace-completeness judgement."""

from __future__ import annotations

from collections.abc import Mapping

from eval.osp.tracecheck import (
    ATTRIBUTION_MISSING_CLIENT,
    ATTRIBUTION_MISSING_INGEST,
    ATTRIBUTION_OK,
    ATTRIBUTION_UNKNOWN,
    NMS_KIND_LOCAL,
    NMS_KIND_REAL,
    NMS_KIND_SIM,
    NMS_KIND_UNKNOWN,
    PROTOCOL_A2A,
    PROTOCOL_A2A_T,
    REASON_DATA_MISSING,
    REASON_FRAMES_MISMATCH,
    REASON_NMS_HOP_UNKNOWN,
    REASON_NOT_EXPORTED,
    REASON_OPTIONAL_DATA_MISSING,
    TRACE_FAIL,
    TRACE_NOT_APPLICABLE,
    TRACE_PARTIAL,
    TRACE_PASS,
    TRACE_REASONS,
    TraceContext,
    TracePoint,
    attribute_points,
    dispatched_task_t,
    expected_client_points,
    fidelity_summary,
    frame_gate,
    intent_corroboration,
    intent_params,
    judge_trace,
    match_points,
    nms_kind,
    original_request,
    points_summary,
    process_points,
    task_t_fidelity,
    trace_applicability,
)
from shared.langfuse_tracing import exported_span_kinds

TRACE_ID = "a" * 32


def _observation(
    *,
    name: str = "row",
    type: str = "SPAN",
    kind: str = "",
    stage: str = "",
    round: int | None = None,
    run_id: str = "run-1",
    interception: bool = False,
    side: str = "client",
    **extra: object,
) -> dict[str, object]:
    return {
        "name": name,
        "type": type,
        "kind": kind,
        "stage": stage,
        "round": round,
        "interception": interception,
        "run_id": run_id,
        "side": side,
        **extra,
    }


def _context(**overrides: object) -> TraceContext:
    values: dict[str, object] = {
        "protocol": PROTOCOL_A2A_T,
        "run_mode": "create",
        "blocked": False,
        "followup": False,
        "rounds": 0,
        "accepted": True,
        "validation_on": True,
        "run_id": "run-1",
    }
    values.update(overrides)
    return TraceContext(**values)  # type: ignore[arg-type]


def _frames(*, expected: int | None = 1, observed: int | None = 1) -> Mapping[str, object]:
    return {
        "rounds": 0,
        "client": {"runId": "run-1", "observed": 1},
        "nms": {"expected": expected, "observed": observed, "scope": "a2a-t-server"},
        "otherHops": [],
        "serverSendCount": {"total": observed or 0, "byScope": []},
    }


def test_expected_points_follow_the_run_mode_and_the_interception() -> None:
    create = {point.id for point in expected_client_points(_context())}
    assert {"task_prompt_rendered", "gen:task_t_slot_extraction", "task_created"} <= create
    assert "gen:task_t_modify_extraction" not in create

    modify = {point.id for point in expected_client_points(_context(run_mode="modify"))}
    assert "gen:task_t_modify_extraction" in modify
    assert "gen:task_t_slot_extraction" not in modify

    blocked = {point.id for point in expected_client_points(_context(blocked=True, followup=True, run_mode="modify"))}
    assert "correction_applied" in blocked
    assert "blocked_replay_extraction" not in blocked
    assert "gen:task_t_modify_extraction" in blocked

    pure = {point.id for point in expected_client_points(_context(protocol=PROTOCOL_A2A))}
    assert "task_prompt_rendered" not in pure
    assert "task_validation" not in pure
    assert pure == {"message_sent", "task_created"}


def test_expected_points_count_every_negotiation_round() -> None:
    points = expected_client_points(_context(rounds=2))
    ids = {point.id for point in points}
    assert {"decision_requested:R1", "decision:R1"} <= ids
    assert {"decision_requested:R2", "decision:R2"} <= ids
    # The downstream proposal and the ADK decision agent never appear in a real-machine tester
    # run, so expecting them would mark a correct acceptance run as missing.
    assert not any(point_id.startswith("propose:") for point_id in ids)
    assert "gen:client_decision_agent" not in ids
    assert "tool" not in ids
    message = next(point for point in points if point.id == "message_sent")
    assert message.count == 3


def test_match_points_uses_kind_stage_round_and_run_id() -> None:
    observations = [
        _observation(kind="task_prompt_rendered"),
        _observation(type="GENERATION", stage="task_t_client_validation"),
        _observation(kind="task_validation"),
        _observation(kind="message_sent", run_id="run-1"),
        _observation(kind="message_sent", run_id="oss-hop"),
        _observation(kind="decision_requested", round=1),
        _observation(kind="propose", round=1),
        _observation(kind="accept", round=1),
    ]
    results = match_points(
        expected_client_points(_context(rounds=1, accepted=False)),
        observations,
        run_id="run-1",
    )
    by_id = {result.point.id: result.observed for result in results}
    assert by_id["message_sent"] == 1
    assert by_id["decision_requested:R1"] == 1
    assert by_id["decision:R1"] == 1


def test_match_points_carries_the_matching_span_names() -> None:
    results = match_points(
        [
            TracePoint("message_sent", "客户端消息帧", kind="message_sent", count=2),
            TracePoint("task_created", "任务接受", kind="task_created"),
        ],
        [
            _observation(name="SendMessage · R1", kind="message_sent"),
            _observation(name="SendMessage · R2", kind="message_sent"),
            _observation(name="SendMessage · R2", kind="message_sent"),
        ],
        run_id="run-1",
    )
    by_id = {result.point.id: result for result in results}

    # Names ride along deduplicated, in observation order; a point with no observation has none
    # so the console falls back to the point id.
    assert by_id["message_sent"].names == ("SendMessage · R1", "SendMessage · R2")
    assert by_id["task_created"].names == ()


def test_match_points_reads_the_payload_camel_case_run_id() -> None:
    results = match_points(
        [TracePoint("message_sent", "客户端消息帧", kind="message_sent")],
        [{"kind": "message_sent", "type": "SPAN", "runId": "run-1"}],
        run_id="run-1",
    )
    assert results[0].observed == 1


def test_frame_gate_requires_a_known_nms_hop() -> None:
    assert frame_gate(_frames(expected=None, observed=None)) == (
        TRACE_NOT_APPLICABLE,
        REASON_NMS_HOP_UNKNOWN,
    )
    assert frame_gate(_frames(expected=2, observed=1)) == (TRACE_FAIL, REASON_FRAMES_MISMATCH)
    assert frame_gate(_frames(expected=1, observed=1)) == (TRACE_PASS, "")


def test_attribution_never_blames_the_producer_without_local_evidence() -> None:
    results = match_points((expected_client_points(_context())[0],), (), run_id="run-1")
    status, findings = attribute_points(results, local_points=None)
    assert status == ATTRIBUTION_UNKNOWN
    assert findings[0].status == ATTRIBUTION_UNKNOWN

    status, findings = attribute_points(results, local_points=["task_prompt_rendered"])
    assert status == ATTRIBUTION_MISSING_INGEST
    assert findings[0].status == ATTRIBUTION_MISSING_INGEST

    status, findings = attribute_points(results, local_points=[])
    assert status == ATTRIBUTION_MISSING_CLIENT
    assert findings[0].status == ATTRIBUTION_MISSING_CLIENT


def _complete_observations() -> list[dict[str, object]]:
    return [
        _observation(kind="task_prompt_rendered", user_request="create a task", prompt_text="TASK-T"),
        _observation(
            type="GENERATION",
            kind="llm",
            stage="task_t_slot_extraction",
            response='{"slots": {}}',
        ),
        _observation(
            type="GENERATION",
            kind="llm",
            stage="task_t_client_validation",
            response='{"semantic_verdict": true}',
        ),
        _observation(kind="task_validation"),
        _observation(
            kind="message_sent",
            run_id="run-1",
            performative="TASK_T",
            summary="create a task",
            task_t="TASK-T",
        ),
        _observation(kind="task_created", task_id="task-1", context_id="ctx-1", state="TASK_STATE_SUBMITTED"),
    ]


def test_judge_trace_applies_the_acceptance_order() -> None:
    not_exported = judge_trace(
        {"exportState": "not_exported", "frames": _frames(), "observations": []},
        _context(),
    )
    assert (not_exported.status, not_exported.reason) == (TRACE_FAIL, REASON_NOT_EXPORTED)

    missing_points = judge_trace(
        {"exportState": "exported", "frames": _frames(), "observations": []},
        _context(),
    )
    assert (missing_points.status, missing_points.reason) == (TRACE_FAIL, REASON_DATA_MISSING)
    assert missing_points.attribution == ATTRIBUTION_OK

    # The NMS frame count is reference evidence now; it no longer fails the dimension.
    mismatch = judge_trace(
        {
            "exportState": "exported",
            "frames": _frames(expected=2, observed=1),
            "observations": _complete_observations(),
        },
        _context(),
    )
    assert (mismatch.status, mismatch.reason) == (TRACE_PASS, "")
    assert mismatch.frames["nms"] == {"expected": 2, "observed": 1, "scope": "a2a-t-server"}


def test_attribution_keeps_unknown_outside_the_local_coverage() -> None:
    points = expected_client_points(_context())[:2]
    results = match_points(points, (), run_id="run-1")
    status, findings = attribute_points(
        results,
        local_points=(),
        local_known=("task_validation",),
    )
    by_point = {finding.point: finding.status for finding in findings}
    assert by_point["task_prompt_rendered"] == ATTRIBUTION_UNKNOWN
    assert by_point["task_validation"] == ATTRIBUTION_MISSING_CLIENT
    assert status == ATTRIBUTION_MISSING_CLIENT


def test_judge_trace_reads_missing_optional_data_as_partial() -> None:
    observations = [item for item in _complete_observations() if item["kind"] != "llm"]
    judgement = judge_trace(
        {
            "exportState": "exported",
            "frames": _frames(),
            "observations": observations,
        },
        _context(),
    )
    assert judgement.status == TRACE_PARTIAL
    assert judgement.reason == REASON_OPTIONAL_DATA_MISSING
    assert [finding.point for finding in judgement.findings] == ["slot_extraction"]


def test_judge_trace_passes_when_every_point_and_frame_is_present() -> None:
    judgement = judge_trace(
        {
            "exportState": "exported",
            "frames": _frames(),
            "observations": _complete_observations(),
        },
        _context(),
    )
    assert judgement.status == TRACE_PASS
    assert judgement.reason == ""
    assert [row.point.id for row in judgement.data] == [
        "request.original",
        "request.dispatched",
        "slot_extraction",
        "task_t",
        "negotiation.message",
        "negotiation.reply",
        "task.succeeded",
    ]


def test_judge_trace_fails_when_a_required_datum_is_missing() -> None:
    observations = [item for item in _complete_observations() if item["kind"] != "task_prompt_rendered"]
    judgement = judge_trace(
        {
            "exportState": "exported",
            "frames": {**_frames(), "client": {"runId": "run-1", "observed": 0}},
            "observations": observations,
        },
        _context(),
    )
    assert (judgement.status, judgement.reason) == (TRACE_FAIL, REASON_DATA_MISSING)
    assert "request.original" in {finding.point for finding in judgement.findings}


def test_reason_codes_are_exposed_as_one_tuple() -> None:
    assert set(TRACE_REASONS) == {
        "trace_not_exported",
        "trace_frames_mismatch",
        "trace_missing_client",
        "trace_missing_ingest",
        "trace_missing_vendor",
        "trace_content_unavailable",
        "trace_nms_hop_unknown",
        "trace_data_missing",
        "trace_optional_data_missing",
    }


TASK_T = "## Operation Type\nCreate\n\n## Task Object\nA2ATestRegion"

INTENT = {
    "Operation type": "Create",
    "Energy saving cell RAT": "ALL",
    "Energy saving aggressiveness": "",
    "Energy saving region": "A2ATestRegion",
    "Whether Experience-Preserving Energy Saving is Required": "",
    "Special Requirements for Carrier Shutdown Frequencies": "",
    "Energy saving time": "",
    "Frequencies of carriers that can be shut down": [],
    "Gain target": "",
    "Guaranteed throughput target by zone": [],
    "Scenario-based energy saving": [],
}


def test_task_t_fidelity_compares_the_judged_and_exported_copies() -> None:
    assert task_t_fidelity(TASK_T, TASK_T).equal is True
    changed = task_t_fidelity(TASK_T, TASK_T.replace("Create", "Modify"))
    assert changed.comparable is True
    assert changed.equal is False
    assert task_t_fidelity("", TASK_T).comparable is False


def test_intent_corroboration_compares_the_oracle_with_the_langfuse_reading() -> None:
    assert intent_corroboration(INTENT, dict(INTENT)).equal is True
    drifted = dict(INTENT)
    drifted["Energy saving region"] = "OtherRegion"
    assert intent_corroboration(INTENT, drifted).equal is False
    assert intent_corroboration(INTENT, {"foo": "bar"}).comparable is False


def test_fidelity_summary_exports_only_the_differences() -> None:
    summary = fidelity_summary(task_t_fidelity(TASK_T, TASK_T.replace("Create", "Modify")))
    assert summary["comparable"] is True
    assert summary["equal"] is False
    assert summary["differenceCount"] >= 1
    assert summary["differences"]
    assert all(
        (row.get("verdict") or row.get("status")) in {"changed", "missing", "added"} for row in summary["differences"]
    )


def test_points_summary_is_compact() -> None:
    summary = points_summary(process_points({"observations": _complete_observations()}, _context()))
    assert summary
    assert {"id", "label", "expected", "observed", "ok"} <= set(summary[0])


def test_dispatched_content_is_read_from_the_snapshot_observations() -> None:
    payload = {
        "observations": [
            {"kind": "task_prompt_rendered", "promptText": "TASK", "interception": False},
            {"kind": "task_validation", "interception": True, "userRequest": "ORIGINAL"},
            {"kind": "task_created", "params": {"Operation type": "Create"}},
        ]
    }
    assert dispatched_task_t(payload) == ("TASK", False)
    assert original_request(payload) == "ORIGINAL"
    assert intent_params(payload) == {"Operation type": "Create"}


def test_process_points_lists_every_expected_point() -> None:
    results = process_points({"observations": _complete_observations()}, _context())
    assert results
    assert all(result.ok for result in results)


def test_a_run_is_audited_when_it_has_a_trace_id() -> None:
    """The declared downstream never gates the audit: a traced run is a traced run."""
    applicability = trace_applicability("run-1", TRACE_ID)
    assert applicability.applicable is True
    assert applicability.to_dict()["applicable"] == "yes"
    assert applicability.to_dict()["traceId"] == TRACE_ID

    assert trace_applicability("run-1", TRACE_ID, simulated=True).applicable is False
    assert trace_applicability("", TRACE_ID).applicable is False
    assert trace_applicability("run-1", "").applicable is False
    # A malformed id would make ``TraceStore`` reject the fetch; say so instead of trying.
    assert trace_applicability("run-1", "trace-1").applicable is False


def test_nms_kind_is_derived_from_the_declared_keys_only() -> None:
    assert nms_kind(True, False) == NMS_KIND_REAL
    assert nms_kind(True, True) == NMS_KIND_SIM
    assert nms_kind(False, False) == NMS_KIND_LOCAL
    assert nms_kind(True, None) == NMS_KIND_UNKNOWN
    assert nms_kind(None, False) == NMS_KIND_UNKNOWN
    assert nms_kind(None, None) == NMS_KIND_UNKNOWN


def test_the_phase_whitelist_is_the_export_contract() -> None:
    phase = exported_span_kinds("phase")
    assert phase is not None
    assert {"message_sent", "task_prompt_rendered", "task_validation", "llm", "tool", "finished"} <= phase
    assert "transport" not in phase
    assert exported_span_kinds("full") is None
    assert exported_span_kinds("minimal") == frozenset({"llm", "tool", "finished"})
