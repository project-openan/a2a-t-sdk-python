"""Tests of the presence-only key-data judgement."""

from __future__ import annotations

from collections.abc import Sequence

from eval.osp.datapoints import (
    STATUS_MISSING,
    STATUS_NOT_APPLICABLE,
    STATUS_PRESENT,
    DataContext,
    DataPointResult,
    data_points,
    missing_optional,
    missing_required,
    resolve_data_points,
)

RUN = "run-1"


def _context(**overrides: object) -> DataContext:
    values: dict[str, object] = {
        "protocol": "a2a-t",
        "run_mode": "create",
        "blocked": False,
        "followup": False,
        "rounds": 0,
        "accepted": True,
        "run_id": RUN,
    }
    values.update(overrides)
    return DataContext(**values)  # type: ignore[arg-type]


def _obs(kind: str, **fields: object) -> dict[str, object]:
    return {"kind": kind, "name": f"{kind} span", "runId": RUN, "side": "client", **fields}


def _complete() -> list[dict[str, object]]:
    return [
        _obs("task_prompt_rendered", user_request="create a task", promptText="TASK-T"),
        _obs("llm", stage="task_t_slot_extraction", response='{"slots": {}}', type="GENERATION"),
        _obs("message_sent", performative="TASK_T", taskT="TASK-T", summary="create a task"),
        _obs("task_created", taskId="task-1", contextId="ctx-1", state="TASK_STATE_SUBMITTED"),
    ]


def _by_id(results: Sequence[DataPointResult]) -> dict[str, DataPointResult]:
    return {result.point.id: result for result in results}


def test_every_a2a_t_item_is_present_when_any_copy_exists() -> None:
    results = _by_id(resolve_data_points({"observations": _complete()}, _context()))
    assert results["request.original"].status == STATUS_PRESENT
    assert results["request.dispatched"].status == STATUS_PRESENT
    assert results["slot_extraction"].status == STATUS_PRESENT
    assert results["task_t"].status == STATUS_PRESENT
    assert results["task.succeeded"].status == STATUS_PRESENT
    # No negotiation round ran, so the two negotiation items are not applicable.
    assert results["negotiation.message"].status == STATUS_NOT_APPLICABLE
    assert results["negotiation.reply"].status == STATUS_NOT_APPLICABLE


def test_a_blocked_original_is_read_from_the_interception_span_only() -> None:
    observations = [
        _obs("task_prompt_rendered", user_request="follow-up text", promptText="TASK-T"),
        _obs("message_sent", performative="TASK_T", taskT="TASK-T", summary="follow-up text"),
    ]
    without = _by_id(
        resolve_data_points(
            {"observations": observations},
            _context(blocked=True, followup=True),
        )
    )
    assert without["request.original"].status == STATUS_MISSING
    # The follow-up run's own request must not fill the original column.
    assert without["request.dispatched"].status == STATUS_PRESENT

    with_interception = _by_id(
        resolve_data_points(
            {
                "observations": [
                    _obs(
                        "task_validation",
                        interception=True,
                        userRequest="blocked original",
                        source="interception",
                    ),
                    *observations,
                ]
            },
            _context(blocked=True, followup=True),
            include_values=True,
        )
    )
    assert with_interception["request.original"].status == STATUS_PRESENT
    assert with_interception["request.original"].copies[0].value == "blocked original"


def test_pure_a2a_marks_the_task_t_only_items_not_applicable() -> None:
    results = _by_id(
        resolve_data_points(
            {"observations": [_obs("task_created", taskId="task-1", contextId="ctx-1")]},
            _context(protocol="a2a"),
        )
    )
    assert results["request.dispatched"].status == STATUS_NOT_APPLICABLE
    assert results["slot_extraction"].status == STATUS_NOT_APPLICABLE
    assert results["task_t"].status == STATUS_NOT_APPLICABLE
    assert results["request.original"].status == STATUS_MISSING


def test_pure_a2a_reads_the_original_from_the_trace_even_for_a_blocked_case() -> None:
    # Task-T validation (and its interception replay) is A2A-T only; an A2A trace carries the
    # request on its own root, so a blocked case must not require the A2A-T span.
    results = _by_id(
        resolve_data_points(
            {"observations": [{"name": "run · night · pure", "runId": RUN, "userRequest": "text"}]},
            _context(protocol="a2a", blocked=True),
        )
    )
    assert results["request.original"].status == STATUS_PRESENT


def test_the_original_request_is_read_from_any_span_that_carries_it() -> None:
    # A pure-A2A root and its anchors carry the request as trace metadata; the span name is not
    # part of the contract (``run · …``, ``run opened`` and ``trace opened`` all appear).
    observations = [
        {"name": "run opened", "runId": RUN, "userRequest": "operator text"},
        {"name": "trace opened", "type": "EVENT", "runId": RUN, "userRequest": "operator text"},
        {"name": "Some other hop", "runId": RUN, "userRequest": "operator text"},
    ]
    results = _by_id(resolve_data_points({"observations": observations}, _context(protocol="a2a")))
    assert results["request.original"].status == STATUS_PRESENT
    assert len(results["request.original"].copies) == 3


def test_a_hop_run_id_mismatch_does_not_hide_exported_data() -> None:
    # A gateway/OSS/agent hop can record its own runId while the review row keyed the UI run; the
    # snapshot is still that trace. Presence must not depend on the two ids being equal.
    observations = [
        {**item, "runId": "another-hop"} for item in _complete()
    ]
    results = _by_id(resolve_data_points({"observations": observations}, _context()))
    for point_id in ("request.original", "request.dispatched", "slot_extraction", "task_t"):
        assert results[point_id].status == STATUS_PRESENT, point_id


def test_negotiation_items_appear_once_a_round_ran() -> None:
    observations = [
        _obs("propose", content="proposal R1", round=1),
        _obs("accept", summary="accept R1", round=1),
        _obs("message_sent", performative="ACCEPT", negotiationT="accept R1", round=1),
    ]
    results = _by_id(
        resolve_data_points({"observations": observations}, _context(rounds=1, accepted=False))
    )
    assert results["negotiation.message"].status == STATUS_PRESENT
    assert results["negotiation.reply"].status == STATUS_PRESENT
    assert {copy.round for copy in results["negotiation.message"].copies} == {1}
    assert len(results["negotiation.reply"].copies) == 2


def test_required_and_optional_missing_are_separated() -> None:
    observations = [
        item for item in _complete() if item["kind"] != "llm"
    ]
    results = resolve_data_points({"observations": observations}, _context())
    assert [row.point.id for row in missing_optional(results)] == ["slot_extraction"]
    assert missing_required(results) == ()


def test_task_created_falls_back_to_a_finished_task_identity() -> None:
    results = _by_id(
        resolve_data_points(
            {
                "observations": [
                    _obs("finished", taskId="task-1", contextId="ctx-1", finalState="task_completed"),
                ]
            },
            _context(accepted=True),
        )
    )
    assert results["task.succeeded"].status == STATUS_PRESENT
    assert results["task.succeeded"].copies[0].source == "finished"


def test_data_points_keep_a_stable_column_set() -> None:
    assert [point.id for point in data_points(_context())] == [
        "request.original",
        "request.dispatched",
        "slot_extraction",
        "task_t",
        "negotiation.message",
        "negotiation.reply",
        "task.succeeded",
    ]
    # A2A keeps the same column set; the Task-T-only items are present but not applicable.
    pure = {point.id: point for point in data_points(_context(protocol="a2a"))}
    assert list(pure) == [
        "request.original",
        "request.dispatched",
        "slot_extraction",
        "task_t",
        "negotiation.message",
        "negotiation.reply",
        "task.succeeded",
    ]
    assert pure["request.dispatched"].applicable is False
    assert pure["slot_extraction"].applicable is False
    assert pure["task_t"].applicable is False
