"""Caller-provided parameter schema for ``validate_task_prompt_and_data_filling``.

The Task-T content validation pipeline extracts these parameters from the submitted prompt. The
schema is the English counterpart of the corpus ``validateTaskPromptAndDataFilling`` schema
(``a2a-t-corpus/suites/task/resources/ran-energy-saving-*``).
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
            "description": "Coverage region of the energy-saving task; a specific physical area name.",
        },
        "energyTarget": {
            "type": "string",
            "description": "Energy-saving target: a percentage (e.g. 30%) or a qualitative goal.",
        },
        "energyTargetDirection": {
            "type": "string",
            "description": "Direction of the energy target.",
            "enum": ["reduce", "increase"],
        },
        "cellMode": {
            "type": "string",
            "description": "Cell mode the energy saving applies to (LTE / NR / all).",
        },
        "startTime": {
            "type": "string",
            "description": "Daily start time of the energy-saving window, normalized to UTC HH:mm:ssZ.",
        },
        "endTime": {
            "type": "string",
            "description": "Daily end time of the energy-saving window, normalized to UTC HH:mm:ssZ.",
        },
    },
    "required": ["operationType", "region"],
}
