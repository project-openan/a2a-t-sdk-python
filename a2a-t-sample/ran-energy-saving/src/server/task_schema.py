"""Caller-provided parameter schema for ``validate_task_prompt_and_data_filling``.

The Task-T content validation pipeline extracts these parameters from the submitted prompt, aligned
with the UC1 RAN energy-saving intent (region-level task with a time-segment-based guaranteed
throughput target).
"""

from __future__ import annotations

TASK_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "operationType": {
            "type": "string",
            "description": "Operation type of the RAN energy-saving task.",
            "enum": ["create", "modify"],
        },
        "region": {
            "type": "string",
            "description": "Coverage region (task object) of the energy-saving task; a concrete physical area name.",
        },
        "energySavingGoal": {
            "type": "string",
            "description": (
                "Energy-saving goal (task target): a quantitative target (a percentage) or the "
                "qualitative goal to maximize energy saving under the task context."
            ),
        },
        "guaranteedThroughputTarget": {
            "type": "string",
            "description": (
                "Time-segment-based guaranteed throughput target (task context), e.g. "
                "{00:00~07:00,2Mbps}, {07:00~17:30,10Mbps}."
            ),
        },
    },
    "required": ["operationType", "region"],
}
