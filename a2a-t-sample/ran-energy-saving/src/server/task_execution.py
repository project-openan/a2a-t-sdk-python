"""Server-side task execution integration point (INTEGRATION POINT) of the ran-energy-saving demo.

``TaskExecutionService`` is the Protocol the server flow depends on: it is where a customer's
business system executes the negotiated task. ``Uc1DemoExecutionService`` is the colocated demo
implementation, delegating to the static UC1 data in ``server.constants_data`` (the data stays
there so tests keep importing it unchanged). Replace the demo service at the assembly site in
``server.server_main`` with the server's own implementation — see the sample README
(``a2a-t-sample/ran-energy-saving/README.md``), the Task-T chapter / the replace checklist.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from server.constants_data import (
    ARTIFACT_SEND_INTERVAL_SECONDS,
    get_energy_saving_intent_report,
    get_energy_saving_progress_steps,
)

SleepFn = Callable[[float], Awaitable[None]]


@dataclass(slots=True, frozen=True)
class TaskExecutionResult:
    """Result of executing the negotiated task, mapped by the flow onto the report artifact.

    ``report_name`` becomes the artifact name, ``report_text`` the artifact's label text part and
    ``report_metadata`` the report body carried in ``artifact.metadata[Task-T/v1]``.
    """

    report_name: str
    report_text: str
    report_metadata: str


@runtime_checkable
class TaskExecutionService(Protocol):
    """INTEGRATION POINT (server): execute the negotiated task in the server's business system.

    The server flow calls this integration point once the Task-T prompt validated and the negotiation
    concluded: ``params`` carries the filled task parameters extracted by the Task-T content
    validation, and the implementation runs the real business logic (in this demo, the scripted
    UC1 energy-saving execution; in production, the server's EMS/NMS northbound call).

    The implementation only PRODUCES: progress texts and the final result. The flow owns all A2A
    emission — each ``emit_progress`` call becomes a ``TASK_STATE_WORKING`` status update in the
    event stream, the returned :class:`TaskExecutionResult` becomes the report artifact, and an exception
    raised by the implementation is caught by the flow, which emits ``TASK_STATE_FAILED`` and
    re-raises. A customer implementation therefore gets the streaming and terminal-state semantics
    for free.

    ``execute`` returns an awaitable (an ``async def`` implementation conforms); the flow awaits
    it at the call site. The demo implementation is :class:`Uc1DemoExecutionService`; the assembly
    site carrying the ``INTEGRATION POINT`` annotation is ``server.server_main.build_server_runtime``.
    """

    def execute(
        self, params: dict[str, object], emit_progress: Callable[[str], Awaitable[None]]
    ) -> Awaitable[TaskExecutionResult]: ...


class Uc1DemoExecutionService:
    """Demo ``TaskExecutionService`` streaming the scripted UC1 energy-saving execution.

    Delegates to the static data accessors of ``server.constants_data``: the ordered progress
    steps are emitted through ``emit_progress`` with a pacing sleep in between, and the
    energy-saving intent report is returned as the :class:`TaskExecutionResult` (the flow turns it
    into the report artifact). The constructor mirrors the parametrizability the server flow used
    to own (``progress_steps`` / ``intent_report`` / ``sleep_fn`` / ``artifact_send_interval_seconds``,
    same defaults) so tests and callers can script the demo data without touching the flow.
    """

    def __init__(
        self,
        *,
        progress_steps: list[str] | None = None,
        intent_report: dict[str, str] | None = None,
        sleep_fn: SleepFn | None = None,
        artifact_send_interval_seconds: float = ARTIFACT_SEND_INTERVAL_SECONDS,
    ) -> None:
        self._progress_steps = progress_steps
        self._intent_report = intent_report
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._artifact_send_interval_seconds = artifact_send_interval_seconds

    async def execute(
        self, params: dict[str, object], emit_progress: Callable[[str], Awaitable[None]]
    ) -> TaskExecutionResult:
        """Stream the progress steps through ``emit_progress``, then return the intent report.

        ``params`` (the filled Task-T task parameters) is unused by the scripted demo — the UC1
        steps and report are static; a customer implementation consumes them to drive the real
        business task.
        """
        steps = get_energy_saving_progress_steps() if self._progress_steps is None else self._progress_steps
        for step_text in steps:
            await emit_progress(step_text)
            # Pace the stream so the client observes the task progressing step by step.
            await self._sleep_fn(self._artifact_send_interval_seconds)

        report = get_energy_saving_intent_report() if self._intent_report is None else self._intent_report
        report_name = str(report.get("name", "energy saving intent report"))
        return TaskExecutionResult(
            report_name=report_name,
            report_text=str(report.get("text", report_name)),
            report_metadata=str(report.get("metadata", "")),
        )
