"""Tests of the gateway's run-event to outer-Task progress mapping."""

from __future__ import annotations

from protocol.gateway_progress import GatewayProgressMapper


def _event(**entry: object) -> dict[str, object]:
    return {"type": "event", "event": entry}


def test_maps_the_accepted_proposal_decision_and_execution_milestones() -> None:
    mapper = GatewayProgressMapper()

    accepted = mapper.map({"type": "run_started", "runId": "run-1"})
    proposal = mapper.map(
        _event(eventKind="message", performative="PROPOSE", round=1, negotiationType="information")
    )
    decision = mapper.map(
        _event(eventKind="decision", action="accept", round=1, performative="ACCEPT")
    )
    execution = mapper.map(_event(eventKind="status", summary="TASK_STATE_WORKING"))

    assert accepted is not None and accepted.metadata["phase"] == "dispatch"
    assert accepted.metadata["clientRunId"] == "run-1"
    assert proposal is not None and proposal.metadata["phase"] == "negotiation"
    assert proposal.metadata["round"] == 1
    assert "proposed an information negotiation" in proposal.text
    assert decision is not None and decision.metadata["action"] == "accept"
    assert execution is not None and execution.metadata["phase"] == "execution"


def test_maps_apply_and_report_artifacts_to_execution_milestones() -> None:
    mapper = GatewayProgressMapper()
    mapper.map({"type": "run_started", "runId": "run-1"})

    applied = mapper.map(
        _event(
            eventKind="artifact",
            phase="execution",
            detail={"toolName": "apply_energy_saving_plan"},
        )
    )
    report = mapper.map(_event(eventKind="artifact", phase="report"))

    assert applied is not None and applied.metadata["phase"] == "execution"
    assert applied.metadata["state"] == "applied"
    assert "applied the energy-saving plan" in applied.text
    assert report is not None and report.metadata["phase"] == "report"
    assert "published the energy-saving report" in report.text
    # Both milestones are deduplicated independently.
    assert (
        mapper.map(
            _event(
                eventKind="artifact",
                phase="execution",
                detail={"toolName": "apply_energy_saving_plan"},
            )
        )
        is None
    )
    assert mapper.map(_event(eventKind="artifact", phase="report")) is None


def test_ignores_trace_artifacts_and_repeated_milestones() -> None:
    mapper = GatewayProgressMapper()
    mapper.map({"type": "run_started", "runId": "run-1"})
    proposal = _event(eventKind="message", performative="PROPOSE", round=1)

    assert mapper.map(_event(eventKind="trace", kind="llm")) is None
    assert mapper.map(_event(eventKind="artifact", name="tool result")) is None
    assert mapper.map(proposal) is not None
    assert mapper.map(proposal) is None


def test_caps_the_number_of_progress_updates() -> None:
    mapper = GatewayProgressMapper(max_events=2)

    assert mapper.map({"type": "run_started", "runId": "run-1"}) is not None
    assert mapper.map(_event(eventKind="message", performative="PROPOSE", round=1)) is not None
    assert mapper.map(_event(eventKind="decision", action="accept", round=1)) is None
