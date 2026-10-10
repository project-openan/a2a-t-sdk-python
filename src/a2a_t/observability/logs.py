"""Internal structured payload logging (JSON via stdlib logging); no public API by design."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from a2a_t.observability import _otel_compat
from a2a_t.observability.config import A2ATObservabilityConfig
from a2a_t.observability.payload import prepare_payload

logger = logging.getLogger("a2at.observability")


def _current_trace_fields() -> dict[str, str]:
    if not _otel_compat.is_enabled():
        return {}
    span: Any = _otel_compat.get_current_span()
    context = span.get_span_context() if span is not None else None
    if context is None or not getattr(context, "is_valid", False):
        return {}
    return {
        "trace_id": _otel_compat.format_trace_id(context.trace_id),
        "span_id": _otel_compat.format_span_id(context.span_id),
    }


def _prepare_payload(payload: str, config: A2ATObservabilityConfig) -> str | None:
    if not config.resolved_payload_log_enabled:
        return None
    # Shared single-stage preparation (redact → truncate, security order) so the
    # log channel and the span-attribute channel apply identical semantics.
    return prepare_payload(payload, config)


def log_event(
    event: str,
    level: int,
    *,
    fields: Mapping[str, object],
    payload: str | None = None,
    config: A2ATObservabilityConfig | None = None,
) -> None:
    """Emit one structured JSON log record; never raises."""
    if not _otel_compat.is_log_enabled():
        return  # spec §8.1: master or log signal off → no output at all
    try:
        resolved_config = config or A2ATObservabilityConfig()
        if not resolved_config.log_enabled:
            return
        data: dict[str, object] = {"event": event}
        data.update(fields)
        data.update(_current_trace_fields())
        if payload is not None:
            prepared = _prepare_payload(payload, resolved_config)
            if prepared is not None:
                data["payload"] = prepared
        logger.log(level, json.dumps(data, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001 - logging must never break the flow
        logger.debug("a2at log_event failed", exc_info=True)
