"""ADK function tools of the RAN energy-saving client decision agent.

Both tools are deterministic over the in-memory dataset of :mod:`demo.ran_data` (or its static
policy view). They never access the network and never raise for expected failures: unknown cells
and malformed proposals are reported as structured error dictionaries so the LLM can explain them
and adapt.

All return values are JSON-serializable dictionaries. Successful results carry ``"ok": True``;
failures carry ``"ok": False``, ``"error_code"``, and ``"error_message"``.
"""

from __future__ import annotations

import re

from demo import ran_data
from demo.ran_data import PlanEvaluation, RanCell

#: Error code for an unknown cell id.
ERROR_UNKNOWN_CELL = "unknown_cell"
#: Error code for a malformed tool argument or proposal.
ERROR_INVALID_REQUEST = "invalid_request"

#: Marker used by the deterministic proposal parser: a cell id such as ``cell-001``.
_CELL_PATTERN = re.compile(r"\bcell-\d+\b")
#: Marker used by the deterministic proposal parser: a ``HH:MM~HH:MM`` window.
_WINDOW_PATTERN = re.compile(r"\b\d{2}:\d{2}~\d{2}:\d{2}\b")
#: Marker used by the deterministic proposal parser: a target rate in percent.
_RATE_PATTERN = re.compile(r"(?<![\d:])(\d+(?:\.\d+)?)\s*%")


def _error(code: str, message: str, **details: object) -> dict[str, object]:
    """Build a structured error result."""
    return {"ok": False, "error_code": code, "error_message": message, **details}


def _known_cells() -> list[str]:
    """Return the ids of all cells of the dataset."""
    return [cell.cell_id for cell in ran_data.load_cells()]


def _policy_dict(cell: RanCell) -> dict[str, object]:
    """Return the SLA / energy-saving policy view of one cell."""
    return {
        "cell_id": cell.cell_id,
        "cell_name": cell.name,
        "region": cell.region,
        "max_saving_rate_percent": cell.max_saving_rate,
        "available_windows": list(cell.available_windows),
        "sla": {
            "min_throughput_mbps": cell.capacity.sla_min_throughput_mbps,
            "max_throughput_mbps": cell.capacity.max_throughput_mbps,
            "carrier_count": cell.capacity.carrier_count,
            "max_power_watts": cell.capacity.max_power_watts,
        },
    }


def _impact_dict(evaluation: PlanEvaluation) -> dict[str, object]:
    """Return the client-facing impact view of one plan evaluation."""
    return {
        "ok": True,
        "acceptable": evaluation.feasible,
        "cell_id": evaluation.cell_id,
        "window": evaluation.window,
        "target_rate": evaluation.target_rate,
        "estimated_saving_kwh": evaluation.estimated_saving_kwh,
        "estimated_saving_percent": evaluation.estimated_saving_percent,
        "baseline_energy_kwh": evaluation.baseline_energy_kwh,
        "kpi_impact": dict(evaluation.kpi_impact),
        "reasons": list(evaluation.reasons),
        "recommendation": evaluation.recommendation,
    }


def get_sla_policy(cell_id: str) -> dict[str, object]:
    """Read the SLA and energy-saving policy of one radio cell.

    Call this before accepting or countering a proposal: it returns the cell's SLA throughput floor,
    its maximum saving rate, and the energy-saving windows it accepts.

    Args:
        cell_id: Cell identifier, for example ``cell-001``.

    Returns:
        On success ``{"ok": True, "cell_id", "cell_name", "region",
        "max_saving_rate_percent", "available_windows", "sla"}``. An unknown cell returns
        ``{"ok": False, "error_code": "unknown_cell", "error_message", "known_cells"}``.
    """
    if not isinstance(cell_id, str) or not cell_id.strip():
        return _error(
            ERROR_INVALID_REQUEST,
            "Tool argument 'cell_id' must be a non-empty string, for example 'cell-001'.",
            argument="cell_id",
        )
    cell = ran_data.get_cell(cell_id.strip())
    if cell is None:
        return _error(
            ERROR_UNKNOWN_CELL,
            f"Unknown cell '{cell_id}'; known cells: {', '.join(_known_cells())}",
            cell_id=cell_id,
            known_cells=_known_cells(),
        )
    return {"ok": True, **_policy_dict(cell)}


def evaluate_proposal_impact(proposal: str) -> dict[str, object]:
    """Evaluate the energy, KPI, and SLA impact of one proposed energy-saving plan.

    Call this for every server or self-authored proposal before deciding. The proposal text must
    name a cell id (``cell-001``), a window (``00:00~06:00``) and a target rate (``30`` or
    ``30%``); the tool evaluates them against the in-memory RAN dataset.

    Args:
        proposal: Natural-language proposal carrying a cell id, a window, and a target rate.

    Returns:
        On success ``{"ok": True, "acceptable", "cell_id", "window", "target_rate",
        "estimated_saving_kwh", "estimated_saving_percent", "kpi_impact", "reasons",
        "recommendation"}``; ``acceptable`` is ``False`` when the plan violates the cell window,
        rate cap, or SLA. A proposal the parser cannot read returns
        ``{"ok": False, "error_code": "invalid_request", ...}``; an unknown cell returns
        ``"unknown_cell"``.
    """
    if not isinstance(proposal, str) or not proposal.strip():
        return _error(
            ERROR_INVALID_REQUEST,
            "Tool argument 'proposal' must be a non-empty string naming a cell, a window, and a target rate.",
            argument="proposal",
        )
    cell_match = _CELL_PATTERN.search(proposal)
    window_match = _WINDOW_PATTERN.search(proposal)
    rate_match = _RATE_PATTERN.search(proposal)
    missing: list[str] = []
    if cell_match is None:
        missing.append("cell id (for example cell-001)")
    if window_match is None:
        missing.append("window (for example 00:00~06:00)")
    if rate_match is None:
        missing.append("target rate (for example 30%)")
    if missing:
        return _error(
            ERROR_INVALID_REQUEST,
            "The proposal is missing " + ", ".join(missing) + ".",
            proposal=proposal,
            expected="cell-001 00:00~06:00 30%",
        )

    assert cell_match is not None
    assert window_match is not None
    assert rate_match is not None
    cell_id = cell_match.group(0)
    window = window_match.group(0)
    target_rate = rate_match.group(1)
    try:
        evaluation = ran_data.evaluate_plan(cell_id, window, target_rate)
    except ran_data.UnknownCellError:
        return _error(
            ERROR_UNKNOWN_CELL,
            f"Unknown cell '{cell_id}'; known cells: {', '.join(_known_cells())}",
            cell_id=cell_id,
            known_cells=_known_cells(),
        )
    except ran_data.InvalidPlanError as exc:
        return _error(
            ERROR_INVALID_REQUEST,
            str(exc),
            proposal=proposal,
            cell_id=cell_id,
            window=window,
            target_rate=target_rate,
        )
    return _impact_dict(evaluation)
