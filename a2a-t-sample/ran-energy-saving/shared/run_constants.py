"""Protocol-level constants shared by the client, the NMS and the gateway.

These values belong to the interaction contract, not to the demonstration scenarios: the round
budget, the terminal client outcomes and the supported process languages. Keeping them here lets the
core modules import them without depending on the scenario catalog.
"""

from __future__ import annotations

from typing import Final, Literal

#: Round budget of one pre-task negotiation (design D11).
MAX_ROUNDS: Final[int] = 3

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
SUPPORTED_PROTOCOLS: Final[tuple[str, ...]] = (
    PROTOCOL_A2A_T,
    PROTOCOL_PLAIN,
    PROTOCOL_PURE,
)

#: Protocols exposed by the presentation/CLI demo entry points. ``plain`` stays supported by
#: ``/api/runs`` and the tests as an internal single-turn baseline, but is not advertised.
DEMO_PROTOCOLS: Final[tuple[str, ...]] = (PROTOCOL_A2A_T, PROTOCOL_PURE)

#: Terminal client outcomes.
FinalState = Literal[
    "task_completed",
    "no_task_negotiation_ended",
    "no_task_negotiation_aborted",
    "intent_not_confirmed",
    "task_rejected",
    "task_failed",
    "incomplete",
]

FINAL_STATE_TASK_COMPLETED: Final[FinalState] = "task_completed"
FINAL_STATE_NEGOTIATION_ENDED: Final[FinalState] = "no_task_negotiation_ended"
FINAL_STATE_NEGOTIATION_ABORTED: Final[FinalState] = "no_task_negotiation_aborted"
FINAL_STATE_INTENT_NOT_CONFIRMED: Final[FinalState] = "intent_not_confirmed"
FINAL_STATE_TASK_REJECTED: Final[FinalState] = "task_rejected"
FINAL_STATE_TASK_FAILED: Final[FinalState] = "task_failed"
FINAL_STATE_INCOMPLETE: Final[FinalState] = "incomplete"
