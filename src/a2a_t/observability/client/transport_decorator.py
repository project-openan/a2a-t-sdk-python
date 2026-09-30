"""A2ATClientTransportDecorator: structural ClientTransport wrapper (spec 3.1/3.2, a2a-java parity).

Wraps every ``ClientTransport`` method with a2a-java-parity client spans:

- entry spans (``SendMessage`` / ``SendStreamingMessage`` / ``GetTask`` / ...) of
  CLIENT kind, ending when the method returns (sync) or when the stream ends
  (streaming, try/finally);
- per-event link spans ``SendStreamingMessage-event`` (LINK to the entry span);
- parent negotiation spans ``*-negotiation`` (PARENT of the entry span) whenever a
  Message carrying the Negotiation-T extension URI arrives;
- the stream error span ``SendStreamingMessage-error`` (LINK to the entry span);
- traceparent injection into ``context.service_parameters``;
- L1/L3 duration histograms (``gen_ai.client.operation.duration`` +
  ``a2at.task.request.duration``) recorded at entry-span end.

Everything is structural (no a2a import) and guarded: observability failures are
swallowed with a WARNING on logger ``a2at.observability`` and never break the
business flow. Signal semantics follow spec §8.1: the decorator is a full
pass-through only when OTel is unavailable or the master switch is off; with
``A2AT_TRACE_ENABLED=false`` it stays active — no spans are produced, but
metrics and logs keep working.

Regex attributes (``task.type`` / ``notification.topic`` /
``authorization.policy.operation.type``): extracted from the request message
metadata through the config ``invoke_*`` providers, falling back to the built-in
DEFAULT_*_REGEX patterns when the config regex is unset (per the plan note
"DEFAULT_*_REGEX consumed in Task 9/10"); an empty-string regex opt-outs (the
matched value is empty and the attribute is skipped).

This module also hosts the shared ``LLMUsageStash`` contextvar bridge module
import (``a2a_t.observability.llm_stash``): the transport decorator reads + clears
the stash when creating entry spans; the LLM client decorator sets it.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import AsyncIterator, MutableMapping
from contextlib import nullcontext
from typing import Any

from a2a_t.observability import _otel_compat
from a2a_t.observability.attributes import (
    ATTR_A2A_CONTEXT_ID,
    ATTR_A2A_EXTENSIONS,
    ATTR_A2A_MESSAGE_ID,
    ATTR_A2A_OPERATION_NAME,
    ATTR_A2A_PARTS_NUMBER,
    ATTR_A2A_PROTOCOL,
    ATTR_A2A_REQUEST,
    ATTR_A2A_RESPONSE,
    ATTR_A2A_ROLE,
    ATTR_A2A_TASK_ID,
    ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE,
    ATTR_EXTENSION_NAME,
    ATTR_GEN_AI_CONVERSATION_ID,
    ATTR_GEN_AI_OPERATION_NAME,
    ATTR_GEN_AI_REQUEST_MODEL,
    ATTR_GEN_AI_USAGE_INPUT_TOKENS,
    ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
    ATTR_NEGOTIATION_ID,
    ATTR_NEGOTIATION_PERFORMATIVE,
    ATTR_NEGOTIATION_ROUND,
    ATTR_NOTIFICATION_TOPIC,
    ATTR_STREAMING_EVENT_KIND,
    ATTR_TASK_ID,
    ATTR_TASK_STATUS,
    ATTR_TASK_TYPE,
    DEFAULT_AUTHORIZATION_POLICY_OPERATION_TYPE_REGEX,
    DEFAULT_NOTIFICATION_TOPIC_REGEX,
    DEFAULT_TASK_TYPE_REGEX,
    EVENT_LOG_KINDS,
    EventInfo,
    classify_event,
    extension_name_from_uri,
    extract_negotiation_attributes,
    extract_request_attributes,
    normalize_metadata,
    unwrap_stream_response,
)
from a2a_t.observability.config import A2ATObservabilityConfig
from a2a_t.observability.llm_stash import clear_llm_usage, get_llm_usage
from a2a_t.observability.logs import log_event
from a2a_t.observability.payload import extract_payload
from a2a_t.observability.propagation import inject_traceparent
from a2a_t.observability.setup import ensure_otel_configured

logger = logging.getLogger("a2at.observability")

_SEND_MESSAGE = "SendMessage"
_SEND_STREAMING_MESSAGE = "SendStreamingMessage"
_SUBSCRIBE = "SubscribeToTask"
_NEGOTIATION_SUFFIX = "-negotiation"
_EVENT_SPAN_NAME = "SendStreamingMessage-event"
_ERROR_SPAN_NAME = "SendStreamingMessage-error"
_NEGOTIATION_EXTENSION = "Negotiation-T"

_METRIC_GEN_AI_DURATION = "gen_ai.client.operation.duration"
_METRIC_TASK_DURATION = "a2at.task.request.duration"
_METRIC_INSTRUMENTS: dict[str, Any] = {}

#: Non-send ClientTransport methods (span name per spec 3.1). ``send_message`` /
#: ``send_message_streaming`` / ``close`` are defined explicitly; ``list_tasks`` and
#: ``subscribe`` are wrapped for a2a-java parity even though the a2a-python ABC may
#: not declare them.
_METHOD_SPAN_NAMES: dict[str, str] = {
    "get_task": "GetTask",
    "list_tasks": "ListTasks",
    "cancel_task": "CancelTask",
    "subscribe": "SubscribeToTask",
    "create_task_push_notification_config": "CreateTaskPushNotificationConfig",
    "get_task_push_notification_config": "GetTaskPushNotificationConfig",
    "list_task_push_notification_configs": "ListTaskPushNotificationConfigs",
    "delete_task_push_notification_config": "DeleteTaskPushNotificationConfig",
    "get_extended_agent_card": "GetExtendedAgentCard",
}


def _safe_getattr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name)
    except Exception:  # noqa: BLE001 - structural access must never raise
        return None


def _extension_from_metadata(metadata: dict[str, Any]) -> str | None:
    for key in metadata:
        name = extension_name_from_uri(str(key))
        if name:
            return name
    return None


def _is_negotiation_message(message: Any) -> bool:
    metadata = normalize_metadata(_safe_getattr(message, "metadata"))
    return any(extension_name_from_uri(str(key)) == _NEGOTIATION_EXTENSION for key in metadata)


def _unwrap(event: Any) -> Any:
    unwrapped = unwrap_stream_response(event)
    return event if unwrapped is None else unwrapped


def _context_with_span(span: Any) -> Any | None:
    if span is None or not _otel_compat.is_enabled():
        return None
    try:
        return _otel_compat.otel_trace.set_span_in_context(span)
    except Exception:  # noqa: BLE001
        return None


def _span_context_of(span: Any) -> Any | None:
    if span is None:
        return None
    try:
        return span.get_span_context()
    except Exception:  # noqa: BLE001
        return None


def _links_for(span_context: Any) -> list[Any] | None:
    if span_context is None or not _otel_compat.is_enabled():
        return None
    try:
        from opentelemetry.trace import Link

        return [Link(span_context)]
    except Exception:  # noqa: BLE001
        logger.warning("a2at: failed to build span link", exc_info=True)
        return None


def _metric_histogram(name: str, unit: str, description: str) -> Any | None:
    """Lazily create and cache one histogram per metric name (v2 metrics.py pattern)."""
    instrument = _METRIC_INSTRUMENTS.get(name)
    if instrument is None:
        meter: Any = _otel_compat.get_meter()
        instrument = meter.create_histogram(name, unit=unit, description=description)
        _METRIC_INSTRUMENTS[name] = instrument
    return instrument


def _role_value(message: Any, role: Any) -> str:
    """Resolve the wire role to its enum name (real proto enums surface as ints)."""
    value = getattr(role, "value", role)
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        try:
            enum_type = message.DESCRIPTOR.fields_by_name["role"].enum_type
            name = str(enum_type.values_by_number[value].name)
            if name:
                return name
        except Exception:  # noqa: BLE001 - structural access must never raise
            logger.debug("a2at: role enum name resolution failed", exc_info=True)
    return str(value)


class A2ATClientTransportDecorator:
    """Structural ClientTransport wrapper creating a2a-java-parity client spans."""

    _inner: Any

    def __init__(self, inner: Any, *, config: A2ATObservabilityConfig | None = None) -> None:
        while isinstance(inner, A2ATClientTransportDecorator):
            # Double decoration must not double-span (factory-guard parity): keep one layer.
            inner = inner._inner
        self._inner = inner
        self._config = config or A2ATObservabilityConfig()
        # Regex config resolution: unset regex fields fall back to the built-in
        # DEFAULT_*_REGEX patterns (see module docstring); empty string opt-outs.
        self._regex_config = dataclasses.replace(
            self._config,
            task_type_regex=(
                self._config.task_type_regex
                if self._config.task_type_regex is not None
                else DEFAULT_TASK_TYPE_REGEX
            ),
            notification_topic_regex=(
                self._config.notification_topic_regex
                if self._config.notification_topic_regex is not None
                else DEFAULT_NOTIFICATION_TOPIC_REGEX
            ),
            authorization_policy_operation_type_regex=(
                self._config.authorization_policy_operation_type_regex
                if self._config.authorization_policy_operation_type_regex is not None
                else DEFAULT_AUTHORIZATION_POLICY_OPERATION_TYPE_REGEX
            ),
        )

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------

    def _decorator_active(self) -> bool:
        """Master gate (spec §8.1): full pass-through only when OTel is unavailable/off.

        Trace-off (``A2AT_TRACE_ENABLED=false``) is NOT a pass-through: the
        decorator stays active, skips span creation only, and metrics/logs keep
        working (the span gate lives in ``_start_entry_span``).
        """
        if not _otel_compat.is_enabled():
            return False
        return bool(self._config.enabled)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name == "_inner":
            raise AttributeError(name)
        inner = self.__dict__.get("_inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)

    def _start_entry_span(self, name: str, request: Any, context: Any) -> Any | None:
        if not _otel_compat.is_trace_enabled() or not self._config.trace_enabled:
            # Spec §8.1: trace off → no span; metrics/logs stay active. The LLM
            # stash is span-bound (channel 1) — drop it so it cannot leak into a
            # later request's entry span.
            clear_llm_usage()
            return None
        try:
            tracer: Any = _otel_compat.get_tracer()
            span = tracer.start_span(name, kind=_otel_compat.SpanKind.CLIENT)
            if span is None:
                return None
            self._set_request_attributes(span, request, name)
            self._inject_traceparent(context, span)
            return span
        except Exception:  # noqa: BLE001 - observability must never break the flow
            logger.warning("a2at: failed to start client entry span %s", name, exc_info=True)
            return None

    def _set_request_attributes(self, span: Any, request: Any, method: str) -> None:
        attrs = extract_request_attributes(request, method=method)
        for key, value in attrs.items():
            span.set_attribute(key, value)
        message = _safe_getattr(request, "message")
        if message is not None:
            self._set_message_double_attributes(span, message)
        span.set_attribute(ATTR_A2A_OPERATION_NAME, method)
        self._set_protocol_attribute(span)
        self._set_usage_attributes(span)
        self._set_regex_attributes(span, message)

    def _set_message_double_attributes(self, span: Any, message: Any) -> None:
        context_id = _safe_getattr(message, "context_id")
        if isinstance(context_id, str) and context_id:
            span.set_attribute(ATTR_A2A_CONTEXT_ID, context_id)
        task_id = _safe_getattr(message, "task_id")
        if isinstance(task_id, str) and task_id:
            span.set_attribute(ATTR_A2A_TASK_ID, task_id)
        message_id = _safe_getattr(message, "message_id")
        if isinstance(message_id, str) and message_id:
            span.set_attribute(ATTR_A2A_MESSAGE_ID, message_id)
        role = _safe_getattr(message, "role")
        if role is not None:
            try:
                span.set_attribute(ATTR_A2A_ROLE, _role_value(message, role))
            except Exception:  # noqa: BLE001
                logger.debug("a2at: role attribute extraction failed", exc_info=True)
        extensions = _safe_getattr(message, "extensions")
        if isinstance(extensions, (list, tuple)) and extensions:
            joined = ",".join(str(extension) for extension in extensions)
            if joined:
                span.set_attribute(ATTR_A2A_EXTENSIONS, joined)
        try:
            parts = _safe_getattr(message, "parts")
            if parts is not None:
                span.set_attribute(ATTR_A2A_PARTS_NUMBER, len(parts))
        except TypeError:
            logger.debug("a2at: parts count extraction failed", exc_info=True)

    def _set_protocol_attribute(self, span: Any) -> None:
        """Derive ``gen_ai.agent.a2a.protocol`` from the inner transport class name."""
        try:
            name = type(self._inner).__name__.lower()
            if "grpc" in name:
                span.set_attribute(ATTR_A2A_PROTOCOL, "grpc")
            elif "http" in name or "rest" in name or "jsonrpc" in name:
                span.set_attribute(ATTR_A2A_PROTOCOL, "http_json")
        except Exception:  # noqa: BLE001
            logger.debug("a2at: protocol attribute extraction failed", exc_info=True)

    def _set_usage_attributes(self, span: Any) -> None:
        """Read + clear the LLM stash (spec 4.2 gen_ai.usage.* / gen_ai.request.model)."""
        try:
            stash = get_llm_usage()
            if stash is not None:
                if stash.input_tokens is not None:
                    span.set_attribute(ATTR_GEN_AI_USAGE_INPUT_TOKENS, stash.input_tokens)
                if stash.output_tokens is not None:
                    span.set_attribute(ATTR_GEN_AI_USAGE_OUTPUT_TOKENS, stash.output_tokens)
                if stash.request_model:
                    span.set_attribute(ATTR_GEN_AI_REQUEST_MODEL, stash.request_model)
        finally:
            clear_llm_usage()

    def _set_regex_attributes(self, span: Any, message: Any) -> None:
        try:
            metadata = normalize_metadata(_safe_getattr(message, "metadata")) if message is not None else {}
            if not metadata:
                return
            task_type = self._regex_config.invoke_task_type_provider(metadata)
            if task_type:
                span.set_attribute(ATTR_TASK_TYPE, task_type)
            topic = self._regex_config.invoke_notification_topic_provider(metadata)
            if topic:
                span.set_attribute(ATTR_NOTIFICATION_TOPIC, topic)
            authorization = self._regex_config.invoke_authorization_provider(metadata)
            if authorization:
                operation_type = authorization.get(ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE)
                if operation_type:
                    span.set_attribute(ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE, operation_type)
        except Exception:  # noqa: BLE001 - spec 8.1: regex failure → DEBUG + omit
            logger.debug("a2at: regex attribute extraction failed", exc_info=True)

    def _inject_traceparent(self, context: Any, span: Any) -> None:
        try:
            if not _otel_compat.is_enabled():
                return
            service_parameters = _safe_getattr(context, "service_parameters")
            if not isinstance(service_parameters, MutableMapping):
                return
            span_context = _context_with_span(span)
            if span_context is None:
                return
            inject_traceparent(service_parameters, context=span_context)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: traceparent injection failed", exc_info=True)

    def _capture_request_payload(self, span: Any, request: Any) -> None:
        """Channel-1 payload attr onto the span (when present) + channel-2 log."""
        try:
            payload = extract_payload(request, self._config, is_request=True)
            if payload is None:
                return
            if span is not None:
                span.set_attribute(ATTR_A2A_REQUEST, payload)
            attrs = extract_request_attributes(request, method=_SEND_MESSAGE)
            fields: dict[str, object] = {}
            if ATTR_TASK_ID in attrs:
                fields["task.id"] = attrs[ATTR_TASK_ID]
            if ATTR_EXTENSION_NAME in attrs:
                fields["extension.name"] = attrs[ATTR_EXTENSION_NAME]
            log_event(
                "task.request",
                logging.DEBUG,
                fields=fields,
                payload=payload,
                config=self._config,
            )
        except Exception:  # noqa: BLE001
            logger.warning("a2at: request payload capture failed", exc_info=True)

    def _capture_response(self, entry_span: Any, result: Any, method: str) -> None:
        """Rule 3: response attributes onto the entry span / negotiation span (spec 3.1).

        Span-only work is skipped when ``entry_span`` is None (trace off), but the
        negotiation log still fires (§8.1: logs survive trace-off).
        """
        try:
            candidate = _unwrap(result)
            info = classify_event(candidate)
            if info.kind == "message" and _is_negotiation_message(candidate):
                if entry_span is not None:
                    self._emit_negotiation_span(entry_span, method, info, candidate)
                self._log_negotiation(info)
                return
            if entry_span is None:
                return
            if info.kind != "unknown":
                self._set_response_attributes(entry_span, info)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: response capture failed", exc_info=True)

    def _set_response_attributes(self, span: Any, info: EventInfo) -> None:
        if info.task_id:
            span.set_attribute(ATTR_TASK_ID, info.task_id)
            span.set_attribute(ATTR_A2A_TASK_ID, info.task_id)
        if info.task_status:
            span.set_attribute(ATTR_TASK_STATUS, info.task_status)
        metadata = info.message_metadata or {}
        extension = _extension_from_metadata(metadata)
        if extension:
            span.set_attribute(ATTR_EXTENSION_NAME, extension)

    def _emit_negotiation_span(self, entry_span: Any, method: str, info: EventInfo, message: Any) -> None:
        """``*-negotiation`` span: PARENT of the entry span (spec 3.2 parent-vs-link)."""
        try:
            tracer: Any = _otel_compat.get_tracer()
            kwargs: dict[str, Any] = {"kind": _otel_compat.SpanKind.CLIENT}
            parent_context = _context_with_span(entry_span)
            if parent_context is not None:
                kwargs["context"] = parent_context
            span = tracer.start_span(f"{method}{_NEGOTIATION_SUFFIX}", **kwargs)
            if span is None:
                return
            span.set_attribute(ATTR_GEN_AI_OPERATION_NAME, method)
            span.set_attribute(ATTR_EXTENSION_NAME, _NEGOTIATION_EXTENSION)
            conversation_id = _safe_getattr(message, "context_id")
            if isinstance(conversation_id, str) and conversation_id:
                span.set_attribute(ATTR_GEN_AI_CONVERSATION_ID, conversation_id)
            if info.task_id:
                span.set_attribute(ATTR_TASK_ID, info.task_id)
            for key, value in extract_negotiation_attributes(info.message_metadata or {}).items():
                span.set_attribute(key, value)
            span.end()
        except Exception:  # noqa: BLE001
            logger.warning("a2at: failed to emit negotiation span", exc_info=True)

    def _log_negotiation(self, info: EventInfo) -> None:
        metadata = info.message_metadata or {}
        fields: dict[str, object] = {}
        for key in (ATTR_NEGOTIATION_ID, ATTR_NEGOTIATION_ROUND, ATTR_NEGOTIATION_PERFORMATIVE):
            value = extract_negotiation_attributes(metadata).get(key)
            if value is not None:
                fields[key] = value
        log_event("negotiation.message", logging.DEBUG, fields=fields, config=self._config)

    def _emit_event_link_span(self, entry_ctx: Any, info: EventInfo, conversation_id: str | None) -> None:
        """``SendStreamingMessage-event`` span: LINK to the entry span (spec 3.2)."""
        if entry_ctx is None:
            return
        try:
            tracer: Any = _otel_compat.get_tracer()
            span = tracer.start_span(
                _EVENT_SPAN_NAME, kind=_otel_compat.SpanKind.CLIENT, links=_links_for(entry_ctx)
            )
            if span is None:
                return
            span.set_attribute(ATTR_STREAMING_EVENT_KIND, info.kind)
            if info.task_id:
                span.set_attribute(ATTR_TASK_ID, info.task_id)
            if info.task_status:
                span.set_attribute(ATTR_TASK_STATUS, info.task_status)
            span.set_attribute(ATTR_GEN_AI_OPERATION_NAME, _SEND_STREAMING_MESSAGE)
            if conversation_id:
                span.set_attribute(ATTR_GEN_AI_CONVERSATION_ID, conversation_id)
                span.end()
        except Exception:  # noqa: BLE001
            logger.warning("a2at: failed to emit stream event span", exc_info=True)

    def _emit_error_span(self, entry_ctx: Any, error: BaseException) -> None:
        if entry_ctx is None:
            return
        try:
            tracer: Any = _otel_compat.get_tracer()
            span = tracer.start_span(
                _ERROR_SPAN_NAME, kind=_otel_compat.SpanKind.CLIENT, links=_links_for(entry_ctx)
            )
            if span is None:
                return
            span.set_attribute("error.type", type(error).__name__)
            span.set_attribute(ATTR_GEN_AI_OPERATION_NAME, _SEND_STREAMING_MESSAGE)
            status_error = getattr(_otel_compat.StatusCode, "ERROR", None)
            if status_error is not None:
                span.set_status(status_error, str(error))
            span.end()
        except Exception:  # noqa: BLE001
            logger.warning("a2at: failed to emit stream error span", exc_info=True)

    def _observe_stream_event(self, entry_span: Any, entry_ctx: Any, conversation_id: str | None, event: Any) -> None:
        candidate = _unwrap(event)
        info = classify_event(candidate)
        if info.kind == "message":
            if _is_negotiation_message(candidate):
                if entry_span is not None:
                    self._emit_negotiation_span(entry_span, _SEND_STREAMING_MESSAGE, info, candidate)
                self._log_negotiation(info)
                return
            if entry_span is not None:
                self._set_response_attributes(entry_span, info)
            return
        if info.kind == "unknown":
            return
        self._emit_event_link_span(entry_ctx, info, conversation_id)
        self._log_stream_event(info)

    def _log_stream_event(self, info: EventInfo) -> None:
        fields: dict[str, object] = {}
        if info.task_id:
            fields["task.id"] = info.task_id
        if info.kind == "artifact":
            if info.artifact_name:
                fields["artifact.name"] = info.artifact_name
            log_event("task.artifact", logging.INFO, fields=fields, config=self._config)
        elif info.kind in EVENT_LOG_KINDS:
            if info.task_status:
                fields["task.status"] = info.task_status
            log_event("task.status_changed", logging.INFO, fields=fields, config=self._config)

    def _is_stream_end(self, event: Any) -> bool:
        """Spec 7.3: final/terminal status, terminal Task snapshot, or Message response."""
        try:
            info = classify_event(_unwrap(event))
        except Exception:  # noqa: BLE001
            return False
        return info.kind == "message" or info.is_terminal

    def _capture_response_payload(self, span: Any, final_event: Any) -> None:
        """Channel-1 payload attr onto the span (when present) + channel-2 log."""
        try:
            if final_event is None:
                return
            payload = extract_payload(final_event, self._config, is_request=False)
            if payload is None:
                return
            if span is not None:
                span.set_attribute(ATTR_A2A_RESPONSE, payload)
            info = classify_event(_unwrap(final_event))
            fields: dict[str, object] = {"task.id": info.task_id} if info.task_id else {}
            log_event(
                "task.response",
                logging.DEBUG,
                fields=fields,
                payload=payload,
                config=self._config,
            )
        except Exception:  # noqa: BLE001
            logger.warning("a2at: response payload capture failed", exc_info=True)

    def _record_operation_metrics(self, method: str, started: float) -> None:
        """L1 + L3 duration histograms with client-side attribution (spec 5.5)."""
        try:
            if not _otel_compat.is_metric_enabled():
                return
            duration = time.perf_counter() - started
            attributes = {ATTR_GEN_AI_OPERATION_NAME: method}
            gen_ai_histogram = _metric_histogram(
                _METRIC_GEN_AI_DURATION, "s", "GenAI client operation duration"
            )
            if gen_ai_histogram is not None:
                gen_ai_histogram.record(duration, attributes=attributes)
            task_histogram = _metric_histogram(
                _METRIC_TASK_DURATION, "s", "A2A-T task request duration"
            )
            if task_histogram is not None:
                task_histogram.record(duration, attributes=attributes)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: failed to record client metrics", exc_info=True)

    def _finish_entry_span(
        self,
        span: Any,
        method: str,
        started: float,
        *,
        error: BaseException | None = None,
        final_event: Any = None,
    ) -> None:
        """End the span (when present) and always record metrics + response log (§8.1)."""
        try:
            if span is not None:
                if error is not None:
                    span.record_exception(error)
                    status_error = getattr(_otel_compat.StatusCode, "ERROR", None)
                    if status_error is not None:
                        span.set_status(status_error, str(error))
                else:
                    status_ok = getattr(_otel_compat.StatusCode, "OK", None)
                    if status_ok is not None:
                        span.set_status(status_ok)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: failed to set entry span status", exc_info=True)
        self._capture_response_payload(span, final_event)
        if span is not None:
            try:
                span.end()
            except Exception:  # noqa: BLE001
                logger.warning("a2at: failed to end entry span", exc_info=True)
        self._record_operation_metrics(method, started)

    async def _aclose_quietly(self, stream: Any) -> None:
        try:
            aclose = getattr(stream, "aclose", None)
            if callable(aclose):
                await aclose()
        except Exception:  # noqa: BLE001
            logger.debug("a2at: inner stream aclose failed", exc_info=True)

    # ------------------------------------------------------------------
    # send_message (sync request/response)
    # ------------------------------------------------------------------

    async def send_message(self, request: Any, *, context: Any = None) -> Any:
        if not self._decorator_active():
            return await self._inner.send_message(request, context=context)
        try:
            ensure_otel_configured(config=self._config)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: ensure_otel_configured failed", exc_info=True)
        entry_span = self._start_entry_span(_SEND_MESSAGE, request, context)
        started = time.perf_counter()
        error: BaseException | None = None
        result: Any = None
        try:
            cm: Any = (
                _otel_compat.use_span(entry_span, end_on_exit=False)
                if entry_span is not None
                else nullcontext()
            )
            with cm:
                self._capture_request_payload(entry_span, request)
                result = await self._inner.send_message(request, context=context)
                if entry_span is not None:
                    self._capture_response(entry_span, result, _SEND_MESSAGE)
        except Exception as exc:  # noqa: BLE001 - business exception propagates
            error = exc
            raise
        finally:
            self._finish_entry_span(
                entry_span,
                _SEND_MESSAGE,
                started,
                error=error,
                final_event=None if error is not None else result,
            )
        return result

    # ------------------------------------------------------------------
    # send_message_streaming (async generator, spec 3.2 pseudo-code)
    # ------------------------------------------------------------------

    async def send_message_streaming(self, request: Any, *, context: Any = None) -> AsyncIterator[Any]:
        if not self._decorator_active():
            async for event in self._inner.send_message_streaming(request, context=context):
                yield event
            return
        try:
            ensure_otel_configured(config=self._config)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: ensure_otel_configured failed", exc_info=True)
        entry_span = self._start_entry_span(_SEND_STREAMING_MESSAGE, request, context)
        entry_ctx = _span_context_of(entry_span)
        request_message = _safe_getattr(request, "message")
        conversation_id = _safe_getattr(request_message, "context_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            conversation_id = None
        started = time.perf_counter()
        final_event: Any = None
        broke_early = False
        error: BaseException | None = None
        stream = self._inner.send_message_streaming(request, context=context)
        try:
            cm: Any = (
                _otel_compat.use_span(entry_span, end_on_exit=False)
                if entry_span is not None
                else nullcontext()
            )
            with cm:
                self._capture_request_payload(entry_span, request)
                async for event in stream:
                    try:
                        self._observe_stream_event(entry_span, entry_ctx, conversation_id, event)
                        final_event = event
                    except Exception:  # noqa: BLE001 - per-event observation is guarded
                        logger.warning("a2at: streaming event observation failed", exc_info=True)
                    yield event
                    if self._is_stream_end(event):
                        broke_early = True
                        break
        except Exception as exc:  # noqa: BLE001 - business exception propagates
            error = exc
            self._emit_error_span(entry_ctx, exc)
            raise
        finally:
            if broke_early:
                await self._aclose_quietly(stream)
            self._finish_entry_span(
                entry_span,
                _SEND_STREAMING_MESSAGE,
                started,
                error=error,
                final_event=final_event,
            )

    # ------------------------------------------------------------------
    # remaining ClientTransport methods (generic wrapper)
    # ------------------------------------------------------------------

    async def _run_simple(self, inner_method: str, span_name: str, request: Any, context: Any) -> Any:
        inner_call = getattr(self._inner, inner_method)
        if not self._decorator_active():
            return await inner_call(request, context=context)
        try:
            ensure_otel_configured(config=self._config)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: ensure_otel_configured failed", exc_info=True)
        entry_span = self._start_entry_span(span_name, request, context)
        started = time.perf_counter()
        error: BaseException | None = None
        result: Any = None
        try:
            cm: Any = (
                _otel_compat.use_span(entry_span, end_on_exit=False)
                if entry_span is not None
                else nullcontext()
            )
            with cm:
                result = await inner_call(request, context=context)
        except Exception as exc:  # noqa: BLE001 - business exception propagates
            error = exc
            raise
        finally:
            self._finish_entry_span(
                entry_span,
                span_name,
                started,
                error=error,
                final_event=None if error is not None else result,
            )
        return result

    async def get_task(self, request: Any, *, context: Any = None) -> Any:
        return await self._run_simple("get_task", "GetTask", request, context)

    async def list_tasks(self, request: Any, *, context: Any = None) -> Any:
        return await self._run_simple("list_tasks", "ListTasks", request, context)

    async def cancel_task(self, request: Any, *, context: Any = None) -> Any:
        return await self._run_simple("cancel_task", "CancelTask", request, context)

    async def subscribe(self, request: Any, *, context: Any = None) -> AsyncIterator[Any]:
        """``subscribe`` is an async generator on the a2a-python ABC (stream-shaped):
        entry span ``SubscribeToTask`` ends when the inner stream ends (spec 3.1)."""
        if not self._decorator_active():
            async for event in self._inner.subscribe(request, context=context):
                yield event
            return
        try:
            ensure_otel_configured(config=self._config)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: ensure_otel_configured failed", exc_info=True)
        entry_span = self._start_entry_span(_SUBSCRIBE, request, context)
        started = time.perf_counter()
        error: BaseException | None = None
        try:
            cm: Any = (
                _otel_compat.use_span(entry_span, end_on_exit=False)
                if entry_span is not None
                else nullcontext()
            )
            with cm:
                async for event in self._inner.subscribe(request, context=context):
                    yield event
        except Exception as exc:  # noqa: BLE001 - business exception propagates
            error = exc
            raise
        finally:
            self._finish_entry_span(entry_span, _SUBSCRIBE, started, error=error, final_event=None)

    async def create_task_push_notification_config(self, request: Any, *, context: Any = None) -> Any:
        return await self._run_simple(
            "create_task_push_notification_config", "CreateTaskPushNotificationConfig", request, context
        )

    async def get_task_push_notification_config(self, request: Any, *, context: Any = None) -> Any:
        return await self._run_simple(
            "get_task_push_notification_config", "GetTaskPushNotificationConfig", request, context
        )

    async def list_task_push_notification_configs(self, request: Any, *, context: Any = None) -> Any:
        return await self._run_simple(
            "list_task_push_notification_configs", "ListTaskPushNotificationConfigs", request, context
        )

    async def delete_task_push_notification_config(self, request: Any, *, context: Any = None) -> Any:
        return await self._run_simple(
            "delete_task_push_notification_config", "DeleteTaskPushNotificationConfig", request, context
        )

    async def get_extended_agent_card(self, request: Any, *, context: Any = None) -> Any:
        return await self._run_simple("get_extended_agent_card", "GetExtendedAgentCard", request, context)

    async def close(self) -> None:
        await self._inner.close()
