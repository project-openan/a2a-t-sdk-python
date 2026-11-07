"""Structured business intent produced by the ADK intent agent.

The schema mirrors :class:`protocol.intent.ProcessedIntent`: ADK parses the model response into
:class:`BusinessIntent`, so the client never reads business fields out of free text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BusinessIntent(BaseModel):
    """Business reading of one operator request.

    Attributes:
        operation_type: ``Create`` or ``Modify``; ambiguous requests are ``Create``.
        region: Target region, kept verbatim even when the request names an unknown one.
        sites: Site names the request names, in order.
        windows: Guaranteed-throughput windows in the notation the request used.
        energy_saving_goal: Goal stated by the request; empty when it states none.
        task_description: What the request asks the task to do.
        expected_output: Expected output stated by the request.
        background_event: Optional context such as a planned outage, kept verbatim.
        missing_fields: Business fields the request does not provide; never invented.
        conflicts: Conflicts the request contains; never repaired.
    """

    operation_type: Literal["Create", "Modify"] = "Create"
    region: str = ""
    sites: list[str] = Field(default_factory=list)
    windows: list[str] = Field(default_factory=list)
    energy_saving_goal: str = ""
    task_description: str = ""
    expected_output: str = ""
    background_event: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
