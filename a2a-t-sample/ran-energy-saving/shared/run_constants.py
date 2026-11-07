"""Protocol-level constants shared by the client, the NMS and the gateway.

These values belong to the interaction contract, not to the demonstration scenarios: the round
budget, the terminal client outcomes and the supported process languages. Keeping them here lets the
core modules import them without depending on the scenario catalog.
"""

from __future__ import annotations

from typing import Final, Literal

#: Round budget of one pre-task negotiation (design D11).
MAX_ROUNDS: Final[int] = 3

#: Whether a client run creates a new A2A Task or continues a running one with a Modify Task-T.
RUN_MODE_CREATE: Final[str] = "create"
RUN_MODE_MODIFY: Final[str] = "modify"
SUPPORTED_RUN_MODES: Final[tuple[str, ...]] = (RUN_MODE_CREATE, RUN_MODE_MODIFY)

#: Supported process-level languages.
LANGUAGE_EN: Final[str] = "en-US"
LANGUAGE_ZH: Final[str] = "zh-CN"
SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = (LANGUAGE_EN, LANGUAGE_ZH)

#: Presentation brand palettes. The default keeps the reference demo's Orange look.
BRAND_ORANGE: Final[str] = "orange"
BRAND_HUAWEI: Final[str] = "huawei"
SUPPORTED_BRANDS: Final[tuple[str, ...]] = (BRAND_ORANGE, BRAND_HUAWEI)

#: Interaction protocol of one client run. ``a2a-t`` uses Task-T and Negotiation-T; ``plain``
#: sends one natural-language A2A message and has neither a task template nor a negotiation;
#: ``pure`` uses standard A2A Task states (``INPUT_REQUIRED`` continuation) for a multi-turn
#: negotiation without any A2A-T extension.
PROTOCOL_A2A_T: Final[str] = "a2a-t"
PROTOCOL_PLAIN: Final[str] = "plain"
PROTOCOL_PURE: Final[str] = "pure"
#: User-facing name of the standard-A2A request form (the CLI accepts it as ``--protocol a2a``).
#: The internal value remains :data:`PROTOCOL_PURE` for API/DB/backward compatibility.
PROTOCOL_A2A_NAME: Final[str] = "a2a"
SUPPORTED_PROTOCOLS: Final[tuple[str, ...]] = (
    PROTOCOL_A2A_T,
    PROTOCOL_PLAIN,
    PROTOCOL_PURE,
)


def normalize_protocol_name(value: str) -> str:
    """Map the user-facing ``a2a`` protocol name to its internal value."""
    return PROTOCOL_PURE if value == PROTOCOL_A2A_NAME else value


#: Protocols exposed by the presentation/CLI demo entry points. ``plain`` stays supported by
#: ``/api/runs`` and the tests as an internal single-turn baseline, but is not advertised.
DEMO_PROTOCOLS: Final[tuple[str, ...]] = (PROTOCOL_A2A_T, PROTOCOL_PURE)

#: Server-side mode of the single-agent server. A2A-T is an optional extension of A2A, so one
#: process serves both request forms and decides per request whether the extension was used.
#: This is not a client protocol and never appears in :data:`SUPPORTED_PROTOCOLS`.
PROTOCOL_MODE_BOTH: Final[str] = "both"

#: Accepted values of the server's ``A2AT_SAMPLE_PROTOCOL_MODE`` setting.
SERVER_PROTOCOL_MODES: Final[tuple[str, ...]] = (
    PROTOCOL_A2A_T,
    PROTOCOL_PURE,
    PROTOCOL_MODE_BOTH,
)

#: Terminal client outcomes.
FinalState = Literal[
    "task_completed",
    "no_task_negotiation_ended",
    "no_task_negotiation_aborted",
    "intent_not_confirmed",
    "input_invalid",
    "decision_ungrounded",
    "task_rejected",
    "task_failed",
    "incomplete",
]

FINAL_STATE_TASK_COMPLETED: Final[FinalState] = "task_completed"
FINAL_STATE_NEGOTIATION_ENDED: Final[FinalState] = "no_task_negotiation_ended"
FINAL_STATE_NEGOTIATION_ABORTED: Final[FinalState] = "no_task_negotiation_aborted"
FINAL_STATE_INTENT_NOT_CONFIRMED: Final[FinalState] = "intent_not_confirmed"
#: The client-side Task-T validation blocked the send before any negotiation or network call.
FINAL_STATE_INPUT_INVALID: Final[FinalState] = "input_invalid"
#: The automatic decision carried a value that no operator, server proposal or authoritative tool
#: result grounds. The client refuses to send the ACCEPT and closes the negotiation with an ABORT.
FINAL_STATE_DECISION_UNGROUNDED: Final[FinalState] = "decision_ungrounded"
FINAL_STATE_TASK_REJECTED: Final[FinalState] = "task_rejected"
FINAL_STATE_TASK_FAILED: Final[FinalState] = "task_failed"
FINAL_STATE_INCOMPLETE: Final[FinalState] = "incomplete"
