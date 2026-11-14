"""Tests for the deterministic RAN energy-saving server tools."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from demo import ran_data
from demo.agents.server_agent.tools import (
    ERROR_INVALID_DATE,
    ERROR_INVALID_REQUEST,
    ERROR_PLAN_INFEASIBLE,
    ERROR_UNKNOWN_CELL,
    ERROR_UNKNOWN_PLAN,
    apply_energy_saving_plan,
    evaluate_energy_saving_plan,
    generate_energy_saving_report,
    get_cell_energy_profile,
    modify_energy_saving_plan,
)


def _as_mapping(value: object) -> dict[str, object]:
    """Narrow one nested JSON object of a tool result."""
    assert isinstance(value, dict)
    return value


def _as_list(value: object) -> list[object]:
    """Narrow one nested JSON array of a tool result."""
    assert isinstance(value, list)
    return value


@pytest.fixture(autouse=True)
def _reset_plan_store() -> Iterator[None]:
    """Keep every test independent of the plan store state."""
    ran_data.reset()
    yield
    ran_data.reset()


def test_profile_returns_the_full_curve() -> None:
    result = get_cell_energy_profile("cell-001", "2026-10-22")

    assert result["ok"] is True
    assert result["cell_id"] == "cell-001"
    assert result["cell_name"] == "Region-A-North"
    assert len(_as_list(result["hourly_profile"])) == 24
    assert result["available_windows"] == ["00:00-06:00", "23:00-24:00"]
    assert _as_mapping(result["capacity"])["max_throughput_mbps"] == 400
    total_kwh = _as_mapping(result["energy_summary"])["total_kwh"]
    assert isinstance(total_kwh, (int, float))
    assert total_kwh > 0
    json.dumps(result)


def test_profile_reports_an_unknown_cell_without_raising() -> None:
    result = get_cell_energy_profile("cell-999", "2026-10-22")

    assert result["ok"] is False
    assert result["error_code"] == ERROR_UNKNOWN_CELL
    assert "cell-001" in _as_list(result["known_cells"])


def test_profile_reports_an_invalid_date() -> None:
    result = get_cell_energy_profile("cell-001", "22-10-2026")

    assert result["ok"] is False
    assert result["error_code"] == ERROR_INVALID_DATE


def test_profile_reports_a_non_string_cell_id() -> None:
    result = get_cell_energy_profile(123, "2026-10-22")  # ty: ignore[invalid-argument-type]

    assert result["ok"] is False
    assert result["error_code"] == ERROR_INVALID_REQUEST
    assert result["argument"] == "cell_id"


def test_profile_reports_a_non_string_date() -> None:
    result = get_cell_energy_profile("cell-001", 20261022)  # ty: ignore[invalid-argument-type]

    assert result["ok"] is False
    assert result["error_code"] == ERROR_INVALID_REQUEST
    assert result["argument"] == "date"


def test_evaluate_returns_a_feasible_evaluation() -> None:
    result = evaluate_energy_saving_plan("cell-001", "00:00~06:00", "30")

    assert result["ok"] is True
    assert result["feasible"] is True
    assert result["estimated_saving_kwh"] == pytest.approx(32.49)
    assert _as_mapping(result["kpi_impact"])["meets_sla"] is True
    json.dumps(result)


def test_evaluate_reports_an_unknown_cell() -> None:
    result = evaluate_energy_saving_plan("cell-999", "00:00~06:00", "30")

    assert result["ok"] is False
    assert result["error_code"] == ERROR_UNKNOWN_CELL


def test_evaluate_reports_a_malformed_window() -> None:
    result = evaluate_energy_saving_plan("cell-001", "midnight", "30")

    assert result["ok"] is False
    assert result["error_code"] == ERROR_INVALID_REQUEST


def test_evaluate_accepts_a_numeric_target_rate() -> None:
    result = evaluate_energy_saving_plan("cell-001", "00:00~06:00", 30)  # ty: ignore[invalid-argument-type]

    assert result["ok"] is True
    assert result["feasible"] is True
    assert result["target_rate"] == 30.0


def test_evaluate_reports_a_non_string_window() -> None:
    result = evaluate_energy_saving_plan("cell-001", 900, "30")  # ty: ignore[invalid-argument-type]

    assert result["ok"] is False
    assert result["error_code"] == ERROR_INVALID_REQUEST
    assert result["argument"] == "window"


def test_evaluate_reports_an_unsupported_target_rate_type() -> None:
    result = evaluate_energy_saving_plan("cell-001", "00:00~06:00", True)  # ty: ignore[invalid-argument-type]

    assert result["ok"] is False
    assert result["error_code"] == ERROR_INVALID_REQUEST
    assert result["argument"] == "target_rate"


def test_evaluate_reports_an_infeasible_plan() -> None:
    result = evaluate_energy_saving_plan("cell-001", "12:00~14:00", "30")

    assert result["ok"] is False
    assert result["error_code"] == ERROR_PLAN_INFEASIBLE
    assert result["feasible"] is False
    assert result["reasons"]
    assert result["recommendation"] is not None


def test_apply_writes_a_plan_and_rejects_infeasible_plans() -> None:
    applied = apply_energy_saving_plan("cell-001", "00:00~06:00", "30")
    rejected = apply_energy_saving_plan("cell-001", "12:00~14:00", "30")

    assert applied["ok"] is True
    assert applied["plan_id"] == "plan-cell-001-001"
    assert applied["status"] == "applied"
    assert rejected["ok"] is False
    assert rejected["error_code"] == ERROR_PLAN_INFEASIBLE
    assert ran_data.list_plan_ids() == ("plan-cell-001-001",)


def test_modify_updates_the_existing_plan_in_place() -> None:
    applied = apply_energy_saving_plan("cell-002", "01:00~05:00", "10")
    plan_id = applied["plan_id"]

    modified = modify_energy_saving_plan(str(plan_id), "cell-002", "01:00~05:00", "20")

    assert modified["ok"] is True
    assert modified["plan_id"] == plan_id
    assert modified["status"] == "modified"
    assert modified["target_rate"] == 20
    assert ran_data.list_plan_ids() == (plan_id,)


def test_modify_rejects_an_unknown_plan_and_writes_nothing() -> None:
    result = modify_energy_saving_plan("plan-cell-002-999", "cell-002", "01:00~05:00", "20")

    assert result["ok"] is False
    assert result["error_code"] == ERROR_UNKNOWN_PLAN
    assert ran_data.list_plans() == ()


def test_apply_reports_non_string_arguments_and_writes_nothing() -> None:
    result = apply_energy_saving_plan("cell-001", ["00:00~06:00"], "30")  # ty: ignore[invalid-argument-type]

    assert result["ok"] is False
    assert result["error_code"] == ERROR_INVALID_REQUEST
    assert result["argument"] == "window"
    assert ran_data.list_plans() == ()


def test_report_has_the_expected_structure() -> None:
    applied = apply_energy_saving_plan("cell-001", "00:00~06:00", "30")
    plan_id = applied["plan_id"]

    report = generate_energy_saving_report(str(plan_id))

    assert report["ok"] is True
    assert report["plan_id"] == plan_id
    assert report["cell_id"] == "cell-001"
    assert report["status"] == "applied"
    assert report["energy"] == {
        "baseline_kwh": pytest.approx(108.3),
        "saved_kwh": pytest.approx(32.49),
        "remaining_kwh": pytest.approx(75.81),
        "saving_percent": pytest.approx(30.0),
    }
    assert _as_mapping(report["kpi_impact"])["affected_hours"] == 6
    assert str(plan_id) in str(report["summary"])
    assert report["highlights"]
    json.dumps(report)


def test_report_reports_an_unknown_plan() -> None:
    report = generate_energy_saving_report("plan-cell-001-001")

    assert report["ok"] is False
    assert report["error_code"] == ERROR_UNKNOWN_PLAN
    assert report["known_plans"] == []


def test_report_reports_a_non_string_plan_id() -> None:
    report = generate_energy_saving_report(123)  # ty: ignore[invalid-argument-type]

    assert report["ok"] is False
    assert report["error_code"] == ERROR_INVALID_REQUEST
    assert report["argument"] == "plan_id"
