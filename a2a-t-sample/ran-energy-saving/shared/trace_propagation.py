"""W3C trace-context propagation between the sample's A2A hops.

The client's Langfuse exporter owns one OTel trace per run. Local A2A servers run in other
processes; without a shared trace id their SDK spans (``SendMessage`` / ``CancelTask``) show up in
Langfuse as unrelated traces. This module:

* injects the client trace id as ``traceparent`` on every outgoing local A2A request, together with
  the sample's run/conversation/user correlation headers;
* extracts the incoming ``traceparent`` in the server middleware and attaches it to the OTel
  context, so the a2a-sdk spans created inside the request join the client trace;
* starts one lightweight bridge span carrying ``langfuse.trace.metadata.runId`` (the fallback when
  the server has its own trace instead of joining).

The vendor NMS keeps its own ``traceparent`` because it does not export to the same Langfuse
project. Its trace id defaults to the run id, but a run may carry a stable downstream trace id so a
blocked attempt and its retry reach the vendor under the same trace; only local a2a-sdk hops use the
OTel parts of this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Final

import httpx

_LOGGER = logging.getLogger("ran_energy_saving.trace_propagation")

#: Header carrying the W3C trace context.
TRACEPARENT_HEADER: Final[str] = "traceparent"

#: Sample-local correlation headers (not part of the A2A or W3C contract).
RUN_ID_HEADER: Final[str] = "x-a2at-run-id"
CONVERSATION_ID_HEADER: Final[str] = "x-a2at-conversation-id"
USER_ID_HEADER: Final[str] = "x-a2at-user-id"
#: Downstream correlation id carried across local A2A hops and used for the vendor NMS
#: ``traceparent``. Unlike the run id it can stay the same across a retry run.
DOWNSTREAM_TRACE_ID_HEADER: Final[str] = "x-a2at-downstream-trace-id"

#: Langfuse trace metadata attribute (same contract as :mod:`shared.langfuse_tracing`).
TRACE_NAME_ATTRIBUTE: Final[str] = "langfuse.trace.name"
TRACE_METADATA_PREFIX: Final[str] = "langfuse.trace.metadata."
OBSERVATION_TYPE_ATTRIBUTE: Final[str] = "langfuse.observation.type"
SESSION_ID_ATTRIBUTE: Final[str] = "session.id"
USER_ID_ATTRIBUTE: Final[str] = "user.id"

_TRACE_ID: ContextVar[str] = ContextVar("a2at_active_trace_id", default="")
_PARENT_SPAN_ID: ContextVar[str] = ContextVar("a2at_active_parent_span_id", default="")
_RUN_ID: ContextVar[str] = ContextVar("a2at_active_run_id", default="")
_CONVERSATION_ID: ContextVar[str] = ContextVar("a2at_active_conversation_id", default="")
_USER_ID: ContextVar[str] = ContextVar("a2at_active_user_id", default="")
_DOWNSTREAM_TRACE_ID: ContextVar[str] = ContextVar("a2at_downstream_trace_id", default="")


def _is_hex(value: str, length: int) -> bool:
    """Return whether ``value`` is exactly ``length`` lowercase hex digits."""
    return len(value) == length and all(char in "0123456789abcdef" for char in value.lower())


def _valid_trace_id(value: str) -> bool:
    """Return a normalized 32-hex trace id, or ``""`` when it cannot be propagated."""
    trace_id = value.strip().lower()
    if not _is_hex(trace_id, 32) or trace_id == "0" * 32:
        return ""
    return trace_id


def set_active_trace(
    *,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    run_id: str | None = None,
    conversation_id: str | None = None,
    user_id: str | None = None,
    downstream_trace_id: str | None = None,
) -> tuple[Token[str], Token[str], Token[str], Token[str], Token[str], Token[str]]:
    """Set the correlation values used by outgoing requests; return their reset tokens."""
    return (
        _TRACE_ID.set(trace_id or ""),
        _PARENT_SPAN_ID.set(parent_span_id or ""),
        _RUN_ID.set(run_id or ""),
        _CONVERSATION_ID.set(conversation_id or ""),
        _USER_ID.set(user_id or ""),
        _DOWNSTREAM_TRACE_ID.set(downstream_trace_id or ""),
    )


def reset_active_trace(
    tokens: tuple[Token[str], Token[str], Token[str], Token[str], Token[str], Token[str]],
) -> None:
    """Restore the correlation values saved by :func:`set_active_trace`."""
    trace_token, parent_token, run_token, conversation_token, user_token, downstream_token = tokens
    _TRACE_ID.reset(trace_token)
    _PARENT_SPAN_ID.reset(parent_token)
    _RUN_ID.reset(run_token)
    _CONVERSATION_ID.reset(conversation_token)
    _USER_ID.reset(user_token)
    _DOWNSTREAM_TRACE_ID.reset(downstream_token)


@contextmanager
def propagated_trace(
    *,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    run_id: str | None = None,
    conversation_id: str | None = None,
    user_id: str | None = None,
    downstream_trace_id: str | None = None,
) -> Iterator[None]:
    """Bind the correlation values for the duration of one run or request."""
    tokens = set_active_trace(
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        run_id=run_id,
        conversation_id=conversation_id,
        user_id=user_id,
        downstream_trace_id=downstream_trace_id,
    )
    try:
        yield
    finally:
        reset_active_trace(tokens)


def current_trace_id() -> str:
    """Return the active Langfuse trace id, ``""`` when the exporter is disabled."""
    return _valid_trace_id(_TRACE_ID.get())


def current_run_id() -> str:
    """Return the sample run id carried by the current request, ``""`` when absent."""
    return _RUN_ID.get().strip()


def current_downstream_trace_id() -> str:
    """Return the vendor-facing trace id bound to this run, ``""`` when absent."""
    return _valid_trace_id(_DOWNSTREAM_TRACE_ID.get())


def current_parent_span_id() -> str:
    """Return the active parent span id (16 hex), ``""`` when absent.

    The local A2A middleware binds the incoming ``traceparent`` parent id here. Span producers
    copy it into the Run Explorer envelope so a UI exporter can join the caller's trace.
    """
    parent = _PARENT_SPAN_ID.get().strip().lower()
    return parent if _is_hex(parent, 16) and parent != "0" * 16 else ""


def _current_otel_span_id() -> str:
    """Return the current OTel span id (16 hex), ``""`` without an active valid span."""
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if context is not None and context.is_valid:
            return f"{context.span_id:016x}"
    except Exception:  # noqa: BLE001 - propagation must never break the request
        _LOGGER.debug("current OTel span lookup failed", exc_info=True)
    return ""


def _fallback_trace_id() -> str:
    """Derive a stable 32-hex trace id when Langfuse owns no trace.

    The downstream id wins when one is bound: a correction retry reuses the blocked attempt's
    trace id there, so local A2A hops must carry the same id as the vendor-facing ``traceparent``
    instead of falling back to the retry's new run id. Without a downstream id, a run id is
    normally already 32 hex digits (``uuid4().hex``), so it can be used directly; any other id is
    hashed. The run-id guard stays first: a caller that has no run must not emit a traceparent.
    """
    run_id = current_run_id().lower()
    if not run_id:
        return ""
    downstream = current_downstream_trace_id()
    if downstream:
        return downstream
    if _is_hex(run_id, 32) and run_id != "0" * 32:
        return run_id
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]


def current_traceparent() -> str:
    """Return a W3C ``traceparent`` for one outgoing call, ``""`` without a run.

    The ``parent-id`` field must be the sender's current span, not the span that called it. The
    server middleware starts a bridge span for each inbound request, so an OSS hop forwards that
    span id; a client without a current span keeps the run root id (the server spans then hang
    directly under the run root). When the exporter is disabled, the run id (or a hash of it) is
    the trace id, matching the vendor-NMS path.
    """
    trace_id = current_trace_id() or _fallback_trace_id()
    if not trace_id:
        return ""
    parent = _PARENT_SPAN_ID.get().strip().lower()
    root_parent = parent if _is_hex(parent, 16) and parent != "0" * 16 else ""
    span_id = _current_otel_span_id() or root_parent or uuid.uuid4().hex[:16]
    return f"00-{trace_id}-{span_id}-01"


def extract_traceparent(headers: Mapping[str, str]) -> tuple[str, str]:
    """Return ``(trace_id, parent_span_id)`` from a W3C header, ``("", "")`` when invalid."""
    raw = ""
    for key, value in headers.items():
        if str(key).lower() == TRACEPARENT_HEADER:
            raw = str(value).strip()
            break
    parts = raw.split("-")
    if len(parts) < 3 or parts[0] not in ("00", "01"):
        return "", ""
    trace_id = _valid_trace_id(parts[1])
    span_id = parts[2].strip().lower()
    if not trace_id or not _is_hex(span_id, 16):
        return "", ""
    return trace_id, span_id


def extract_trace_id(headers: Mapping[str, str]) -> str:
    """Extract the trace id from a W3C ``traceparent`` header (case-insensitive)."""
    return extract_traceparent(headers)[0]


def annotate_run_id(
    run_id: str,
    *,
    conversation_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Write the sample run id onto the current server span as Langfuse trace metadata.

    The server already reads ``sampleRunId`` from the message metadata; doing it here covers
    clients that do not send the sample correlation headers, and keeps the server trace searchable
    by run even when it cannot join the client trace.
    """
    if not run_id:
        return
    try:
        from opentelemetry import trace
    except ImportError:
        return
    try:
        span = trace.get_current_span()
        if span is None:
            return
        span.set_attribute(f"{TRACE_METADATA_PREFIX}runId", json.dumps(run_id))
        span.set_attribute(TRACE_NAME_ATTRIBUTE, json.dumps(f"server · {run_id[:8]}"))
        if conversation_id:
            span.set_attribute(SESSION_ID_ATTRIBUTE, conversation_id)
            span.set_attribute(
                f"{TRACE_METADATA_PREFIX}conversationId", json.dumps(conversation_id)
            )
        if user_id:
            span.set_attribute(USER_ID_ATTRIBUTE, user_id)
    except Exception:  # noqa: BLE001 - tracing must not break the request
        _LOGGER.debug("run id annotation failed", exc_info=True)


async def inject_outgoing(request: httpx.Request) -> None:
    """An ``httpx`` request hook adding the active trace context to a local A2A call."""
    try:
        traceparent = current_traceparent()
        if traceparent:
            request.headers[TRACEPARENT_HEADER] = traceparent
        run_id = _RUN_ID.get().strip()
        if run_id:
            request.headers[RUN_ID_HEADER] = run_id
        conversation_id = _CONVERSATION_ID.get().strip()
        if conversation_id:
            request.headers[CONVERSATION_ID_HEADER] = conversation_id
        user_id = _USER_ID.get().strip()
        if user_id:
            request.headers[USER_ID_HEADER] = user_id
        downstream_trace_id = current_downstream_trace_id()
        if downstream_trace_id:
            request.headers[DOWNSTREAM_TRACE_ID_HEADER] = downstream_trace_id
    except Exception:  # noqa: BLE001 - propagation never breaks the call
        _LOGGER.debug("traceparent injection failed", exc_info=True)


def _lower_headers(raw_headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    """Decode one ASGI header list into a lowercase mapping."""
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in raw_headers
    }


class TracePropagationMiddleware:
    """Attach the incoming W3C trace context to one local A2A server request.

    The a2a-python server routes are decorated with ``@trace_class``; those spans use the current
    OTel context. Without this middleware they start a new trace. Attaching the extracted context
    makes them children of the client's Langfuse trace, and the bridge span carries the sample run
    id as Langfuse trace metadata when the server cannot join.
    """

    def __init__(self, app: Any) -> None:
        """Wrap one ASGI application."""
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Propagate the request context for HTTP scopes and pass everything else through."""
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = _lower_headers(scope.get("headers") or [])
        trace_id, parent_span_id = extract_traceparent(headers)
        tokens = set_active_trace(
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            run_id=headers.get(RUN_ID_HEADER, ""),
            conversation_id=headers.get(CONVERSATION_ID_HEADER, ""),
            user_id=headers.get(USER_ID_HEADER, ""),
            downstream_trace_id=headers.get(DOWNSTREAM_TRACE_ID_HEADER, ""),
        )
        otel_tokens = _attach_otel_context(headers)
        try:
            await self.app(scope, receive, send)
        finally:
            _detach_otel_context(otel_tokens)
            reset_active_trace(tokens)


def _attach_otel_context(headers: Mapping[str, str]) -> tuple[Any, Any, Any] | None:
    """Extract ``traceparent``, start the bridge span and make it current.

    Returns the tokens needed by :func:`_detach_otel_context`, or ``None`` when OpenTelemetry is
    not installed. A server without a configured provider silently gets non-recording spans.
    """
    try:
        from opentelemetry import context as otel_context
        from opentelemetry import propagate, trace
        from opentelemetry.trace import set_span_in_context
    except ImportError:
        return None
    try:
        parent = propagate.extract(dict(headers))
        run_id = headers.get(RUN_ID_HEADER, "").strip()
        if not run_id:
            # No sample correlation header (health check, discovery, an external caller): just
            # make the incoming traceparent current so the SDK spans still join that trace.
            if not headers.get(TRACEPARENT_HEADER):
                return None
            return (otel_context, otel_context.attach(parent), None)
        tracer = trace.get_tracer("a2at.propagation")
        span = tracer.start_span("a2at server request", context=parent)
        attributes: dict[str, Any] = {OBSERVATION_TYPE_ATTRIBUTE: "span"}
        attributes[f"{TRACE_METADATA_PREFIX}runId"] = json.dumps(run_id)
        attributes[TRACE_NAME_ATTRIBUTE] = json.dumps(f"server · {run_id[:8]}")
        conversation_id = headers.get(CONVERSATION_ID_HEADER, "").strip()
        if conversation_id:
            attributes[SESSION_ID_ATTRIBUTE] = conversation_id
            attributes[f"{TRACE_METADATA_PREFIX}conversationId"] = json.dumps(conversation_id)
        user_id = headers.get(USER_ID_HEADER, "").strip()
        if user_id:
            attributes[USER_ID_ATTRIBUTE] = user_id
        for key, value in attributes.items():
            span.set_attribute(key, value)
        span_token = otel_context.attach(set_span_in_context(span))
        return (otel_context, span_token, span)
    except Exception:  # noqa: BLE001 - tracing must not break the request
        _LOGGER.debug("OTel context attach failed", exc_info=True)
        return None


def _detach_otel_context(tokens: tuple[Any, Any, Any] | None) -> None:
    """Detach and end the bridge span created by :func:`_attach_otel_context`."""
    if tokens is None:
        return
    otel_context, span_token, span = tokens
    try:
        otel_context.detach(span_token)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("OTel context detach failed", exc_info=True)
    if span is not None:
        try:
            span.end()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("OTel bridge span end failed", exc_info=True)


__all__ = [
    "CONVERSATION_ID_HEADER",
    "DOWNSTREAM_TRACE_ID_HEADER",
    "RUN_ID_HEADER",
    "TRACEPARENT_HEADER",
    "TracePropagationMiddleware",
    "USER_ID_HEADER",
    "annotate_run_id",
    "current_downstream_trace_id",
    "current_parent_span_id",
    "current_run_id",
    "current_trace_id",
    "current_traceparent",
    "extract_trace_id",
    "extract_traceparent",
    "inject_outgoing",
    "propagated_trace",
    "reset_active_trace",
    "set_active_trace",
]
