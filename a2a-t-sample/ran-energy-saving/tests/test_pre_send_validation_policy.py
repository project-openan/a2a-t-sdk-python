"""Tests of the pre-send validation scope switch."""

from __future__ import annotations

from dataclasses import replace

import pytest
from protocol.client_service.run_manager import _should_validate_before_send
from shared.config import SampleConfig


@pytest.mark.parametrize(
    ("setting", "source_kind", "expected"),
    [
        ("on", "presentation", True),
        ("on", "tester", False),
        ("on", "api", False),
        ("always", "presentation", True),
        ("always", "tester", True),
        ("off", "presentation", False),
        ("off", "always", False),
    ],
)
def test_validation_scope(
    offline_config: SampleConfig,
    setting: str,
    source_kind: str,
    expected: bool,
) -> None:
    config = replace(offline_config, pre_send_validation=setting)

    assert _should_validate_before_send(config, {"kind": source_kind}) is expected
