"""Caller-provided parameter schema for the ran-energy-saving Task-T content validation.

The Task-T content validation pipeline extracts these parameters from the submitted prompt, aligned
with the UC1 RAN energy-saving intent: a region-level or site-level task with a time-segment-based
guaranteed throughput target; the site-level context also carries the site list and an optional
background event (the feasibility scenario uses a power outage). Both sides of the sample share the
schema: the server validates the submitted prompt against it, and the client's negotiation accept
regenerates the filled Task-T prompt from it.
"""

from __future__ import annotations

TASK_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "operationType": {
            "type": "string",
            "title": "Operation Type",
            "x-title-zh-CN": "操作类型",
            "description": "Operation type of the RAN energy-saving task.",
            # The Task-T template's allowed values are capitalized ("Create, Modify"); the
            # accept-time refill renders the prompt from this schema, and the server extracts the
            # rendered value verbatim, so the caller schema must use the same casing.
            "enum": ["Create", "Modify"],
        },
        "region": {
            "type": "string",
            "title": "Area",
            "x-title-zh-CN": "区域",
            "description": "Coverage region (task object) of the energy-saving task; a concrete physical area name.",
        },
        "energySavingGoal": {
            "type": "string",
            "title": "Energy Saving Goal",
            "x-title-zh-CN": "节能目标",
            "description": (
                "Energy-saving goal (task target) stated by the user: a quantitative target (a "
                "percentage) or a qualitative goal. Leave empty when the request does not state "
                "one; never invent a goal."
            ),
        },
        "guaranteedThroughputTarget": {
            "type": "string",
            "title": "Rate guarantee goal",
            "x-title-zh-CN": "速率保障目标",
            "description": (
                "Time-segment-based guaranteed throughput target (task context), e.g. "
                "{00:00~07:00,2Mbps}, {07:00~17:30,10Mbps}."
            ),
        },
        "siteList": {
            "type": "string",
            "title": "Site List",
            "x-title-zh-CN": "站点列表",
            "description": "Site list of a site-level energy-saving task (comma-separated).",
        },
        "backgroundEvent": {
            "type": "string",
            "title": "Background Event",
            "x-title-zh-CN": "背景事件",
            "description": "Optional background event and its period of a site-level task.",
        },
    },
    "required": ["operationType", "region", "guaranteedThroughputTarget"],
}
