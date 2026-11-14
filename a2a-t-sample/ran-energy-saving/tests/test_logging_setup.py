"""Tests for the shared logging setup and its correlation context."""

from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest
from shared.logging_setup import (
    JsonLogFormatter,
    TextLogFormatter,
    configure_logging,
    current_log_context,
    log_context,
    redact_mapping,
    redact_text,
)


def _capture(formatter: logging.Formatter) -> tuple[logging.Logger, io.StringIO]:
    """Return an isolated logger writing to an in-memory stream."""
    stream = io.StringIO()
    logger = logging.getLogger(f"ran_energy_saving.test.{id(stream)}")
    logger.handlers = [logging.StreamHandler(stream)]
    logger.handlers[0].setFormatter(formatter)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, stream


def test_text_formatter_carries_the_context() -> None:
    logger, stream = _capture(TextLogFormatter())

    with log_context(run_id="run-1", conversation_id="conv-1", negotiation_id="neg-1", round=2):
        logger.info("proposal received: kind=%s", "information")

    line = stream.getvalue()
    assert "INFO" in line
    assert "run_id=run-1" in line
    assert "conversation_id=conv-1" in line
    assert "negotiation_id=neg-1 round=2" in line
    assert "proposal received: kind=information" in line


def test_json_formatter_carries_the_context_and_parses() -> None:
    logger, stream = _capture(JsonLogFormatter())

    with log_context(run_id="run-1", task_id="task-1", role="server", round=2):
        logger.warning("task failed")

    payload = json.loads(stream.getvalue())
    assert payload["level"] == "WARNING"
    assert payload["message"] == "task failed"
    assert payload["runId"] == "run-1"
    assert payload["taskId"] == "task-1"
    assert payload["role"] == "server"
    assert payload["round"] == 2


def test_context_follows_a_child_task_and_does_not_leak_outside() -> None:
    async def scenario() -> tuple[dict[str, str], dict[str, str]]:
        with log_context(run_id="run-1"):
            assert current_log_context()["run_id"] == "run-1"

            async def child() -> dict[str, str]:
                return dict(current_log_context())

            child_context = await asyncio.create_task(child())
        return child_context, dict(current_log_context())

    child_context, outside = asyncio.run(scenario())

    assert child_context["run_id"] == "run-1"
    assert "run_id" not in outside


def test_empty_context_values_are_ignored() -> None:
    with log_context(run_id="run-1", task_id=None, context_id=""):
        assert dict(current_log_context()) == {"run_id": "run-1"}


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("api_key=sk-secret-value", "api_key=***"),
        ("Authorization: Bearer abc.def.ghi", "Authorization: ***"),
        ("token sk-1234567890 rejected", "token sk-*** rejected"),
        ("trace token invalid", "trace token invalid"),
    ],
)
def test_redaction_masks_credentials(message: str, expected: str) -> None:
    assert redact_text(message) == expected


def test_redact_mapping_masks_secret_keys_recursively() -> None:
    payload = {
        "endpoint": "http://model",
        "apiKey": "value",
        "nested": {"authorization": "Bearer x", "name": "ok"},
        "items": [{"client_secret": "value"}],
    }

    redacted = redact_mapping(payload)

    assert redacted == {
        "endpoint": "http://model",
        "apiKey": "***",
        "nested": {"authorization": "***", "name": "ok"},
        "items": [{"client_secret": "***"}],
    }


def test_configure_logging_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="A2AT_SAMPLE_LOG_LEVEL"):
        configure_logging(level="verbose")
    with pytest.raises(ValueError, match="A2AT_SAMPLE_LOG_FORMAT"):
        configure_logging(log_format="xml")


def test_configure_logging_installs_the_json_handler() -> None:
    stream = io.StringIO()
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        configure_logging(level="INFO", log_format="json", stream=stream, force=True)
        with log_context(run_id="run-1"):
            logging.getLogger("ran_energy_saving.test.configure").info("hello")
        payload = json.loads(stream.getvalue())
        assert payload["message"] == "hello"
        assert payload["runId"] == "run-1"
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)
        root.setLevel(original_level)
