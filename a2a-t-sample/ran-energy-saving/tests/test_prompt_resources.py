"""Tests pinning the sample-owned overlay of the Task-T slot schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from shared.config import SAMPLE_ROOT

#: Directory of the sample's overlay for the RAN energy-saving Task-T template.
_SLOT_DIR: Final[Path] = (
    SAMPLE_ROOT / "prompt_resources" / "slots" / "Task-T" / "network-layer" / "ran-energy-saving" / "v1"
)

#: Slot carrying the rate guarantee goal and its time windows, per language.
_TASK_CONTEXT_SLOT: Final[dict[str, str]] = {"en-US": "task_context", "zh-CN": "任务上下文"}


def _task_context_constraint(language: str) -> str:
    """Return the extraction constraint of the task-context slot for one language."""
    path = _SLOT_DIR / language / "slot.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    return str(schema["properties"][_TASK_CONTEXT_SLOT[language]]["x-a2at-value-constraint"])


def test_the_slot_rule_defines_24_00_as_the_end_of_the_day() -> None:
    """A window ending at 24:00 must not be read as wrapping around to 00:00.

    Without this rule a live extractor reports a structural overlap between
    ``{00:00~03:00}`` and ``{05:00~24:00}`` even though they are disjoint.
    """
    assert "24:00 is the end of that same day" in _task_context_constraint("en-US")
    assert "24:00 是当日结束" in _task_context_constraint("zh-CN")


def test_the_slot_rule_keeps_the_overlap_violation() -> None:
    """The overlap rule itself must survive edits to the day-boundary clarification."""
    assert "MUST NOT overlap" in _task_context_constraint("en-US")
    assert "不允许重叠" in _task_context_constraint("zh-CN")
