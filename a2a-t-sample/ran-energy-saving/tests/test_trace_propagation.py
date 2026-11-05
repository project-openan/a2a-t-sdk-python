"""W3C trace-context propagation between the sample's A2A hops."""

from __future__ import annotations

import httpx
import pytest
from shared.trace_propagation import (
    CONVERSATION_ID_HEADER,
    RUN_ID_HEADER,
    TRACEPARENT_HEADER,
    USER_ID_HEADER,
    TracePropagationMiddleware,
    current_trace_id,
    extract_trace_id,
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
    assert (
        extract_trace_id(
            {"TraceParent": f"00-{_TRACE_ID}-0123456789abcdef-01"}
        )
        == _TRACE_ID
    )


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
