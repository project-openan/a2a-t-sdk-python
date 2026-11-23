"""Contract tests of the optional per-case client instructions."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from eval.osp.conditions import case_client_conditions
from eval.osp.runner import load_cases


def test_a_case_without_conditions_returns_an_empty_string() -> None:
    assert case_client_conditions({"id": 1}) == ""


def test_a_case_with_conditions_returns_the_stripped_text() -> None:
    case = {"id": 30, "client_conditions": "  Accept 2Mbps during the outage.\n"}
    assert case_client_conditions(case) == "Accept 2Mbps during the outage."


@pytest.mark.parametrize("value", [["not", "text"], 2, {"a": 1}])
def test_a_non_string_value_is_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        case_client_conditions({"id": 30, "client_conditions": value})


def test_the_case_loader_rejects_a_malformed_field(tmp_path: Path) -> None:
    """A typo fails at load time, not when the case is about to run."""
    (tmp_path / "30.yaml").write_text(
        yaml.safe_dump({"id": 30, "client_conditions": ["not", "text"]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_cases(tmp_path)
