"""ADK tracing of the RAN energy-saving sample (Run Explorer design §5.2).

The ADK agents of both sides run through this module:

* :class:`TracingLlm` wraps a ``BaseLlm`` (the deterministic fake model or ``LiteLlm``) and emits
  one ``llm`` span per model turn with the full request (system instruction, contents, tools,
  response schema, generation params) and response (text, function calls, usage, finish reason,
  errors) plus latency. It delegates ``model``, ``capabilities`` and ``generate_content_async`` and
  keeps **no per-call state on the instance**: ADK shares one model instance across sessions.
* :class:`TracePlugin` is an ADK ``BasePlugin`` that turns the tool callbacks into ``tool`` spans
  (name, args, result, duration, error). It is keyed by function-call id, not by instance state,
  so one plugin safely serves concurrent sessions, and it links each tool span to the last LLM
  span of the same invocation through :class:`SpanLinker`.

Everything is best effort: serialization tolerates ``None`` (the fake model) and arbitrary
pydantic objects, secrets are redacted by :func:`~shared.trace.record_span`, and a tracing failure
never changes the agent result or swallows an agent error.
"""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from shared.trace import (
    PHASE_EXECUTION,
    current_trace,
    elapsed_ms,
    phase_for_stage,
    record_span,
)

#: Default plugin name registered on both ADK runners.
TRACE_PLUGIN_NAME: Final[str] = "ran_energy_run_explorer_trace"

#: Maximum depth :func:`json_safe` descends into nested objects.
_MAX_JSON_DEPTH: Final[int] = 8


def _now_iso() -> str:
    """Return the current UTC time in ISO 8601 form."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _context_key() -> str:
    """Return the correlation key used to link LLM and tool spans of one invocation."""
    trace = current_trace()
    return trace.context_id or trace.task_id or f"thread-{threading.get_ident()}"


def _role() -> str:
    """Return the producer side (``client`` / ``server``) of the current trace context."""
    return current_trace().role or "client"


def _span_prefix(kind: str) -> str:
    """Return a role-scoped, cross-process unique producer id namespace."""
    return f"{kind}_{_role()}_adk"


def json_safe(value: object, depth: int = 0) -> object:
    """Convert one arbitrary ADK/genai value into a JSON-serializable, ``None``-tolerant value.

    Args:
        value: Value to convert (pydantic model, enum, mapping, sequence, primitive or ``None``).
        depth: Current recursion depth (nested objects beyond :data:`_MAX_JSON_DEPTH` are stringified).

    Returns:
        A JSON-serializable value; unknown objects fall back to ``str(value)``.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, type):
        # ``LlmAgent(output_schema=Model)`` keeps the class on ``config.response_schema``; LiteLLM
        # converts it with ``model_json_schema()`` before calling the provider, so the trace must
        # render the schema rather than ``<class '...'>``.
        model_json_schema = getattr(value, "model_json_schema", None)
        if callable(model_json_schema):
            try:
                return json_safe(model_json_schema(), depth + 1)
            except Exception:  # noqa: BLE001 - serialization must never break the agent
                return getattr(value, "__name__", str(value))
        return getattr(value, "__name__", str(value))
    if depth >= _MAX_JSON_DEPTH:
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item, depth + 1) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return json_safe(model_dump(mode="json", exclude_none=True), depth + 1)
        except Exception:  # noqa: BLE001 - serialization must never break the agent
            return str(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return json_safe(to_dict(), depth + 1)
        except Exception:  # noqa: BLE001
            return str(value)
    return str(value)


def _content_text(content: types.Content | None) -> str | None:
    """Return the concatenated text of one content, ``None`` when absent."""
    if content is None:
        return None
    texts: list[str] = []
    for part in content.parts or ():
        text = getattr(part, "text", None)
        if isinstance(text, str):
            texts.append(text)
    joined = "".join(texts)
    return joined or None


def serialize_content(content: types.Content | None) -> dict[str, object] | None:
    """Serialize one genai ``Content`` (text and function call/response parts)."""
    if content is None:
        return None
    parts: list[dict[str, object]] = []
    for part in content.parts or ():
        rendered: dict[str, object] = {}
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            rendered["text"] = text
        if getattr(part, "thought", None):
            rendered["thought"] = True
        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            rendered["functionCall"] = {
                "name": getattr(function_call, "name", None),
                "args": json_safe(getattr(function_call, "args", None)),
                "id": getattr(function_call, "id", None),
            }
        function_response = getattr(part, "function_response", None)
        if function_response is not None:
            rendered["functionResponse"] = {
                "name": getattr(function_response, "name", None),
                "response": json_safe(getattr(function_response, "response", None)),
                "id": getattr(function_response, "id", None),
            }
        if not rendered:
            rendered["type"] = type(part).__name__
        parts.append(rendered)
    return {"role": getattr(content, "role", None), "parts": parts}


def _serialize_system_instruction(value: object) -> object:
    """Serialize one system instruction (plain string, ``Content`` or ``None``)."""
    if isinstance(value, types.Content):
        return serialize_content(value)
    return json_safe(value)


def _serialize_tools(tools: Sequence[object] | None) -> list[dict[str, object]]:
    """Serialize the tool declarations of one request."""
    rendered: list[dict[str, object]] = []
    for tool in tools or ():
        declarations = getattr(tool, "function_declarations", None)
        if not declarations:
            rendered.append({"tool": type(tool).__name__})
            continue
        for declaration in declarations:
            # ADK 2.x emits the JSON-schema form; older declarations carry the schema object.
            parameters = getattr(declaration, "parameters", None) or getattr(
                declaration, "parameters_json_schema", None
            )
            rendered.append(
                {
                    "name": getattr(declaration, "name", None),
                    "description": getattr(declaration, "description", None),
                    "parameters": json_safe(parameters) if parameters is not None else None,
                }
            )
    return rendered


def serialize_llm_request(llm_request: LlmRequest) -> dict[str, object]:
    """Serialize one ``LlmRequest`` (contents, system instruction, tools, schema, params)."""
    config = llm_request.config
    return {
        "model": llm_request.model,
        "contents": [serialize_content(content) for content in llm_request.contents],
        "systemInstruction": _serialize_system_instruction(config.system_instruction),
        "tools": _serialize_tools(config.tools),
        "responseSchema": json_safe(config.response_schema),
        "temperature": config.temperature,
        "topP": config.top_p,
        "maxOutputTokens": config.max_output_tokens,
        "labels": json_safe(config.labels),
    }


def _safe_serialize_request(llm_request: LlmRequest) -> dict[str, object] | None:
    """Serialize one request, returning ``None`` (span dropped) when serialization fails."""
    try:
        return serialize_llm_request(llm_request)
    except Exception:  # noqa: BLE001 - tracing must never change model behavior
        return None


def _content_tokens(usage: types.GenerateContentResponseUsageMetadata | None) -> dict[str, object] | None:
    """Render the token usage of one ADK response."""
    if usage is None:
        return None
    prompt = usage.prompt_token_count
    completion = usage.candidates_token_count
    total = usage.total_token_count
    if total is None and isinstance(prompt, int) and isinstance(completion, int):
        total = prompt + completion
    cached = getattr(usage, "cached_content_token_count", None)
    return {
        "prompt": prompt,
        "completion": completion,
        "total": total,
        "reasoning": usage.thoughts_token_count,
        "cached": cached if isinstance(cached, int) and not isinstance(cached, bool) else None,
        "estimated": False,
    }


def serialize_llm_response(response: LlmResponse | None) -> dict[str, object] | None:
    """Serialize one ``LlmResponse`` (content, function calls, usage, finish reason, errors)."""
    if response is None:
        return None
    calls: list[dict[str, object]] = []
    for call in response.get_function_calls():
        calls.append(
            {
                "name": getattr(call, "name", None),
                "args": json_safe(getattr(call, "args", None)),
                "id": getattr(call, "id", None),
            }
        )
    finish_reason = response.finish_reason
    return {
        "text": _content_text(response.content),
        "content": serialize_content(response.content),
        "functionCalls": calls,
        "partial": response.partial,
        "finishReason": getattr(finish_reason, "name", None) if finish_reason is not None else None,
        "errorCode": response.error_code,
        "errorMessage": response.error_message,
        "usage": _content_tokens(response.usage_metadata),
        "modelVersion": response.model_version,
    }


class SpanLinker:
    """Thread-safe map of the last LLM span id of each invocation (tool-parent linking)."""

    def __init__(self) -> None:
        """Create one empty linker."""
        self._lock = threading.Lock()
        self._last: dict[str, str] = {}

    def set_last_llm(self, key: str, span_id: str) -> None:
        """Remember the most recent LLM span id of one invocation key."""
        with self._lock:
            self._last[key] = span_id

    def last_llm(self, key: str) -> str | None:
        """Return the most recent LLM span id of one invocation key, when known."""
        with self._lock:
            return self._last.get(key)

    def clear(self, key: str) -> None:
        """Forget one invocation key."""
        with self._lock:
            self._last.pop(key, None)


#: Linker shared by the default ``TracingLlm`` and ``TracePlugin`` instances of one process.
SHARED_LINKER: Final[SpanLinker] = SpanLinker()


class TracingLlm(BaseLlm):
    """``BaseLlm`` decorator emitting one ``llm`` span per model turn.

    The wrapped model is the deterministic fake model in offline mode and ``LiteLlm`` in live mode.
    ``model`` and ``capabilities`` are delegated, and no per-call state is stored on the instance
    (ADK shares one model across sessions).
    """

    inner: Any
    linker: Any = SHARED_LINKER

    @classmethod
    def wrap(cls, model: BaseLlm, *, linker: SpanLinker | None = None) -> TracingLlm:
        """Wrap one ADK model, delegating ``model``/``capabilities`` to it."""
        resolved_linker = linker or SHARED_LINKER
        return cls(model=model.model, inner=model, linker=resolved_linker)

    @property
    def capabilities(self) -> LlmCapabilities:
        """Return the capabilities of the wrapped model."""
        return self.inner.capabilities  # type: ignore[no-any-return]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        """Delegate to the wrapped model and emit one span per complete response.

        Tracing is an observer: serialization and emission are guarded, so a broken serializer drops
        the span instead of changing what the model returns to ADK.
        """
        started = time.perf_counter()
        started_at = _now_iso()
        stage = current_trace().stage
        request_detail = _safe_serialize_request(llm_request)
        last: LlmResponse | None = None
        emitted = False
        try:
            async for response in self.inner.generate_content_async(llm_request, stream=stream):
                last = response
                if not response.partial:
                    self._emit_guarded(stage, request_detail, response, started, started_at)
                    emitted = True
                yield response
        except Exception as exc:
            self._emit_guarded(
                stage,
                request_detail,
                None,
                started,
                started_at,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        if not emitted and last is not None:
            self._emit_guarded(stage, request_detail, last, started, started_at)

    def _emit_guarded(
        self,
        stage: str | None,
        request_detail: Mapping[str, object] | None,
        response: LlmResponse | None,
        started: float,
        started_at: str,
        *,
        error: str | None = None,
    ) -> None:
        """Emit one span; a failed request serialization or emission drops it silently."""
        if request_detail is None:
            return
        try:
            self._emit(stage, request_detail, response, started, started_at, error=error)
        except Exception:  # noqa: BLE001 - tracing must never change model behavior
            return

    def _emit(
        self,
        stage: str | None,
        request_detail: Mapping[str, object],
        response: LlmResponse | None,
        started: float,
        started_at: str,
        *,
        error: str | None = None,
    ) -> None:
        """Emit one ``llm`` span (the model instance stays stateless)."""
        response_detail = serialize_llm_response(response)
        model = None
        tokens = None
        if response_detail is not None:
            model_version = response_detail.get("modelVersion")
            model = model_version if isinstance(model_version, str) else None
            usage = response_detail.get("usage")
            tokens = usage if isinstance(usage, Mapping) else None
        payload = record_span(
            kind="llm",
            phase=phase_for_stage(stage, default=PHASE_EXECUTION),
            title=f"LLM call · {stage or 'adk_agent'}",
            purpose=f"ADK model turn of stage {stage or 'adk_agent'}",
            prefix=_span_prefix("llm"),
            stage=stage,
            status="error" if error is not None else "ok",
            model=model or self.model,
            tokens=tokens,
            error=error,
            detail={"request": dict(request_detail), "response": response_detail},
            started_at=started_at,
            ended_at=_now_iso(),
            duration_ms=elapsed_ms(started),
        )
        if error is None:
            span_id = payload.get("spanId")
            if isinstance(span_id, str):
                self.linker.set_last_llm(_context_key(), span_id)


@dataclass
class _PendingToolCall:
    """State of one in-flight tool call (keyed by function-call id in the plugin)."""

    name: str
    args: dict[str, object]
    context_key: str
    started: float
    started_at: str = field(default_factory=_now_iso)


class TracePlugin(BasePlugin):
    """ADK plugin emitting one ``tool`` span per tool call (args, result, duration, error).

    Pending calls are kept in a lock-protected map keyed by ``context key + function-call id``, so
    a single plugin instance is safe for concurrent sessions. The callbacks always return ``None``:
    the tool arguments, the tool result and any raised error are left untouched.
    """

    def __init__(self, *, linker: SpanLinker | None = None, name: str = TRACE_PLUGIN_NAME) -> None:
        """Create one tool-tracing plugin."""
        super().__init__(name=name)
        self._linker = linker or SHARED_LINKER
        self._pending: dict[str, _PendingToolCall] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(tool: BaseTool, tool_context: ToolContext) -> str:
        """Return the pending-call key of one invocation."""
        function_call_id = getattr(tool_context, "function_call_id", None)
        return f"{_context_key()}:{function_call_id or tool.name}"

    def _pop(self, key: str) -> _PendingToolCall | None:
        """Remove and return one pending call."""
        with self._lock:
            return self._pending.pop(key, None)

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        """Remember the start time and arguments of one tool call."""
        rendered_args = json_safe(tool_args)
        args = rendered_args if isinstance(rendered_args, dict) else {"args": rendered_args}
        with self._lock:
            self._pending[self._key(tool, tool_context)] = _PendingToolCall(
                name=tool.name,
                args=args,
                context_key=_context_key(),
                started=time.perf_counter(),
            )
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Emit one ``ok`` tool span; the result is returned unchanged."""
        pending = self._pop(self._key(tool, tool_context))
        self._emit(
            pending,
            tool_name=tool.name,
            tool_args=tool_args,
            result=json_safe(result),
            error=None,
            stage=current_trace().stage,
        )
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> dict[str, Any] | None:
        """Emit one ``error`` tool span; the error still propagates."""
        pending = self._pop(self._key(tool, tool_context))
        self._emit(
            pending,
            tool_name=tool.name,
            tool_args=tool_args,
            result=None,
            error=f"{type(error).__name__}: {error}",
            stage=current_trace().stage,
        )
        return None

    def _emit(
        self,
        pending: _PendingToolCall | None,
        *,
        tool_name: str,
        tool_args: Mapping[str, object],
        result: object,
        error: str | None,
        stage: str | None,
    ) -> None:
        """Emit one tool span (a missing ``before`` still produces a usable span)."""
        context_key = pending.context_key if pending is not None else _context_key()
        args = pending.args if pending is not None else json_safe(tool_args)
        started = pending.started if pending is not None else time.perf_counter()
        started_at = pending.started_at if pending is not None else _now_iso()
        record_span(
            kind="tool",
            phase=phase_for_stage(stage, default=PHASE_EXECUTION),
            title=f"Tool call · {tool_name}",
            purpose=f"ADK tool call of stage {stage or 'adk_agent'}",
            prefix=_span_prefix("tool"),
            actor="tool",
            stage=stage,
            status="error" if error is not None else "ok",
            error=error,
            detail={
                "toolName": tool_name,
                "args": args,
                "result": result,
                "stage": stage,
            },
            parent_span_id=self._linker.last_llm(context_key),
            started_at=started_at,
            ended_at=_now_iso(),
            duration_ms=elapsed_ms(started),
        )


__all__ = [
    "SHARED_LINKER",
    "TRACE_PLUGIN_NAME",
    "SpanLinker",
    "TracePlugin",
    "TracingLlm",
    "json_safe",
    "serialize_content",
    "serialize_llm_request",
    "serialize_llm_response",
]
