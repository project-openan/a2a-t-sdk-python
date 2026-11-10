"""W3C trace-context propagation between the sample's A2A hops."""

from __future__ import annotations

import httpx
import pytest
from shared.trace_propagation import (
    CONVERSATION_ID_HEADER,
    DECISION_MODE_HEADER,
    DOWNSTREAM_TRACE_ID_HEADER,
    RUN_ID_HEADER,
    TRACEPARENT_HEADER,
    USER_ID_HEADER,
    WORKFLOW_TRACE_HEADER,
    TracePropagationMiddleware,
    current_decision_mode,
    current_downstream_trace_id,
    current_trace_id,
    current_workflow_trace,
    extract_trace_id,
    extract_traceparent,
    inject_outgoing,
    propagated_trace,
)

_TRACE_ID = "0123456789abcdef0123456789abcdef"


async def test_a_traceparent_is_built_from_the_active_trace_id() -> None:
    request = httpx.Request("POST", "http://agent.test/a2a/json/message:send")
    with propagated_trace(
        trace_id=_TRACE_ID,
        parent_span_id="fedcba9876543210",
        run_id="run-1",
        conversation_id="conv-1",
        user_id="user-1",
    ):
        await inject_outgoing(request)

    traceparent = request.headers[TRACEPARENT_HEADER]
    assert traceparent == f"00-{_TRACE_ID}-fedcba9876543210-01"
    assert request.headers[RUN_ID_HEADER] == "run-1"
    assert request.headers[CONVERSATION_ID_HEADER] == "conv-1"
    assert request.headers[USER_ID_HEADER] == "user-1"


def test_an_invalid_traceparent_is_not_propagated() -> None:
    assert extract_trace_id({TRACEPARENT_HEADER: "00-not-hex-span-01"}) == ""
    assert extract_trace_id({TRACEPARENT_HEADER: "00-" + "0" * 32 + "-" + "0" * 16 + "-01"}) == ""
    assert extract_trace_id({}) == ""


def test_extract_trace_id_is_case_insensitive() -> None:
    assert extract_trace_id({"TraceParent": f"00-{_TRACE_ID}-0123456789abcdef-01"}) == _TRACE_ID


async def test_a_traceparent_falls_back_to_the_run_id_without_langfuse() -> None:
    """Local A2A hops stay traceable even when no Langfuse exporter owns the trace."""
    request = httpx.Request("POST", "http://agent.test/a2a/json/message:send")
    with propagated_trace(run_id=_TRACE_ID):
        await inject_outgoing(request)

    traceparent = request.headers[TRACEPARENT_HEADER]
    trace_id, span_id = extract_traceparent(request.headers)
    assert traceparent.startswith(f"00-{_TRACE_ID}-")
    assert len(span_id) == 16
    assert trace_id == _TRACE_ID


async def test_a_non_hex_run_id_is_hashed_into_a_valid_trace_id() -> None:
    request = httpx.Request("POST", "http://agent.test/a2a/json/message:send")
    with propagated_trace(run_id="run-42"):
        await inject_outgoing(request)

    trace_id, _ = extract_traceparent(request.headers)
    assert len(trace_id) == 32
    assert trace_id != "0" * 32


async def test_a_downstream_trace_id_travels_on_its_own_header() -> None:
    """The vendor-facing correlation id is independent of the local A2A trace id."""
    request = httpx.Request("POST", "http://agent.test/a2a/json/message:send")
    with propagated_trace(run_id="run-1", downstream_trace_id=_TRACE_ID):
        await inject_outgoing(request)

    assert request.headers[DOWNSTREAM_TRACE_ID_HEADER] == _TRACE_ID
    # The local A2A hop still correlates on the active trace id / run id.
    assert request.headers[TRACEPARENT_HEADER].startswith("00-")

    request = httpx.Request("POST", "http://agent.test/a2a/json/message:send")
    with propagated_trace(run_id="run-1"):
        await inject_outgoing(request)

    assert DOWNSTREAM_TRACE_ID_HEADER not in request.headers


async def test_a_local_hop_prefers_the_shared_downstream_trace_id() -> None:
    """A retry's local A2A hop must carry the blocked attempt's trace id, not the new run id."""
    request = httpx.Request("POST", "http://agent.test/a2a/json/message:send")
    with propagated_trace(run_id="retry-run", downstream_trace_id=_TRACE_ID):
        await inject_outgoing(request)

    trace_id, span_id = extract_traceparent(request.headers)
    assert trace_id == _TRACE_ID
    assert len(span_id) == 16


async def test_a_workflow_trace_marks_its_local_hops() -> None:
    """The server must know not to overwrite the client-owned trace name/session."""
    request = httpx.Request("POST", "http://agent.test/a2a/json/message:send")
    with propagated_trace(run_id="run-1", workflow_trace=True):
        await inject_outgoing(request)

    assert request.headers[WORKFLOW_TRACE_HEADER] == "1"

    plain = httpx.Request("POST", "http://agent.test/a2a/json/message:send")
    with propagated_trace(run_id="run-1"):
        await inject_outgoing(plain)
    assert WORKFLOW_TRACE_HEADER not in plain.headers


async def test_the_middleware_binds_the_workflow_trace_flag() -> None:
    seen: list[bool] = []

    async def app(scope: dict[str, object], receive: object, send: object) -> None:
        del scope, receive, send
        seen.append(current_workflow_trace())

    middleware = TracePropagationMiddleware(app)
    await middleware(
        {
            "type": "http",
            "headers": [
                (RUN_ID_HEADER.encode(), b"run-1"),
                (WORKFLOW_TRACE_HEADER.encode(), b"1"),
            ],
        },
        object(),
        object(),
    )

    assert seen == [True]
    assert current_workflow_trace() is False


async def test_the_middleware_binds_the_incoming_downstream_trace_id() -> None:
    seen: list[str] = []

    async def app(scope: dict[str, object], receive: object, send: object) -> None:
        del scope, receive, send
        seen.append(current_downstream_trace_id())

    middleware = TracePropagationMiddleware(app)
    await middleware(
        {
            "type": "http",
            "headers": [
                (RUN_ID_HEADER.encode(), b"run-1"),
                (DOWNSTREAM_TRACE_ID_HEADER.encode(), _TRACE_ID.encode()),
            ],
        },
        object(),
        object(),
    )

    assert seen == [_TRACE_ID]
    assert current_downstream_trace_id() == ""


async def test_no_run_id_means_no_traceparent() -> None:
    request = httpx.Request("POST", "http://agent.test/a2a/json/message:send")
    with propagated_trace():
        await inject_outgoing(request)

    assert TRACEPARENT_HEADER not in request.headers


async def test_the_middleware_binds_the_incoming_context_for_the_request() -> None:
    seen: list[str] = []

    async def app(scope: dict[str, object], receive: object, send: object) -> None:
        del scope, receive, send
        seen.append(current_trace_id())

    middleware = TracePropagationMiddleware(app)
    scope = {
        "type": "http",
        "headers": [
            (TRACEPARENT_HEADER.encode(), f"00-{_TRACE_ID}-0123456789abcdef-01".encode()),
            (RUN_ID_HEADER.encode(), b"run-1"),
        ],
    }

    await middleware(scope, object(), object())

    assert seen == [_TRACE_ID]
    # The request-scoped values must not leak into the caller.
    assert current_trace_id() == ""


async def test_the_middleware_starts_a_bridge_span_with_the_run_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server trace is searchable by run id even when it cannot join the client trace."""
    import opentelemetry.propagate as propagate_module
    import opentelemetry.trace as trace_module

    captured: dict[str, object] = {}

    class _FakeSpan:
        def set_attribute(self, key: str, value: object) -> None:
            attributes = captured.setdefault("attributes", {})
            assert isinstance(attributes, dict)
            attributes[key] = value

        def end(self) -> None:
            captured["ended"] = True

    class _FakeTracer:
        def start_span(self, name: str, *, context: object = None) -> _FakeSpan:
            captured["name"] = name
            captured["context"] = context
            return _FakeSpan()

    monkeypatch.setattr(trace_module, "get_tracer", lambda name: _FakeTracer())
    monkeypatch.setattr(propagate_module, "extract", lambda carrier: "PARENT-CONTEXT")

    async def app(scope: dict[str, object], receive: object, send: object) -> None:
        del scope, receive, send

    middleware = TracePropagationMiddleware(app)
    await middleware(
        {
            "type": "http",
            "headers": [
                (TRACEPARENT_HEADER.encode(), f"00-{_TRACE_ID}-0123456789abcdef-01".encode()),
                (RUN_ID_HEADER.encode(), b"run-1"),
            ],
        },
        object(),
        object(),
    )

    assert captured["name"] == "a2at server request"
    assert captured["context"] == "PARENT-CONTEXT"
    attributes = captured["attributes"]
    assert isinstance(attributes, dict)
    assert attributes["langfuse.trace.metadata.runId"] == '"run-1"'
    assert captured["ended"] is True


async def test_a_traceparent_uses_the_current_otel_span_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server hop forwards its bridge span, not the span that called it (W3C semantics)."""
    import opentelemetry.trace as trace_module

    class _SpanContext:
        is_valid = True
        span_id = 0x0123456789ABCDEF

    class _Span:
        def get_span_context(self) -> _SpanContext:
            return _SpanContext()

    monkeypatch.setattr(trace_module, "get_current_span", lambda: _Span())
    request = httpx.Request("POST", "http://agent.test/a2a/json/message:send")
    with propagated_trace(
        trace_id=_TRACE_ID,
        parent_span_id="fedcba9876543210",
        run_id="run-1",
    ):
        await inject_outgoing(request)

    assert request.headers[TRACEPARENT_HEADER] == f"00-{_TRACE_ID}-0123456789abcdef-01"


def test_current_parent_span_id_normalizes_the_bound_parent() -> None:
    from shared.trace_propagation import current_parent_span_id

    with propagated_trace(parent_span_id="FEDCBA9876543210"):
        assert current_parent_span_id() == "fedcba9876543210"
    with propagated_trace(parent_span_id="not-hex"):
        assert current_parent_span_id() == ""


async def test_the_decision_mode_travels_on_its_own_header() -> None:
    """The OSS Agent must know whether the run that opened the Task has an operator."""
    request = httpx.Request("POST", "http://agent.test/a2a/json/message:send")
    with propagated_trace(run_id="run-1", decision_mode="confirm"):
        await inject_outgoing(request)

    assert request.headers[DECISION_MODE_HEADER] == "confirm"

    plain = httpx.Request("POST", "http://agent.test/a2a/json/message:send")
    with propagated_trace(run_id="run-1"):
        await inject_outgoing(plain)
    assert DECISION_MODE_HEADER not in plain.headers


async def test_the_middleware_binds_the_incoming_decision_mode() -> None:
    seen: list[str] = []

    async def app(scope: dict[str, object], receive: object, send: object) -> None:
        del scope, receive, send
        seen.append(current_decision_mode())

    middleware = TracePropagationMiddleware(app)
    await middleware(
        {
            "type": "http",
            "headers": [
                (RUN_ID_HEADER.encode(), b"run-1"),
                (DECISION_MODE_HEADER.encode(), b"confirm"),
            ],
        },
        object(),
        object(),
    )

    assert seen == ["confirm"]
    assert current_decision_mode() == ""
