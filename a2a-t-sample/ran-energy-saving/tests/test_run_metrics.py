"""Unit tests of the per-run metrics used by the protocol comparison."""

from __future__ import annotations

from demo.catalog import DemoRunCatalog
from protocol.client_flow import ClientFlowResult
from protocol.run_metrics import build_run_metrics
from protocol.transcript import NegotiationTranscript
from shared.run_constants import (
    FINAL_STATE_TASK_COMPLETED,
    LANGUAGE_ZH,
    PROTOCOL_A2A_T,
    PROTOCOL_PURE,
)
from shared.trace import Span

START = "2026-01-01T00:00:00.000Z"
END = "2026-01-01T00:00:10.000Z"


def _transcript() -> NegotiationTranscript:
    transcript = NegotiationTranscript(
        scenario="information",
        language=LANGUAGE_ZH,
        model_mode="live",
        mode="auto",
        transport="sse",
    )
    transcript.started_at = START
    transcript.finished_at = END
    return transcript


def _span(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "runId": "run-1",
        "spanId": "span-1",
        "kind": "llm",
        "side": "client",
        "stage": "task_t_extraction",
        "startedAt": START,
        "durationMs": 100.0,
    }
    base.update(overrides)
    return Span.from_dict(base).to_dict()


def test_tokens_are_normalized_across_producer_shapes() -> None:
    spans = [
        _span(
            spanId="client",
            tokens={"prompt": 10, "completion": 5, "total": 15, "estimated": False},
        ),
        _span(
            spanId="server",
            side="server",
            stage="planner",
            tokens={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        ),
        _span(spanId="tool", kind="tool", durationMs=50.0),
    ]
    metrics = build_run_metrics(
        run_id="run-1",
        conversation_id="conv-1",
        protocol=PROTOCOL_A2A_T,
        scenario="information",
        mode="auto",
        transport="sse",
        language=LANGUAGE_ZH,
        model_mode="live",
        model_label="test",
        status="finished",
        transcript=_transcript(),
        result=ClientFlowResult(final_state=FINAL_STATE_TASK_COMPLETED),
        spans=spans,
        expected=DemoRunCatalog().spec("information", language=LANGUAGE_ZH),
    )

    tokens = metrics["tokens"]
    assert isinstance(tokens, dict)
    assert tokens["available"] is True
    assert tokens["prompt"] == 110
    assert tokens["completion"] == 25
    assert tokens["total"] == 135
    assert tokens["llmCalls"] == 2
    assert tokens["missingCalls"] == 0
    by_side = tokens["bySide"]
    assert isinstance(by_side, dict)
    assert by_side["client"]["total"] == 15
    assert by_side["server"]["total"] == 120


def test_scripted_runs_report_tokens_as_unavailable() -> None:
    metrics = build_run_metrics(
        run_id="run-1",
        conversation_id=None,
        protocol=PROTOCOL_PURE,
        scenario="information",
        mode="auto",
        transport="sse",
        language=LANGUAGE_ZH,
        model_mode="offline",
        model_label="offline",
        status="finished",
        transcript=_transcript(),
        result=ClientFlowResult(final_state=FINAL_STATE_TASK_COMPLETED),
        spans=[_span(tokens={"scripted": True, "prompt": None, "completion": None})],
        expected=None,
    )
    tokens = metrics["tokens"]
    assert isinstance(tokens, dict)
    assert tokens["available"] is False
    assert tokens["scripted"] is True
    assert tokens["total"] is None


def test_latency_splits_model_tool_and_approval_wait() -> None:
    spans = [
        _span(spanId="llm", durationMs=100.0, tokens={"prompt": 1, "completion": 1}),
        _span(spanId="tool", kind="tool", durationMs=50.0),
        Span.from_dict(
            {
                "spanId": "requested",
                "kind": "approval",
                "startedAt": "2026-01-01T00:00:02.000Z",
                "detail": {"approvalState": "requested"},
            }
        ).to_dict(),
        Span.from_dict(
            {
                "spanId": "resolved",
                "kind": "approval",
                "startedAt": "2026-01-01T00:00:04.000Z",
                "detail": {"approvalState": "approved"},
            }
        ).to_dict(),
    ]
    metrics = build_run_metrics(
        run_id="run-1",
        conversation_id=None,
        protocol=PROTOCOL_PURE,
        scenario="information",
        mode="auto",
        transport="sse",
        language=LANGUAGE_ZH,
        model_mode="live",
        model_label="test",
        status="finished",
        transcript=_transcript(),
        result=ClientFlowResult(final_state=FINAL_STATE_TASK_COMPLETED),
        spans=spans,
        expected=None,
    )
    latency = metrics["latency"]
    assert isinstance(latency, dict)
    assert latency["totalMs"] == 10_000
    assert latency["modelMs"] == 100
    assert latency["toolMs"] == 50
    assert latency["approvalWaitMs"] == 2000
    assert latency["approvalCount"] == 1
    assert latency["timeToApprovalMs"] == 2000
    assert latency["otherMs"] == 7850


def test_a2a_t_accuracy_passes_against_the_scenario_truth() -> None:
    transcript = _transcript()
    transcript.events = [
        {"seq": 1, "performative": "PROPOSE", "round": 1},
    ]
    transcript.decisions = [
        {
            "seq": 2,
            "action": "accept",
            "performative": "ACCEPT",
            "round": 1,
            "filledParams": {
                "guaranteedThroughputTarget": (
                    "{00:00~17:30,10Mbps}、{17:30~23:00,20Mbps}、{23:00~23:59,10Mbps}"
                )
            },
        }
    ]
    metrics = build_run_metrics(
        run_id="run-1",
        conversation_id=None,
        protocol=PROTOCOL_A2A_T,
        scenario="information",
        mode="auto",
        transport="sse",
        language=LANGUAGE_ZH,
        model_mode="live",
        model_label="test",
        status="finished",
        transcript=transcript,
        result=ClientFlowResult(
            final_state=FINAL_STATE_TASK_COMPLETED,
            report={"cell_id": "cell-001", "target_rate": 30},
        ),
        spans=[],
        expected=DemoRunCatalog().spec("information", language=LANGUAGE_ZH),
    )
    accuracy = metrics["accuracy"]
    assert isinstance(accuracy, dict)
    assert accuracy["passed"] is True
    assert accuracy["score"] == 1.0
    assert accuracy["checks"]["finalState"]["passed"] is True
    assert accuracy["checks"]["sequence"]["passed"] is True
    assert accuracy["checks"]["rounds"]["passed"] is True
    assert accuracy["checks"]["target"]["passed"] is True
    assert accuracy["checks"]["report"]["passed"] is True


def test_pure_accuracy_uses_the_answer_and_rounds() -> None:
    result = ClientFlowResult(
        final_state=FINAL_STATE_TASK_COMPLETED,
        events=[
            {
                "kind": "status",
                "state": "TASK_STATE_WORKING",
                "metadata": {
                    "pure": {"kind": "negotiation", "phase": "target", "round": 2}
                },
            },
            {
                "kind": "status",
                "state": "TASK_STATE_WORKING",
                "text": "{00:00~17:30,10Mbps}、{17:30~23:00,20Mbps}、{23:00~23:59,10Mbps}",
                "metadata": {"pure": {"kind": "answer", "phase": "target", "round": 2}},
            },
        ],
        report={"cell_id": "cell-001", "target_rate": 30},
    )
    metrics = build_run_metrics(
        run_id="run-1",
        conversation_id=None,
        protocol=PROTOCOL_PURE,
        scenario="information",
        mode="auto",
        transport="sse",
        language=LANGUAGE_ZH,
        model_mode="live",
        model_label="test",
        status="finished",
        transcript=_transcript(),
        result=result,
        spans=[],
        expected=DemoRunCatalog().spec("information", language=LANGUAGE_ZH),
    )
    accuracy = metrics["accuracy"]
    assert isinstance(accuracy, dict)
    assert accuracy["checks"]["sequence"] is None
    assert accuracy["checks"]["rounds"]["actual"] == 2
    assert accuracy["checks"]["rounds"]["passed"] is False
    assert accuracy["checks"]["target"]["passed"] is True


def test_cached_tokens_are_summed_only_over_reporting_calls() -> None:
    """An unreported cache is ``None`` and must neither add to the sum nor to the rate."""
    spans = [
        _span(
            spanId="hit",
            tokens={"prompt": 793, "completion": 2, "total": 795, "cached": 768},
        ),
        _span(
            spanId="no-field",
            tokens={"prompt": 100, "completion": 1, "total": 101},
        ),
        _span(
            spanId="miss",
            side="server",
            stage="planner",
            tokens={"input_tokens": 200, "output_tokens": 10, "input_cached": 0},
        ),
    ]
    metrics = build_run_metrics(
        run_id="run-1",
        conversation_id=None,
        protocol=PROTOCOL_A2A_T,
        scenario="information",
        mode="auto",
        transport="sse",
        language=LANGUAGE_ZH,
        model_mode="live",
        model_label="test",
        status="finished",
        transcript=_transcript(),
        result=ClientFlowResult(final_state=FINAL_STATE_TASK_COMPLETED),
        spans=spans,
        expected=None,
    )

    tokens = metrics["tokens"]
    assert isinstance(tokens, dict)
    assert tokens["cached"] == 768
    assert tokens["cachedCalls"] == 2
    assert tokens["cacheHitRate"] == round(768 / 993, 4)
    by_side = tokens["bySide"]
    assert isinstance(by_side, dict)
    assert by_side["client"]["cachedCalls"] == 1
    assert by_side["client"]["cacheHitRate"] == round(768 / 793, 4)
    assert by_side["server"]["cached"] == 0
    assert by_side["server"]["cacheHitRate"] == 0.0


def test_cached_tokens_stay_none_when_no_call_reports_them() -> None:
    metrics = build_run_metrics(
        run_id="run-1",
        conversation_id=None,
        protocol=PROTOCOL_A2A_T,
        scenario="information",
        mode="auto",
        transport="sse",
        language=LANGUAGE_ZH,
        model_mode="live",
        model_label="test",
        status="finished",
        transcript=_transcript(),
        result=ClientFlowResult(final_state=FINAL_STATE_TASK_COMPLETED),
        spans=[_span(tokens={"prompt": 10, "completion": 2, "total": 12})],
        expected=None,
    )

    tokens = metrics["tokens"]
    assert isinstance(tokens, dict)
    assert tokens["cached"] is None
    assert tokens["cachedCalls"] == 0
    assert tokens["cacheHitRate"] is None
