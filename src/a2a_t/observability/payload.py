"""Payload extraction and serialization (spec §4.3).

``extract_payload`` turns a protobuf (or arbitrary) request/response event into
a bounded, optionally-redacted JSON string for observability logs: protobuf →
MessageToDict → JSON → truncate → redact. It returns ``None`` whenever the
matching extraction switch is off or anything goes wrong — it never raises.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from a2a_t.observability.config import A2ATObservabilityConfig

logger = logging.getLogger("a2at.observability")

_TRUNCATED_SUFFIX = "[truncated]"


def extract_payload(event: Any, config: A2ATObservabilityConfig, *, is_request: bool) -> str | None:
    """Serialize a protobuf request/response event to JSON string; None when disabled."""
    enabled = config.resolved_extract_request if is_request else config.resolved_extract_response
    if not enabled:
        return None
    try:
        if hasattr(event, "DESCRIPTOR"):
            # Lazy: google.protobuf is imported only when a real protobuf message
            # reaches this function. A message with a DESCRIPTOR cannot exist
            # without protobuf installed, so an ImportError here means the
            # environment lacks protobuf entirely → caught by the outer except
            # and returns None (no payload). The {"raw": str(event)} branch is
            # for NON-protobuf duck-typed objects.
            from google.protobuf.json_format import MessageToDict

            payload_dict: dict[str, Any] = MessageToDict(event, preserving_proto_field_name=True)
        else:
            payload_dict = {"raw": str(event)}
        payload = json.dumps(payload_dict, ensure_ascii=False, default=str)
        max_length = config.resolved_payload_log_max_length
        if len(payload) > max_length:
            payload = payload[:max_length] + _TRUNCATED_SUFFIX
        if config.payload_redactor is not None:
            try:
                payload = config.payload_redactor(payload)
            except Exception:
                payload = "[redaction-failed]"
        return payload
    except Exception:
        logger.debug("payload extraction failed", exc_info=True)
        return None
