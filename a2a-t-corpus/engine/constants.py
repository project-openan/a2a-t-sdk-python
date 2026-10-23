"""Shared constants of the accuracy verification corpus (port of the Java ``CorpusWorkFlowSuite`` constants)."""

from __future__ import annotations

from typing import Final

__all__ = [
    "API_GENERATE_TASK_FROM_DATA",
    "API_GENERATE_TASK_FROM_TEXT",
    "API_VALIDATE_TASK",
    "TASK_API_NAMES",
    "API_GENERATE_NEGOTIATION_PROPOSE_FROM_TEXT",
    "API_GENERATE_NEGOTIATION_ACCEPT_FROM_TEXT",
    "API_GENERATE_NEGOTIATION_REJECT_FROM_TEXT",
    "API_GENERATE_NEGOTIATION_ABORT_FROM_TEXT",
    "API_GENERATE_NEGOTIATION_PROPOSE_FROM_DATA",
    "API_GENERATE_NEGOTIATION_ACCEPT_FROM_DATA",
    "API_GENERATE_NEGOTIATION_REJECT_FROM_DATA",
    "API_GENERATE_NEGOTIATION_ABORT_FROM_DATA",
    "API_VALIDATE_NEGOTIATION_PROPOSE",
    "API_VALIDATE_NEGOTIATION_ACCEPT",
    "API_VALIDATE_NEGOTIATION_REJECT",
    "API_VALIDATE_NEGOTIATION_ABORT",
    "NEGOTIATION_API_NAMES",
    "API_GENERATE_NOTIFICATION_FROM_TEXT",
    "API_GENERATE_NOTIFICATION_FROM_DATA",
    "API_VALIDATE_NOTIFICATION",
    "NOTIFICATION_API_NAMES",
    "FLOW_FROM_DATA",
    "FLOW_FROM_TEXT",
    "INPUT_CASE_FROM_DATA",
    "INPUT_CASE_FROM_TEXT",
]

#: Case file consumed by the natural-language workflow suites.
INPUT_CASE_FROM_TEXT: Final[str] = "input_case_from_text.json"

#: Case file consumed by the structured-input workflow suites.
INPUT_CASE_FROM_DATA: Final[str] = "input_case_from_data.json"

#: Flow tag of the natural-language suites (transcript and summary file names).
FLOW_FROM_TEXT: Final[str] = "from_text"

#: Flow tag of the structured-input suites.
FLOW_FROM_DATA: Final[str] = "from_data"

# ---------- Task-T ----------

#: Task-T client generation API (natural language -> task prompt).
API_GENERATE_TASK_FROM_TEXT: Final[str] = "generateTaskPromptFromText"

#: Task-T client generation API (structured data -> task prompt).
API_GENERATE_TASK_FROM_DATA: Final[str] = "generateTaskPromptFromDataWithSchema"

#: Task-T server validation API (prompt validation + parameter extraction).
API_VALIDATE_TASK: Final[str] = "validateTaskPromptAndDataFilling"

#: Task-T phase-1 API names, used for load-time membership checks without touching the LLM runtime.
TASK_API_NAMES: Final[frozenset[str]] = frozenset(
    {API_GENERATE_TASK_FROM_TEXT, API_GENERATE_TASK_FROM_DATA, API_VALIDATE_TASK}
)

# ---------- Negotiation-T ----------

#: Negotiation-T from-text generation APIs.
API_GENERATE_NEGOTIATION_PROPOSE_FROM_TEXT: Final[str] = "generateNegotiationProposePromptFromText"
API_GENERATE_NEGOTIATION_ACCEPT_FROM_TEXT: Final[str] = "generateNegotiationAcceptPromptFromText"
API_GENERATE_NEGOTIATION_REJECT_FROM_TEXT: Final[str] = "generateNegotiationRejectPromptFromText"
API_GENERATE_NEGOTIATION_ABORT_FROM_TEXT: Final[str] = "generateNegotiationAbortPromptFromText"

#: Negotiation-T from-data generation APIs.
API_GENERATE_NEGOTIATION_PROPOSE_FROM_DATA: Final[str] = "generateNegotiationProposePromptFromData"
API_GENERATE_NEGOTIATION_ACCEPT_FROM_DATA: Final[str] = "generateNegotiationAcceptPromptFromData"
API_GENERATE_NEGOTIATION_REJECT_FROM_DATA: Final[str] = "generateNegotiationRejectPromptFromData"
API_GENERATE_NEGOTIATION_ABORT_FROM_DATA: Final[str] = "generateNegotiationAbortPromptFromData"

#: Negotiation-T validation APIs.
API_VALIDATE_NEGOTIATION_PROPOSE: Final[str] = "validateProposePromptAndDataFilling"
API_VALIDATE_NEGOTIATION_ACCEPT: Final[str] = "validateAcceptPromptAndDataFilling"
API_VALIDATE_NEGOTIATION_REJECT: Final[str] = "validateRejectPromptAndDataFilling"
API_VALIDATE_NEGOTIATION_ABORT: Final[str] = "validateAbortPromptAndDataFilling"

#: Full set of Negotiation-T phase-1 API names.
NEGOTIATION_API_NAMES: Final[frozenset[str]] = frozenset({
    API_GENERATE_NEGOTIATION_PROPOSE_FROM_TEXT,
    API_GENERATE_NEGOTIATION_ACCEPT_FROM_TEXT,
    API_GENERATE_NEGOTIATION_REJECT_FROM_TEXT,
    API_GENERATE_NEGOTIATION_ABORT_FROM_TEXT,
    API_GENERATE_NEGOTIATION_PROPOSE_FROM_DATA,
    API_GENERATE_NEGOTIATION_ACCEPT_FROM_DATA,
    API_GENERATE_NEGOTIATION_REJECT_FROM_DATA,
    API_GENERATE_NEGOTIATION_ABORT_FROM_DATA,
    API_VALIDATE_NEGOTIATION_PROPOSE,
    API_VALIDATE_NEGOTIATION_ACCEPT,
    API_VALIDATE_NEGOTIATION_REJECT,
    API_VALIDATE_NEGOTIATION_ABORT,
})

# ---------- Notification-T ----------

#: Notification-T client generation API (natural language -> notification prompt).
API_GENERATE_NOTIFICATION_FROM_TEXT: Final[str] = "generateNotificationPromptFromText"

#: Notification-T client generation API (structured data -> notification prompt).
API_GENERATE_NOTIFICATION_FROM_DATA: Final[str] = "generateNotificationPromptFromDataWithSchema"

#: Notification-T server validation API (prompt validation + parameter extraction).
API_VALIDATE_NOTIFICATION: Final[str] = "validateNotificationPromptAndDataFilling"

#: Full set of Notification-T phase-1 API names.
NOTIFICATION_API_NAMES: Final[frozenset[str]] = frozenset(
    {API_GENERATE_NOTIFICATION_FROM_TEXT, API_GENERATE_NOTIFICATION_FROM_DATA, API_VALIDATE_NOTIFICATION}
)
