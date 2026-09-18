"""Static data for the sample server: agent card definition, artifact data, and message constants."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

SUBMITTED_MESSAGE = "Subscription accepted, starting Incident reporting task"
WORKING_MESSAGE = "Incident reporting task in progress"
ARTIFACT_SEND_INTERVAL_SECONDS = 5.0

NOTIFICATION_T_EXTENSION_URI = (
    "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1"
)

PUBLIC_AGENT_CARD: dict[str, Any] = {
    "name": "SPN Domain Agent",
    "description": "SPN Domain Agent",
    "version": "1.0.0",
    "defaultInputModes": ["application/json", "text/plain"],
    "defaultOutputModes": ["application/json", "text/plain"],
    "provider": {
        "organization": "Huawei",
        "url": "https://www.huawei.com",
    },
    "skills": [
        {
            "id": "Incident-Subscription",
            "name": "Incident reporting",
            "description": "Mock incident reporting sample skill",
            "tags": ["incident", "reporting"],
        }
    ],
    "capabilities": {
        "streaming": True,
        "pushNotifications": False,
        "extensions": [
            {
                "uri": NOTIFICATION_T_EXTENSION_URI,
                "description": "Extension of structured prompt Notification-T requests.",
            },
        ],
    },
    "supportedInterfaces": [],
}

INCIDENT_ARTIFACT_DATA: dict[str, Any] = {
    "faultManagement.Incident": {
        "csn": 1673735459373056,
        "name": "LASER_MOD_ERR",
        "domain": "PTN",
        "priority": "high",
        "occurTime": "2026-04-28T07:21:00Z",
        "createTime": "2026-04-28T07:29:19Z",
        "updateTime": "2026-04-28T12:35:15Z",
        "status": "unacknowledged-and-uncleared",
        "category": "Line",
        "sourceObjects": [
            {
                "id": "9fc7ee3b-e4fb-450e-87d3-e03f027a4f64",
                "type": "network-element",
                "location": "Level1",
                "name": "HUAWEI40-SPE",
                "subObjList": [
                    {
                        "id": "36f6f0e4-9fc3-4508-bc50-fa1831a7c179",
                        "type": "ltp",
                        "name": "1-TPA1EG24-15(M)",
                    }
                ],
            }
        ],
        "rootCauses": [
            {
                "name": "The connected peer network element on HUAWEI40-SPE 1-TPA1EG24-15(M)-MAC:1 is down.",
                "repairAdvice": "Check the network element power connection state and restore power supply.",
                "detailInformation": "The connected peer network element on "
                "HUAWEI40-SPE 1-TPA1EG24-15(M)-MAC:1 is down.",
                "rootCauseObj": {
                    "id": "9fc7ee3b-e4fb-450e-87d3-e03f027a4f64",
                    "type": "network-element",
                    "name": "HUAWEI40-SPE",
                    "location": "Level1",
                    "subObjList": [
                        {
                            "id": "36f6f0e4-9fc3-4508-bc50-fa1831a7c179",
                            "type": "FixedNetworkLTP",
                            "name": "1-TPA1EG24-15(M)",
                        }
                    ],
                },
            }
        ],
        "detail": (
            "Intelligent diagnosis result:\n"
            "The connected peer network element on HUAWEI40-SPE 1-TPA1EG24-15(M)-MAC:1 is down.\n"
            "Fault detail:\n"
            "HUAWEI40-SPE optical module fault, location info: 1-TPA1EG24-15(M)-LASER:1.\n"
            "The user-side port 1-TPA1EG24-15(M)-MAC:1 on HUAWEI40-SPE is abnormal."
        ),
        "repairAdvice": "Check the network element power connection state and restore power supply.",
        "messageType": "update",
        "rootEventCsns": [
            {
                "csn": "524261",
                "type": "0",
            }
        ],
    }
}


def get_public_agent_card() -> dict[str, Any]:
    """Return a deep copy of the sample public AgentCard definition."""
    return deepcopy(PUBLIC_AGENT_CARD)
