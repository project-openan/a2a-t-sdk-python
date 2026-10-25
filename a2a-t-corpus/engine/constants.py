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
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "case_file_variants",
    "localized_case_file",
]

#: Default prompt and case-file language of the corpus (the committed cases are zh-CN).
DEFAULT_LANGUAGE: Final[str] = "zh-CN"

#: Languages the SDK bundles prompt resources for and the corpus accepts as case-file variants.
SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset({"zh-CN", "en-US"})

#: Case file consumed by the natural-language workflow suites.
INPUT_CASE_FROM_TEXT: Final[str] = "input_case_from_text.json"

#: Case file consumed by the structured-input workflow suites.
INPUT_CASE_FROM_DATA: Final[str] = "input_case_from_data.json"

#: Flow tag of the natural-language suites (transcript and summary file names).
FLOW_FROM_TEXT: Final[str] = "from_text"

#: Flow tag of the structured-input suites.
FLOW_FROM_DATA: Final[str] = "from_data"


def localized_case_file(base_name: str, language: str) -> str:
    """Return the case-file name of one language.

    The committed zh-CN cases keep the historical ``input_case_from_*.json`` names; every other
    supported language appends its locale tag before the extension, for example
    ``input_case_from_text_en-US.json``.

    Args:
        base_name: language-neutral case-file name such as ``input_case_from_text.json``.
        language: locale identifier such as ``zh-CN`` or ``en-US``.

    Returns:
        the language-specific case-file name.
    """
    if language == DEFAULT_LANGUAGE:
        return base_name
    stem, separator, suffix = base_name.rpartition(".")
    if not separator:
        return f"{base_name}_{language}"
    return f"{stem}_{language}.{suffix}"


def case_file_variants(base_name: str) -> tuple[str, ...]:
    """Return the default case file followed by every optional language variant.

    Args:
        base_name: language-neutral case-file name such as ``input_case_from_text.json``.

    Returns:
        the default-language name first, then one name per non-default supported language in
        sorted order.
    """
    others = (language for language in sorted(SUPPORTED_LANGUAGES) if language != DEFAULT_LANGUAGE)
    return (base_name, *(localized_case_file(base_name, language) for language in others))

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
