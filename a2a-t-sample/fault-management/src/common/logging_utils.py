"""Logging utilities: stage/payload formatters, secret masking, and SampleLogger."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from google.protobuf.json_format import MessageToDict

_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_SECRET_KEYWORDS = ("api_key", "authorization", "token", "secret", "password")


def format_stage_log(*, role: str, stage: str, detail: str) -> str:
    return f"[{role}] {stage}: {detail}"


def summarize_text(text: str, *, max_length: int = 80) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3]}..."


def resolve_sample_debug(*, env_path: Path | None = None) -> bool:
    raw_value = os.environ.get("A2AT_SAMPLE_DEBUG")
    if raw_value is None:
        resolved_env_path = env_path or Path.cwd() / ".env"
        env_values = dotenv_values(resolved_env_path) if resolved_env_path.exists() else {}
        raw_value = env_values.get("A2AT_SAMPLE_DEBUG")
    return str(raw_value or "").strip().lower() in _TRUTHY_VALUES


class SampleLogger:
    """Lightweight logger that emits level-prefixed messages to a sink, with debug gated by a flag."""
    def __init__(self, *, sink: Any, debug_enabled: bool) -> None:
        self._sink = sink
        self._debug_enabled = debug_enabled

    def info(self, message: str, *args: object) -> None:
        self._emit("info", message, *args)

    def debug(self, message: str, *args: object) -> None:
        if self._debug_enabled:
            self._emit("debug", message, *args)

    def warning(self, message: str, *args: object) -> None:
        self._emit("warning", message, *args)

    def error(self, message: str, *args: object) -> None:
        self._emit("error", message, *args)

    def _emit(self, level: str, message: str, *args: object) -> None:
        rendered = message % args if args else message
        self._sink(f"[sdk][{level}] {rendered}")


def build_sample_logger(*, sink: Any, debug_enabled: bool) -> SampleLogger:
    return SampleLogger(sink=sink, debug_enabled=debug_enabled)


def format_payload_log(*, role: str, stage: str, payload: object) -> str:
    """Format a payload as a single-line JSON log string with role/stage prefix, masking secret fields."""
    return f"[{role}] {stage}: {json.dumps(_normalize_payload(payload), ensure_ascii=False, sort_keys=True)}"


def _normalize_payload(value: object, *, key_name: str | None = None) -> object:
    if key_name is not None and key_name.lower() in _SECRET_KEYWORDS:
        return "***"

    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if hasattr(value, "DESCRIPTOR"):
        return _normalize_payload(MessageToDict(value, preserving_proto_field_name=True))

    if isinstance(value, dict):
        return {str(key): _normalize_payload(item, key_name=str(key)) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_normalize_payload(item) for item in value]

    if hasattr(value, "__dict__"):
        return _normalize_payload(vars(value))

    return str(value)
