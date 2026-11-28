"""Dependency-free Prometheus metrics of the sample services.

Each process owns its registry: counters and summaries are aggregated in memory and rendered at
``/metrics`` in the Prometheus text format. A deployment scrapes every process (or aggregates with
its own collector); there is no cross-process state, so a metric is always local to one service.

Only the standard library is used. The metric surface is intentionally small: run lifecycle,
negotiation rounds and server task outcomes.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Final

#: Every registered metric, rendered by :func:`render_prometheus`.
_METRICS: list[_Metric] = []


def _escape(value: str) -> str:
    """Escape one label value for the Prometheus text format."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_value(value: float) -> str:
    """Render a metric value without a trailing ``.0`` for integral counters."""
    if value.is_integer():
        return str(int(value))
    return repr(value)


def _render_labels(key: tuple[tuple[str, str], ...]) -> str:
    """Render one sorted label set (empty when the metric is unlabeled)."""
    if not key:
        return ""
    inner = ",".join(f'{name}="{_escape(value)}"' for name, value in key)
    return "{" + inner + "}"


class _Metric:
    """Base class owning the name, help text, label set and lock of one metric."""

    kind: str = "untyped"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.help = help_text
        self.label_names = label_names
        self._lock = threading.Lock()
        _METRICS.append(self)

    def _key(self, labels: Mapping[str, object] | None) -> tuple[tuple[str, str], ...]:
        """Return the canonical label tuple of one observation."""
        if not self.label_names:
            return ()
        supplied = labels or {}
        return tuple((name, str(supplied.get(name, ""))) for name in self.label_names)

    def _header(self) -> list[str]:
        """Return the HELP/TYPE lines of this metric."""
        return [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} {self.kind}"]

    def render(self) -> list[str]:
        """Render this metric as Prometheus text lines."""
        raise NotImplementedError

    def reset(self) -> None:
        """Clear every recorded value (tests)."""
        raise NotImplementedError


class Counter(_Metric):
    """A monotonically increasing counter, optionally labeled."""

    kind = "counter"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
    ) -> None:
        super().__init__(name, help_text, label_names)
        self._values: dict[tuple[tuple[str, str], ...], float] = {}

    def inc(
        self,
        labels: Mapping[str, object] | None = None,
        value: float = 1.0,
    ) -> None:
        """Increase one labeled series."""
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def render(self) -> list[str]:
        """Render the counter lines."""
        with self._lock:
            items = sorted(self._values.items())
        return [
            *self._header(),
            *(f"{self.name}{_render_labels(key)} {_format_value(value)}" for key, value in items),
        ]

    def reset(self) -> None:
        """Clear every series."""
        with self._lock:
            self._values.clear()


class Gauge(_Metric):
    """A value that can go up and down, optionally labeled."""

    kind = "gauge"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
    ) -> None:
        super().__init__(name, help_text, label_names)
        self._values: dict[tuple[tuple[str, str], ...], float] = {}

    def inc(
        self,
        labels: Mapping[str, object] | None = None,
        value: float = 1.0,
    ) -> None:
        """Increase one labeled series."""
        self._add(labels, value)

    def dec(
        self,
        labels: Mapping[str, object] | None = None,
        value: float = 1.0,
    ) -> None:
        """Decrease one labeled series."""
        self._add(labels, -value)

    def set(
        self,
        value: float,
        labels: Mapping[str, object] | None = None,
    ) -> None:
        """Set one labeled series."""
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(value)

    def _add(self, labels: Mapping[str, object] | None, value: float) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def render(self) -> list[str]:
        """Render the gauge lines."""
        with self._lock:
            items = sorted(self._values.items())
        return [
            *self._header(),
            *(f"{self.name}{_render_labels(key)} {_format_value(value)}" for key, value in items),
        ]

    def reset(self) -> None:
        """Clear every series."""
        with self._lock:
            self._values.clear()


class Summary(_Metric):
    """A summary exposing ``_sum`` and ``_count`` (no client-side quantiles)."""

    kind = "summary"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
    ) -> None:
        super().__init__(name, help_text, label_names)
        self._values: dict[tuple[tuple[str, str], ...], tuple[float, int]] = {}

    def observe(
        self,
        value: float,
        labels: Mapping[str, object] | None = None,
    ) -> None:
        """Record one observation."""
        key = self._key(labels)
        with self._lock:
            total, count = self._values.get(key, (0.0, 0))
            self._values[key] = (total + value, count + 1)

    def render(self) -> list[str]:
        """Render the summary lines."""
        with self._lock:
            items = sorted(self._values.items())
        lines = self._header()
        for key, (total, count) in items:
            labels = _render_labels(key)
            lines.append(f"{self.name}_sum{labels} {_format_value(total)}")
            lines.append(f"{self.name}_count{labels} {count}")
        return lines

    def reset(self) -> None:
        """Clear every series."""
        with self._lock:
            self._values.clear()


def render_prometheus() -> str:
    """Render every registered metric as one Prometheus text payload."""
    lines: list[str] = []
    for metric in _METRICS:
        lines.extend(metric.render())
    return "\n".join(lines) + "\n"


def reset_metrics() -> None:
    """Clear every metric; used by tests to keep assertions independent."""
    for metric in _METRICS:
        metric.reset()


#: Client runs by terminal status and final state.
CLIENT_RUNS_TOTAL: Final[Counter] = Counter(
    "a2at_client_runs_total",
    "Client runs by terminal status and final state.",
    ("status", "final_state"),
)

#: Client runs currently executing in this process.
CLIENT_RUNS_IN_FLIGHT: Final[Gauge] = Gauge(
    "a2at_client_runs_in_flight",
    "Client runs currently executing in this process.",
)

#: End-to-end duration of a client run.
CLIENT_RUN_DURATION_SECONDS: Final[Summary] = Summary(
    "a2at_client_run_duration_seconds",
    "End-to-end duration of a client run.",
)

#: Negotiation messages by side.
NEGOTIATION_ROUNDS_TOTAL: Final[Counter] = Counter(
    "a2at_negotiation_rounds_total",
    "Negotiation messages by side and round.",
    ("role", "round"),
)

#: Server task executions by terminal state.
SERVER_TASKS_TOTAL: Final[Counter] = Counter(
    "a2at_server_tasks_total",
    "Server task executions by terminal state.",
    ("state",),
)
