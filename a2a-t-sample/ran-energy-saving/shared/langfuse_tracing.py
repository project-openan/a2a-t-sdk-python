"""Best-effort Langfuse export of one client run's Run Explorer spans.

The sample already records every protocol, intent, approval, LLM and tool event as one span in
:mod:`shared.trace`. This module replays that stream into Langfuse instead of adding a second
instrumentation layer:

* one Langfuse trace per client run (``session.id`` = conversation, ``user.id`` = caller);
* one child span per Run Explorer phase, and one more per negotiation round;
* ``llm`` spans become ``generation`` observations with model and token usage;
* ``tool`` spans become ``tool`` observations;
* every other span stays a generic observation.

The exporter never changes the protocol flow. Langfuse is optional: without the keys, without
the package, or in offline mode it is a no-op, and every export failure is swallowed into a
warning that the run snapshot can display.

Python SDK v5's high-level ``start_observation`` uses the current time and cannot replay a finished
span, so the exporter talks to OpenTelemetry directly and passes the recorded ``startedAt`` /
``endedAt`` timestamps. The ``langfuse.*`` attribute names are the SDK's public ingestion contract.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlsplit

from shared.config import SampleConfig

_LOGGER = logging.getLogger("ran_energy_saving.langfuse")

#: Instrumentation scope of the exporter. Langfuse v5 filters unknown scopes by default, so the
#: provider is configured with ``should_export_span`` allowing exactly this scope.
LANGFUSE_SCOPE_NAME: Final[str] = "ran_energy_saving.trace"

#: Langfuse OTel attribute contract (``langfuse/_client/attributes.py`` of the v5 SDK).
TRACE_NAME_ATTRIBUTE: Final[str] = "langfuse.trace.name"
TRACE_METADATA_PREFIX: Final[str] = "langfuse.trace.metadata."
TRACE_SESSION_ATTRIBUTE: Final[str] = "session.id"
TRACE_USER_ATTRIBUTE: Final[str] = "user.id"
OBSERVATION_TYPE_ATTRIBUTE: Final[str] = "langfuse.observation.type"
OBSERVATION_INPUT_ATTRIBUTE: Final[str] = "langfuse.observation.input"
OBSERVATION_OUTPUT_ATTRIBUTE: Final[str] = "langfuse.observation.output"
OBSERVATION_METADATA_PREFIX: Final[str] = "langfuse.observation.metadata."
OBSERVATION_LEVEL_ATTRIBUTE: Final[str] = "langfuse.observation.level"
OBSERVATION_STATUS_MESSAGE_ATTRIBUTE: Final[str] = "langfuse.observation.status_message"
OBSERVATION_MODEL_ATTRIBUTE: Final[str] = "langfuse.observation.model.name"
OBSERVATION_USAGE_ATTRIBUTE: Final[str] = "langfuse.observation.usage_details"
ROOT_ATTRIBUTE: Final[str] = "langfuse.internal.as_root"

#: Observations exported under these span kinds.
OBSERVATION_GENERATION: Final[str] = "generation"
OBSERVATION_TOOL: Final[str] = "tool"
OBSERVATION_SPAN: Final[str] = "span"

#: Span kinds whose detail payload is already carried by input/output.
_PAYLOAD_KINDS: Final[frozenset[str]] = frozenset({"llm", "tool"})

#: Decision span kinds recorded by ``RunState.record_decision``.
_DECISION_KINDS: Final[frozenset[str]] = frozenset({"accept", "counter", "reject", "abort"})

#: Fields copied into the structural observation metadata.
_STRUCTURAL_FIELDS: Final[tuple[str, ...]] = (
    "kind",
    "phase",
    "actor",
    "side",
    "direction",
    "round",
    "stage",
    "status",
    "seq",
    "spanId",
    "parentSpanId",
    "contextId",
    "taskId",
    "runId",
    "purpose",
)

try:  # pragma: no cover - exercised by the import-guard test
    from langfuse import Langfuse
    from opentelemetry.context import Context
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import format_span_id, format_trace_id, set_span_in_context

    LANGFUSE_AVAILABLE = True
except ImportError:  # pragma: no cover - default test environment installs the SDK
    Langfuse = None  # type: ignore[assignment,misc]
    Context = None  # type: ignore[assignment,misc]
    Resource = None  # type: ignore[assignment,misc]
    TracerProvider = None  # type: ignore[assignment,misc]
    format_span_id = None  # type: ignore[assignment]
    format_trace_id = None  # type: ignore[assignment]
    set_span_in_context = None  # type: ignore[assignment]
    LANGFUSE_AVAILABLE = False


@dataclass(frozen=True)
class LangfuseRunMetadata:
    """Run-level values of one Langfuse trace."""

    run_id: str
    scenario: str
    conversation_id: str | None = None
    user_id: str | None = None
    language: str = ""
    mode: str = ""
    protocol: str = ""
    transport: str = ""
    discovery: str = ""
    intent_mode: str = ""
    model_mode: str = ""
    model_label: str = ""


@dataclass
class _LangfuseRuntime:
    """Process-wide Langfuse client, provider and tracer shared by every run."""

    client: Any
    provider: Any
    tracer: Any


_RUNTIME: _LangfuseRuntime | None = None
_RUNTIME_LOCK = threading.Lock()


def _should_export_span(span: Any) -> bool:
    """Allow exactly the exporter's instrumentation scope (Langfuse filters unknown scopes)."""
    scope = getattr(span, "instrumentation_scope", None)
    return scope is not None and getattr(scope, "name", None) == LANGFUSE_SCOPE_NAME


def _get_runtime(config: SampleConfig) -> _LangfuseRuntime | None:
    """Return the process-wide Langfuse runtime, creating it once."""
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME
    if not LANGFUSE_AVAILABLE:
        return None
    with _RUNTIME_LOCK:
        if _RUNTIME is not None:
            return _RUNTIME
        provider = TracerProvider(resource=Resource.create({"service.name": "ran-energy-saving-sample"}))
        client = Langfuse(
            public_key=config.langfuse_public_key,
            secret_key=config.langfuse_secret_key,
            base_url=config.langfuse_base_url,
            environment=config.langfuse_environment or None,
            tracer_provider=provider,
            should_export_span=_should_export_span,
            flush_at=32,
            flush_interval=1.0,
        )
        tracer = provider.get_tracer(LANGFUSE_SCOPE_NAME)
        _RUNTIME = _LangfuseRuntime(client=client, provider=provider, tracer=tracer)
        return _RUNTIME


def create_langfuse_exporter(
    config: SampleConfig,
    metadata: LangfuseRunMetadata,
) -> "LangfuseSpanExporter | None":
    """Build the exporter of one run, or ``None`` when Langfuse is disabled.

    Langfuse is active when both keys are configured and the run is live. Offline runs use the
    deterministic model whose token usage is not real, so they stay out of the dashboard.
    """
    if not config.langfuse_configured:
        return None
    if config.is_offline:
        _LOGGER.info("Langfuse export skipped: offline (scripted) runs are not exported")
        return None
    if not LANGFUSE_AVAILABLE:
        _LOGGER.warning("LANGFUSE_* is configured but the langfuse package is not installed; export disabled")
        return None
    try:
        runtime = _get_runtime(config)
        if runtime is None:
            return None
        return LangfuseSpanExporter(runtime=runtime, config=config, metadata=metadata)
    except Exception as exc:  # noqa: BLE001 - observability must never break the run
        _LOGGER.warning("Langfuse initialization failed: %s: %s", type(exc).__name__, exc)
        return None


def _current_parent_context() -> Any:
    """Return the current OTel context when it carries a valid span, else an empty context.

    The UI adopts an incoming ``traceparent`` (see ``TracePropagationMiddleware`` on the Web UI),
    so a run started by an instrumented caller joins that caller's trace instead of opening an
    unrelated one. A browser request has no parent and keeps the historical behavior.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is not None and span.get_span_context().is_valid:
            return trace.set_span_in_context(span)
    except Exception:  # noqa: BLE001 - observability must never break the run
        _LOGGER.debug("current OTel parent lookup failed", exc_info=True)
    return Context()


class LangfuseSpanExporter:
    """Translate the Run Explorer span stream of one run into Langfuse observations."""

    def __init__(self, *, runtime: _LangfuseRuntime, config: SampleConfig, metadata: LangfuseRunMetadata) -> None:
        """Create the run root span and remember the trace id."""
        self._runtime = runtime
        self._config = config
        self._metadata = metadata
        self._lock = threading.Lock()
        self._closed = False
        self._finished = False
        self._status = "exporting"
        self._warning: str | None = None
        self._exported = 0
        self._phase_spans: dict[str, Any] = {}
        self._round_spans: dict[int, Any] = {}
        self._span_map: dict[str, Any] = {}
        self._trace_url: str | None = None
        started = _now_ns()
        self._root = runtime.tracer.start_span(
            _run_span_name(metadata),
            context=_current_parent_context(),
            start_time=started,
            attributes=_root_attributes(metadata),
        )
        root_context = self._root.get_span_context()
        self._trace_id = format_trace_id(root_context.trace_id)
        self._root_span_id = format_span_id(root_context.span_id)
        # The root stays open for the whole run, so its trace-level attributes would arrive only
        # at run end. Export one zero-length child immediately: it is the trace's first
        # observation, so Langfuse names the trace and fills session/user from the start.
        bootstrap = runtime.tracer.start_span(
            "trace opened",
            context=set_span_in_context(self._root),
            start_time=started,
            attributes={
                key: value
                for key, value in _root_attributes(metadata).items()
                if key != ROOT_ATTRIBUTE
            }
            | {OBSERVATION_TYPE_ATTRIBUTE: "event"},
        )
        bootstrap.end(end_time=started)

    @property
    def trace_id(self) -> str:
        """Return the Langfuse trace id (the OTel trace id)."""
        return self._trace_id

    @property
    def root_span_id(self) -> str:
        """Return the root span id, used as the parent of outgoing server hops."""
        return self._root_span_id

    def __call__(self, span: Mapping[str, object]) -> None:
        """Export one completed Run Explorer span; failures become a snapshot warning.

        Spans that arrive after :meth:`finish` (the local demo's server trace emitter flushes in
        batches) are still exported under the closed root; the Langfuse atexit handler sends them.
        """
        if self._closed:
            return
        try:
            with self._lock:
                self._export(span)
        except Exception as exc:  # noqa: BLE001 - observability must never break the run
            self._warning = f"{type(exc).__name__}: {exc}"
            _LOGGER.warning("Langfuse span export failed: %s", exc)

    def finish(self, *, result: Any | None = None, error: str | None = None) -> None:
        """Close every open phase/round span, write the run result and flush the client."""
        with self._lock:
            if self._closed or self._finished:
                return
            ended = _now_ns()
            for span in self._round_spans.values():
                span.end(end_time=ended)
            for span in self._phase_spans.values():
                span.end(end_time=ended)
            payload = _final_payload(result, error)
            if payload:
                self._root.set_attribute(OBSERVATION_OUTPUT_ATTRIBUTE, _json_value(payload))
            if error:
                self._root.set_attribute(OBSERVATION_LEVEL_ATTRIBUTE, "ERROR")
                self._root.set_attribute(OBSERVATION_STATUS_MESSAGE_ATTRIBUTE, error[:2048])
            self._root.end(end_time=ended)
            self._finished = True
        try:
            self._runtime.client.flush()
            self._status = "exported"
        except Exception as exc:  # noqa: BLE001 - the run already finished successfully
            self._status = "failed"
            self._warning = f"{type(exc).__name__}: {exc}"
            _LOGGER.warning("Langfuse flush failed: %s", exc)

    def snapshot(self, *, resolve_url: bool = False) -> dict[str, object]:
        """Return the Langfuse state shown by the run snapshot and the presentation UI."""
        with self._lock:
            if resolve_url and self._trace_url is None:
                try:
                    sdk_url = self._runtime.client.get_trace_url(trace_id=self._trace_id)
                    self._trace_url = _ui_trace_url(sdk_url, self._config.langfuse_ui_base_url)
                except Exception as exc:  # noqa: BLE001 - the trace id is still useful
                    self._warning = self._warning or f"{type(exc).__name__}: {exc}"
            return {
                "enabled": True,
                "traceId": self._trace_id,
                "url": self._trace_url,
                "status": self._status,
                "warning": self._warning,
                "exportedSpans": self._exported,
            }

    def _export(self, span: Mapping[str, object]) -> None:
        """Create and immediately end one OTel observation from one recorded span."""
        phase = _as_str(span.get("phase")) or "dispatch"
        started = _iso_to_ns(span.get("startedAt"), default=_now_ns())
        ended = _span_end_ns(span, started)
        parent_context = self._parent_context(span, phase, started)
        observation = self._runtime.tracer.start_span(
            _observation_name(span),
            context=parent_context,
            start_time=started,
            attributes=self._attributes(span),
        )
        observation.end(end_time=ended)
        span_id = span.get("spanId")
        if isinstance(span_id, str) and span_id:
            self._span_map[span_id] = observation
        self._exported += 1

    def _parent_context(self, span: Mapping[str, object], phase: str, started: int) -> Any:
        """Attach one span to its recorded parent, its negotiation round, or its phase."""
        parent_id = span.get("parentSpanId")
        if isinstance(parent_id, str) and parent_id in self._span_map:
            return set_span_in_context(self._span_map[parent_id])
        phase_span = self._phase_spans.get(phase)
        if phase_span is None:
            phase_span = self._runtime.tracer.start_span(
                f"phase · {phase}",
                context=set_span_in_context(self._root),
                start_time=started,
                attributes={OBSERVATION_TYPE_ATTRIBUTE: OBSERVATION_SPAN, "phase": phase},
            )
            self._phase_spans[phase] = phase_span
        round_number = span.get("round")
        if phase == "negotiation" and isinstance(round_number, int) and not isinstance(round_number, bool):
            round_span = self._round_spans.get(round_number)
            if round_span is None:
                round_span = self._runtime.tracer.start_span(
                    f"round {round_number}",
                    context=set_span_in_context(phase_span),
                    start_time=started,
                    attributes={OBSERVATION_TYPE_ATTRIBUTE: OBSERVATION_SPAN, "round": round_number},
                )
                self._round_spans[round_number] = round_span
            return set_span_in_context(round_span)
        return set_span_in_context(phase_span)

    def _attributes(self, span: Mapping[str, object]) -> dict[str, Any]:
        """Render the Langfuse attributes of one recorded span."""
        kind = _as_str(span.get("kind"))
        observation_type = _observation_type(kind)
        attributes: dict[str, Any] = {OBSERVATION_TYPE_ATTRIBUTE: observation_type}
        status = _as_str(span.get("status"), "ok") or "ok"
        error = _as_str(span.get("error"))
        if status == "error" or error:
            attributes[OBSERVATION_LEVEL_ATTRIBUTE] = "ERROR"
            if error:
                attributes[OBSERVATION_STATUS_MESSAGE_ATTRIBUTE] = error[:2048]
        model = _as_str(span.get("model"))
        if observation_type == OBSERVATION_GENERATION and model:
            attributes[OBSERVATION_MODEL_ATTRIBUTE] = model
        usage = _usage_details(span.get("tokens"))
        if observation_type == OBSERVATION_GENERATION and usage:
            attributes[OBSERVATION_USAGE_ATTRIBUTE] = _json_value(usage)
        detail = span.get("detail")
        detail_map = detail if isinstance(detail, Mapping) else {}
        if self._config.langfuse_capture_content:
            input_payload, output_payload = _observation_payload(kind, detail_map)
            if input_payload is not None:
                attributes[OBSERVATION_INPUT_ATTRIBUTE] = _json_value(input_payload)
            if output_payload is not None:
                attributes[OBSERVATION_OUTPUT_ATTRIBUTE] = _json_value(output_payload)
        for key, value in _observation_metadata(
            span,
            detail_map,
            capture_content=self._config.langfuse_capture_content,
        ).items():
            attributes[f"{OBSERVATION_METADATA_PREFIX}{key}"] = _json_value(value)
        return attributes


def _run_span_name(metadata: LangfuseRunMetadata) -> str:
    """Return the root observation name of one run."""
    protocol = metadata.protocol or "a2a"
    return f"run · {metadata.scenario} · {protocol}"


def _root_attributes(metadata: LangfuseRunMetadata) -> dict[str, Any]:
    """Return the trace-level attributes of one run root span."""
    attributes: dict[str, Any] = {
        TRACE_NAME_ATTRIBUTE: f"{metadata.scenario} · {metadata.protocol or 'a2a'}",
        OBSERVATION_TYPE_ATTRIBUTE: OBSERVATION_SPAN,
        ROOT_ATTRIBUTE: True,
    }
    if metadata.conversation_id:
        attributes[TRACE_SESSION_ATTRIBUTE] = metadata.conversation_id
    if metadata.user_id:
        attributes[TRACE_USER_ATTRIBUTE] = metadata.user_id
    trace_metadata = {
        "runId": metadata.run_id,
        "scenario": metadata.scenario,
        "language": metadata.language,
        "mode": metadata.mode,
        "protocol": metadata.protocol,
        "transport": metadata.transport,
        "discovery": metadata.discovery,
        "intentMode": metadata.intent_mode,
        "modelMode": metadata.model_mode,
        "modelLabel": metadata.model_label,
    }
    for key, value in trace_metadata.items():
        if value:
            attributes[f"{TRACE_METADATA_PREFIX}{key}"] = _json_value(value)
    return attributes


def _observation_name(span: Mapping[str, object]) -> str:
    """Return the observation name of one span (its timeline title when present)."""
    title = _as_str(span.get("title"))
    if title:
        return title
    kind = _as_str(span.get("kind"))
    return kind or "event"


def _observation_type(kind: str) -> str:
    """Map one Run Explorer span kind to a Langfuse observation type."""
    if kind == "llm":
        return OBSERVATION_GENERATION
    if kind == "tool":
        return OBSERVATION_TOOL
    return OBSERVATION_SPAN


def _observation_payload(
    kind: str,
    detail: Mapping[str, object],
) -> tuple[object | None, object | None]:
    """Return the input/output payload of one span from its recorded detail."""
    if kind == "llm":
        request = detail.get("request")
        response = detail.get("response")
        input_payload: object | None = request
        if isinstance(request, Mapping) and "messages" in request:
            input_payload = request.get("messages")
        output_payload: object | None = response
        if isinstance(response, Mapping):
            output_payload = response.get("rawContent")
            if output_payload is None:
                output_payload = response.get("content")
            if output_payload is None:
                output_payload = response.get("parsed", response)
        return input_payload, output_payload
    if kind == "tool":
        return detail.get("args"), detail.get("result")
    if kind == "intent_processed":
        return detail.get("rawRequest"), detail.get("normalizedText")
    if kind in _DECISION_KINDS:
        rendered = {
            key: detail.get(key)
            for key in (
                "action",
                "rationale",
                "filledParams",
                "counterProposal",
                "counterParams",
                "confirmedIntent",
                "feasibilitySummary",
                "confirmedBy",
            )
            if detail.get(key) is not None
        }
        return None, rendered or None
    event = detail.get("event")
    if isinstance(event, Mapping):
        return None, dict(event)
    return (
        _first_present(detail, ("input", "request", "promptText", "rawRequest", "content", "text", "message")),
        _first_present(detail, ("output", "result", "normalizedText", "summary", "report")),
    )


def _observation_metadata(
    span: Mapping[str, object],
    detail: Mapping[str, object],
    *,
    capture_content: bool,
) -> dict[str, object]:
    """Return the metadata attributes of one observation."""
    metadata: dict[str, object] = {}
    for key in _STRUCTURAL_FIELDS:
        value = span.get(key)
        if value is not None and value != "":
            metadata[key] = value
    tokens = span.get("tokens")
    if isinstance(tokens, Mapping):
        metadata["tokens"] = dict(tokens)
    if capture_content and _as_str(span.get("kind")) not in _PAYLOAD_KINDS and detail:
        metadata["detail"] = dict(detail)
    return metadata


def _usage_details(tokens: object) -> dict[str, int] | None:
    """Map the sample token map to Langfuse usage details."""
    if not isinstance(tokens, Mapping):
        return None
    usage: dict[str, int] = {}
    for source, target in (
        ("prompt", "input"),
        ("cached", "input_cached"),
        ("completion", "output"),
        ("total", "total"),
    ):
        value = tokens.get(source)
        if isinstance(value, int) and not isinstance(value, bool):
            usage[target] = value
    return usage or None


def _ui_trace_url(url: str | None, ui_base_url: str) -> str | None:
    """Move a Langfuse trace URL onto the browser-facing UI base.

    The SDK builds the URL from its API ``base_url``. When reporting goes through a proxy (the
    yellow-zone setup), that host is not what the browser opens, so the path — project id and trace
    id included — is kept and only the origin is replaced with ``A2AT_SAMPLE_LANGFUSE_UI_BASE_URL``.
    """
    if not url:
        return url
    base = (ui_base_url or "").rstrip("/")
    if not base:
        return url
    parsed = urlsplit(url)
    suffix = parsed.path or ""
    if parsed.query:
        suffix = f"{suffix}?{parsed.query}"
    if parsed.fragment:
        suffix = f"{suffix}#{parsed.fragment}"
    return f"{base}{suffix}"


def _final_payload(result: Any | None, error: str | None) -> dict[str, object] | None:
    """Return the run result written on the root observation."""
    payload: dict[str, object] = {}
    final_state = getattr(result, "final_state", None)
    task_id = getattr(result, "task_id", None)
    report = getattr(result, "report", None)
    if final_state:
        payload["finalState"] = final_state
    if task_id:
        payload["taskId"] = task_id
    if report:
        payload["report"] = report
    if error:
        payload["error"] = error
    return payload or None


def _json_value(value: object) -> str:
    """Serialize one attribute value the way the Langfuse SDK does."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _first_present(mapping: Mapping[str, object], keys: tuple[str, ...]) -> object | None:
    """Return the first non-``None`` value among ``keys``."""
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _as_str(value: object, default: str = "") -> str:
    """Return one value as a string, ``default`` when absent."""
    return value if isinstance(value, str) else default


def _now_ns() -> int:
    """Return the current epoch time in nanoseconds."""
    return time.time_ns()


def _iso_to_ns(value: object, *, default: int) -> int:
    """Parse one ISO 8601 timestamp into epoch nanoseconds, ``default`` when unparseable."""
    if not isinstance(value, str) or not value:
        return default
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1_000_000_000)
    except (OverflowError, ValueError):
        return default


def _span_end_ns(span: Mapping[str, object], started: int) -> int:
    """Return the recorded end time of one span, falling back to its duration."""
    ended = _iso_to_ns(span.get("endedAt"), default=-1)
    if ended >= started:
        return ended
    duration = span.get("durationMs")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
        return started + int(float(duration) * 1_000_000)
    return started


__all__ = [
    "LANGFUSE_AVAILABLE",
    "LANGFUSE_SCOPE_NAME",
    "LangfuseRunMetadata",
    "LangfuseSpanExporter",
    "create_langfuse_exporter",
]
