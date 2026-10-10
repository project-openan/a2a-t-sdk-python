"""Payload extraction and serialization (spec §4.3).

``extract_payload`` turns a protobuf (or arbitrary) request/response event into
a JSON string for the observability payload channels: protobuf → MessageToDict
→ JSON. It returns ``None`` whenever the matching extraction switch is off or
anything goes wrong — it never raises.

``prepare_payload`` is the single-stage preparation shared by both payload
channels (span attributes and structured logs): redact FIRST, then truncate.
The order is a security property — truncation only removes characters and can
never expose new content, while truncating first would split a secret spanning
the ``max_length`` boundary so the redactor's full pattern can no longer match,
leaking partial plaintext.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from a2a_t.observability.config import A2ATObservabilityConfig

logger = logging.getLogger("a2at.observability")

_TRUNCATED_SUFFIX = "[truncated]"


def prepare_payload(payload: str, config: A2ATObservabilityConfig) -> str:
    """Single-stage preparation for both payload channels: redact → truncate."""
    text = payload
    if config.payload_redactor is not None:
        try:
            text = config.payload_redactor(text)
        except Exception:  # noqa: BLE001 - redaction failure never leaks raw content
            text = "[redaction-failed]"
    max_length = config.resolved_payload_log_max_length
    if len(text) > max_length:
        text = text[:max_length] + _TRUNCATED_SUFFIX
    return text


def extract_payload(event: Any, config: A2ATObservabilityConfig, *, is_request: bool) -> str | None:
    """Serialize a protobuf request/response event to a raw JSON string; None when disabled.

    Serialization only — no truncation, no redaction: each channel (span
    attribute / structured log) prepares the raw string exactly once via
    ``prepare_payload`` (redact → truncate).
    """
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
        return json.dumps(payload_dict, ensure_ascii=False, default=str)
    except Exception:
        logger.debug("payload extraction failed", exc_info=True)
        return None
