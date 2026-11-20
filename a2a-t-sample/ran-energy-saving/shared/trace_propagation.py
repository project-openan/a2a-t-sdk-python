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
from collections.abc import Callable, Generator, Mapping
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
#: Marks a hop whose trace is owned by a client workflow root. The server then puts run identity
#: in observation metadata instead of overwriting the trace name/session owned by the anchor.
WORKFLOW_TRACE_HEADER: Final[str] = "x-a2at-workflow-trace"

#: Decision mode of the run that opened the outer A2A Task (``auto`` / ``confirm`` / ``manual`` /
#: ``scripted``). A local hop carries it so the OSS Agent knows whether a downstream question may
#: be handed to a human. It is sample-local correlation, like the run id: the A2A message and Task
#: contract are unchanged.
DECISION_MODE_HEADER: Final[str] = "x-a2at-decision-mode"

#: Langfuse trace metadata attribute (same contract as :mod:`shared.langfuse_tracing`).
TRACE_NAME_ATTRIBUTE: Final[str] = "langfuse.trace.name"
TRACE_METADATA_PREFIX: Final[str] = "langfuse.trace.metadata."
OBSERVATION_TYPE_ATTRIBUTE: Final[str] = "langfuse.observation.type"
OBSERVATION_METADATA_PREFIX: Final[str] = "langfuse.observation.metadata."
SESSION_ID_ATTRIBUTE: Final[str] = "session.id"
USER_ID_ATTRIBUTE: Final[str] = "user.id"

_TRACE_ID: ContextVar[str] = ContextVar("a2at_active_trace_id", default="")
_PARENT_SPAN_ID: ContextVar[str] = ContextVar("a2at_active_parent_span_id", default="")
_RUN_ID: ContextVar[str] = ContextVar("a2at_active_run_id", default="")
_CONVERSATION_ID: ContextVar[str] = ContextVar("a2at_active_conversation_id", default="")
_USER_ID: ContextVar[str] = ContextVar("a2at_active_user_id", default="")
_DOWNSTREAM_TRACE_ID: ContextVar[str] = ContextVar("a2at_downstream_trace_id", default="")
_WORKFLOW_TRACE: ContextVar[str] = ContextVar("a2at_workflow_trace", default="")
_DECISION_MODE: ContextVar[str] = ContextVar("a2at_decision_mode", default="")

#: Starts one real OTel span for an outbound vendor request. The run binds its Langfuse exporter
#: here so the W3C ``span-id`` names a span that exists in our trace instead of a generated id.
_VendorSpanStarter = Callable[[str, dict[str, object]], Any]
_VENDOR_SPAN_STARTER: ContextVar[_VendorSpanStarter | None] = ContextVar(
    "a2at_vendor_span_starter", default=None
)


def _is_hex(value: str, length: int) -> bool:
    """Return whether ``value`` is exactly ``length`` lowercase hex digits."""
    return len(value) == length and all(char in "0123456789abcdef" for char in value.lower())


def _valid_trace_id(value: str) -> str:
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
    workflow_trace: bool = False,
    decision_mode: str | None = None,
) -> tuple[
    Token[str], Token[str], Token[str], Token[str], Token[str], Token[str], Token[str], Token[str]
]:
    """Set the correlation values used by outgoing requests; return their reset tokens."""
    return (
        _TRACE_ID.set(trace_id or ""),
        _PARENT_SPAN_ID.set(parent_span_id or ""),
        _RUN_ID.set(run_id or ""),
        _CONVERSATION_ID.set(conversation_id or ""),
        _USER_ID.set(user_id or ""),
        _DOWNSTREAM_TRACE_ID.set(downstream_trace_id or ""),
        _WORKFLOW_TRACE.set("1" if workflow_trace else ""),
        _DECISION_MODE.set(decision_mode or ""),
    )


def reset_active_trace(
    tokens: tuple[
        Token[str], Token[str], Token[str], Token[str], Token[str], Token[str], Token[str], Token[str]
    ],
) -> None:
    """Restore the correlation values saved by :func:`set_active_trace`."""
    (
        trace_token,
        parent_token,
        run_token,
        conversation_token,
        user_token,
        downstream_token,
        workflow_token,
        decision_mode_token,
    ) = tokens
    _TRACE_ID.reset(trace_token)
    _PARENT_SPAN_ID.reset(parent_token)
    _RUN_ID.reset(run_token)
    _CONVERSATION_ID.reset(conversation_token)
    _USER_ID.reset(user_token)
    _DOWNSTREAM_TRACE_ID.reset(downstream_token)
    _WORKFLOW_TRACE.reset(workflow_token)
    _DECISION_MODE.reset(decision_mode_token)


def current_decision_mode() -> str:
    """Return the decision mode of the inbound run, empty when the caller did not declare one."""
    return _DECISION_MODE.get().strip().lower()


@contextmanager
def propagated_trace(
    *,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    run_id: str | None = None,
    conversation_id: str | None = None,
    user_id: str | None = None,
    downstream_trace_id: str | None = None,
    workflow_trace: bool = False,
    decision_mode: str | None = None,
) -> Generator[None, None, None]:
    """Bind the correlation values for the duration of one run or request."""
    tokens = set_active_trace(
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        run_id=run_id,
        conversation_id=conversation_id,
        user_id=user_id,
        downstream_trace_id=downstream_trace_id,
        workflow_trace=workflow_trace,
        decision_mode=decision_mode,
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


def current_workflow_trace() -> bool:
    """Return whether the current hop belongs to a client-owned workflow trace."""
    return _WORKFLOW_TRACE.get() == "1"


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


def span_traceparent(span: Any | None) -> str:
    """Return the W3C ``traceparent`` of one OTel span, ``""`` without a valid context.

    The vendor NMS joins its backend trace to the *span that sent the request*. A generated id
    names nothing, so the header must carry this span's own trace/span ids.
    """
    if span is None:
        return ""
    try:
        context = span.get_span_context()
    except Exception:  # noqa: BLE001 - propagation must never break the call
        _LOGGER.debug("span context lookup failed", exc_info=True)
        return ""
    if context is None or not context.is_valid:
        return ""
    return f"00-{context.trace_id:032x}-{context.span_id:016x}-01"


def set_vendor_span_starter(starter: _VendorSpanStarter | None) -> Token[Any]:
    """Bind the span factory used by :func:`vendor_request_span`; return its reset token."""
    return _VENDOR_SPAN_STARTER.set(starter)


def reset_vendor_span_starter(token: Token[Any]) -> None:
    """Restore the span factory saved by :func:`set_vendor_span_starter`."""
    _VENDOR_SPAN_STARTER.reset(token)


@contextmanager
def vendor_request_span(
    name: str,
    *,
    attributes: Mapping[str, object] | None = None,
) -> Generator[Any | None, None, None]:
    """Start one exported span for an outbound vendor request and end it on exit.

    Yields ``None`` when the run has no exporter: the caller then falls back to the bound run
    root span, or to the legacy generated id when there is no trace context at all.
    """
    starter = _VENDOR_SPAN_STARTER.get()
    if starter is None:
        yield None
        return
    span = starter(name, dict(attributes or {}))
    try:
        yield span
    finally:
        try:
            span.end()
        except Exception:  # noqa: BLE001 - observability must not change the call result
            _LOGGER.debug("vendor span end failed", exc_info=True)


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
        if current_workflow_trace():
            # The client's workflow anchor owns the trace name/session; a per-run value written
            # here would overwrite it with the retry's new run/conversation.
            span.set_attribute(f"{OBSERVATION_METADATA_PREFIX}runId", json.dumps(run_id))
            if conversation_id:
                span.set_attribute(
                    f"{OBSERVATION_METADATA_PREFIX}conversationId", json.dumps(conversation_id)
                )
            return
        span.set_attribute(f"{TRACE_METADATA_PREFIX}runId", json.dumps(run_id))
        span.set_attribute(TRACE_NAME_ATTRIBUTE, json.dumps(f"server · {run_id[:8]}"))
        if conversation_id:
            span.set_attribute(SESSION_ID_ATTRIBUTE, conversation_id)
            span.set_attribute(f"{TRACE_METADATA_PREFIX}conversationId", json.dumps(conversation_id))
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
        if current_workflow_trace():
            request.headers[WORKFLOW_TRACE_HEADER] = "1"
        decision_mode = current_decision_mode()
        if decision_mode:
            request.headers[DECISION_MODE_HEADER] = decision_mode
    except Exception:  # noqa: BLE001 - propagation never breaks the call
        _LOGGER.debug("traceparent injection failed", exc_info=True)


def _lower_headers(raw_headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    """Decode one ASGI header list into a lowercase mapping."""
    return {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in raw_headers}


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
            workflow_trace=headers.get(WORKFLOW_TRACE_HEADER, "").strip() == "1",
            decision_mode=headers.get(DECISION_MODE_HEADER, ""),
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
        workflow_trace = headers.get(WORKFLOW_TRACE_HEADER, "").strip() == "1"
        conversation_id = headers.get(CONVERSATION_ID_HEADER, "").strip()
        user_id = headers.get(USER_ID_HEADER, "").strip()
        if workflow_trace:
            # The shared workflow trace is owned by the client's anchor; keep this hop's run
            # identity at observation level and never rewrite the trace name/session.
            attributes[f"{OBSERVATION_METADATA_PREFIX}runId"] = json.dumps(run_id)
            if conversation_id:
                attributes[f"{OBSERVATION_METADATA_PREFIX}conversationId"] = json.dumps(
                    conversation_id
                )
        else:
            attributes[f"{TRACE_METADATA_PREFIX}runId"] = json.dumps(run_id)
            attributes[TRACE_NAME_ATTRIBUTE] = json.dumps(f"server · {run_id[:8]}")
            if conversation_id:
                attributes[SESSION_ID_ATTRIBUTE] = conversation_id
                attributes[f"{TRACE_METADATA_PREFIX}conversationId"] = json.dumps(conversation_id)
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
    "WORKFLOW_TRACE_HEADER",
    "annotate_run_id",
    "current_downstream_trace_id",
    "current_parent_span_id",
    "current_run_id",
    "current_trace_id",
    "current_workflow_trace",
    "current_traceparent",
    "extract_trace_id",
    "extract_traceparent",
    "inject_outgoing",
    "propagated_trace",
    "reset_active_trace",
    "reset_vendor_span_starter",
    "set_active_trace",
    "set_vendor_span_starter",
    "span_traceparent",
    "vendor_request_span",
]
