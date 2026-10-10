"""A2A-T span attribute constants and wire-attribute extraction (structural, no a2a import)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, cast

AttributeValue = str | int | bool

ATTR_EXTENSION_NAME = "gen_ai.agent.a2at.extension.name"
ATTR_TASK_ID = "gen_ai.agent.a2at.task.id"
ATTR_TASK_STATUS = "gen_ai.agent.a2at.task.status"
ATTR_TASK_TYPE = "gen_ai.agent.a2at.task.type"
ATTR_NEGOTIATION_ID = "gen_ai.agent.a2at.negotiation.id"
ATTR_NEGOTIATION_ROUND = "gen_ai.agent.a2at.negotiation.round"
ATTR_NEGOTIATION_MAX_ROUNDS = "gen_ai.agent.a2at.negotiation.max_rounds"
ATTR_NEGOTIATION_PERFORMATIVE = "gen_ai.agent.a2at.negotiation.performative"
ATTR_NEGOTIATION_TOTAL_ROUNDS = "gen_ai.agent.a2at.negotiation.total_rounds"
ATTR_NOTIFICATION_TOPIC = "gen_ai.agent.a2at.notification.topic"
ATTR_STREAMING_EVENT_KIND = "gen_ai.agent.a2at.streaming.event.kind"
ATTR_PUSH_NOTIFICATION_URL = "gen_ai.agent.a2at.push.notification.url"
#: Metric dimension for dual-recorded histograms (``a2at.task.request.duration``
#: is recorded on both ends): spans distinguish sides via SpanKind, metrics have
#: no kind - this attribute separates the client/server populations (spec 3.4/5.5).
ATTR_SPAN_SIDE = "a2at.span.side"
ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE = "gen_ai.agent.a2at.authorization.policy.operation.type"
ATTR_AUTHORIZATION_POLICY_ID = "authorization.policy.id"
ATTR_AUTHORIZATION_OPERATION_TYPE = "authorization.operation_type"
ATTR_AUTHORIZATION_OPERATION_RISK_LEVEL = "authorization.operation_risk_level"
# a2a-java dashboard double-write attributes (spec 4.1/4.2): gen_ai.agent.a2a.*
ATTR_A2A_OPERATION_NAME = "gen_ai.agent.a2a.operation.name"
ATTR_A2A_CONTEXT_ID = "gen_ai.agent.a2a.context_id"
ATTR_A2A_TASK_ID = "gen_ai.agent.a2a.task_id"
ATTR_A2A_MESSAGE_ID = "gen_ai.agent.a2a.message_id"
ATTR_A2A_ROLE = "gen_ai.agent.a2a.role"
ATTR_A2A_EXTENSIONS = "gen_ai.agent.a2a.extensions"
ATTR_A2A_PARTS_NUMBER = "gen_ai.agent.a2a.parts.number"
ATTR_A2A_PROTOCOL = "gen_ai.agent.a2a.protocol"
ATTR_A2A_REQUEST = "gen_ai.agent.a2a.request"
ATTR_A2A_RESPONSE = "gen_ai.agent.a2a.response"
ATTR_GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
ATTR_GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
ATTR_GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
ATTR_GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ATTR_GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
ATTR_GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
ATTR_GEN_AI_TOKEN_TYPE = "gen_ai.token.type"
ATTR_GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
ATTR_STREAMING = "streaming"

DEFAULT_TASK_TYPE_REGEX = r"##\s*任务类型\(Task Type\)\s*\n+\s*(\S[^\n]*)"
DEFAULT_NOTIFICATION_TOPIC_REGEX = r"##\s*通知主题\s*\n+\s*(\S[^\n]*)"
DEFAULT_AUTHORIZATION_POLICY_OPERATION_TYPE_REGEX = r"##\s*授权策略的操作类型\s*\n+\s*(\S[^\n]*)"

_EXTENSION_BASE = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/"
_TERMINAL_PERFORMATIVES = frozenset({"ACCEPT", "REJECT", "ABORT"})


def extension_name_from_uri(uri: str) -> str | None:
    """Map a TMF telecommunication extension URI to its short name (Task-T / Negotiation-T / ...)."""
    if not uri.startswith(_EXTENSION_BASE):
        return None
    rest = uri[len(_EXTENSION_BASE) :]
    name = rest.split("/", 1)[0]
    return name or None


def _value_to_python(value: Any) -> Any:
    which = None
    try:
        which = value.WhichOneof("kind")
    except Exception:  # noqa: BLE001 - structural access must never raise
        which = None
    if which == "string_value":
        return value.string_value
    if which == "number_value":
        return value.number_value
    if which == "bool_value":
        return value.bool_value
    if which == "struct_value":
        return {str(k): _value_to_python(v) for k, v in value.struct_value.fields.items()}
    if which == "list_value":
        return [_value_to_python(v) for v in value.list_value.values]
    if isinstance(value, Mapping):
        # Real protobuf Struct registers as a Mapping whose __getitem__ converts
        # scalars but leaves nested containers as raw Struct-like objects.
        return {str(k): _value_to_python(v) for k, v in value.items()}
    values = getattr(value, "values", None)
    if values is not None and not callable(values):
        # Real protobuf ListValue: not a list instance and its repeated container
        # has no __iter__ attribute (old-style iteration protocol).
        try:
            return [_value_to_python(v) for v in values]
        except TypeError:
            pass
    if isinstance(value, (list, tuple)):
        return [_value_to_python(v) for v in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return None


def normalize_metadata(metadata: Any) -> dict[str, Any]:
    """Normalize a Mapping or a protobuf-like Struct into a plain dict; never raises."""
    if metadata is None:
        return {}
    try:
        if isinstance(metadata, Mapping):
            return {str(k): _value_to_python(v) for k, v in metadata.items()}
        fields = getattr(metadata, "fields", None)
        if fields is not None and hasattr(fields, "items"):
            return {str(k): _value_to_python(v) for k, v in fields.items()}
    except Exception:  # noqa: BLE001
        return {}
    return {}


def _as_wire_int(value: Any) -> int | None:
    """Coerce integral wire numbers (protobuf doubles read back as float) to int; reject bool."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_negotiation_attributes(metadata: dict[str, Any]) -> dict[str, AttributeValue]:
    """Extract negotiation.* attributes from a normalized metadata dict."""
    context = metadata.get("negotiationContext")
    if not isinstance(context, Mapping):
        return {}
    attrs: dict[str, AttributeValue] = {}
    ctx_id = context.get("id")
    if isinstance(ctx_id, str) and ctx_id:
        attrs[ATTR_NEGOTIATION_ID] = ctx_id
    round_ = _as_wire_int(context.get("round"))
    if isinstance(round_, int):
        attrs[ATTR_NEGOTIATION_ROUND] = round_
    max_rounds = _as_wire_int(context.get("maxRounds"))
    if isinstance(max_rounds, int):
        attrs[ATTR_NEGOTIATION_MAX_ROUNDS] = max_rounds
    performative = context.get("performative")
    if isinstance(performative, str) and performative:
        attrs[ATTR_NEGOTIATION_PERFORMATIVE] = performative
        if performative in _TERMINAL_PERFORMATIVES and isinstance(round_, int):
            attrs[ATTR_NEGOTIATION_TOTAL_ROUNDS] = round_
    return attrs


#: Negotiation-T extension identity shared by both decorators (v3 addendum).
NEGOTIATION_EXTENSION = "Negotiation-T"
#: Span-name suffix for negotiation spans (``SendMessage-negotiation`` etc.).
NEGOTIATION_SUFFIX = "-negotiation"


def _safe_getattr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name)
    except Exception:  # noqa: BLE001
        return None


def is_negotiation_message(message: Any) -> bool:
    """True when the message metadata carries the Negotiation-T extension key."""
    try:
        metadata = normalize_metadata(_safe_getattr(message, "metadata"))
    except Exception:  # noqa: BLE001
        return False
    return any(extension_name_from_uri(str(key)) == NEGOTIATION_EXTENSION for key in metadata)


def _extension_name_from_metadata(metadata: dict[str, Any]) -> str | None:
    for key in metadata:
        name = extension_name_from_uri(str(key))
        if name:
            return name
    return None


def extract_request_attributes(input_obj: Any, *, method: str) -> dict[str, AttributeValue]:
    """Extract automatic A2A-T attributes from a client-side request object; never raises."""
    attrs: dict[str, AttributeValue] = {ATTR_GEN_AI_OPERATION_NAME: method}
    try:
        message = _safe_getattr(input_obj, "message")
        metadata = normalize_metadata(_safe_getattr(message, "metadata")) if message is not None else {}
        if message is not None:
            conversation_id = _safe_getattr(message, "context_id")
            if isinstance(conversation_id, str) and conversation_id:
                attrs[ATTR_GEN_AI_CONVERSATION_ID] = conversation_id
            task_id = _safe_getattr(message, "task_id")
            if isinstance(task_id, str) and task_id:
                attrs[ATTR_TASK_ID] = task_id
        extension = _extension_name_from_metadata(metadata)
        if extension:
            attrs[ATTR_EXTENSION_NAME] = extension
        attrs.update(extract_negotiation_attributes(metadata))
        push_url = _safe_getattr(input_obj, "url")
        if not isinstance(push_url, str) or not push_url:
            push_config = _safe_getattr(input_obj, "push_notification_config")
            push_url = _safe_getattr(push_config, "url")
        if isinstance(push_url, str) and push_url:
            attrs[ATTR_PUSH_NOTIFICATION_URL] = push_url
    except Exception:  # noqa: BLE001
        return {ATTR_GEN_AI_OPERATION_NAME: method}
    return attrs


def metadata_view_for_provider(metadata: dict[str, Any], headers: Mapping[str, str] | None) -> dict[str, Any]:
    """Merged metadata view handed to A2ATObservabilityConfig provider callbacks."""
    view: dict[str, Any] = {}
    if headers is not None:
        for key, value in headers.items():
            view[str(key)] = value
    view.update(metadata)
    return view


@dataclasses.dataclass(slots=True)
class EventInfo:
    """Classified view of one A2A stream event (structural, protocol-agnostic)."""

    kind: str
    task_id: str | None = None
    task_status: str | None = None
    final: bool = False
    is_terminal: bool = False
    message_metadata: dict[str, Any] | None = None
    artifact_name: str | None = None


_TERMINAL_STATES = frozenset(
    {"TASK_STATE_COMPLETED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED", "TASK_STATE_REJECTED"}
)
_STREAM_RESPONSE_FIELDS = ("status_update", "message", "artifact_update", "task")


def _enum_field_name(message: Any, field_name: str, value: Any) -> str | None:
    """Resolve a protobuf enum int to its name via structural DESCRIPTOR access."""
    if isinstance(value, str):
        return value
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    try:
        descriptor = message.DESCRIPTOR
        enum_type = descriptor.fields_by_name[field_name].enum_type
        return cast("str | None", enum_type.values_by_number[value].name)
    except Exception:  # noqa: BLE001
        return None


def _normalize_state_name(enum_name: str | None) -> str | None:
    if enum_name is None:
        return None
    prefix = "TASK_STATE_"
    return enum_name[len(prefix) :].lower() if enum_name.startswith(prefix) else enum_name.lower()


#: Spec §3.7 log unification: event-time logs fire only for status-changed and artifact
#: events; message/task/unknown kinds stay silent (entry-point logs cover messages).
#: Derived from _TERMINAL_STATES (normalized state names) plus the status/artifact kinds.
EVENT_LOG_KINDS: frozenset[str] = frozenset(
    cast("str", _normalize_state_name(state)) for state in _TERMINAL_STATES
) | {"status", "artifact"}


def classify_event(event: Any) -> EventInfo:
    """Classify one A2A event into EventInfo; never raises."""
    try:
        status = _safe_getattr(event, "status")
        task_id = _safe_getattr(event, "task_id")
        if status is not None and task_id is not None:
            enum_name = _enum_field_name(status, "state", status.state)
            state_name = _normalize_state_name(enum_name)
            terminal_state = enum_name in _TERMINAL_STATES
            final = bool(_safe_getattr(event, "final"))
            kind: str = "status"
            if terminal_state and state_name:
                kind = state_name
            return EventInfo(
                kind=kind,
                task_id=str(task_id) if task_id else None,
                task_status=state_name,
                final=final,
                is_terminal=terminal_state or final,
            )
        artifact = _safe_getattr(event, "artifact")
        if artifact is not None and task_id is not None:
            artifact_name = _safe_getattr(artifact, "name")
            return EventInfo(
                kind="artifact",
                task_id=str(task_id) if task_id else None,
                artifact_name=artifact_name if isinstance(artifact_name, str) and artifact_name else None,
            )
        if _safe_getattr(event, "parts") is not None and _safe_getattr(event, "role") is not None:
            event_task_id = _safe_getattr(event, "task_id")
            return EventInfo(
                kind="message",
                task_id=str(event_task_id) if event_task_id else None,
                message_metadata=normalize_metadata(_safe_getattr(event, "metadata")),
            )
        if status is not None and _safe_getattr(event, "id"):
            enum_name = _enum_field_name(status, "state", status.state)
            return EventInfo(
                kind="task",
                task_id=str(event.id),
                task_status=_normalize_state_name(enum_name),
                is_terminal=enum_name in _TERMINAL_STATES,
            )
    except Exception:  # noqa: BLE001
        return EventInfo(kind="unknown")
    return EventInfo(kind="unknown")


def unwrap_stream_response(result: Any) -> Any | None:
    """Unwrap a StreamResponse-like oneof into the inner event; None when not stream-shaped.

    Field probes are per-field tolerant: wrapper shapes that share only part of
    the probed oneof names (e.g. SendMessageResponse has message/task but no
    status_update) must still unwrap instead of failing the whole probe.
    """
    try:
        has_field = getattr(result, "HasField", None)
        if not callable(has_field):
            return None
        for field in _STREAM_RESPONSE_FIELDS:
            try:
                if result.HasField(field):
                    return getattr(result, field)
            except ValueError:
                continue  # field absent on this wrapper shape; probe the next one
    except Exception:  # noqa: BLE001
        return None
    return None


def _write_request_attributes(span: Any, message: Any) -> None:
    metadata = normalize_metadata(_safe_getattr(message, "metadata"))
    _write_metadata_attributes(span, metadata)
    task_id = _safe_getattr(message, "task_id")
    if isinstance(task_id, str) and task_id:
        span.set_attribute(ATTR_TASK_ID, task_id)


def _write_metadata_attributes(span: Any, metadata: dict[str, Any]) -> None:
    extension = _extension_name_from_metadata(metadata)
    if extension:
        span.set_attribute(ATTR_EXTENSION_NAME, extension)
    for key, value in extract_negotiation_attributes(metadata).items():
        span.set_attribute(key, value)


def _write_event_attributes(span: Any, event: Any) -> bool:
    """Classify one event candidate and write its span attributes; False when unclassifiable."""
    info = classify_event(event)
    if info.kind == "unknown":
        return False
    span.set_attribute(ATTR_STREAMING_EVENT_KIND, info.kind)
    if info.task_id:
        span.set_attribute(ATTR_TASK_ID, info.task_id)
    if info.task_status:
        span.set_attribute(ATTR_TASK_STATUS, info.task_status)
    if info.message_metadata:
        _write_metadata_attributes(span, info.message_metadata)
    return True


def a2at_attribute_extractor(
    span: Any, args: tuple[Any, ...], kwargs: dict[str, Any], result: Any, exception: Any
) -> None:
    """attribute_extractor callback for a2a-python's @trace_function; writes A2A-T attributes.

    Real protobuf message-typed oneof fields return a default instance (never None) when
    unset, so StreamResponse shapes MUST be probed before the ``.message`` request branch —
    otherwise a StreamResponse carrying e.g. status_update is misread as a plain request.
    """
    for candidate in (*args, result):
        if candidate is None:
            continue
        unwrapped = unwrap_stream_response(candidate)
        if unwrapped is not None:
            # Stream-shaped candidate: event branch only (never probe .message — that hits
            # the default instance of an unset message-typed field).
            if _write_event_attributes(span, unwrapped):
                return
            continue
        message = _safe_getattr(candidate, "message")
        if message is not None:
            _write_request_attributes(span, message)
            return
        if _safe_getattr(candidate, "parts") is not None and _safe_getattr(candidate, "role") is not None:
            _write_request_attributes(span, candidate)
            return
        if _write_event_attributes(span, candidate):
            return
