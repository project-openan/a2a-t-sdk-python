"""Tests of the review-time judging of stored tester evidence."""

from __future__ import annotations

from typing import Any

from eval.osp.review import (
    TRACE_NOT_EXPORTED_STATUS,
    VERDICT_FAIL,
    VERDICT_MISSING,
    VERDICT_NOT_APPLICABLE,
    VERDICT_PARTIAL,
    VERDICT_PASS,
    VERDICT_RECORDED,
    case_signature,
    evidence_input_signature,
    judge_protocol,
)

INTENT: dict[str, object] = {
    "Operation type": "Modify",
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

TASK_T = """## Operation Type
Modify

## Task Type
RAN energy saving

## Task Description
Execute the energy-saving task.

## Task Object
A2ATestRegion

## Task Target

## Task Context
Site list: ["LNR_KPI_7745"], rate target: 2Mbps.
"""

MESSAGE = """## Feasibility Negotiation
Reduce the throughput target from 10Mbps to 2Mbps for 08:00-18:00.
"""


def _case(
    *,
    protocol: str = "a2a-t",
    blocked: bool = False,
    rounds: int = 1,
    messages: object = None,
    slot_errors: object = None,
    intent: dict[str, object] | None = None,
    followup: str | None = None,
) -> dict[str, Any]:
    """Return a minimal case document the judge understands."""
    negotiation: dict[str, object] = {
        "expected": rounds > 0,
        "expected_rounds": rounds,
        "kinds": None,
        "rounds": [],
    }
    if messages is not None:
        negotiation["messages"] = messages
    a2at: dict[str, object] = {
        "validate": "blocked" if blocked else "pass",
        "validate_label": "",
        "send": True,
        "task_t": TASK_T,
        "negotiation": negotiation,
        "final_state": "task_completed",
    }
    if slot_errors is not None:
        a2at["slot_errors"] = slot_errors
    a2a: dict[str, object] = {
        "negotiation": {
            "expected": False,
            "expected_rounds": 0,
            "kinds": None,
            "rounds": [],
        },
        "final_state": None,
    }
    case: dict[str, Any] = {
        "id": 1,
        "name": "case",
        "user_input": "do it",
        "expect": {
            "intent": dict(intent or INTENT),
            "protocols": {"a2a-t": a2at, "a2a": a2a},
        },
        "assert": {
            "intent": True,
            "a2a-t": {
                "validate": True,
                "task_t": True,
                "send": True,
                "negotiation": True,
                "final_state": True,
            },
            "a2a": {"negotiation": True, "final_state": False},
        },
    }
    if followup is not None:
        case["blocked_followup"] = {"input": followup}
    return case


def _evidence(
    *,
    server_intent: dict[str, object] | None = None,
    sdk_intent: dict[str, object] | None = None,
    task_t: str = TASK_T,
    validate_verdict: str = "pass",
    slot_errors: list[dict[str, object]] | None = None,
    action: str = "passed",
    rounds: int = 1,
    messages: list[dict[str, object]] | None = None,
    final_state: str | None = "task_completed",
    refs_input: str = "do it",
    interception: dict[str, object] | None = None,
    run_id: str = "run-1",
    batch: dict[str, object] | None = None,
    trace: dict[str, object] | None = None,
    trace_id: str | None = None,
    accepted_status: str | None = None,
) -> dict[str, object]:
    """Return one stored tester item (trimmed like the review corpus stores it)."""
    stages: dict[str, object] = {
        "intent": {"stage": "intent", "status": action, "actual": dict(INTENT)},
        "task_t": {"stage": "task_t", "status": "passed", "actual": task_t},
        "validate": {
            "stage": "validate",
            "status": "passed",
            "actual": validate_verdict,
            "detail": {
                "verdict": validate_verdict,
                "slotErrors": slot_errors or [],
            },
        },
        "negotiate": {
            "stage": "negotiate",
            "status": "passed",
            "actual": {"rounds": rounds, "kinds": ["feasibility"]},
        },
    }
    if final_state is not None:
        stages["final"] = {"stage": "final", "status": "passed", "actual": final_state}
    if accepted_status is not None:
        stages["accepted"] = {
            "stage": "accepted",
            "status": accepted_status,
            "actual": "accepted",
        }
    # Applicability is "the run has a trace id". A helper call that carries trace evidence (or a
    # downstream declaration) models a traced run; the non-trace dimensions keep passing no
    # batch/trace and stay out of the gate.
    resolved_trace_id = (
        trace_id if trace_id is not None else ("a" * 32 if batch is not None or trace is not None else None)
    )
    return {
        "runId": run_id,
        "traceId": resolved_trace_id,
        "batch": batch if batch is not None else {},
        "trace": trace,
        "refs": {"userInput": refs_input},
        "stages": stages,
        "intent": dict(sdk_intent or INTENT),
        "serverIntent": dict(server_intent or INTENT),
        "messages": messages or [],
        "interception": interception,
    }


def _dimension(judgement: Any, name: str) -> Any:
    row = judgement.dimension(name)
    assert row is not None
    return row


def test_a_matching_final_intent_passes() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence())

    assert _dimension(judgement, "intent").status == VERDICT_PASS
    assert judgement.status == VERDICT_PASS


def test_a_changed_final_intent_fails() -> None:
    server = dict(INTENT)
    server["Energy saving region"] = "OtherRegion"

    judgement = judge_protocol(_case(), "a2a-t", _evidence(server_intent=server))

    intent = _dimension(judgement, "intent")
    assert intent.status == VERDICT_FAIL
    assert [diff["path"] for diff in intent.diffs] == ["intent.Energy saving region"]
    assert judgement.status == VERDICT_FAIL


def test_an_unjudgeable_intent_is_recorded_and_makes_the_task_partial() -> None:
    """A simulator/proxy run keeps the reading but cannot be accepted on it."""
    judgement = judge_protocol(_case(), "a2a-t", _evidence(action="recorded"))

    assert _dimension(judgement, "intent").status == VERDICT_RECORDED
    assert judgement.status == VERDICT_PARTIAL


def test_pure_a2a_judges_an_intent_the_tester_could_judge() -> None:
    """A direct vendor A2A payload is the downstream's own reading, so the review judges it too.

    The tester already decides whether an A2A run has a direct payload worth judging (proxy and
    simulator hops stay ``recorded``); the review must not downgrade a judged run to "not
    applicable" just because the hop is pure A2A.
    """
    judgement = judge_protocol(_case(protocol="a2a", rounds=0), "a2a", _evidence())

    assert _dimension(judgement, "intent").status == VERDICT_PASS
    assert judgement.status == VERDICT_PASS


def test_a_changed_pure_a2a_intent_fails() -> None:
    server = dict(INTENT)
    server["Energy saving region"] = "OtherRegion"

    judgement = judge_protocol(_case(protocol="a2a", rounds=0), "a2a", _evidence(server_intent=server))

    intent = _dimension(judgement, "intent")
    assert intent.status == VERDICT_FAIL
    assert [diff["path"] for diff in intent.diffs] == ["intent.Energy saving region"]
    assert judgement.status == VERDICT_FAIL


def test_a_pure_a2a_proxy_intent_stays_recorded() -> None:
    """The OSS hop returns a different payload shape; recording it must not turn the row red."""
    judgement = judge_protocol(_case(protocol="a2a", rounds=0), "a2a", _evidence(action="recorded"))

    assert _dimension(judgement, "intent").status == VERDICT_RECORDED
    assert judgement.status == VERDICT_PARTIAL


def test_pure_a2a_without_intent_evidence_is_not_applicable() -> None:
    evidence = _evidence()
    evidence["serverIntent"] = None
    stages = evidence["stages"]
    assert isinstance(stages, dict)
    stages.pop("intent")

    judgement = judge_protocol(_case(protocol="a2a", rounds=0), "a2a", evidence)

    assert _dimension(judgement, "intent").status == VERDICT_NOT_APPLICABLE
    assert judgement.status == VERDICT_NOT_APPLICABLE


def test_a_changed_task_t_section_fails() -> None:
    actual = TASK_T.replace('Site list: ["LNR_KPI_7745"]', 'Site list: ["NES_27A_321071"]')

    judgement = judge_protocol(_case(), "a2a-t", _evidence(task_t=actual))

    task_t = _dimension(judgement, "task_t")
    assert task_t.status == VERDICT_FAIL
    assert all(diff["path"] != "task_t.Operation Type" for diff in task_t.diffs)


def test_the_blocked_case_judges_the_interception_segment() -> None:
    case = _case(
        blocked=True,
        slot_errors={
            "mode": "any",
            "expect": [{"code": "slot.constraint_violated", "contains": ["gain"]}],
        },
        followup="do it properly",
    )
    evidence = _evidence(
        refs_input="do it properly",
        interception={
            "stages": {
                "validate": {
                    "stage": "validate",
                    "status": "failed",
                    "detail": {
                        "verdict": "blocked",
                        "slotErrors": [
                            {
                                "slotName": "Gain target",
                                "code": "slot.constraint_violated",
                                "message": "gain target is not allowed",
                                "facts": {"actual": "20%"},
                            }
                        ],
                    },
                }
            }
        },
    )

    judgement = judge_protocol(case, "a2a-t", evidence)

    validate = _dimension(judgement, "validate")
    assert validate.status == VERDICT_PASS
    assert validate.actual == "blocked"
    assert judgement.status == VERDICT_PASS


def test_a_blocked_case_without_the_expected_slot_error_fails() -> None:
    case = _case(
        blocked=True,
        slot_errors={"mode": "any", "expect": [{"code": "slot.not_provided"}]},
        followup="do it properly",
    )
    evidence = _evidence(
        refs_input="do it properly",
        interception={
            "stages": {
                "validate": {
                    "stage": "validate",
                    "detail": {
                        "verdict": "blocked",
                        "slotErrors": [
                            {
                                "slotName": "Gain target",
                                "code": "slot.constraint_violated",
                                "message": "gain target is not allowed",
                                "facts": {},
                            }
                        ],
                    },
                }
            }
        },
    )

    judgement = judge_protocol(case, "a2a-t", evidence)

    assert _dimension(judgement, "validate").status == VERDICT_FAIL


def test_an_interception_miss_judges_only_the_block() -> None:
    case = _case(blocked=True, followup="do it in A2ATestRegion")
    evidence = _evidence(
        run_id="",
        interception={
            "stages": {
                "validate": {
                    "stage": "validate",
                    "status": "failed",
                    "actual": "pass",
                    "detail": {"verdict": "pass", "slotErrors": []},
                }
            },
            "followupSkipped": True,
        },
    )
    # The tester never dispatched the replacement, so the run stages are absent.
    evidence["stages"] = {}

    judgement = judge_protocol(case, "a2a-t", evidence)

    assert judgement.status == VERDICT_FAIL
    validate = _dimension(judgement, "validate")
    assert validate.status == VERDICT_FAIL
    assert validate.actual == "pass"
    # The replacement's expectations describe a request that never ran: missing evidence would
    # read as an incomplete batch, so every follow-up dimension is not applicable instead.
    for name in ("intent", "task_t", "final_state"):
        row = _dimension(judgement, name)
        assert row.status == VERDICT_NOT_APPLICABLE
        assert "未执行" in row.reason


def test_a_followup_that_ran_is_still_judged() -> None:
    # The flag is what stops the judging; a blocked case without it keeps the two-segment rules.
    case = _case(blocked=True, followup="do it properly")
    evidence = _evidence(
        refs_input="do it properly",
        interception={
            "stages": {
                "validate": {
                    "stage": "validate",
                    "status": "passed",
                    "actual": "blocked",
                    "detail": {"verdict": "blocked", "slotErrors": []},
                }
            }
        },
    )

    judgement = judge_protocol(case, "a2a-t", evidence)

    assert _dimension(judgement, "validate").status == VERDICT_PASS


def test_the_negotiation_messages_are_judged_from_the_joined_evidence() -> None:
    case = _case(rounds=1, messages=[[MESSAGE]])
    matching = _evidence(messages=[{"round": 1, "kind": "feasibility", "content": MESSAGE}])

    judgement = judge_protocol(case, "a2a-t", matching)

    assert _dimension(judgement, "negotiation").status == VERDICT_PASS

    other = MESSAGE.replace("2Mbps", "1Mbps")
    failing = _evidence(messages=[{"round": 1, "kind": "feasibility", "content": other}])
    failed = judge_protocol(case, "a2a-t", failing)

    assert _dimension(failed, "negotiation").status == VERDICT_FAIL
    assert "negotiation.messages[1]" in [diff["path"] for diff in _dimension(failed, "negotiation").diffs]


def test_the_message_match_names_the_frame_it_used() -> None:
    """A round with two inbound frames keeps both visible but judges only the first."""
    case = _case(rounds=1, messages=[[MESSAGE]])
    evidence = _evidence(
        messages=[
            {"round": 1, "kind": "feasibility", "content": MESSAGE},
            {"round": 1, "kind": "confirm", "content": "Please confirm the revised target."},
        ]
    )

    judgement = judge_protocol(case, "a2a-t", evidence)
    detail = judgement.to_dict()["detail"]
    message_match = detail["negotiation"]["messageMatch"]

    assert message_match["rounds"][0]["sourceIndex"] == 0


def test_an_expected_message_without_evidence_is_partial() -> None:
    case = _case(rounds=1, messages=[[MESSAGE]])

    judgement = judge_protocol(case, "a2a-t", _evidence(messages=[]))

    assert _dimension(judgement, "negotiation").status == VERDICT_MISSING
    assert judgement.status == VERDICT_PARTIAL


def test_a_wrong_round_count_fails() -> None:
    judgement = judge_protocol(_case(rounds=2), "a2a-t", _evidence(rounds=1))

    assert _dimension(judgement, "negotiation").status == VERDICT_FAIL


REAL_BATCH: dict[str, object] = {"nms": True, "nmsSim": False}

#: The client-side observations a one-round A2A-T run exports at phase level. The content fields
#: are the key-data copies the presence gate reads; the point table still matches on kind/stage.
A2A_T_OBSERVATIONS: tuple[dict[str, object], ...] = (
    {
        "kind": "task_prompt_rendered",
        "runId": "run-1",
        "userRequest": "do it",
        "promptText": TASK_T,
    },
    {"kind": "task_validation", "runId": "run-1"},
    {"kind": "task_created", "runId": "run-1", "taskId": "task-1", "contextId": "ctx-1"},
    {
        "kind": "llm",
        "type": "GENERATION",
        "stage": "task_t_slot_extraction",
        "runId": "run-1",
        "response": '{"slots": {}}',
    },
    {
        "kind": "message_sent",
        "runId": "run-1",
        "performative": "TASK_T",
        "taskT": TASK_T,
        "summary": "do it",
    },
    {"kind": "message_sent", "runId": "run-1", "performative": "ACCEPT", "negotiationT": "accept"},
    {"kind": "decision_requested", "round": 1, "runId": "run-1", "content": MESSAGE},
    {"kind": "accept", "round": 1, "runId": "run-1"},
)


def _trace(
    *,
    expected: int | None = 2,
    observed: int | None = 2,
    client: int = 2,
    export_state: str = "exported",
    observations: tuple[dict[str, object], ...] | None = None,
    local_points: list[str] | None = None,
    capture: bool | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "traceId": "trace-1",
        "exportState": export_state,
        "capturedAt": "2026-11-16T10:00:00+00:00",
        "frames": {
            "rounds": 1,
            "nms": {"expected": expected, "observed": observed, "scope": "a2a-t-server"},
            "client": {"runId": "run-1", "observed": client},
        },
        "observations": list(A2A_T_OBSERVATIONS if observations is None else observations),
        "attribution": {"status": "ok", "findings": []},
    }
    if local_points is not None:
        payload["localPoints"] = list(local_points)
    if capture is not None:
        payload["capture_content"] = capture
    return payload


def _trace_code(judgement: Any) -> str:
    diff = _dimension(judgement, "trace").diffs[0]
    return str(diff["reason"])


def test_a_real_run_without_a_trace_snapshot_is_missing_not_failed() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(batch=REAL_BATCH))

    assert _dimension(judgement, "trace").status == TRACE_NOT_EXPORTED_STATUS
    assert TRACE_NOT_EXPORTED_STATUS == VERDICT_MISSING
    assert _trace_code(judgement) == "trace_not_exported"
    # Not collecting a snapshot is missing evidence: the task is partial, not a failed run.
    assert judgement.status == VERDICT_PARTIAL


def test_a_failed_export_is_missing_like_an_absent_snapshot() -> None:
    judgement = judge_protocol(
        _case(),
        "a2a-t",
        _evidence(batch=REAL_BATCH, trace=_trace(export_state="not_exported")),
    )

    assert _dimension(judgement, "trace").status == VERDICT_MISSING
    assert _trace_code(judgement) == "trace_not_exported"


def test_a_simulator_run_never_judges_the_trace() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(batch={"nms": True, "nmsSim": True}))

    assert _dimension(judgement, "trace").status == VERDICT_NOT_APPLICABLE
    assert judgement.status == VERDICT_PASS


def test_a_run_without_a_dispatch_never_judges_the_trace() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(batch=REAL_BATCH, run_id=""))

    assert _dimension(judgement, "trace").status == VERDICT_NOT_APPLICABLE
    assert judgement.status == VERDICT_PASS


def test_a_legacy_batch_without_declared_downstream_is_still_audited() -> None:
    """The real-machine complaint: an old batch has no nms/nmsSim key but does have a trace."""
    judgement = judge_protocol(
        _case(),
        "a2a-t",
        _evidence(batch={"discovery": "nms"}, trace=_trace()),
    )

    assert _dimension(judgement, "trace").status == VERDICT_PASS
    assert judgement.status == VERDICT_PASS


def test_a_run_without_a_trace_id_says_why_it_abstains() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(batch=REAL_BATCH, trace=_trace(), trace_id=""))

    trace = _dimension(judgement, "trace")
    assert trace.status == VERDICT_NOT_APPLICABLE
    assert "traceId" in trace.reason


def test_a_complete_trace_passes() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(batch=REAL_BATCH, trace=_trace()))

    trace = _dimension(judgement, "trace")
    assert trace.status == VERDICT_PASS
    assert trace.expected == 2
    assert trace.actual == 2


def test_the_trace_uses_the_observed_rounds_not_the_expected_ones() -> None:
    judgement = judge_protocol(
        _case(rounds=2),
        "a2a-t",
        _evidence(batch=REAL_BATCH, trace=_trace(), rounds=1),
    )

    assert _dimension(judgement, "trace").status == VERDICT_PASS
    assert _dimension(judgement, "negotiation").status == VERDICT_FAIL


def test_a_failed_accepted_stage_does_not_require_task_created() -> None:
    without_task_created = tuple(row for row in A2A_T_OBSERVATIONS if row.get("kind") != "task_created")

    judgement = judge_protocol(
        _case(),
        "a2a-t",
        _evidence(
            batch=REAL_BATCH,
            trace=_trace(observations=without_task_created),
            accepted_status="failed",
            final_state="",
        ),
    )

    assert _dimension(judgement, "trace").status == VERDICT_PASS


def test_a_missing_required_datum_fails_with_its_code() -> None:
    without_rendered = tuple(
        row
        for row in A2A_T_OBSERVATIONS
        if row.get("kind") not in {"task_prompt_rendered", "message_sent"}
    )

    judgement = judge_protocol(
        _case(),
        "a2a-t",
        _evidence(batch=REAL_BATCH, trace=_trace(observations=without_rendered)),
    )

    assert _dimension(judgement, "trace").status == VERDICT_FAIL
    assert _trace_code(judgement) == "trace_data_missing"
    assert judgement.status == VERDICT_FAIL

    trace = judgement.to_dict()["detail"]["trace"]
    assert {row["id"] for row in trace["data"] if row["status"] == "missing"} >= {
        "request.original",
        "request.dispatched",
        "task_t",
    }
    assert trace["nmsKind"] == "real"


def test_a_missing_optional_datum_is_partial() -> None:
    without_extraction = tuple(row for row in A2A_T_OBSERVATIONS if row.get("kind") != "llm")

    judgement = judge_protocol(
        _case(),
        "a2a-t",
        _evidence(batch=REAL_BATCH, trace=_trace(observations=without_extraction)),
    )

    assert _dimension(judgement, "trace").status == VERDICT_PARTIAL
    assert _trace_code(judgement) == "trace_optional_data_missing"
    trace = judgement.to_dict()["detail"]["trace"]
    assert [row["id"] for row in trace["data"] if row["status"] == "missing"] == ["slot_extraction"]


def test_a_frame_mismatch_is_reference_only() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(batch=REAL_BATCH, trace=_trace(observed=1)))

    assert _dimension(judgement, "trace").status == VERDICT_PASS
    frames = judgement.to_dict()["detail"]["trace"]["frames"]
    assert frames["nms"]["expected"] == 2
    assert frames["nms"]["observed"] == 1


def test_an_unidentified_nms_hop_no_longer_gates_the_data() -> None:
    judgement = judge_protocol(
        _case(),
        "a2a-t",
        _evidence(batch=REAL_BATCH, trace=_trace(expected=None, observed=None)),
    )

    assert _dimension(judgement, "trace").status == VERDICT_PASS
    assert judgement.status == VERDICT_PASS
    trace = judgement.to_dict()["detail"]["trace"]
    assert trace["frames"]["nms"]["expected"] is None


def test_a_recognized_hop_without_a_round_count_is_reference_only() -> None:
    judgement = judge_protocol(
        _case(),
        "a2a-t",
        _evidence(batch=REAL_BATCH, trace=_trace(expected=None, observed=2)),
    )

    assert _dimension(judgement, "trace").status == VERDICT_PASS
    trace = judgement.to_dict()["detail"]["trace"]
    assert trace["frames"]["nms"]["observed"] == 2


def test_the_capture_flag_is_not_a_gate_by_itself() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(batch=REAL_BATCH, trace=_trace(capture=False)))

    # A snapshot whose observations carry the data passes even if the legacy flag says otherwise;
    # a snapshot whose data is absent fails on the missing item, not on the flag.
    assert _dimension(judgement, "trace").status == VERDICT_PASS


def test_the_client_frame_count_is_reference_only() -> None:
    a2a_t = judge_protocol(_case(), "a2a-t", _evidence(batch=REAL_BATCH, trace=_trace(client=0)))
    pure_observations = (
        {"kind": "message_sent", "runId": "run-1"},
        {"kind": "task_created", "runId": "run-1"},
    )
    pure = judge_protocol(
        _case(protocol="a2a", rounds=0),
        "a2a",
        _evidence(
            batch=REAL_BATCH,
            trace=_trace(client=0, observations=pure_observations),
            rounds=0,
        ),
    )

    assert _dimension(a2a_t, "trace").status == VERDICT_PASS
    # Pure A2A has no request.original copy at all (no Task-T, no root), so the required item fails.
    assert _dimension(pure, "trace").status == VERDICT_FAIL
    assert _trace_code(pure) == "trace_data_missing"


def test_the_detail_compares_the_judged_task_t_with_the_exported_one() -> None:
    trace = _trace()
    trace["observations"] = [
        *A2A_T_OBSERVATIONS,
        {"kind": "task_prompt_rendered", "promptText": TASK_T},
        {"kind": "task_created", "params": dict(INTENT)},
    ]

    judgement = judge_protocol(_case(), "a2a-t", _evidence(batch=REAL_BATCH, trace=trace))
    detail = judgement.to_dict()["detail"]

    assert detail["taskT"]["judged"] == TASK_T
    assert detail["taskT"]["dispatched"] == TASK_T.rstrip()
    assert detail["taskT"]["fidelity"]["comparable"] is True
    assert detail["taskT"]["fidelity"]["equal"] is True
    assert detail["intent"]["corroboration"]["equal"] is True
    assert detail["trace"]["points"]


def test_a_different_exported_task_t_is_reported() -> None:
    other = TASK_T.replace("2Mbps", "5Mbps")
    trace = _trace()
    trace["observations"] = [
        {**row, "promptText": other} if row.get("kind") == "task_prompt_rendered" else row
        for row in A2A_T_OBSERVATIONS
    ]

    judgement = judge_protocol(_case(), "a2a-t", _evidence(batch=REAL_BATCH, trace=trace))
    fidelity = judgement.to_dict()["detail"]["taskT"]["fidelity"]

    assert fidelity["comparable"] is True
    assert fidelity["equal"] is False
    changed = [row["path"] for row in fidelity["rows"] if row["verdict"] in {"changed", "missing"}]
    assert changed == ["task_t.Task Context"]


def test_a_drifting_exported_intent_is_not_the_oracle() -> None:
    trace = _trace()
    trace["observations"] = [
        *A2A_T_OBSERVATIONS,
        {"kind": "task_created", "params": {**INTENT, "Energy saving region": "Other"}},
    ]

    judgement = judge_protocol(_case(), "a2a-t", _evidence(batch=REAL_BATCH, trace=trace))
    detail = judgement.to_dict()["detail"]

    assert detail["intent"]["server"]["Energy saving region"] == "A2ATestRegion"
    assert detail["intent"]["corroboration"]["equal"] is False


def test_the_trace_summary_is_part_of_the_detail_payload() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(batch=REAL_BATCH, trace=_trace()))

    trace = judgement.to_dict()["detail"]["trace"]

    assert trace["present"] is True
    assert trace["traceId"] == "trace-1"
    assert trace["exportState"] == "exported"
    assert trace["frames"]["nms"]["expected"] == 2


def test_a_wrong_final_state_fails() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(final_state="task_failed"))

    assert _dimension(judgement, "final_state").status == VERDICT_FAIL


def test_acceptance_is_the_success_point_when_the_batch_stops_at_accepted() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(final_state=None, accepted_status="passed"))

    final = _dimension(judgement, "final_state")
    assert final.status == VERDICT_PASS
    assert final.actual == "accepted"
    assert "受理阶段" in final.reason
    assert judgement.status == VERDICT_PASS


def test_a_failed_acceptance_fails_instead_of_going_missing() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(final_state=None, accepted_status="failed"))

    assert _dimension(judgement, "final_state").status == VERDICT_FAIL
    assert judgement.status == VERDICT_FAIL


def test_an_unobserved_acceptance_stays_missing() -> None:
    judgement = judge_protocol(_case(), "a2a-t", _evidence(final_state=None, accepted_status="waiting"))

    assert _dimension(judgement, "final_state").status == VERDICT_MISSING
    assert judgement.status == VERDICT_PARTIAL


def test_the_frozen_input_signature_records_what_the_run_dispatched() -> None:
    """The signature is provenance: it changes with the run's input but never gates the verdict."""
    assert evidence_input_signature(_evidence()) != evidence_input_signature(
        _evidence(refs_input="old text")
    )


def test_the_case_signature_tracks_expectations_and_the_followup() -> None:
    case = _case()
    before = case_signature(case)

    changed = _case()
    changed["expect"]["protocols"]["a2a-t"]["task_t"] = TASK_T + "\nextra\n"
    assert case_signature(changed) != before

    followup = _case(blocked=True, followup="do it properly")
    assert case_signature(followup) != case_signature({**followup, "blocked_followup": {"input": "x"}})
