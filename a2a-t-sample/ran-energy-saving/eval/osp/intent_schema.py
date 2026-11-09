"""Caller schema for the OSP 11-key structured intent.

The OSP evaluation set judges a request on a structured intent that is wider than the sample's
runtime parameter schema (``shared.task_schema.TASK_PARAM_SCHEMA``): it carries the cell RAT, the
energy-saving mode, the region, the experience requirement, carrier frequencies, the gain target,
area-level throughput targets and site-level scenario targets.

The offline runner passes this schema to the SDK content-validation pipeline so the same semantic
extraction that fills a Task-T also produces the structured intent.  The schema is intentionally
evaluation-only: the runtime client flow keeps using ``TASK_PARAM_SCHEMA``.
"""

from __future__ import annotations

from typing import Final

#: The 11 keys, in the order used by the case files.
OSP_INTENT_KEYS: Final[tuple[str, ...]] = (
    "Operation type",
    "Energy saving cell RAT",
    "Energy saving mode",
    "Energy saving region",
    "Whether Experience-Preserving Energy Saving is Required",
    "Special Requirements for Carrier Shutdown Frequencies",
    "Energy saving time",
    "Frequencies of carriers that can be shut down",
    "Gain target",
    "Guaranteed throughput target by zone",
    "Scenario-based energy saving",
)

_THROUGHPUT_TARGET_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "Guaranteed throughput target": {
            "type": "string",
            "description": "Guaranteed downlink throughput such as '10Mbps'.",
        },
        "Effective time period": {
            "type": "string",
            "description": (
                "Hour-by-hour intervals HH:00:00-HH:59:59 joined by '&', e.g. "
                "'08:00:00-08:59:59&09:00:00-09:59:59'. Use the rate target's own effective window from the request (e.g. 19:00-22:00 -> 19:00:00-19:59:59&20:00:00-20:59:59&21:00:00-21:59:59); do not default to the full day when the request names an activation period."
            ),
        },
    },
    "required": ["Guaranteed throughput target", "Effective time period"],
}

_SITE_TARGET_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "Site list": {
            "type": "array",
            "description": "Concrete site names, in the order stated by the request.",
            "items": {"type": "string"},
        },
        "Guaranteed throughput target by site": {
            "type": "array",
            "description": (
                "Time-segmented throughput targets applying to the listed sites. When the "
                "request describes an outage, each target's Effective time period is the outage "
                "period, not the full day."
            ),
            "items": _THROUGHPUT_TARGET_SCHEMA,
        },
        "Scenario": {
            "type": "string",
            "description": "Optional background scenario such as 'Power outage'.",
        },
        "Scenario period": {
            "type": "string",
            "description": (
                "Optional hour-by-hour scenario period, in the same '&'-joined "
                "HH:00:00-HH:59:59 form."
            ),
        },
    },
    "required": ["Site list"],
}

#: Caller schema of the 11-key structured intent.
OSP_INTENT_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "Operation type": {
            "type": "string",
            "description": (
                "Operation type of the energy-saving task. 'Create' starts a new task; "
                "'Modify' changes an existing one."
            ),
            "enum": ["Create", "Modify"],
        },
        "Energy saving cell RAT": {
            "type": "string",
            "description": (
                "Cell radio access technology covered by energy saving. Map 4G / E-UTRAN to "
                "'LTE', 5G / NG-RAN to 'NR', and LNR to 'ALL'. Use 'ALL' when the request does "
                "not mention or restrict the RAT; never return null. A frequency numbering "
                "scheme such as NR-ARFCN or EARFCN names a frequency, not a RAT: do not infer "
                "'NR' or 'LTE' from it."
            ),
            "enum": ["ALL", "LTE", "NR", "LNR"],
        },
        "Energy saving mode": {
            "type": "string",
            "description": (
                "Qualitative energy-saving aggressiveness: 'Aggressive' or 'Conservative'. "
                "Maximize Energy Saving / high GS -> 'Aggressive'; Maximize User Experience "
                "Assurance / Maximize KPI Assurance -> 'Conservative'. Empty when the request "
                "states no mode. A numeric rate target is not a mode."
            ),
        },
        "Energy saving region": {
            "type": "string",
            "description": (
                "Region named by the request, kept verbatim. The served test region is sometimes "
                "called 'A2A test area'; normalize that phrase to 'A2ATestRegion'. Empty when the "
                "request names no region; never invent one."
            ),
        },
        "Whether Experience-Preserving Energy Saving is Required": {
            "type": "string",
            "description": (
                "'Y' when the request says user experience first / lossless experience; 'N' "
                "when it says energy saving first / lossy experience; empty otherwise. A "
                "bitrate or rate-quality phrase such as 'ensuring user experience for 720p "
                "video' or 'UX target 10Mbps' is a guaranteed throughput target, not an "
                "experience-preserving mode: leave this field empty for it."
            ),
            "enum": ["Y", "N", ""],
        },
        "Special Requirements for Carrier Shutdown Frequencies": {
            "type": "string",
            "description": (
                "Free-text special requirement about carrier shutdown frequencies, beyond the "
                "frequency number itself. Keep it empty when the request only names a concrete "
                "frequency to shut down (EARFCN/NR-ARFCN); put that number in 'Frequencies of "
                "carriers that can be shut down' instead."
            ),
        },
        "Energy saving time": {
            "type": "string",
            "description": (
                "Daily energy-saving period, rendered as hour-by-hour intervals "
                "HH:00:00-HH:59:59 joined by '&'. Extract an explicit range even when it sits "
                "inside a throughput sentence. A range whose end is HH:00 stops before that hour: "
                "19:00-22:00 covers 19:00:00-19:59:59, 20:00:00-20:59:59 and 21:00:00-21:59:59; "
                "never add the 22:00-22:59:59 interval. For an area-level request with no explicit "
                "range, use the full day (all 24 hourly intervals). For a site-level request, "
                "leave empty and put the outage window in 'Scenario period'."
            ),
        },
        "Frequencies of carriers that can be shut down": {
            "type": "array",
            "description": (
                "Concrete carrier frequency numbers that may be shut down, as strings. Empty "
                "when none are given or when only an approximate frequency is stated."
            ),
            "items": {"type": "string"},
        },
        "Gain target": {
            "type": "string",
            "description": (
                "Quantitative energy-saving gain target as a percentage number without the '%' "
                "sign (e.g. '30'). Empty when no quantitative gain target is stated. A "
                "throughput or rate target is not a gain target."
            ),
        },
        "Guaranteed throughput target by zone": {
            "type": "array",
            "description": (
                "Area-level time-segmented guaranteed throughput targets, one entry per "
                "non-overlapping segment. Empty for a site-level request."
            ),
            "items": _THROUGHPUT_TARGET_SCHEMA,
        },
        "Scenario-based energy saving": {
            "type": "array",
            "description": (
                "Site-level energy-saving targets. Empty for an area-level request."
            ),
            "items": _SITE_TARGET_SCHEMA,
        },
    },
    "required": ["Operation type"],
}
