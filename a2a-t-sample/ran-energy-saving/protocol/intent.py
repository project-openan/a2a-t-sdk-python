"""Protocol-neutral operator intent layer.

A deployment usually receives an operator request through its business/OSS system before any A2A
message leaves the client. That system understands the request, may show it to an engineer, and may
route it to more than one downstream. This module keeps that seam explicit without depending on the
sample's business fields:

* :class:`OperatorRequest` is what the operator (or a gateway) supplied.
* :class:`ProcessedIntent` is the business system's normalized reading of it.
* :class:`IntentProcessor` is the pluggable understanding step.

The intent layer never repairs a missing or conflicting business value. It records the gap so a
later A2A-T negotiation can still happen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

INTENT_MODE_PROCESSED: Final[str] = "processed"
INTENT_MODE_RAW: Final[str] = "raw"
SUPPORTED_INTENT_MODES: Final[tuple[str, ...]] = (INTENT_MODE_PROCESSED, INTENT_MODE_RAW)
DEFAULT_INTENT_MODE: Final[str] = INTENT_MODE_PROCESSED

#: Source labels recorded on a processed intent.
INTENT_SOURCE_SCENARIO: Final[str] = "scenario"
INTENT_SOURCE_MODEL: Final[str] = "model"
INTENT_SOURCE_RAW: Final[str] = "raw_fallback"


@dataclass(frozen=True)
class OperatorRequest:
    """One request as it arrived from the operator-facing surface.

    Attributes:
        text: Raw operator text (Web UI, CLI ``--request`` or an inbound A2A message).
        language: Process-level language of the sample.
        scenario: Catalog scenario, when the caller started one.
        source: ``ui`` / ``cli`` / ``gateway`` / ``test``; used for audit only.
        custom: Whether the text overrides the scenario's canned request.
    """

    text: str
    language: str
    scenario: str | None = None
    source: str = "ui"
    custom: bool = False


@dataclass(frozen=True)
class ProcessedIntent:
    """The business system's normalized reading of an operator request.

    Attributes:
        raw_request: Text the operator supplied.
        normalized_text: Canonical Task-T text sent to A2A-T after approval.
        plain_text: Natural-language text sent on a standard A2A hop. It carries the same
            business values as ``normalized_text`` but no Task-T section headings, so an A2A
            run does not look like an A2A-T prompt.
        operation_type: ``Create`` or ``Modify``.
        region: Target region, preserved even when it is not served.
        sites: Optional site list.
        windows: Time-segmented guaranteed-throughput strings, in their original notation.
        energy_saving_goal: Business goal.
        task_description: Task description understood by the business system.
        expected_output: Expected output of the task.
        background_event: Optional context such as a planned outage.
        missing_fields: Fields the request does not provide; never auto-filled.
        conflicts: Conflicts the business system detected; never auto-corrected.
        source: ``scenario`` / ``model`` / ``raw_fallback``.
        warnings: Human-readable processing warnings.
    """

    raw_request: str
    normalized_text: str
    plain_text: str = ""
    operation_type: str = ""
    region: str = ""
    sites: tuple[str, ...] = ()
    windows: tuple[str, ...] = ()
    energy_saving_goal: str = ""
    task_description: str = ""
    expected_output: str = ""
    background_event: str | None = None
    missing_fields: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    source: str = INTENT_SOURCE_RAW
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def raw(cls, text: str, *, reason: str | None = None) -> ProcessedIntent:
        """Return a passthrough intent used when no processor can understand the request."""
        warnings = (reason,) if reason else ()
        return cls(
            raw_request=text,
            normalized_text=text,
            plain_text=text,
            source=INTENT_SOURCE_RAW,
            warnings=warnings,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly document carried by the UI and the transcript."""
        return {
            "rawRequest": self.raw_request,
            "normalizedText": self.normalized_text,
            "plainText": self.plain_text,
            "operationType": self.operation_type,
            "region": self.region,
            "sites": list(self.sites),
            "windows": list(self.windows),
            "energySavingGoal": self.energy_saving_goal,
            "taskDescription": self.task_description,
            "expectedOutput": self.expected_output,
            "backgroundEvent": self.background_event,
            "missingFields": list(self.missing_fields),
            "conflicts": list(self.conflicts),
            "source": self.source,
            "warnings": list(self.warnings),
        }


@runtime_checkable
class IntentProcessor(Protocol):
    """One business-system intent understanding step."""

    async def process(self, request: OperatorRequest) -> ProcessedIntent:
        """Return the normalized intent of one operator request."""
        ...


class RawIntentProcessor:
    """Intent processor used when the business layer is absent: normalized text is the raw text."""

    async def process(self, request: OperatorRequest) -> ProcessedIntent:
        """Return a passthrough intent."""
        return ProcessedIntent.raw(request.text, reason="No intent processor is configured.")


__all__ = [
    "DEFAULT_INTENT_MODE",
    "INTENT_MODE_PROCESSED",
    "INTENT_MODE_RAW",
    "INTENT_SOURCE_MODEL",
    "INTENT_SOURCE_RAW",
    "INTENT_SOURCE_SCENARIO",
    "SUPPORTED_INTENT_MODES",
    "IntentProcessor",
    "OperatorRequest",
    "ProcessedIntent",
    "RawIntentProcessor",
]
