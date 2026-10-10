"""A2ATSpan: manual span API (context manager) for user-created spans; NoOp when OTel is off."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from a2a_t.observability import _otel_compat

_VALID_KINDS = frozenset({"INTERNAL", "CLIENT", "SERVER", "PRODUCER", "CONSUMER"})


class A2ATSpan:
    """Manual A2A-T span. Absorbs all calls when OpenTelemetry is unavailable."""

    def __init__(
        self,
        name: str,
        *,
        kind: str = "INTERNAL",
        attributes: Mapping[str, str | int | bool] | None = None,
        context: Any | None = None,
    ) -> None:
        if kind not in _VALID_KINDS:
            raise ValueError(f"invalid span kind: {kind}")
        self._name = name
        self._kind = kind
        self._attributes = dict(attributes) if attributes else {}
        self._context = context
        self._links: list[Any] = []
        self._span: Any = None
        self._cm: Any = None
        self._ended = False

    def _start(self) -> Any:
        if not _otel_compat.is_enabled():
            return None
        tracer: Any = _otel_compat.get_tracer()
        kwargs: dict[str, Any] = {}
        span_kind = getattr(_otel_compat.SpanKind, self._kind, None)
        if span_kind is not None:
            kwargs["kind"] = span_kind
        if self._links:
            kwargs["links"] = list(self._links)
        if self._context is not None:
            kwargs["context"] = self._context
        self._cm = tracer.start_as_current_span(self._name, **kwargs)
        self._span = self._cm.__enter__()
        if self._attributes:
            self._span.set_attributes(self._attributes)
        return self._span

    def _end(self, exc: BaseException | None) -> None:
        if self._span is None or self._ended:
            return
        self._ended = True
        try:
            if exc is not None:
                self._span.record_exception(exc)
                error = getattr(_otel_compat.StatusCode, "ERROR", None)
                if error is not None:
                    self._span.set_status(error, str(exc))
            else:
                ok = getattr(_otel_compat.StatusCode, "OK", None)
                if ok is not None:
                    self._span.set_status(ok)
        finally:
            cm, self._cm = self._cm, None
            cm.__exit__(None, None, None)

    def __enter__(self) -> A2ATSpan:
        if self._cm is not None or self._ended:
            raise RuntimeError("A2ATSpan is single-use; create a new instance for each span")
        self._start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._end(exc_val)

    def set_attribute(self, key: str, value: str | int | bool) -> None:
        if self._span is not None:
            self._span.set_attribute(key, value)

    def add_link(self, other: A2ATSpan) -> None:
        """Link another A2ATSpan. Before enter: applied at creation; after enter: span.add_link."""
        if other._span is None:
            return
        other_context = other._span.get_span_context()
        if other_context is None:
            return
        if self._span is not None:
            add_link = getattr(self._span, "add_link", None)
            if callable(add_link):
                add_link(other_context)
            return
        if _otel_compat.is_enabled():
            from opentelemetry.trace import Link

            self._links.append(Link(other_context))
