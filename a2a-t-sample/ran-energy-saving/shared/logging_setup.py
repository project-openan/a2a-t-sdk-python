"""Process-wide logging and run-scoped correlation of the sample services.

One stream carries the client, the NMS and the gateway. A log line is tied back to the run it
belongs to through context variables bound at the run or task boundary, so an operator can follow a
single negotiation across processes without reading the transcript files.

Two formats are available: ``text`` for a terminal and ``json`` for a log collector. Both redact
credentials; the caller still decides whether a payload is safe to log at all (prompts and tool
results are summarized, never dumped at ``info``).
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Final, TextIO

#: Log level environment key (``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR``).
LOG_LEVEL_ENV_KEY: Final[str] = "A2AT_SAMPLE_LOG_LEVEL"

#: Log format environment key (``text`` or ``json``).
LOG_FORMAT_ENV_KEY: Final[str] = "A2AT_SAMPLE_LOG_FORMAT"

DEFAULT_LOG_LEVEL: Final[str] = "INFO"
DEFAULT_LOG_FORMAT: Final[str] = "text"
SUPPORTED_LOG_FORMATS: Final[tuple[str, ...]] = ("text", "json")

#: Context key and its JSON name. The text format uses the key, JSON the camelCase name.
CONTEXT_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("run_id", "runId"),
    ("conversation_id", "conversationId"),
    ("task_id", "taskId"),
    ("context_id", "contextId"),
    ("negotiation_id", "negotiationId"),
    ("round", "round"),
    ("role", "role"),
)

_CONTEXT: contextvars.ContextVar[Mapping[str, str]] = contextvars.ContextVar(
    "a2at_log_context", default={}
)

_SECRET_KEYWORDS: Final[tuple[str, ...]] = (
    "api_key",
    "apikey",
    "access_token",
    "auth_token",
    "secret",
    "password",
    "authorization",
)

_KEY_VALUE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|authorization)"
    r"\b(\s*[=:]\s*)(?:bearer\s+)?[^\s,;]+"
)
_BEARER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"
)
_SK_PATTERN: Final[re.Pattern[str]] = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")


def _mask_key_value(match: re.Match[str]) -> str:
    """Keep the key and its separator, mask the value."""
    return f"{match.group(1)}{match.group(2)}***"


def redact_text(text: str) -> str:
    """Mask credential-looking values in a rendered log message."""
    if not text:
        return text
    redacted = _KEY_VALUE_PATTERN.sub(_mask_key_value, text)
    redacted = _BEARER_PATTERN.sub("Bearer ***", redacted)
    return _SK_PATTERN.sub("sk-***", redacted)


def redact_mapping(value: object) -> object:
    """Return a copy of a mapping with credential-looking keys masked.

    Used when a payload is logged as structured fields rather than as a rendered message. Nested
    dicts and lists are handled; other values are returned unchanged.
    """
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            text_key = str(key)
            lowered = text_key.lower()
            if any(keyword in lowered for keyword in _SECRET_KEYWORDS):
                result[text_key] = "***"
            else:
                result[text_key] = redact_mapping(item)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_mapping(item) for item in value]
    return value


@contextmanager
def log_context(**fields: object) -> Generator[None]:
    """Bind correlation fields for every log record emitted inside the block.

    Fields with ``None``/empty values are ignored, so callers can pass an optional id directly.
    The binding is a context variable: it follows ``asyncio`` tasks created inside the block and
    never leaks to a sibling task.
    """
    current = dict(_CONTEXT.get())
    for key, value in fields.items():
        if value is None or value == "":
            continue
        current[key] = str(value)
    token = _CONTEXT.set(current)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def current_log_context() -> Mapping[str, str]:
    """Return the correlation fields bound in the current context."""
    return dict(_CONTEXT.get())


def _json_fields(context: Mapping[str, str]) -> dict[str, object]:
    """Map context keys to their JSON names; the negotiation round stays numeric."""
    fields: dict[str, object] = {}
    for key, json_name in CONTEXT_FIELDS:
        value = context.get(key)
        if not value:
            continue
        fields[json_name] = int(value) if key == "round" and value.isdigit() else value
    return fields


def _now() -> str:
    """Return the current UTC time in ISO 8601 with milliseconds."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class TextLogFormatter(logging.Formatter):
    """Human-readable formatter: timestamp, level, logger, correlation fields, message."""

    def format(self, record: logging.LogRecord) -> str:
        """Render one record as a single line (plus a traceback when present)."""
        context = _CONTEXT.get()
        header = f"{_now()} {record.levelname:<7} {record.name}"
        if context:
            rendered = " ".join(f"{key}={context[key]}" for key, _ in CONTEXT_FIELDS if key in context)
            header = f"{header} [{rendered}]"
        line = f"{header} {redact_text(record.getMessage())}"
        if record.exc_info:
            line = f"{line}\n{redact_text(self.formatException(record.exc_info))}"
        return line


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line, with correlation fields as top-level keys."""

    def format(self, record: logging.LogRecord) -> str:
        """Render one record as a JSON object."""
        payload: dict[str, Any] = {
            "ts": _now(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        payload.update(_json_fields(_CONTEXT.get()))
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    *,
    level: str | None = None,
    log_format: str | None = None,
    stream: TextIO | None = None,
    force: bool = False,
) -> None:
    """Configure the root logger once per process.

    Args:
        level: log level name; defaults to ``A2AT_SAMPLE_LOG_LEVEL`` or ``INFO``.
        log_format: ``text`` or ``json``; defaults to ``A2AT_SAMPLE_LOG_FORMAT`` or ``text``.
        stream: output stream; defaults to ``stderr`` (captured by the demo supervisors).
        force: replace an existing handler. Entry points leave it ``False``; tests set it.

    Raises:
        ValueError: when the level or the format is not supported.
    """
    resolved_level = (level or os.environ.get(LOG_LEVEL_ENV_KEY) or DEFAULT_LOG_LEVEL).strip().upper()
    numeric_level = logging.getLevelNamesMapping().get(resolved_level)
    if not isinstance(numeric_level, int):
        raise ValueError(
            f"Invalid value for {LOG_LEVEL_ENV_KEY}: {resolved_level!r}; "
            "expected DEBUG, INFO, WARNING, ERROR or CRITICAL"
        )
    resolved_format = (
        log_format or os.environ.get(LOG_FORMAT_ENV_KEY) or DEFAULT_LOG_FORMAT
    ).strip().lower()
    if resolved_format not in SUPPORTED_LOG_FORMATS:
        raise ValueError(
            f"Invalid value for {LOG_FORMAT_ENV_KEY}: {resolved_format!r}; "
            f"expected one of {SUPPORTED_LOG_FORMATS}"
        )

    root = logging.getLogger()
    root.setLevel(numeric_level)
    formatter: logging.Formatter = (
        JsonLogFormatter() if resolved_format == "json" else TextLogFormatter()
    )
    if force:
        for handler in list(root.handlers):
            root.removeHandler(handler)
    if force or not root.handlers:
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(formatter)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setFormatter(formatter)
