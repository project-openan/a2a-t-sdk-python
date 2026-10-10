"""Static data for the ran-energy-saving server: agent card, UC1 progress steps, intent report."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

SUBMITTED_MESSAGE = "Energy saving task accepted, starting RAN energy saving task"
ARTIFACT_SEND_INTERVAL_SECONDS = 1.0

#: Task-T extension URI (Java ``ExtensionUriConstants``).
TASK_T_EXTENSION_URI = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1"

#: Negotiation-T extension URI, declared on the AgentCard alongside Task-T.
NEGOTIATION_T_EXTENSION_URI = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1"

#: Template addressed by this case.
ENERGY_SAVING_TEMPLATE_URI = "Task-T/network-layer/ran-energy-saving/v1"

#: Area the sample energy-saving intent is scoped to (from the RAN energy-saving interface spec).
ENERGY_SAVING_REGION = "Songshanhu Administration Committee"

PUBLIC_AGENT_CARD: dict[str, Any] = {
    "name": "RAN Domain Agent",
    "description": "RAN Domain Agent",
    "version": "1.0.0",
    "defaultInputModes": ["application/json", "text/plain"],
    "defaultOutputModes": ["application/json", "text/plain"],
    "provider": {
        "organization": "Huawei",
        "url": "https://www.huawei.com",
    },
    "skills": [
        {
            "id": "energy-saving",
            "name": "RAN energy saving",
            "description": (
                "Creates an energy-saving task based on the energy-saving intent, and periodically "
                "report energy-saving intent reports."
            ),
            "tags": ["energy saving"],
            "examples": [
                "Create a RAN energy-saving task for XX Region to maximize energy savings. The "
                "time-segment-based guaranteed throughput target is {00:00~07:00,2Mbps}, "
                "{07:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,2Mbps}.",
                "Create a RAN energy-saving task for XX Region to maximize energy savings. The "
                "guaranteed throughput target is 10Mbps.",
                "Modify the RAN energy-saving task for XX Region to maximize energy savings. The "
                "time-segment-based guaranteed throughput target is {00:00~06:00,2Mbps}, "
                "{06:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,10Mbps}.",
                "Modify the RAN energy-saving task for XX Region to maximize energy savings. The "
                "site list is [\"Site1\", \"Site2\"]. The time-segment-based guaranteed throughput "
                "target is {03:00-05:00, 2Mbps}. The background event is a power outage from 03:00 "
                "to 05:00.",
            ],
        }
    ],
    "capabilities": {
        "streaming": True,
        "pushNotifications": True,
        "extensions": [
            {
                "uri": TASK_T_EXTENSION_URI,
                "description": "Extension of structured prompt TASK-T requests.",
                "required": False,
            },
            {
                "uri": NEGOTIATION_T_EXTENSION_URI,
                "description": "Extension of structured prompt Negotiation-T requests.",
                "required": False,
            },
        ],
    },
    "supportedInterfaces": [],
}

#: Ordered progress status messages (``TaskStatusUpdateEvent``, ``TASK_STATE_WORKING``) the server
#: streams while executing the UC1 energy-saving task: strategy recommendation -> main task start
#: -> data collection -> optimization suggestion generation and execution.
ENERGY_SAVING_PROGRESS_STEPS: list[str] = [
    "Region: {region}; Number of Cells in the Region: 100 (LTE), 100 (NR); "
    "Progress: the strategy recommendation is being executed.",
    "Region: {region}; Number of Cells in the Region: LTE (100), NR (100); "
    "Progress: strategy recommendation in progress, 60%.",
    "Region: {region}; Number of Cells in the Region: 100 (LTE), 100 (NR); "
    "Progress: strategy recommendation and parameter setting have been completed.",
    "Region: {region}; Number of Cells in the Region: LTE (100), NR (100); "
    "Progress: Multi-RAT Intelligent Network Energy Saving process is starting.",
    "Region: {region}; Number of Cells in the Region: LTE (100), NR (100); "
    "Progress: Multi-RAT Intelligent Network Energy Saving process has been started.",
    "Region: {region}; Number of Cells in the Region: LTE (100), NR (100); "
    "Task: Multi-RAT Intelligent Network Energy Saving; "
    "Progress: data collection has started and is expected to take 3 to 7 days.",
    "Region: {region}; Number of Cells in the Region: 100 (LTE), 100 (NR); "
    "Task: Multi-RAT Intelligent Network Energy Saving; "
    "Progress: data is being collected. The collection is expected to be completed on April 26, 2026.",
    "Region: {region}; Number of Cells in the Region: LTE (100), NR (100); "
    "Task: Multi-RAT Intelligent Network Energy Saving; Progress: data collection is completed.",
    "Region: {region}; Number of Cells in the Region: LTE (100), NR (100); "
    "Task: Multi-RAT Intelligent Network Energy Saving; "
    "Progress: the generation of optimization suggestions are completed. 10 suggestions are generated.",
    "Region: {region}; Number of Cells in the Region: LTE (100), NR (100); "
    "Task: Multi-RAT Intelligent Network Energy Saving; "
    "Progress: 9 optimization suggestions are successfully executed, and 1 optimization suggestion "
    "fails to be executed.",
]

#: The UC1 energy-saving intent report, streamed as a single ``TaskArtifactUpdateEvent``. The report
#: body is carried in ``artifact.metadata[Task-T/v1]``; the artifact parts only carry a label text.
ENERGY_SAVING_INTENT_REPORT: dict[str, str] = {
    "name": "energy saving intent report",
    "text": "energy saving intent report",
    "metadata": (
        "1. Basic Information about the Intent Execution Result:\n"
        "- Region: {region}\n"
        "- Intent achievement result: achieved\n"
        "2. Details about the Intent Execution Result:\n"
        "- Energy saving gain:\n"
        "  - The current energy saving gain is 3%.\n"
        "- Throughput rate achievement result:\n"
        "  - The current throughput rate target is {00:00-06:00, 20 Mbps} and {06:00-18:00, 50 Mbps}.\n"
        "  - The current actual throughput rate is {00:00-06:00, 25 Mbps} and {06:00-18:00, 60 Mbps}.\n"
        "  - It is recommended to change the throughput rate target to {00:00-06:00, 10 Mbps} and "
        "{06:00-18:00, 20 Mbps}, which is expected to improve the gain by 1%."
    ),
}


def get_public_agent_card() -> dict[str, Any]:
    """Return a deep copy of the sample public AgentCard definition."""
    return deepcopy(PUBLIC_AGENT_CARD)


def get_energy_saving_progress_steps() -> list[str]:
    """Return the ordered UC1 progress status messages, with the region substituted in."""
    return [step.replace("{region}", ENERGY_SAVING_REGION) for step in ENERGY_SAVING_PROGRESS_STEPS]


def get_energy_saving_intent_report() -> dict[str, str]:
    """Return the UC1 energy-saving intent report with the region substituted in."""
    report = deepcopy(ENERGY_SAVING_INTENT_REPORT)
    report["metadata"] = report["metadata"].replace("{region}", ENERGY_SAVING_REGION)
    return report
