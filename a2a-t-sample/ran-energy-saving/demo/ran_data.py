"""In-memory RAN dataset and energy-saving plan store of the sample.

The dataset lives in ``resources/ran_data/cells.json``. It holds two radio cells of Region A
Administration Committee area, each with a representative 24-hour load and energy curve, the
energy-saving windows the cell accepts, and its capacity / SLA limits.

Plans are written to a process-local store. Everything here is deterministic and offline; the only
mutable state is the plan store, which :func:`reset` clears for tests and repeatable demos.
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from pathlib import Path

SAMPLE_ROOT = Path(__file__).resolve().parents[1]

#: Location of the JSON dataset.
DATASET_PATH = SAMPLE_ROOT / "resources" / "ran_data" / "cells.json"

#: Status of a plan written by :func:`apply_plan`.
PLAN_STATUS_APPLIED = "applied"

#: Status of a plan updated in place by :func:`modify_plan`.
PLAN_STATUS_MODIFIED = "modified"

#: Hours covered by the 24-hour curves.
HOURS_PER_DAY = 24


class RanDataError(Exception):
    """Base class of the RAN dataset errors."""


class UnknownCellError(RanDataError):
    """Raised when a cell id is not part of the dataset.

    Attributes:
        cell_id: The unknown cell id.
    """

    def __init__(self, cell_id: str) -> None:
        self.cell_id = cell_id
        super().__init__(f"Unknown cell '{cell_id}'; known cells: {', '.join(cell.cell_id for cell in load_cells())}")


class UnknownPlanError(RanDataError):
    """Raised when a plan id is not in the process-local plan store.

    Attributes:
        plan_id: The unknown plan id.
    """

    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id
        super().__init__(f"Unknown plan '{plan_id}'; no plan with this id has been applied")


class InvalidPlanError(RanDataError):
    """Raised when a window, date, or target rate cannot be interpreted."""


class PlanInfeasibleError(InvalidPlanError):
    """Raised when a well-formed plan violates the cell's windows, rate cap, or SLA.

    Attributes:
        evaluation: The evaluation that explains why the plan is infeasible.
    """

    def __init__(self, evaluation: PlanEvaluation) -> None:
        self.evaluation = evaluation
        super().__init__("; ".join(evaluation.reasons) or "The plan is infeasible")


@dataclass(frozen=True)
class HourlySample:
    """One hourly point of a cell's 24-hour curve.

    Attributes:
        hour: Hour of day (``0``-``23``).
        load_rate: Normalized load (``0.0``-``1.0``).
        energy_kw: Energy consumption of the hour in kWh (1-hour bucket).
    """

    hour: int
    load_rate: float
    energy_kw: float


@dataclass(frozen=True)
class CellCapacity:
    """Capacity and SLA limits of one cell.

    Attributes:
        max_throughput_mbps: Peak throughput the cell can serve.
        sla_min_throughput_mbps: Minimum guaranteed throughput the cell must keep.
        carrier_count: Number of carriers of the cell.
        max_power_watts: Maximum transmit power of the cell.
    """

    max_throughput_mbps: float
    sla_min_throughput_mbps: float
    carrier_count: int
    max_power_watts: float

    def to_dict(self) -> dict[str, object]:
        """Return the capacity as a JSON-serializable dict."""
        return {
            "max_throughput_mbps": self.max_throughput_mbps,
            "sla_min_throughput_mbps": self.sla_min_throughput_mbps,
            "carrier_count": self.carrier_count,
            "max_power_watts": self.max_power_watts,
        }


@dataclass(frozen=True)
class RanCell:
    """One radio cell of the in-memory dataset.

    Attributes:
        cell_id: Stable cell identifier used by the tools.
        name: Human-readable cell name.
        region: Coverage region of the cell.
        description: One-line description of the cell.
        max_saving_rate: Maximum energy-saving rate (percent) the cell accepts.
        available_windows: Energy-saving windows the cell accepts, in ``HH:MM~HH:MM`` form.
        capacity: Capacity and SLA limits of the cell.
        hourly: Exactly 24 hourly curve points.
    """

    cell_id: str
    name: str
    region: str
    description: str
    max_saving_rate: float
    available_windows: tuple[str, ...]
    capacity: CellCapacity
    hourly: tuple[HourlySample, ...]


@dataclass(frozen=True)
class RanDataset:
    """The whole in-memory RAN dataset.

    Attributes:
        dataset_date: Date the representative curves were recorded for (``YYYY-MM-DD``).
        cells: Cells of the dataset.
    """

    dataset_date: str
    cells: tuple[RanCell, ...]


@dataclass(frozen=True)
class PlanEvaluation:
    """Deterministic evaluation of one requested plan.

    Attributes:
        cell_id: Cell the plan applies to.
        window: Canonical plan window (``HH:MM~HH:MM``).
        target_rate: Requested energy-saving rate in percent.
        feasible: Whether the plan satisfies the windows, rate cap, and SLA.
        reasons: Human-readable reasons; empty when feasible.
        recommendation: Suggested correction when infeasible, otherwise ``None``.
        baseline_energy_kwh: Energy consumption of the window in the baseline curve.
        estimated_saving_kwh: Energy expected to be saved by the plan.
        estimated_saving_percent: Expected saving relative to the window baseline.
        kpi_impact: KPI impact of the plan.
    """

    cell_id: str
    window: str
    target_rate: float
    feasible: bool
    reasons: tuple[str, ...]
    recommendation: str | None
    baseline_energy_kwh: float
    estimated_saving_kwh: float
    estimated_saving_percent: float
    kpi_impact: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        """Return the evaluation as a JSON-serializable dict."""
        return {
            "cell_id": self.cell_id,
            "window": self.window,
            "target_rate": self.target_rate,
            "feasible": self.feasible,
            "reasons": list(self.reasons),
            "recommendation": self.recommendation,
            "baseline_energy_kwh": self.baseline_energy_kwh,
            "estimated_saving_kwh": self.estimated_saving_kwh,
            "estimated_saving_percent": self.estimated_saving_percent,
            "kpi_impact": dict(self.kpi_impact),
        }


@dataclass(frozen=True)
class EnergySavingPlan:
    """An applied energy-saving plan held by the process-local store.

    Attributes:
        plan_id: Generated plan identifier (``plan-<cell_id>-<sequence>``).
        cell_id: Cell the plan applies to.
        cell_name: Human-readable cell name.
        region: Coverage region of the cell.
        date: Date of the dataset curves the plan was computed from.
        window: Canonical plan window (``HH:MM~HH:MM``).
        target_rate: Energy-saving rate in percent.
        status: Lifecycle status, always ``applied`` for stored plans.
        baseline_energy_kwh: Energy consumption of the window in the baseline curve.
        estimated_saving_kwh: Energy expected to be saved by the plan.
        remaining_energy_kwh: Energy expected to remain after the plan is applied.
        kpi_impact: KPI impact of the plan.
    """

    plan_id: str
    cell_id: str
    cell_name: str
    region: str
    date: str
    window: str
    target_rate: float
    status: str
    baseline_energy_kwh: float
    estimated_saving_kwh: float
    remaining_energy_kwh: float
    kpi_impact: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        """Return the plan as a JSON-serializable dict."""
        return {
            "plan_id": self.plan_id,
            "cell_id": self.cell_id,
            "cell_name": self.cell_name,
            "region": self.region,
            "date": self.date,
            "window": self.window,
            "target_rate": self.target_rate,
            "status": self.status,
            "baseline_energy_kwh": self.baseline_energy_kwh,
            "estimated_saving_kwh": self.estimated_saving_kwh,
            "remaining_energy_kwh": self.remaining_energy_kwh,
            "kpi_impact": dict(self.kpi_impact),
        }


#: Process-local plan store and per-cell plan sequence counters. Several server Tasks may create
#: or modify plans concurrently, so every mutation takes the re-entrant lock; a plain ``+=`` on the
#: sequence or a read-then-write on the store is not atomic across threads.
_PLANS: dict[str, EnergySavingPlan] = {}
_PLAN_SEQUENCES: dict[str, int] = {}
_PLAN_LOCK = threading.RLock()


def _as_float(value: object, field: str) -> float:
    """Return ``value`` as a float, raising a readable dataset error otherwise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RanDataError(f"Dataset field '{field}' must be a number, got {value!r}")
    return float(value)


def _as_int(value: object, field: str) -> int:
    """Return ``value`` as an int, raising a readable dataset error otherwise."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise RanDataError(f"Dataset field '{field}' must be an integer, got {value!r}")
    return value


def _as_str(value: object, field: str) -> str:
    """Return ``value`` as a string, raising a readable dataset error otherwise."""
    if not isinstance(value, str):
        raise RanDataError(f"Dataset field '{field}' must be a string, got {value!r}")
    return value


def _as_mapping(value: object, field: str) -> dict[str, object]:
    """Return ``value`` as a string-keyed mapping, raising a readable dataset error otherwise."""
    if not isinstance(value, dict):
        raise RanDataError(f"Dataset field '{field}' must be an object, got {value!r}")
    return {str(key): item for key, item in value.items()}


def _parse_hourly(raw: object, cell_id: str) -> tuple[HourlySample, ...]:
    """Parse the 24-hour curve of one cell."""
    if not isinstance(raw, list):
        raise RanDataError(f"Cell '{cell_id}' must carry an hourly list")
    samples: list[HourlySample] = []
    for item in raw:
        data = _as_mapping(item, f"{cell_id}.hourly")
        samples.append(
            HourlySample(
                hour=_as_int(data["hour"], f"{cell_id}.hourly.hour"),
                load_rate=_as_float(data["load_rate"], f"{cell_id}.hourly.load_rate"),
                energy_kw=_as_float(data["energy_kw"], f"{cell_id}.hourly.energy_kw"),
            )
        )
    if len(samples) != HOURS_PER_DAY:
        raise RanDataError(f"Cell '{cell_id}' must carry exactly {HOURS_PER_DAY} hourly samples")
    if tuple(sample.hour for sample in samples) != tuple(range(HOURS_PER_DAY)):
        raise RanDataError(f"Cell '{cell_id}' hourly samples must cover hours 0..23 in order")
    return tuple(samples)


def _parse_capacity(raw: object, cell_id: str) -> CellCapacity:
    """Parse the capacity block of one cell."""
    data = _as_mapping(raw, f"{cell_id}.capacity")
    return CellCapacity(
        max_throughput_mbps=_as_float(data["max_throughput_mbps"], f"{cell_id}.capacity.max_throughput_mbps"),
        sla_min_throughput_mbps=_as_float(
            data["sla_min_throughput_mbps"], f"{cell_id}.capacity.sla_min_throughput_mbps"
        ),
        carrier_count=_as_int(data["carrier_count"], f"{cell_id}.capacity.carrier_count"),
        max_power_watts=_as_float(data["max_power_watts"], f"{cell_id}.capacity.max_power_watts"),
    )


def _parse_cell(raw: object) -> RanCell:
    """Parse one cell of the dataset."""
    data = _as_mapping(raw, "cell")
    cell_id = _as_str(data["cell_id"], "cell.cell_id")
    windows_raw = data["available_windows"]
    if not isinstance(windows_raw, list):
        raise RanDataError(f"Cell '{cell_id}' must carry an available_windows list")
    windows = tuple(canonical_window(_as_str(window, f"{cell_id}.available_windows")) for window in windows_raw)
    return RanCell(
        cell_id=cell_id,
        name=_as_str(data["name"], f"{cell_id}.name"),
        region=_as_str(data["region"], f"{cell_id}.region"),
        description=_as_str(data["description"], f"{cell_id}.description"),
        max_saving_rate=_as_float(data["max_saving_rate"], f"{cell_id}.max_saving_rate"),
        available_windows=windows,
        capacity=_parse_capacity(data["capacity"], cell_id),
        hourly=_parse_hourly(data["hourly"], cell_id),
    )


@lru_cache(maxsize=1)
def load_dataset() -> RanDataset:
    """Load and cache the JSON dataset.

    Raises:
        RanDataError: when the file is missing or malformed.
    """
    try:
        raw: object = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RanDataError(f"RAN dataset not found at {DATASET_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise RanDataError(f"RAN dataset at {DATASET_PATH} is not valid JSON: {exc}") from exc

    data = _as_mapping(raw, "dataset")
    cells_raw = data["cells"]
    if not isinstance(cells_raw, list):
        raise RanDataError("Dataset field 'cells' must be a list")
    cells = tuple(_parse_cell(cell) for cell in cells_raw)
    if len(cells) < 2:
        raise RanDataError("The RAN dataset must contain at least two cells")
    return RanDataset(
        dataset_date=_as_str(data["dataset_date"], "dataset_date"),
        cells=cells,
    )


def dataset_date() -> str:
    """Return the date of the representative curves."""
    return load_dataset().dataset_date


def load_cells() -> tuple[RanCell, ...]:
    """Return all cells of the dataset."""
    return load_dataset().cells


def get_cell(cell_id: str) -> RanCell | None:
    """Return the cell with ``cell_id``, or ``None`` when it is not in the dataset."""
    for cell in load_cells():
        if cell.cell_id == cell_id:
            return cell
    return None


def get_cell_or_raise(cell_id: str) -> RanCell:
    """Return the cell with ``cell_id``.

    Raises:
        UnknownCellError: when the cell is not in the dataset.
    """
    cell = get_cell(cell_id)
    if cell is None:
        raise UnknownCellError(cell_id)
    return cell


def _parse_clock(text: str, *, allow_end_of_day: bool) -> int:
    """Parse ``HH:MM`` into minutes since midnight."""
    parts = text.strip().split(":")
    if len(parts) != 2:
        raise InvalidPlanError(f"Invalid time '{text}'; expected HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise InvalidPlanError(f"Invalid time '{text}'; expected HH:MM") from exc
    if minute != 0:
        raise InvalidPlanError(f"Invalid time '{text}'; only whole hours are supported")
    if hour == HOURS_PER_DAY and allow_end_of_day:
        return HOURS_PER_DAY * 60
    if not 0 <= hour < HOURS_PER_DAY:
        raise InvalidPlanError(f"Invalid time '{text}'; hour must be within 00:00-23:59")
    return hour * 60


def parse_window(window: str) -> tuple[str, tuple[int, ...]]:
    """Parse a ``HH:MM~HH:MM`` window.

    Args:
        window: Window string, e.g. ``00:00~06:00`` or ``23:00~24:00``.

    Returns:
        The canonical window string and the whole hours it covers.

    Raises:
        InvalidPlanError: when the window is malformed, uses partial hours, or is empty/reversed.
    """
    if "~" not in window:
        raise InvalidPlanError(f"Invalid window '{window}'; expected HH:MM~HH:MM")
    start_text, end_text = window.split("~", 1)
    start_minute = _parse_clock(start_text, allow_end_of_day=False)
    end_minute = _parse_clock(end_text, allow_end_of_day=True)
    if end_minute <= start_minute:
        raise InvalidPlanError(f"Invalid window '{window}'; the end must be after the start")
    if end_minute - start_minute > HOURS_PER_DAY * 60:
        raise InvalidPlanError(f"Invalid window '{window}'; a window cannot exceed 24 hours")
    canonical = f"{start_minute // 60:02d}:{start_minute % 60:02d}~{end_minute // 60:02d}:{end_minute % 60:02d}"
    return canonical, tuple(range(start_minute // 60, end_minute // 60))


def canonical_window(window: str) -> str:
    """Return the canonical form of a window string.

    Raises:
        InvalidPlanError: when the window is malformed.
    """
    return parse_window(window)[0]


def parse_target_rate(target_rate: str) -> float:
    """Parse a percentage target rate such as ``30`` or ``30%``.

    Raises:
        InvalidPlanError: when the text is not a finite number in ``(0, 100]``.
    """
    text = target_rate.strip().removesuffix("%").strip()
    try:
        value = float(text)
    except ValueError as exc:
        raise InvalidPlanError(f"Invalid target rate '{target_rate}'; expected a percentage such as 30") from exc
    if not math.isfinite(value) or not 0 < value <= 100:
        raise InvalidPlanError(f"Invalid target rate '{target_rate}'; expected a value in (0, 100]")
    return value


def validate_date(value: str) -> str:
    """Validate an ISO date and return it unchanged.

    Raises:
        InvalidPlanError: when the value is not an ISO ``YYYY-MM-DD`` date.
    """
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidPlanError(f"Invalid date '{value}'; expected YYYY-MM-DD") from exc
    return value


def _hourly_in_window(cell: RanCell, hours: tuple[int, ...]) -> tuple[HourlySample, ...]:
    """Return the hourly samples covered by a window."""
    return tuple(cell.hourly[hour] for hour in hours)


def evaluate_plan(cell_id: str, window: str, target_rate: str) -> PlanEvaluation:
    """Evaluate a plan against the cell's windows, rate cap, and SLA.

    The evaluation never raises for an infeasible plan; ``feasible`` is ``False`` and ``reasons``
    explains why. It raises for an unknown cell or an unparsable window / rate.

    Raises:
        UnknownCellError: when the cell is not in the dataset.
        InvalidPlanError: when the window or target rate is malformed.
    """
    cell = get_cell_or_raise(cell_id)
    canonical, hours = parse_window(window)
    rate = parse_target_rate(target_rate)
    samples = _hourly_in_window(cell, hours)

    reasons: list[str] = []
    if canonical not in cell.available_windows:
        reasons.append(
            f"Window {canonical} is not available for cell {cell_id}; "
            f"available windows: {', '.join(cell.available_windows)}"
        )
    if rate > cell.max_saving_rate:
        reasons.append(f"Target rate {rate:g}% exceeds the {cell.max_saving_rate:g}% maximum of cell {cell_id}")

    max_throughput = cell.capacity.max_throughput_mbps
    remaining_throughput = round(max_throughput * (1 - rate / 100), 2)
    sla_min = cell.capacity.sla_min_throughput_mbps
    if remaining_throughput < sla_min:
        reasons.append(
            f"The plan leaves {remaining_throughput:g} Mbps of peak throughput, "
            f"below the {sla_min:g} Mbps SLA floor of cell {cell_id}"
        )

    baseline = round(sum(sample.energy_kw for sample in samples), 2)
    saved = round(baseline * rate / 100, 2)
    average_load = sum(sample.load_rate for sample in samples) / len(samples)
    kpi_impact: dict[str, object] = {
        "affected_hours": len(hours),
        "load_reduction_percent": round(rate, 2),
        "average_load_before": round(average_load, 3),
        "average_load_after": round(average_load * (1 - rate / 100), 3),
        "peak_throughput_before_mbps": max_throughput,
        "peak_throughput_after_mbps": remaining_throughput,
        "sla_min_throughput_mbps": sla_min,
        "meets_sla": remaining_throughput >= sla_min,
    }
    recommendation: str | None = None
    if reasons:
        recommendation = (
            f"Choose one of the available windows ({', '.join(cell.available_windows)}) "
            f"with a target rate of at most {cell.max_saving_rate:g}%."
        )

    return PlanEvaluation(
        cell_id=cell_id,
        window=canonical,
        target_rate=rate,
        feasible=not reasons,
        reasons=tuple(reasons),
        recommendation=recommendation,
        baseline_energy_kwh=baseline,
        estimated_saving_kwh=saved,
        estimated_saving_percent=round(rate, 2),
        kpi_impact=kpi_impact,
    )


def apply_plan(cell_id: str, window: str, target_rate: str) -> EnergySavingPlan:
    """Evaluate a plan and write it to the process-local plan store.

    Raises:
        UnknownCellError: when the cell is not in the dataset.
        InvalidPlanError: when the window or target rate is malformed.
        PlanInfeasibleError: when the evaluation rejects the plan; nothing is stored then.
    """
    evaluation = evaluate_plan(cell_id, window, target_rate)
    if not evaluation.feasible:
        raise PlanInfeasibleError(evaluation)
    cell = get_cell_or_raise(cell_id)
    with _PLAN_LOCK:
        sequence = _PLAN_SEQUENCES.get(cell_id, 0) + 1
        _PLAN_SEQUENCES[cell_id] = sequence
        plan = EnergySavingPlan(
            plan_id=f"plan-{cell_id}-{sequence:03d}",
            cell_id=cell.cell_id,
            cell_name=cell.name,
            region=cell.region,
            date=dataset_date(),
            window=evaluation.window,
            target_rate=evaluation.target_rate,
            status=PLAN_STATUS_APPLIED,
            baseline_energy_kwh=evaluation.baseline_energy_kwh,
            estimated_saving_kwh=evaluation.estimated_saving_kwh,
            remaining_energy_kwh=round(evaluation.baseline_energy_kwh - evaluation.estimated_saving_kwh, 2),
            kpi_impact=dict(evaluation.kpi_impact),
        )
        _PLANS[plan.plan_id] = plan
    return plan


def modify_plan(plan_id: str, cell_id: str, window: str, target_rate: str) -> EnergySavingPlan:
    """Evaluate a new plan and update an existing plan in place.

    The ``plan_id`` is kept so a Modify Task updates the plan it already owns instead of allocating
    a second one. The whole plan is re-derived from the new evaluation; the store replaces the old
    record atomically.

    Raises:
        UnknownPlanError: when ``plan_id`` has not been applied.
        UnknownCellError: when the cell is not in the dataset.
        InvalidPlanError: when the window or target rate is malformed.
        PlanInfeasibleError: when the evaluation rejects the plan; the stored plan is unchanged.
    """
    existing = get_plan_or_raise(plan_id)
    evaluation = evaluate_plan(cell_id, window, target_rate)
    if not evaluation.feasible:
        raise PlanInfeasibleError(evaluation)
    cell = get_cell_or_raise(cell_id)
    plan = replace(
        existing,
        cell_id=cell.cell_id,
        cell_name=cell.name,
        region=cell.region,
        date=dataset_date(),
        window=evaluation.window,
        target_rate=evaluation.target_rate,
        status=PLAN_STATUS_MODIFIED,
        baseline_energy_kwh=evaluation.baseline_energy_kwh,
        estimated_saving_kwh=evaluation.estimated_saving_kwh,
        remaining_energy_kwh=round(evaluation.baseline_energy_kwh - evaluation.estimated_saving_kwh, 2),
        kpi_impact=dict(evaluation.kpi_impact),
    )
    with _PLAN_LOCK:
        _PLANS[plan.plan_id] = plan
    return plan


def restore_plan(payload: Mapping[str, object]) -> EnergySavingPlan | None:
    """Restore one plan from its report or tool-artifact payload.

    The plan store is process-local, while the Task itself is durable. A two-phase Modify may run
    after a restart or a test reset, so the baseline plan is rebuilt from the Task artifact before
    the modification tool runs. An already stored plan wins: the artifact is a recovery source, not
    an overwrite of newer state.
    """
    plan_id = payload.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        return None
    existing = _PLANS.get(plan_id)
    if existing is not None:
        return existing
    cell_id = payload.get("cell_id")
    if not isinstance(cell_id, str) or not cell_id:
        return None
    window = payload.get("window")
    if not isinstance(window, str) or not window:
        return None
    target_rate = _number(payload.get("target_rate"))
    if target_rate is None:
        return None
    baseline = _number(payload.get("baseline_energy_kwh"))
    saved = _number(payload.get("estimated_saving_kwh"))
    remaining = _number(payload.get("remaining_energy_kwh"))
    baseline_value = 0.0 if baseline is None else baseline
    saved_value = 0.0 if saved is None else saved
    remaining_value = round(baseline_value - saved_value, 2) if remaining is None else remaining
    kpi = payload.get("kpi_impact")
    plan = EnergySavingPlan(
        plan_id=plan_id,
        cell_id=cell_id,
        cell_name=str(payload.get("cell_name") or cell_id),
        region=str(payload.get("region") or ""),
        date=str(payload.get("date") or dataset_date()),
        window=window,
        target_rate=target_rate,
        status=str(payload.get("status") or PLAN_STATUS_APPLIED),
        baseline_energy_kwh=baseline_value,
        estimated_saving_kwh=saved_value,
        remaining_energy_kwh=remaining_value,
        kpi_impact=dict(kpi) if isinstance(kpi, Mapping) else {},
    )
    with _PLAN_LOCK:
        _PLANS[plan.plan_id] = plan
        _restore_plan_sequence(plan.cell_id, plan.plan_id)
    return plan


def _number(value: object) -> float | None:
    """Return a finite number from an artifact payload, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _restore_plan_sequence(cell_id: str, plan_id: str) -> None:
    """Keep the per-cell sequence ahead of a restored plan id."""
    prefix = f"plan-{cell_id}-"
    if not plan_id.startswith(prefix):
        return
    suffix = plan_id[len(prefix) :]
    if suffix.isdigit():
        _PLAN_SEQUENCES[cell_id] = max(_PLAN_SEQUENCES.get(cell_id, 0), int(suffix))


def get_plan(plan_id: str) -> EnergySavingPlan | None:
    """Return the plan with ``plan_id``, or ``None`` when it has not been applied."""
    return _PLANS.get(plan_id)


def get_plan_or_raise(plan_id: str) -> EnergySavingPlan:
    """Return the plan with ``plan_id``.

    Raises:
        UnknownPlanError: when no plan with this id has been applied.
    """
    plan = get_plan(plan_id)
    if plan is None:
        raise UnknownPlanError(plan_id)
    return plan


def list_plans() -> tuple[EnergySavingPlan, ...]:
    """Return the applied plans in insertion order."""
    return tuple(_PLANS.values())


def list_plan_ids() -> tuple[str, ...]:
    """Return the ids of the applied plans in insertion order."""
    return tuple(_PLANS)


def reset() -> None:
    """Clear the plan store and the per-cell plan sequences (used by tests)."""
    with _PLAN_LOCK:
        _PLANS.clear()
        _PLAN_SEQUENCES.clear()
