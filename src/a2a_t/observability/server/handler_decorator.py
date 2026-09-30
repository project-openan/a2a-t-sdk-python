"""A2ATRequestHandlerDecorator: structural RequestHandler wrapper (spec 3.3, a2a-java parity).

Spans created (SERVER side, names per the a2a-java dashboard):

- entry spans ``SendMessage`` / ``SendStreamingMessage`` / ``GetTask`` / ... of
  SERVER kind, explicit parent = traceparent extracted from
  ``context.state["headers"]`` (no ASGI middleware; a RequestContext-shaped
  ``context.call_context.state["headers"]`` is honoured as a fallback), ending
  when the method returns (sync) or when the stream ends (streaming, try/finally);
- per-event INTERNAL spans ``SendStreamingMessage-event`` PARENT to the server
  entry span. Mechanism (verified against a2a-python's DefaultRequestHandler):
  the handler creates its EventQueue internally and hands it to
  ``agent_executor.execute(request_context, queue)`` — the decorator therefore
  wraps the inner handler's ``agent_executor`` attribute at ``__init__``
  (``A2ATAgentExecutorDecorator``), and the executor decorator wraps the queue;
- push sender CLIENT spans ``SendMessage-pushNotification`` (the inner handler's
  ``_push_sender`` attribute is wrapped at ``__init__``) LINKed to the server
  entry span via the task_id → entry registry;
- the server-side ``a2at.task.request.duration`` histogram at entry-span end.

A module-level bounded registry (task_id/context_id → entry record) connects the
three wrappers: registered at entry-span creation (params.message task_id +
context_id), re-registered per actual event task_id at enqueue time (covers
server-generated task ids), consumed by the push sender for the LINK.

Everything is structural (no a2a import) and guarded: observability failures are
swallowed with a WARNING on logger ``a2at.observability`` and never break the
business flow (spec 8.2). Signal semantics follow spec 8.1: the decorator is a
full pass-through only when OTel is unavailable or the master switch/config is
off; with ``A2AT_TRACE_ENABLED=false`` it stays active — no spans are produced,
but metrics and logs keep working.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

from a2a_t.observability import _otel_compat
from a2a_t.observability.attributes import (
    ATTR_A2A_CONTEXT_ID,
    ATTR_A2A_EXTENSIONS,
    ATTR_A2A_OPERATION_NAME,
    ATTR_A2A_PARTS_NUMBER,
    ATTR_A2A_REQUEST,
    ATTR_A2A_RESPONSE,
    ATTR_A2A_TASK_ID,
    ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE,
    ATTR_EXTENSION_NAME,
    ATTR_GEN_AI_CONVERSATION_ID,
    ATTR_GEN_AI_OPERATION_NAME,
    ATTR_NOTIFICATION_TOPIC,
    ATTR_PUSH_NOTIFICATION_URL,
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
    extract_request_attributes,
    normalize_metadata,
    unwrap_stream_response,
)
from a2a_t.observability.config import A2ATObservabilityConfig
from a2a_t.observability.logs import log_event
from a2a_t.observability.payload import extract_payload
from a2a_t.observability.propagation import extract_trace_context
from a2a_t.observability.setup import ensure_otel_configured

logger = logging.getLogger("a2at.observability")

_SEND_MESSAGE = "SendMessage"
_SEND_STREAMING_MESSAGE = "SendStreamingMessage"
_EVENT_SPAN_NAME = "SendStreamingMessage-event"
_PUSH_SPAN_NAME = "SendMessage-pushNotification"

_METRIC_TASK_DURATION = "a2at.task.request.duration"
_METRIC_INSTRUMENTS: dict[str, Any] = {}

#: Non-send RequestHandler methods (span name per spec 3.3). ``on_message_send`` /
#: ``on_message_send_stream`` / ``on_subscribe_to_task`` are defined explicitly.
_METHOD_SPAN_NAMES: dict[str, str] = {
    "on_get_task": "GetTask",
    "on_list_tasks": "ListTasks",
    "on_cancel_task": "CancelTask",
    "on_create_task_push_notification_config": "CreateTaskPushNotificationConfig",
    "on_get_task_push_notification_config": "GetTaskPushNotificationConfig",
    "on_list_task_push_notification_configs": "ListTaskPushNotificationConfigs",
    "on_delete_task_push_notification_config": "DeleteTaskPushNotificationConfig",
    "on_get_extended_agent_card": "GetExtendedAgentCard",
}

_REGISTRY_MAX = 2048


def _safe_getattr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name)
    except Exception:  # noqa: BLE001 - structural access must never raise
        return None


def _safe_use_span(span: Any) -> Any:
    """Activation context manager; ``nullcontext`` for absent spans / activation failure."""
    if span is None:
        return nullcontext()
    try:
        return _otel_compat.use_span(span, end_on_exit=False)
    except Exception:  # noqa: BLE001 - activation failure must not break the flow
        logger.warning("a2at: span activation failed", exc_info=True)
        return nullcontext()


def _span_is_recording(span: Any) -> bool:
    try:
        return bool(span.is_recording())
    except Exception:  # noqa: BLE001 - structural access must never raise
        return False


def _extension_from_metadata(metadata: dict[str, Any]) -> str | None:
    for key in metadata:
        name = extension_name_from_uri(str(key))
        if name:
            return name
    return None


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
    """Lazily create and cache one histogram per metric name (shared v2 pattern)."""
    instrument = _METRIC_INSTRUMENTS.get(name)
    if instrument is None:
        meter: Any = _otel_compat.get_meter()
        instrument = meter.create_histogram(name, unit=unit, description=description)
        _METRIC_INSTRUMENTS[name] = instrument
    return instrument


def _extract_headers(context: Any) -> Mapping[str, str] | None:
    """Handler-level context is ServerCallContext (``.state``); RequestContext nests ``.call_context``."""
    for state in (_safe_getattr(context, "state"), _safe_getattr(_safe_getattr(context, "call_context"), "state")):
        if isinstance(state, Mapping):
            headers = state.get("headers")
            if isinstance(headers, Mapping) and headers:
                return headers
    return None


class _EntryRecord:
    """Per-request server entry span record shared through the task registry."""

    __slots__ = ("conversation_id", "operation_name", "otel_context", "span", "span_context")

    def __init__(
        self,
        span: Any,
        span_context: Any,
        otel_context: Any,
        conversation_id: str | None,
        operation_name: str,
    ) -> None:
        self.span = span
        self.span_context = span_context
        self.otel_context = otel_context
        self.conversation_id = conversation_id
        self.operation_name = operation_name


_TASK_SPAN_REGISTRY: dict[str, _EntryRecord] = {}


def _register_task_span_context(key: Any, record: _EntryRecord) -> None:
    """Bounded FIFO registry (task ids and context ids); never raises."""
    try:
        if not isinstance(key, str) or not key:
            return
        if len(_TASK_SPAN_REGISTRY) >= _REGISTRY_MAX:
            oldest = next(iter(_TASK_SPAN_REGISTRY))
            _TASK_SPAN_REGISTRY.pop(oldest, None)
        _TASK_SPAN_REGISTRY[key] = record
    except Exception:  # noqa: BLE001
        logger.debug("a2at: task span registry registration failed", exc_info=True)


def _lookup_task_span_context(key: Any) -> _EntryRecord | None:
    try:
        if not isinstance(key, str) or not key:
            return None
        return _TASK_SPAN_REGISTRY.get(key)
    except Exception:  # noqa: BLE001
        return None


def _end_span_quietly(span: Any) -> None:
    try:
        span.end()
    except Exception:  # noqa: BLE001
        logger.warning("a2at: failed to end span", exc_info=True)


class _ObservabilityEventQueue:
    """EventQueue wrapper: per-event INTERNAL span PARENT to the server entry span (spec 3.3/4.4)."""

    def __init__(self, inner: Any, record: _EntryRecord, config: A2ATObservabilityConfig) -> None:
        self._inner = inner
        self._record = record
        self._config = config

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name == "_inner":
            raise AttributeError(name)
        inner = self.__dict__.get("_inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)

    def _span_active(self) -> bool:
        return bool(
            _otel_compat.is_trace_enabled() and self._config.trace_enabled and self._record.otel_context is not None
        )

    def _classify(self, event: Any) -> EventInfo:
        return classify_event(_unwrap(event))

    async def enqueue_event(self, event: Any) -> None:
        info = self._classify(event)
        if info.task_id:
            _register_task_span_context(info.task_id, self._record)
        span = self._start_event_span(info)
        cm: Any = _safe_use_span(span)
        try:
            with cm:
                await self._inner.enqueue_event(event)
        finally:
            if span is not None:
                _end_span_quietly(span)
        self._log_event_info(info)

    def _start_event_span(self, info: EventInfo) -> Any | None:
        if self._record.operation_name != _SEND_STREAMING_MESSAGE:
            # Sync SendMessage also routes through a queue (DefaultRequestHandler):
            # only the streaming operation gets per-event spans (no phantom spans).
            return None
        if not self._span_active():
            return None
        try:
            tracer: Any = _otel_compat.get_tracer()
            span = tracer.start_span(
                _EVENT_SPAN_NAME, kind=_otel_compat.SpanKind.INTERNAL, context=self._record.otel_context
            )
            if span is None:
                return None
            span.set_attribute(ATTR_STREAMING_EVENT_KIND, info.kind)
            if info.task_id:
                span.set_attribute(ATTR_TASK_ID, info.task_id)
            if info.task_status:
                span.set_attribute(ATTR_TASK_STATUS, info.task_status)
            span.set_attribute(ATTR_GEN_AI_OPERATION_NAME, _SEND_STREAMING_MESSAGE)
            if self._record.conversation_id:
                span.set_attribute(ATTR_GEN_AI_CONVERSATION_ID, self._record.conversation_id)
            return span
        except Exception:
            logger.warning("a2at: failed to start stream event span", exc_info=True)
            return None

    def _log_event_info(self, info: EventInfo) -> None:
        try:
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
        except Exception:  # noqa: BLE001
            logger.debug("a2at: stream event log failed", exc_info=True)


class A2ATAgentExecutorDecorator:
    """AgentExecutor wrapper: injects per-event spans + entry-span activation (spec 3.3/3.4/7.1).

    The a2a-python DefaultRequestHandler creates the EventQueue internally and
    passes it to ``execute(context, event_queue)``; this wrapper swaps in a
    queue that creates the per-event spans, and activates the server entry span
    so user executor code sees ``current_span()`` = SERVER entry span.
    """

    def __init__(self, inner: Any, *, config: A2ATObservabilityConfig | None = None) -> None:
        self._inner = inner
        self._config = config or A2ATObservabilityConfig()

    def _decorator_active(self) -> bool:
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

    def _find_record(self, context: Any) -> _EntryRecord | None:
        record = _lookup_task_span_context(_safe_getattr(context, "task_id"))
        if record is None:
            fallback = _lookup_task_span_context(_safe_getattr(context, "context_id"))
            if fallback is not None and fallback.span is not None and not _span_is_recording(fallback.span):
                # The context_id key may point at a previous request's record
                # (shared context_id, no client task_id): only a live entry span
                # (still recording) may be adopted via the fallback.
                return None
            record = fallback
        return record

    async def execute(self, context: Any, event_queue: Any) -> Any:
        if not self._decorator_active():
            return await self._inner.execute(context, event_queue)
        record = self._find_record(context)
        if record is None:
            return await self._inner.execute(context, event_queue)
        wrapped = _ObservabilityEventQueue(event_queue, record, self._config)
        if record.span is None or not self._config.trace_enabled or not _otel_compat.is_trace_enabled():
            return await self._inner.execute(context, wrapped)
        with _safe_use_span(record.span):
            return await self._inner.execute(context, wrapped)

    async def cancel(self, context: Any, event_queue: Any) -> Any:
        return await self._inner.cancel(context, event_queue)


class A2ATPushSenderDecorator:
    """PushNotificationSender wrapper: ``SendMessage-pushNotification`` CLIENT span (spec 3.3/4.7).

    LINKs to the server entry span through the task registry (populated by the
    EventQueue wrapper at enqueue time). ``push.notification.url`` comes from the
    concrete sender's ``_dispatch_notification`` push_info argument when that
    method exists (``BasePushNotificationSender`` shape): the decorator then
    instance-wraps ``_dispatch_notification`` — one span per fanout push_info —
    instead of wrapping the ABC ``send_notification(task_id, event)`` surface,
    whose proto events carry no ``.url`` (dead attribute otherwise).
    """

    def __init__(self, inner: Any, *, config: A2ATObservabilityConfig | None = None) -> None:
        self._inner = inner
        self._config = config or A2ATObservabilityConfig()

    def _decorator_active(self) -> bool:
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

    def install_dispatch_wrapper(self) -> bool:
        """Instance-wrap ``_dispatch_notification`` (per-push_info span with its URL).

        Returns ``False`` (no-op) when the inner sender lacks the method or the
        assignment fails — the caller then falls back to the ABC-surface wrap.
        """
        dispatch = _safe_getattr(self._inner, "_dispatch_notification")
        if dispatch is None:
            return False
        try:
            already_wrapped = bool(dispatch._a2at_wrapped)
        except AttributeError:
            already_wrapped = False
        if already_wrapped:
            return True

        async def _dispatch_with_span(event: Any, push_info: Any, task_id: str) -> Any:
            info = classify_event(_unwrap(event))
            self._log_push(task_id, info)
            span = self._start_push_span_for_url(task_id, info, _safe_getattr(push_info, "url"))
            cm: Any = _safe_use_span(span)
            try:
                with cm:
                    return await dispatch(event, push_info, task_id)
            finally:
                if span is not None:
                    _end_span_quietly(span)

        wrapper: Any = _dispatch_with_span
        wrapper._a2at_wrapped = True
        try:
            setattr(self._inner, "_dispatch_notification", _dispatch_with_span)
            return True
        except Exception:  # noqa: BLE001
            logger.warning("a2at: failed to wrap _dispatch_notification", exc_info=True)
            return False

    async def send_notification(self, task_id: str, event: Any) -> Any:
        if not self._decorator_active():
            return await self._inner.send_notification(task_id, event)
        info = classify_event(_unwrap(event))
        self._log_push(task_id, info)
        span = self._start_push_span_for_url(task_id, info, _safe_getattr(event, "url"))
        cm: Any = _safe_use_span(span)
        try:
            with cm:
                return await self._inner.send_notification(task_id, event)
        finally:
            if span is not None:
                _end_span_quietly(span)

    def _start_push_span_for_url(self, task_id: str, info: EventInfo, url: Any) -> Any | None:
        if not _otel_compat.is_trace_enabled() or not self._config.trace_enabled:
            return None
        try:
            record = _lookup_task_span_context(task_id)
            tracer: Any = _otel_compat.get_tracer()
            kwargs: dict[str, Any] = {"kind": _otel_compat.SpanKind.CLIENT}
            links = _links_for(record.span_context if record is not None else None)
            if links is not None:
                kwargs["links"] = links
            span = tracer.start_span(_PUSH_SPAN_NAME, **kwargs)
            if span is None:
                return None
            span.set_attribute(ATTR_TASK_ID, task_id)
            span.set_attribute(ATTR_STREAMING_EVENT_KIND, info.kind)
            if isinstance(url, str) and url:
                span.set_attribute(ATTR_PUSH_NOTIFICATION_URL, url)
            return span
        except Exception:  # noqa: BLE001
            logger.warning("a2at: failed to start push notification span", exc_info=True)
            return None

    def _log_push(self, task_id: str, info: EventInfo) -> None:
        try:
            log_event(
                "notification.push",
                logging.INFO,
                fields={"task.id": task_id, ATTR_STREAMING_EVENT_KIND: info.kind},
                config=self._config,
            )
        except Exception:  # noqa: BLE001
            logger.debug("a2at: push log failed", exc_info=True)


class A2ATRequestHandlerDecorator:
    """Structural RequestHandler wrapper creating a2a-java-parity server spans (spec 3.3)."""

    def __init__(self, inner: Any, *, config: A2ATObservabilityConfig | None = None) -> None:
        self._inner = inner
        self._config = config or A2ATObservabilityConfig()
        # Regex config resolution: unset regex fields fall back to the built-in
        # DEFAULT_*_REGEX patterns (mirrors the client decorator); empty string opt-outs.
        self._regex_config = dataclasses.replace(
            self._config,
            task_type_regex=(
                self._config.task_type_regex if self._config.task_type_regex is not None else DEFAULT_TASK_TYPE_REGEX
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
        self._wrap_collaborators()

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------

    def _warn_if_v2_handler(self) -> None:
        """One-time WARNING for a2a-sdk 1.1.x V2 ``DefaultRequestHandler`` inners.

        The V2 implementation's ``ActiveTaskRegistry`` captures the original
        executor/push-sender references at construction, so post-construction
        wraps (``agent_executor`` / ``_push_sender`` attribute swaps) are silently
        bypassed — per-event spans and push LINKs are lost while the entry spans
        keep working. ``LegacyRequestHandler`` keeps full observability.
        """
        if getattr(self, "_v2_warned", False):
            return
        if _safe_getattr(self._inner, "_active_task_registry") is None:
            return
        self._v2_warned = True
        logger.warning(
            "a2at: DefaultRequestHandler V2 bypasses post-construction executor/push wraps; "
            "use LegacyRequestHandler for full per-event/push observability"
        )

    def _decorator_active(self) -> bool:
        """Master gate (spec §8.1): full pass-through only when OTel is unavailable/off.

        Trace-off (``A2AT_TRACE_ENABLED=false``) is NOT a pass-through: the
        decorator stays active, skips span creation only, and metrics/logs keep
        working (the span gate lives in ``_start_server_entry_span``).
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

    def _wrap_collaborators(self) -> None:
        """Wrap the inner handler's ``agent_executor``/``_push_sender`` attrs (DefaultRequestHandler shape)."""
        executor = _safe_getattr(self._inner, "agent_executor")
        if executor is not None and not isinstance(executor, A2ATAgentExecutorDecorator):
            try:
                self._inner.agent_executor = A2ATAgentExecutorDecorator(executor, config=self._config)
            except Exception:  # noqa: BLE001
                logger.warning("a2at: failed to wrap agent_executor", exc_info=True)
        push_sender = _safe_getattr(self._inner, "_push_sender")
        if push_sender is not None and not isinstance(push_sender, A2ATPushSenderDecorator):
            try:
                decorator = A2ATPushSenderDecorator(push_sender, config=self._config)
                if not decorator.install_dispatch_wrapper():
                    self._inner._push_sender = decorator
            except Exception:  # noqa: BLE001
                logger.warning("a2at: failed to wrap push sender", exc_info=True)
        self._warn_if_v2_handler()

    def _start_server_entry_span(self, name: str, params: Any, context: Any) -> Any | None:
        if not _otel_compat.is_trace_enabled() or not self._config.trace_enabled:
            # Spec §8.1: trace off → no span; metrics/logs stay active.
            return None
        try:
            tracer: Any = _otel_compat.get_tracer()
            kwargs: dict[str, Any] = {"kind": _otel_compat.SpanKind.SERVER}
            parent_context = self._extract_parent_context(context)
            if parent_context is not None:
                kwargs["context"] = parent_context
            span = tracer.start_span(name, **kwargs)
            if span is None:
                return None
            self._set_request_attributes(span, params, name)
            return span
        except Exception:  # noqa: BLE001 - observability must never break the flow
            logger.warning("a2at: failed to start server entry span %s", name, exc_info=True)
            return None

    def _extract_parent_context(self, context: Any) -> Any | None:
        headers = _extract_headers(context)
        if not headers:
            return None
        return extract_trace_context(headers)

    def _set_request_attributes(self, span: Any, params: Any, method: str) -> None:
        attrs = extract_request_attributes(params, method=method)
        for key, value in attrs.items():
            span.set_attribute(key, value)
        message = _safe_getattr(params, "message")
        if message is not None:
            self._set_message_attributes(span, message)
        span.set_attribute(ATTR_A2A_OPERATION_NAME, method)
        self._set_regex_attributes(span, message)

    def _set_message_attributes(self, span: Any, message: Any) -> None:
        """a2a-java double-writes + structural counts (spec 4.2; message_id/role are client-only)."""
        context_id = _safe_getattr(message, "context_id")
        if isinstance(context_id, str) and context_id:
            span.set_attribute(ATTR_A2A_CONTEXT_ID, context_id)
        task_id = _safe_getattr(message, "task_id")
        if isinstance(task_id, str) and task_id:
            span.set_attribute(ATTR_A2A_TASK_ID, task_id)
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

    def _register_entry_record(self, params: Any, span: Any, method: str) -> _EntryRecord:
        """Register the entry record under params task_id + context_id (registry bootstrap)."""
        message = _safe_getattr(params, "message")
        conversation_id = _safe_getattr(message, "context_id")
        record = _EntryRecord(
            span=span,
            span_context=_span_context_of(span),
            otel_context=_context_with_span(span),
            conversation_id=conversation_id if isinstance(conversation_id, str) and conversation_id else None,
            operation_name=method,
        )
        _register_task_span_context(_safe_getattr(message, "task_id"), record)
        _register_task_span_context(conversation_id, record)
        return record

    def _capture_request_payload(self, span: Any, params: Any) -> None:
        """Channel-1 payload attr onto the span (when present) + channel-2 log (spec 4.3)."""
        try:
            payload = extract_payload(params, self._config, is_request=True)
            if payload is None:
                return
            if span is not None:
                span.set_attribute(ATTR_A2A_REQUEST, payload)
            attrs = extract_request_attributes(params, method=_SEND_MESSAGE)
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

    def _capture_response(self, span: Any, result: Any) -> None:
        """Final-event attributes onto the entry span (spec 7.3 stream-end shapes)."""
        try:
            info = classify_event(_unwrap(result))
            if info.kind == "unknown":
                return
            if info.task_id:
                span.set_attribute(ATTR_TASK_ID, info.task_id)
                span.set_attribute(ATTR_A2A_TASK_ID, info.task_id)
            if info.task_status:
                span.set_attribute(ATTR_TASK_STATUS, info.task_status)
            metadata = info.message_metadata or {}
            extension = _extension_from_metadata(metadata)
            if extension:
                span.set_attribute(ATTR_EXTENSION_NAME, extension)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: response capture failed", exc_info=True)

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

    def _record_task_duration_metric(self, method: str, started: float) -> None:
        """``a2at.task.request.duration`` with server-side attribution (spec 5.5)."""
        try:
            if not _otel_compat.is_metric_enabled():
                return
            duration = time.perf_counter() - started
            attributes = {ATTR_GEN_AI_OPERATION_NAME: method}
            histogram = _metric_histogram(_METRIC_TASK_DURATION, "s", "A2A-T task request duration")
            if histogram is not None:
                histogram.record(duration, attributes=attributes)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: failed to record server metrics", exc_info=True)

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
        if span is not None and final_event is not None:
            self._capture_response(span, final_event)
        self._capture_response_payload(span, final_event)
        if span is not None:
            _end_span_quietly(span)
        self._record_task_duration_metric(method, started)

    # ------------------------------------------------------------------
    # on_message_send (sync request/response)
    # ------------------------------------------------------------------

    async def on_message_send(self, params: Any, context: Any) -> Any:
        if not self._decorator_active():
            return await self._inner.on_message_send(params, context)
        try:
            ensure_otel_configured(config=self._config)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: ensure_otel_configured failed", exc_info=True)
        entry_span = self._start_server_entry_span(_SEND_MESSAGE, params, context)
        self._register_entry_record(params, entry_span, _SEND_MESSAGE)
        started = time.perf_counter()
        error: BaseException | None = None
        result: Any = None
        try:
            cm: Any = _safe_use_span(entry_span)
            with cm:
                self._capture_request_payload(entry_span, params)
                result = await self._inner.on_message_send(params, context)
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
    # on_message_send_stream (async generator, spec 3.3)
    # ------------------------------------------------------------------

    async def on_message_send_stream(self, params: Any, context: Any) -> Any:
        call = self._run_stream(_SEND_STREAMING_MESSAGE, "on_message_send_stream", params, context, register=True)
        async for event in call:
            yield event

    async def on_subscribe_to_task(self, params: Any, context: Any) -> Any:
        # Re-attach to a running stream: entry span only — the tapped queue's events
        # are already observed by the producing request's EventQueue wrapper.
        async for event in self._run_stream("SubscribeToTask", "on_subscribe_to_task", params, context, register=False):
            yield event

    async def _run_stream(self, span_name: str, inner_method: str, params: Any, context: Any, *, register: bool) -> Any:
        if not self._decorator_active():
            async for event in getattr(self._inner, inner_method)(params, context):
                yield event
            return
        try:
            ensure_otel_configured(config=self._config)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: ensure_otel_configured failed", exc_info=True)
        entry_span = self._start_server_entry_span(span_name, params, context)
        if register:
            self._register_entry_record(params, entry_span, span_name)
        started = time.perf_counter()
        final_event: Any = None
        error: BaseException | None = None
        try:
            cm: Any = _safe_use_span(entry_span)
            with cm:
                if register:
                    self._capture_request_payload(entry_span, params)
                async for event in getattr(self._inner, inner_method)(params, context):
                    final_event = event
                    yield event
        except Exception as exc:  # noqa: BLE001 - business exception propagates
            error = exc
            raise
        finally:
            self._finish_entry_span(
                entry_span,
                span_name,
                started,
                error=error,
                final_event=None if error is not None else final_event,
            )

    # ------------------------------------------------------------------
    # remaining RequestHandler methods (generic wrapper)
    # ------------------------------------------------------------------

    async def _run_simple(self, inner_method: str, span_name: str, params: Any, context: Any) -> Any:
        inner_call = getattr(self._inner, inner_method)
        if not self._decorator_active():
            return await inner_call(params, context)
        try:
            ensure_otel_configured(config=self._config)
        except Exception:  # noqa: BLE001
            logger.warning("a2at: ensure_otel_configured failed", exc_info=True)
        entry_span = self._start_server_entry_span(span_name, params, context)
        started = time.perf_counter()
        error: BaseException | None = None
        result: Any = None
        try:
            cm: Any = _safe_use_span(entry_span)
            with cm:
                result = await inner_call(params, context)
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

    async def on_get_task(self, params: Any, context: Any) -> Any:
        return await self._run_simple("on_get_task", "GetTask", params, context)

    async def on_list_tasks(self, params: Any, context: Any) -> Any:
        return await self._run_simple("on_list_tasks", "ListTasks", params, context)

    async def on_cancel_task(self, params: Any, context: Any) -> Any:
        return await self._run_simple("on_cancel_task", "CancelTask", params, context)

    async def on_create_task_push_notification_config(self, params: Any, context: Any) -> Any:
        return await self._run_simple(
            "on_create_task_push_notification_config", "CreateTaskPushNotificationConfig", params, context
        )

    async def on_get_task_push_notification_config(self, params: Any, context: Any) -> Any:
        return await self._run_simple(
            "on_get_task_push_notification_config", "GetTaskPushNotificationConfig", params, context
        )

    async def on_list_task_push_notification_configs(self, params: Any, context: Any) -> Any:
        return await self._run_simple(
            "on_list_task_push_notification_configs", "ListTaskPushNotificationConfigs", params, context
        )

    async def on_delete_task_push_notification_config(self, params: Any, context: Any) -> Any:
        return await self._run_simple(
            "on_delete_task_push_notification_config", "DeleteTaskPushNotificationConfig", params, context
        )

    async def on_get_extended_agent_card(self, params: Any, context: Any) -> Any:
        return await self._run_simple("on_get_extended_agent_card", "GetExtendedAgentCard", params, context)
