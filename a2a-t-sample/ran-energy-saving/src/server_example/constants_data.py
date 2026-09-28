"""Static data for the ran-energy-saving server: agent card, task-execution steps, constants."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

SUBMITTED_MESSAGE = "Energy saving task accepted, starting RAN energy saving task"
WORKING_MESSAGE = "RAN energy saving task in progress"
COMPLETED_MESSAGE = "RAN energy saving task completed"
ARTIFACT_SEND_INTERVAL_SECONDS = 2.0

#: Task-T extension URI (Java ``ExtensionUriConstants``).
TASK_T_EXTENSION_URI = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1"

#: Template addressed by this case.
ENERGY_SAVING_TEMPLATE_URI = "Task-T/network-layer/ran-energy-saving/v1"

PUBLIC_AGENT_CARD: dict[str, Any] = {
    "name": "RAN Energy Saving Agent",
    "description": "RAN Energy Saving Agent",
    "version": "1.0.0",
    "defaultInputModes": ["application/json", "text/plain"],
    "defaultOutputModes": ["application/json", "text/plain"],
    "provider": {
        "organization": "Huawei",
        "url": "https://www.huawei.com",
    },
    "skills": [
        {
            "id": "Ran-Energy-Saving",
            "name": "RAN energy saving",
            "description": "Mock RAN energy saving task sample skill",
            "tags": ["energy-saving", "ran"],
        }
    ],
    "capabilities": {
        "streaming": True,
        "pushNotifications": False,
        "extensions": [
            {
                "uri": TASK_T_EXTENSION_URI,
                "description": "Extension of structured prompt Task-T requests.",
            },
        ],
    },
    "supportedInterfaces": [],
}

#: Ordered artifacts the server streams while executing the RAN energy-saving task. Each entry is
#: ``(artifact_name, human-readable step label, DataPart payload)``; the payload mimics the
#: lifecycle of a real energy-saving task: plan -> candidate cell selection -> activation ->
#: intent report with the achieved energy/rate goals.
ENERGY_SAVING_TASK_STEPS: list[dict[str, Any]] = [
    {
        "name": "energySaving.TaskPlan",
        "label": "energy saving plan built",
        "data": {
            "energySaving.TaskPlan": {
                "operationType": "Create",
                "area": "Songshanhu Administration Committee",
                "cellMode": "NR",
                "energySavingTimeRange": {"startTime": "00:00:00+08:00", "endTime": "12:00:00+08:00"},
                "energyTarget": {"direction": "reduce", "value": "30%"},
                "candidateCellCount": 128,
            }
        },
    },
    {
        "name": "energySaving.CellSelection",
        "label": "candidate energy-saving cells selected",
        "data": {
            "energySaving.CellSelection": {
                "cellMode": "NR",
                "candidateCellCount": 128,
                "selectedCellCount": 37,
                "selectionPolicy": "minimize energy subject to the rate guarantee goal",
            }
        },
    },
    {
        "name": "energySaving.EnergySavingActivated",
        "label": "energy saving activated on the selected cells",
        "data": {
            "energySaving.EnergySavingActivated": {
                "activatedCellCount": 37,
                "deactivatedCellCount": 0,
                "powerSavingMode": "carrier shutdown",
                "activationTime": "2026-04-28T07:29:19Z",
            }
        },
    },
    {
        "name": "energySaving.IntentReport",
        "label": "energy saving intent report",
        "data": {
            "energySaving.IntentReport": {
                "area": "Songshanhu Administration Committee",
                "intentGoalStatus": "achieved",
                "energySavingGain": {"target": "30%", "actual": "31.2%", "suggestion": "none"},
                "rateGoal": {"target": "10Mbps", "actual": "12.5Mbps", "suggestion": "none"},
            }
        },
    },
]


def get_public_agent_card() -> dict[str, Any]:
    """Return a deep copy of the sample public AgentCard definition."""
    return deepcopy(PUBLIC_AGENT_CARD)


def get_energy_saving_task_steps() -> list[dict[str, Any]]:
    """Return a deep copy of the ordered energy-saving task-execution steps."""
    return deepcopy(ENERGY_SAVING_TASK_STEPS)
