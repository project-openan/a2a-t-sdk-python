"""Caller-provided parameter schema for ``validate_task_prompt_and_data_filling``.

The schema is shared with the client's negotiation round (which regenerates the filled Task-T prompt
from it) and therefore lives in ``common.task_schema``; this module re-exports it so the server-side
imports stay unchanged.
"""

from __future__ import annotations

from common.task_schema import TASK_PARAM_SCHEMA

__all__ = ["TASK_PARAM_SCHEMA"]
