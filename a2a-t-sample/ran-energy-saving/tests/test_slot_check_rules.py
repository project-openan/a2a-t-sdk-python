"""Guard the sample overlay against reintroducing declarative slot checks.

The Task-T business verdict is the extraction model's ``slot_errors``. The overlay used to carry
``x-a2at-checks`` regex rules; a field run (2026-11-11, run ``76a9ab9a``) showed them vetoing a
correct extraction (a site name outside the rule's naming convention was reported as "Site list is
not provided"), while the same rules also let incomplete requests through ("needs" matched the NE
prefix branch). The SDK evaluator remains available for other scenarios, opt-in through
``A2AT_VALIDATE_SLOT_CONSTRAINTS``; the sample must not ship heuristic rules again unless they are
replaced by a structured check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_OVERLAY = (
    Path(__file__).resolve().parents[1]
    / "prompt_resources/slots/Task-T/network-layer/ran-energy-saving/v1"
)


@pytest.mark.parametrize("language", ["en-US", "zh-CN"])
def test_the_overlay_carries_no_declarative_slot_checks(language: str) -> None:
    schema = json.loads((_OVERLAY / language / "slot.json").read_text(encoding="utf-8"))

    assert "x-a2at-checks" not in schema
