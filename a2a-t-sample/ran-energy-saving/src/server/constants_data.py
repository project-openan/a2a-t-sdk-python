"""Static data for the ran-energy-saving server: agent card, UC1 progress steps, intent report."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from a2a_t.core.standard_templates import (
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from dotenv import dotenv_values

SUBMITTED_MESSAGE = "Energy saving task accepted, starting RAN energy saving task"
ARTIFACT_SEND_INTERVAL_SECONDS = 1.0

#: Text part of the INPUT_REQUIRED status message carrying the Negotiation-T propose metadata.
INPUT_REQUIRED_MESSAGE = "Additional information is required to start the energy saving task"

#: Text part of the COMPLETED status message closing the negotiation continuation flow.
COMPLETED_MESSAGE = "Energy saving task completed"

#: Hint rendered into each missing-item entry of a Negotiation-T information propose; the
#: ``{slot}`` / ``{description}`` placeholders are filled from the validation error's slot name
#: and the caller schema's property description.
NEGOTIATION_MISSING_ITEM_HINT = "Please provide {slot}: {description}."

#: Relationship note carried by a Negotiation-T information propose when more than one item is
#: missing (omitted for a single item).
NEGOTIATION_RELATIONSHIP = "Multiple items must be provided together."

#: Task-T extension URI (Java ``ExtensionUriConstants``).
TASK_T_EXTENSION_URI = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1"

#: Negotiation-T extension URI, declared on the AgentCard alongside Task-T.
NEGOTIATION_T_EXTENSION_URI = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1"

#: Template addressed by this case.
ENERGY_SAVING_TEMPLATE_URI = "Task-T/network-layer/ran-energy-saving/v1"

#: Negotiation-T information-propose template addressed by the server's round-1 propose
#: (re-exported from ``a2a_t.core.standard_templates``; the URIs used by this sample are
#: centralized here).
NEGOTIATION_PROPOSE_TEMPLATE_URI = INFORMATION_NEGOTIATION_PROPOSE_URI

#: Negotiation-T per-type propose templates addressed by the server's round-1 propose
#: (re-exported from ``a2a_t.core.standard_templates``). The proposal type is selected at runtime
#: by the first content-validation error's code (plan R1); the templateUri inside the INPUT_REQUIRED
#: metadata carries the per-type URI, which is what the client dispatches its accept on.
TARGET_NEGOTIATION_PROPOSE_TEMPLATE_URI = TARGET_NEGOTIATION_PROPOSE_URI
FEASIBILITY_NEGOTIATION_PROPOSE_TEMPLATE_URI = FEASIBILITY_NEGOTIATION_PROPOSE_URI

#: Task-T content-validation error codes that select the negotiation type (demo convention, plan
#: R1): the first structured error of a failed Task-T content validation carries the code —
#: ``content.param_missing`` proposes information (v1), ``content.value_not_allowed`` target and
#: ``content.semantic_conflict`` feasibility.
CONTENT_ERROR_CODE_INFORMATION = "content.param_missing"
CONTENT_ERROR_CODE_TARGET = "content.value_not_allowed"
CONTENT_ERROR_CODE_FEASIBILITY = "content.semantic_conflict"

#: Generation channel variants selected by ``A2AT_SAMPLE_CHANNEL``: ``data`` generates the
#: negotiation propose from typed content (no extraction LLM call), ``text`` from a natural-language
#: rendering plus one extraction LLM call. Server-side mirror of the ``client.scenario_data``
#: resolver — the server imports no client resolver (only its scenario accessors).
CHANNEL_DATA = "data"
CHANNEL_TEXT = "text"
SUPPORTED_CHANNELS = (CHANNEL_DATA, CHANNEL_TEXT)

#: ``.env`` key selecting the generation channel.
CHANNEL_ENV_KEY = "A2AT_SAMPLE_CHANNEL"

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
                'site list is ["Site1", "Site2"]. The time-segment-based guaranteed throughput '
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


def resolve_negotiation_channel(*, env_path: Path | None = None) -> str:
    """Resolve the configured ``A2AT_SAMPLE_CHANNEL`` (defaults to ``data``).

    The channel switches only the negotiation propose generation (typed data vs a natural-language
    rendering plus one extraction LLM call); an unset or unknown value resolves to ``data``. The
    ``.env`` value wins over the OS environment (the sample resolver convention, mirroring the
    client-side ``client.scenario_data.resolve_channel`` without importing it).
    """
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    raw_value = str(values.get(CHANNEL_ENV_KEY, "")).strip() or os.environ.get(CHANNEL_ENV_KEY, "").strip()
    return raw_value if raw_value in SUPPORTED_CHANNELS else CHANNEL_DATA
