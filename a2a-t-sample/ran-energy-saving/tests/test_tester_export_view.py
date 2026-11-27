"""Tests of the arbitration export renderers.

The dump format mirrors the reference workbook the test team arbitrates with: a nested JSON
path with array indices, and three difference classes (missing / added / changed).
"""

from __future__ import annotations

import json
from pathlib import Path

from demo.web.tester.export_view import (
    build_answer,
    read_traceparent,
    render_intent_diff,
    render_trajectory,
    render_wire_frames,
    render_wire_trajectory,
    result_label,
    score_for,
)
from protocol.client_flow import ClientFlowResult
from protocol.client_service import RunState
from protocol.transcript import NegotiationTranscript


def test_an_identical_intent_is_reported_as_fully_matching() -> None:
    expected = {"Operation type": "Create", "Energy saving region": "A2ATestRegion"}

    assert render_intent_diff(expected, dict(expected)) == "完全一致"


def test_a_missing_nested_field_names_its_array_indices() -> None:
    expected = {
        "Scenario-based energy saving": [
            {"Site list": ["NE 1"], "Guaranteed throughput target by site": [{"Rate": "2Mbps"}]}
        ]
    }
    actual = {"Scenario-based energy saving": [{"Site list": ["NE 1"]}]}

    diff = render_intent_diff(expected, actual)

    assert diff == (
        "缺失: Scenario-based energy saving[0].Guaranteed throughput target by site[0].Rate "
        '(ref值为 "2Mbps")'
    )


def test_an_added_nested_field_is_reported_with_the_answer_value() -> None:
    expected = {"Guaranteed throughput target by zone": []}
    actual = {
        "Guaranteed throughput target by zone": [
            {"Guaranteed throughput target": "2Mbps", "Effective time period": "00:00:00-00:59:59"}
        ]
    }

    diff = render_intent_diff(expected, actual)

    assert diff.splitlines() == [
        '新增: Guaranteed throughput target by zone[0].Guaranteed throughput target '
        '(ans值为 "2Mbps")',
        "新增: Guaranteed throughput target by zone[0].Effective time period "
        '(ans值为 "00:00:00-00:59:59")',
    ]


def test_a_changed_scalar_shows_both_values() -> None:
    expected = {"Scenario-based energy saving": [{"Scenario period": "08:00:00-17:59:59"}]}
    actual = {"Scenario-based energy saving": [{"Scenario period": "08:00:00-12:59:59"}]}

    diff = render_intent_diff(expected, actual)

    assert diff == (
        "值变更: Scenario-based energy saving[0].Scenario period "
        "(08:00:00-17:59:59 → 08:00:00-12:59:59)"
    )


def test_every_difference_is_reported_in_one_block() -> None:
    expected = {"A": "1", "B": "2", "C": "3"}
    actual = {"A": "1", "B": "9", "D": "4"}

    diff = render_intent_diff(expected, actual)

    assert diff.splitlines() == [
        "缺失: C (ref值为 \"3\")",
        "新增: D (ans值为 \"4\")",
        "值变更: B (2 → 9)",
    ]


def test_a_segmented_target_list_renders_an_expression_difference() -> None:
    """A merged window is the same schedule; the cell must not read as missing/changed."""
    expected = {
        "Scenario-based energy saving": [
            {
                "Site list": ["NE 1"],
                "Guaranteed throughput target by site": [
                    {
                        "Guaranteed throughput target": "2Mbps",
                        "Effective time period": "08:00:00-11:59:59",
                    },
                    {
                        "Guaranteed throughput target": "2Mbps",
                        "Effective time period": "12:00:00-17:59:59",
                    },
                ],
            }
        ]
    }
    actual = {
        "Scenario-based energy saving": [
            {
                "Site list": ["NE 1"],
                "Guaranteed throughput target by site": [
                    {
                        "Guaranteed throughput target": "2Mbps",
                        "Effective time period": "08:00:00-17:59:59",
                    }
                ],
            }
        ]
    }

    diff = render_intent_diff(expected, actual)

    assert diff.startswith(
        "表达差异（归一化后一致）: "
        "Scenario-based energy saving[0].Guaranteed throughput target by site"
    )
    assert "缺失" not in diff
    assert "值变更" not in diff


def test_no_collected_intent_renders_an_empty_diff() -> None:
    assert render_intent_diff({"A": "1"}, None) == ""


def test_the_trajectory_keeps_milestones_like_the_reference_workbook() -> None:
    exchanges = [
        {"round": 1, "direction": "OUT", "messageType": "Task-T"},
        {"round": 1, "direction": "IN", "messageType": "RESULT"},
        {"round": None, "direction": "IN", "messageType": "TASK_STATE_SUBMITTED"},
    ]

    assert render_trajectory(exchanges) == (
        "### Round 1 | Client | Task-T\n\n"
        "### Server | TASK_STATE_SUBMITTED"
    )


def test_the_trajectory_keeps_negotiation_performatives() -> None:
    exchanges = [
        {"round": 1, "direction": "OUT", "messageType": "Task-T"},
        {"round": 1, "direction": "IN", "messageType": "TARGET"},
        {"round": 1, "direction": "OUT", "messageType": "PROPOSE"},
        {"round": 1, "direction": "IN", "messageType": "ACCEPT"},
        {"round": None, "direction": "IN", "messageType": "TASK_STATE_WORKING"},
    ]

    assert render_trajectory(exchanges) == (
        "### Round 1 | Client | Task-T\n\n"
        "### Round 1 | Server | TARGET\n\n"
        "### Round 1 | Client | PROPOSE\n\n"
        "### Round 1 | Server | ACCEPT\n\n"
        "### Server | TASK_STATE_WORKING"
    )


def test_the_wire_trajectory_prints_one_summary_line_per_business_frame() -> None:
    task_t = "## Operation Type\nCreate\n\n## Task Object\nThe area is Region A."
    records = [
        {
            "dir": "out",
            "method": "POST",
            "url": "http://nms.test/a2a/json/message:stream",
            "headers": {"content-type": "application/json"},
            "body": {
                "message": {
                    "parts": [{"text": "create task"}],
                    "metadata": {"https://example/Task-T/v1": task_t},
                }
            },
        },
        {"dir": "in", "method": "POST", "status": 200, "stream": True},
        {"dir": "in", "line": ""},
        {"dir": "in", "line": ": heartbeat"},
        {
            "dir": "in",
            "line": (
                'data: {"task": {"status": {"state": "TASK_STATE_SUBMITTED", '
                '"message": {"parts": [{"text": "Task creation succeeded{\\"region\\":\\"A\\"}"}]}}}}'
            ),
        },
        {
            "dir": "in",
            "line": (
                'data: {"statusUpdate": {"status": {"state": "TASK_STATE_WORKING", '
                '"message": {"parts": [{"text": "Task is executing the energy-saving plan."}]}}}}'
            ),
        },
        {"type": "sdk_rejected", "message": "content.param_missing", "status": 400},
    ]

    assert render_wire_trajectory(records) == (
        "R1 Client Task-T | ## Operation Type Create ## Task Object The area is Region A.\n"
        "R1 Server SUBMITTED | Task creation succeeded\n"
        "R1 Server WORKING | Task is executing the energy-saving plan.\n"
        "ERROR | sdk_rejected: content.param_missing"
    )


def test_the_wire_trajectory_drops_the_fixed_task_t_terminology_tail() -> None:
    task_t = (
        "## Operation Type\nCreate\n\n"
        "## Task Object\nThe area is Region A.\n\n"
        "## Expected Output\nOutput the intent report.\n\n"
        "## Terminology Explanation\n1. LTE\n   - Synonyms: 4G, E-UTRAN"
    )
    records = [
        {
            "dir": "out",
            "body": {
                "message": {
                    "parts": [{"text": "create task"}],
                    "metadata": {"https://example/Task-T/v1": task_t},
                }
            },
        }
    ]

    assert render_wire_trajectory(records) == (
        "R1 Client Task-T | ## Operation Type Create ## Task Object The area is Region A. "
        "## Expected Output Output the intent report."
    )


def test_the_wire_trajectory_unwraps_a_json_rpc_sse_result() -> None:
    records = [
        {
            "dir": "in",
            "line": (
                'data: {"jsonrpc": "2.0", "id": 1, "result": {"task": {"status": '
                '{"state": "TASK_STATE_SUBMITTED", "message": {"parts": '
                '[{"text": "Task creation succeeded"}]}}}}}'
            ),
        }
    ]

    assert render_wire_trajectory(records) == "Server SUBMITTED | Task creation succeeded"


def test_the_wire_trajectory_expands_a_json_array_sse_frame() -> None:
    records = [
        {
            "dir": "in",
            "line": (
                'data: [{"statusUpdate": {"status": {"state": "TASK_STATE_WORKING", '
                '"message": {"parts": [{"text": "working"}]}}}}, '
                '{"statusUpdate": {"status": {"state": "TASK_STATE_COMPLETED", '
                '"message": {"parts": [{"text": "done"}]}}}}]'
            ),
        }
    ]

    assert render_wire_trajectory(records) == (
        "Server WORKING | working\nServer COMPLETED | done"
    )


def test_the_wire_trajectory_summarises_an_inbound_json_body_and_a_wrapped_push() -> None:
    records = [
        {
            "dir": "in",
            "method": "POST",
            "status": 200,
            "body": {
                "result": {
                    "task": {
                        "status": {
                            "state": "TASK_STATE_SUBMITTED",
                            "message": {"parts": [{"text": "Task creation succeeded"}]},
                        }
                    }
                }
            },
        },
        {
            "dir": "in",
            "source": "webhook",
            "body": {
                "data": {
                    "statusUpdate": {
                        "status": {
                            "state": "TASK_STATE_WORKING",
                            "message": {"parts": [{"text": "Task is executing."}]},
                        }
                    }
                }
            },
        },
    ]

    assert render_wire_trajectory(records) == (
        "Server SUBMITTED | Task creation succeeded\nServer WORKING | Task is executing."
    )


def test_the_wire_trajectory_names_a_negotiation_performative_and_round() -> None:
    records = [
        {
            "dir": "out",
            "body": {
                "message": {
                    "parts": [{"text": "propose"}],
                    "metadata": {
                        "negotiationContext": {"round": 2, "performative": "PROPOSE"},
                        "https://example/Negotiation-T/v1": (
                            "## Feasibility Negotiation Result\nPropose\n\n"
                            "## Content\nShift to 20:00."
                        ),
                    },
                }
            },
        },
        {
            "dir": "in",
            "line": (
                'data: {"message": {"parts": [{"text": "accepted"}], "metadata": '
                '{"negotiationContext": {"round": 2, "performative": "ACCEPT"}}}}'
            ),
        },
    ]

    assert render_wire_trajectory(records) == (
        "R2 Client PROPOSE | ## Feasibility Negotiation Result Propose ## Content Shift to 20:00.\n"
        "R2 Server ACCEPT | accepted"
    )


def test_a_run_without_business_wire_frames_has_an_empty_wire_trajectory() -> None:
    assert render_wire_trajectory([{"dir": "in", "line": ": heartbeat"}]) == ""


def test_the_real_traceparent_is_read_from_the_wire_log(tmp_path: Path) -> None:
    run_id = "a" * 32
    wire = tmp_path / run_id / "wire.jsonl"
    wire.parent.mkdir(parents=True)
    wire.write_text(
        json.dumps(
            {
                "dir": "out",
                "headers": {"traceparent": "00-cb2cee4c3471176ec84cef5c15210afa-d4e7c0c1ac569729-01"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    value, source = read_traceparent(tmp_path, run_id, trace_id="b" * 32)

    assert value == "00-cb2cee4c3471176ec84cef5c15210afa-d4e7c0c1ac569729-01"
    assert source == "wire"


def test_a_missing_wire_log_reconstructs_a_labelled_traceparent(tmp_path: Path) -> None:
    trace_id = "b" * 32

    value, source = read_traceparent(tmp_path, "a" * 32, trace_id=trace_id)

    assert source == "reconstructed"
    assert value.startswith(f"00-{trace_id}-")
    assert value.endswith("-01")


def test_the_answer_carries_the_reference_workbook_keys() -> None:
    run = _answer_run()

    answer = build_answer(
        run,
        refs={"taskT": "## Operation Type\nCreate"},
        traceparent="00-" + "b" * 32 + "-" + "0" * 15 + "1-01",
    )

    assert set(answer) == {
        "code",
        "message",
        "finalResult",
        "negotiationSummary",
        "exchanges",
        "traceparent",
        "taskId",
    }
    assert answer["code"] == "SUCCESS"
    assert answer["taskId"] == "task-1"
    assert answer["negotiationSummary"] == {
        "missingItems": [],
        "filledParams": {},
        "negotiationRounds": 0,
    }
    exchanges = answer["exchanges"]
    assert isinstance(exchanges, list)
    assert [exchange["direction"] for exchange in exchanges] == ["OUT", "IN", "IN"]
    assert [exchange["messageType"] for exchange in exchanges] == [
        "Task-T",
        "RESULT",
        "TASK_STATE_SUBMITTED",
    ]
    # The outbound Task-T falls back to round 1; the submission status carries no round.
    assert [exchange["round"] for exchange in exchanges] == [1, 1, None]


def test_scores_and_labels_follow_the_reference_mapping() -> None:
    assert score_for("passed") == 10
    assert score_for("failed") == 0
    assert score_for("partial") is None
    assert score_for("error") == 0
    assert score_for("cancelled") == 0

    assert result_label("passed") == "通过"
    assert result_label("failed") == "失败"
    assert result_label("partial") == "待定"
    assert result_label("error") == "错误"
    assert result_label("cancelled") == "取消"


def _answer_run() -> RunState:
    """Return a finished run with one outbound Task-T and two inbound events."""
    run = RunState(run_id="a" * 32, scenario="night", mode="auto", transport="sse")
    run.trace_id = "b" * 32
    run.remote_task_id = "task-1"
    run.traces = [
        {
            "kind": "message_sent",
            "round": None,
            "startedAt": "2026-11-07T16:31:47.911Z",
            "durationMs": 120,
            "detail": {
                "performative": "TASK_T",
                "summary": "Task-T",
                "messageId": "message-1",
                "templateUri": "Task-T/network-layer/ran-energy-saving/v1",
                "taskT": "## Operation Type\nCreate",
            },
        }
    ]
    transcript = NegotiationTranscript(scenario="night", language="en-US", model_mode="live")
    transcript.events = [
        {
            "entryType": "event",
            "direction": "server->client",
            "kind": "message",
            "performative": None,
            "round": 1,
            "ts": "2026-11-07T16:31:47.912Z",
            "durationMs": 0,
            "content": "Task creation succeeded",
            "summary": "Task creation succeeded",
            "metadata": {},
        },
        {
            "entryType": "event",
            "direction": "server->client",
            "kind": "status",
            "state": "TASK_STATE_SUBMITTED",
            "round": None,
            "ts": "2026-11-07T16:31:47.913Z",
            "durationMs": 0,
            "content": None,
            "summary": "TASK_STATE_SUBMITTED",
            "metadata": {},
        },
    ]
    run.transcript = transcript
    run.result = ClientFlowResult(
        final_state="task_completed",
        task_id="task-1",
        report={"code": "SUCCESS", "message": "异步任务已创建", "finalResult": "Task creation succeeded"},
    )
    return run


def test_wire_frames_render_the_request_headers_and_body() -> None:
    frames = render_wire_frames(
        [
            {
                "ts": "2026-11-26T01:00:00.000Z",
                "dir": "out",
                "method": "POST",
                "url": "http://nms.test/a2a/json/message:send",
                "headers": {"Content-Type": "application/json", "Authorization": "***"},
                "body": {"message": {"messageId": "m-1"}},
            }
        ]
    )

    assert frames == [
        "[1] 2026-11-26T01:00:00.000Z OUT POST http://nms.test/a2a/json/message:send\n"
        'headers: {"Content-Type":"application/json","Authorization":"***"}\n'
        'body: {"message":{"messageId":"m-1"}}'
    ]


def test_wire_frames_mark_sse_lines_webhook_pushes_and_rejected_frames() -> None:
    frames = render_wire_frames(
        [
            {"ts": "t1", "dir": "in", "line": ""},
            {
                "ts": "t2",
                "dir": "in",
                "source": "webhook",
                "dropped": "duplicate",
                "body": {"ok": True},
            },
            {"ts": "t3", "type": "sdk_rejected", "message": "bad frame", "frame": {"a": 1}},
        ]
    )

    assert frames[0] == "[1] t1 IN SSE\nline: (空行)"
    assert frames[1] == '[2] t2 IN webhook (dropped: duplicate)\nbody: {"ok":true}'
    assert frames[2] == '[3] t3 ERROR sdk_rejected: bad frame\nframe: {"a":1}'
