from __future__ import annotations

from typing import Any

from a2a_t.observability.attributes import EVENT_LOG_KINDS, classify_event, unwrap_stream_response
from tests.observability.stubs import (
    FakeMessage,
    FakeStreamResponse,
    make_artifact_event,
    make_status_event,
)


class _RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


class _FakeTask:
    def __init__(self) -> None:
        from tests.observability.stubs import FakeStatus

        self.id = "T1"
        self.status = FakeStatus("TASK_STATE_WORKING")


def test_classify_status_event() -> None:
    info = classify_event(make_status_event("T1", "TASK_STATE_WORKING"))
    assert info.kind == "status"
    assert info.task_id == "T1"
    assert info.task_status == "working"
    assert info.is_terminal is False
    assert info.final is False


def test_classify_terminal_status_events() -> None:
    assert classify_event(make_status_event("T1", "TASK_STATE_COMPLETED")).kind == "completed"
    assert classify_event(make_status_event("T1", "TASK_STATE_CANCELED")).is_terminal is True
    assert classify_event(make_status_event("T1", "TASK_STATE_FAILED")).kind == "failed"


def test_event_log_kinds_derived_from_terminal_states() -> None:
    # Spec §3.7：事件期日志只覆盖 status 变体 + artifact；由 _TERMINAL_STATES 归一化派生。
    assert EVENT_LOG_KINDS == frozenset(
        {"status", "artifact", "completed", "canceled", "failed", "rejected"}
    )


def test_classify_final_non_terminal() -> None:
    info = classify_event(make_status_event("T1", "TASK_STATE_INPUT_REQUIRED", final=True))
    assert info.kind == "status"      # kind 按 state 判定
    assert info.final is True         # final 是权威流结束信号
    assert info.is_terminal is True   # final 即视为流终结


def test_classify_artifact_message_task() -> None:
    art = classify_event(make_artifact_event("T1"))
    assert art.kind == "artifact" and art.task_id == "T1"
    msg = classify_event(FakeMessage(metadata={"k": "v"}))
    assert msg.kind == "message" and msg.message_metadata == {"k": "v"}
    task = classify_event(_FakeTask())
    assert task.kind == "task" and task.task_status == "working"


def test_classify_artifact_carries_name() -> None:
    info = classify_event(make_artifact_event("T1"))
    assert info.artifact_name == "faultManagement.Incident"


def test_classify_unknown_shape() -> None:
    assert classify_event(object()).kind == "unknown"


def test_unwrap_stream_response() -> None:
    event = make_status_event("T1", "TASK_STATE_WORKING")
    resp = FakeStreamResponse("status_update", event)
    assert unwrap_stream_response(resp) is event
    assert unwrap_stream_response(event) is None


def test_a2at_attribute_extractor() -> None:
    from a2a_t.observability.attributes import a2at_attribute_extractor

    span = _RecordingSpan()
    message = FakeMessage(
        metadata={
            "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1": "prompt",
            "negotiationContext": {"id": "N1", "round": 1, "maxRounds": 5, "performative": "PROPOSE"},
        },
        task_id="T1",
    )
    a2at_attribute_extractor(span, (message,), {}, None, None)
    assert span.attributes["gen_ai.agent.a2at.extension.name"] == "Task-T"
    assert span.attributes["gen_ai.agent.a2at.task.id"] == "T1"
    assert span.attributes["gen_ai.agent.a2at.negotiation.id"] == "N1"

    span2 = _RecordingSpan()
    event = make_status_event("T2", "TASK_STATE_WORKING")
    a2at_attribute_extractor(span2, (), {}, FakeStreamResponse("status_update", event), None)
    assert span2.attributes["gen_ai.agent.a2at.streaming.event.kind"] == "status"
    assert span2.attributes["gen_ai.agent.a2at.task.status"] == "working"


def test_extractor_prefers_stream_response_over_default_message() -> None:
    from a2a_t.observability.attributes import a2at_attribute_extractor

    event = make_status_event("T5", "TASK_STATE_WORKING")

    class _DefaultInstance:
        """Real protobuf unset message-typed fields return a default instance, not None."""

        def __getattr__(self, name: str) -> Any:
            if name == "message":
                return FakeMessage(metadata={"x": "y"})  # default instance — must NOT win
            if name == "status_update":
                return event
            raise AttributeError(name)

    span = _RecordingSpan()
    wrapper = _DefaultInstance()
    object.__setattr__(wrapper, "HasField", lambda name: name == "status_update")
    a2at_attribute_extractor(span, (wrapper,), {}, None, None)
    assert span.attributes["gen_ai.agent.a2at.streaming.event.kind"] == "status"
    assert span.attributes["gen_ai.agent.a2at.task.id"] == "T5"
    assert span.attributes["gen_ai.agent.a2at.task.status"] == "working"
