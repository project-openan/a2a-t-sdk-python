"""Business scripts of the eight RAN energy-saving demonstration scenarios.

Every scenario carries the natural-language request (both languages), the expected negotiation
kind, the exact performative/round sequence from the design, the expected final state, and the
decisions a scripted (offline) client returns.

The request text is the single source of business values: the page shows it, the client sends it,
and the intent/Task-T layers parse it. There is no built-in intent or parameter table. The
scenario-specific flaws are therefore visible in the text itself:

* the information request omits the time-segmented guaranteed throughput target, so the client
  blocks it before sending (the demo shows the local required-field gate);
* the reject and abort requests name an area the vendor does not serve and carry a complete target;
* the target request carries overlapping windows;
* the feasibility request asks for a rate through an 18-hour outage;
* the unknown-area request names an area the vendor does not serve.

``night`` and ``outage`` are complete requests: the RAN Agent creates the task without a pre-task
negotiation. The planned-outage Modify request is the separate request the presentation puts in the
editor after the operator selects a running Task.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from shared.models import NegotiationDecision
from shared.run_constants import (
    FINAL_STATE_INPUT_INVALID,
    FINAL_STATE_NEGOTIATION_ABORTED,  # noqa: F401 - re-exported for the scenario tests
    FINAL_STATE_NEGOTIATION_ENDED,
    FINAL_STATE_TASK_COMPLETED,
    LANGUAGE_EN,
    LANGUAGE_ZH,
    FinalState,
)

#: Supported scenario names.
SCENARIO_NIGHT = "night"
SCENARIO_INFORMATION = "information"
SCENARIO_UNKNOWN_AREA = "unknown_area"
SCENARIO_TARGET = "target"
SCENARIO_FEASIBILITY = "feasibility"
SCENARIO_REJECT = "reject"
SCENARIO_ABORT = "abort"
SCENARIO_OUTAGE = "outage"

#: Supported negotiation kinds (server-side trigger families). ``KIND_NONE`` marks a complete
#: request that the RAN Agent accepts without opening a pre-task negotiation.
KIND_NONE = "none"
KIND_INFORMATION = "information"
KIND_TARGET = "target"
KIND_FEASIBILITY = "feasibility"

#: The one request shown in the editor and sent to A2A-T, per scenario and language.
EXAMPLE_REQUESTS: dict[str, dict[str, str]] = {
    SCENARIO_NIGHT: {
        LANGUAGE_EN: (
            "Create an energy saving task for Region A with these guaranteed throughputs: "
            "{00:00-17:30,10Mbps}, {17:30-23:00,20Mbps}, {23:00-24:00,10Mbps}. "
            "Output the wireless network energy saving plan and return the intent report."
        ),
        LANGUAGE_ZH: (
            "在区域 A 创建节能任务，保障速率为：{00:00-17:30,10Mbps}、{17:30-23:00,20Mbps}、"
            "{23:00-24:00,10Mbps}。输出无线网络节能方案并返回意图报告。"
        ),
    },
    SCENARIO_INFORMATION: {
        LANGUAGE_EN: (
            "Create an energy saving task. "
            "Output the wireless network energy saving plan and return the intent report."
        ),
        LANGUAGE_ZH: "创建节能任务。输出无线网络节能方案并返回意图报告。",
    },
    SCENARIO_UNKNOWN_AREA: {
        LANGUAGE_EN: (
            "Create an energy saving task for Region Z with these guaranteed throughputs: "
            "{00:00-07:00,2Mbps}, {07:00-23:00,20Mbps}, {23:00-24:00,2Mbps}. "
            "Output the wireless network energy saving plan and return the intent report."
        ),
        LANGUAGE_ZH: (
            "在未知区域创建节能任务，保障速率为：{00:00-07:00,2Mbps}、{07:00-23:00,20Mbps}、"
            "{23:00-24:00,2Mbps}。输出无线网络节能方案并返回意图报告。"
        ),
    },
    SCENARIO_TARGET: {
        LANGUAGE_EN: (
            "Create an energy saving task for Region A with these guaranteed throughputs: "
            "{00:00-07:00,2Mbps}, {07:00-09:00,10Mbps}, {09:00-17:30,10Mbps}, "
            "{17:30-23:00,20Mbps}, {20:00-00:00,10Mbps}. "
            "Output the wireless network energy saving plan and return the intent report."
        ),
        LANGUAGE_ZH: (
            "在区域 A 创建节能任务，保障速率为：{00:00-07:00,2Mbps}、{07:00-09:00,10Mbps}、"
            "{09:00-17:30,10Mbps}、{17:30-23:00,20Mbps}、{20:00-00:00,10Mbps}。"
            "输出无线网络节能方案并返回意图报告。"
        ),
    },
    SCENARIO_FEASIBILITY: {
        LANGUAGE_EN: (
            "Create an energy saving task for NRES_XCS_6102937 and LTE_26441_LNR_OCL in Region A "
            "with these guaranteed throughputs: {00:00-06:00,2Mbps}, {06:00-18:00,10Mbps}. "
            "A power outage runs from 00:00-18:00. Output the wireless network energy saving plan "
            "and return the intent report."
        ),
        LANGUAGE_ZH: (
            "在区域 A 为 NRES_XCS_6102937 和 LTE_26441_LNR_OCL 创建节能任务，保障速率为："
            "{00:00-06:00,2Mbps}、{06:00-18:00,10Mbps}；00:00-18:00 有停电事件。"
            "输出无线网络节能方案并返回意图报告。"
        ),
    },
    SCENARIO_REJECT: {
        LANGUAGE_EN: (
            "Create an energy saving task for Region A. "
            "Output the wireless network energy saving plan and return the intent report."
        ),
        LANGUAGE_ZH: "在区域 A 创建节能任务。输出无线网络节能方案并返回意图报告。",
    },
    SCENARIO_ABORT: {
        LANGUAGE_EN: (
            "Create an energy saving task for Region A. "
            "Output the wireless network energy saving plan and return the intent report."
        ),
        LANGUAGE_ZH: "在区域 A 创建节能任务。输出无线网络节能方案并返回意图报告。",
    },
    SCENARIO_OUTAGE: {
        LANGUAGE_EN: (
            "Create a baseline energy saving task for NRES_XCS_6102937 and LTE_26441_LNR_OCL in "
            "Region A with these guaranteed throughputs: {00:00-24:00,10Mbps}. "
            "Output the wireless network energy saving plan and return the intent report."
        ),
        LANGUAGE_ZH: (
            "在区域 A 为 NRES_XCS_6102937 和 LTE_26441_LNR_OCL 创建节能基线任务，"
            "保障速率为：{00:00-24:00,10Mbps}。输出无线网络节能方案并返回意图报告。"
        ),
    },
}

#: Request the presentation puts in the editor after the operator selects a running Task. It is a
#: Modify-only request: the baseline Task already exists on the NMS.
MODIFY_REQUEST: dict[str, str] = {
    LANGUAGE_EN: (
        "Modify the running energy saving task for NRES_XCS_6102937 and LTE_26441_LNR_OCL in "
        "Region A for the planned outage 03:00-05:00: {00:00-03:00,10Mbps}, "
        "{03:00-05:00,2Mbps}, {05:00-24:00,10Mbps}. "
        "Output the wireless network energy saving plan and return the intent report."
    ),
    LANGUAGE_ZH: (
        "在区域 A 为 NRES_XCS_6102937 和 LTE_26441_LNR_OCL 修改运行中的节能任务"
        "以应对 03:00-05:00 的计划停电：{00:00-03:00,10Mbps}、{03:00-05:00,2Mbps}、"
        "{05:00-24:00,10Mbps}。输出无线网络节能方案并返回意图报告。"
    ),
}


def resolve_modify_request(language: str) -> str:
    """Return the Modify-only request shown in the presentation for one language."""
    return MODIFY_REQUEST.get(language, MODIFY_REQUEST[LANGUAGE_ZH])


#: Corrected target the target negotiation agrees on.
TARGET_TARGET: Final[str] = "{00:00-17:30,10Mbps}, {17:30-23:00,20Mbps}, {23:00-24:00,10Mbps}"

#: Agreed target of the feasibility negotiation (the outage window is lowered to 2 Mbps).
FEASIBILITY_TARGET: Final[str] = "{00:00-06:00,2Mbps}, {06:00-18:00,2Mbps}"

#: Agreed target and region of the unknown-area negotiation.
UNKNOWN_AREA_TARGET: Final[str] = "{00:00-07:00,2Mbps}, {07:00-23:00,20Mbps}, {23:00-24:00,2Mbps}"

#: Site context the feasibility accept carries.
FEASIBILITY_CONTEXT: Final[dict[str, object]] = {
    "siteList": "NRES_XCS_6102937,LTE_26441_LNR_OCL",
    "backgroundEvent": "power outage from 00:00 to 18:00",
}

#: Confirmed intent of the target negotiation accept. A2A-T answers a target proposal with
#: Accept, so the corrected target is the accept content, not a client counter proposal.
TARGET_ACCEPT_CONFIRMATION: Final[str] = (
    f"The energy saving target is corrected to {TARGET_TARGET}."
)

#: Feasibility summary of the feasibility negotiation accept.
FEASIBILITY_ACCEPT_SUMMARY: Final[str] = (
    "Agreed to lower the guaranteed throughput target for 06:00-18:00 from 10Mbps to 2Mbps; "
    "the negotiation is confirmed as complete."
)


@dataclass(frozen=True)
class ExpectedStep:
    """One expected message of a scenario's negotiation sequence.

    Attributes:
        actor: Side that emits the message.
        performative: Expected performative (``PROPOSE``/``ACCEPT``/``REJECT``/``ABORT``), or
            ``MESSAGE`` for the plain end message that carries no Negotiation-T metadata.
        round: Expected negotiation round, or ``None`` when the message carries no negotiation
            context (the plain end message).
    """

    actor: Literal["server", "client"]
    performative: str
    round: int | None


@dataclass(frozen=True)
class Scenario:
    """One demonstration scenario.

    Attributes:
        name: Scenario name (also the CLI value).
        expected_kind: Negotiation kind the server opens (``information``/``target``/
            ``feasibility``).
        user_request: The request shown and sent, per language.
        expected_sequence: Exact performative/round sequence from the design.
        expected_final_state: Terminal state the run must reach.
        scripted_client_decisions: Decisions returned by the offline scripted decision provider,
            in order. They are the deterministic test double of the ADK decision agent.
        description: One-line business description.
    """

    name: str
    expected_kind: str
    user_request: Mapping[str, str]
    expected_sequence: tuple[ExpectedStep, ...]
    expected_final_state: FinalState
    scripted_client_decisions: tuple[NegotiationDecision, ...]
    description: str


SCENARIO_NIGHT_SPEC = Scenario(
    name=SCENARIO_NIGHT,
    expected_kind=KIND_NONE,
    user_request=EXAMPLE_REQUESTS[SCENARIO_NIGHT],
    expected_sequence=(),
    expected_final_state=FINAL_STATE_TASK_COMPLETED,
    scripted_client_decisions=(),
    description=(
        "A complete night-time energy-saving request; the RAN Agent creates the task without a pre-task negotiation."
    ),
)

SCENARIO_INFORMATION_SPEC = Scenario(
    name=SCENARIO_INFORMATION,
    expected_kind=KIND_INFORMATION,
    user_request=EXAMPLE_REQUESTS[SCENARIO_INFORMATION],
    # The request omits the task object. The client catches that locally, so the demo shows the
    # pre-send gate instead of a server negotiation; no scripted decision is consumed. The rate
    # guarantee target is optional (the vendor executes an area-level request without one), so it
    # can no longer serve as the missing field.
    expected_sequence=(),
    expected_final_state=FINAL_STATE_INPUT_INVALID,
    scripted_client_decisions=(),
    description=(
        "Task-T misses the area; the client blocks the request before sending it and asks the "
        "operator to edit it."
    ),
)

SCENARIO_UNKNOWN_AREA_SPEC = Scenario(
    name=SCENARIO_UNKNOWN_AREA,
    expected_kind=KIND_INFORMATION,
    user_request=EXAMPLE_REQUESTS[SCENARIO_UNKNOWN_AREA],
    expected_sequence=(
        ExpectedStep(actor="server", performative="PROPOSE", round=1),
        ExpectedStep(actor="client", performative="ACCEPT", round=1),
    ),
    expected_final_state=FINAL_STATE_TASK_COMPLETED,
    scripted_client_decisions=(
        NegotiationDecision(
            action="accept",
            rationale=(
                "The area is not served; the client confirms Region A and keeps the "
                "requested guaranteed throughput windows."
            ),
            filled_params={"region": "Region A", "guaranteedThroughputTarget": UNKNOWN_AREA_TARGET},
        ),
    ),
    description=(
        "The request names an unknown area; the RAN Agent asks the client to confirm a served area before executing."
    ),
)

SCENARIO_TARGET_SPEC = Scenario(
    name=SCENARIO_TARGET,
    expected_kind=KIND_TARGET,
    user_request=EXAMPLE_REQUESTS[SCENARIO_TARGET],
    expected_sequence=(
        ExpectedStep(actor="server", performative="PROPOSE", round=1),
        ExpectedStep(actor="client", performative="ACCEPT", round=1),
    ),
    expected_final_state=FINAL_STATE_TASK_COMPLETED,
    scripted_client_decisions=(
        NegotiationDecision(
            action="accept",
            rationale="The revised plan has non-overlapping segments and meets the throughput target.",
            filled_params={"guaranteedThroughputTarget": TARGET_TARGET},
            confirmed_intent=TARGET_ACCEPT_CONFIRMATION,
        ),
    ),
    description=(
        "Overlapping throughput segments; the client corrects the target inside its Accept."
    ),
)

SCENARIO_FEASIBILITY_SPEC = Scenario(
    name=SCENARIO_FEASIBILITY,
    expected_kind=KIND_FEASIBILITY,
    user_request=EXAMPLE_REQUESTS[SCENARIO_FEASIBILITY],
    expected_sequence=(
        ExpectedStep(actor="server", performative="PROPOSE", round=1),
        ExpectedStep(actor="client", performative="ACCEPT", round=1),
    ),
    expected_final_state=FINAL_STATE_TASK_COMPLETED,
    scripted_client_decisions=(
        NegotiationDecision(
            action="accept",
            rationale="The outage-aware alternative plan is feasible and keeps the SLA margin.",
            filled_params={"guaranteedThroughputTarget": FEASIBILITY_TARGET, **FEASIBILITY_CONTEXT},
            feasibility_summary=FEASIBILITY_ACCEPT_SUMMARY,
        ),
    ),
    description="A guaranteed rate conflicts with a long power outage; the client accepts the alternative plan.",
)

SCENARIO_REJECT_SPEC = Scenario(
    name=SCENARIO_REJECT,
    expected_kind=KIND_INFORMATION,
    # A complete Task-T that names an area the vendor does not serve. The server asks for a served
    # area; the client refuses to confirm one, so the negotiation ends without a task.
    user_request=EXAMPLE_REQUESTS[SCENARIO_UNKNOWN_AREA],
    expected_sequence=(
        ExpectedStep(actor="server", performative="PROPOSE", round=1),
        ExpectedStep(actor="client", performative="REJECT", round=1),
        ExpectedStep(actor="server", performative="MESSAGE", round=None),
    ),
    expected_final_state=FINAL_STATE_NEGOTIATION_ENDED,
    scripted_client_decisions=(
        NegotiationDecision(
            action="reject",
            rationale="The client cannot confirm a served area for the requested region.",
        ),
    ),
    description=("The client cannot confirm a served area; the negotiation ends without a task."),
)

SCENARIO_ABORT_SPEC = Scenario(
    name=SCENARIO_ABORT,
    # A complete Task-T with an unserved area. The server opens an information negotiation; the
    # client ends it with the A2A-T ABORT primitive instead of a counter-Propose loop.
    expected_kind=KIND_INFORMATION,
    user_request=EXAMPLE_REQUESTS[SCENARIO_UNKNOWN_AREA],
    expected_sequence=(
        ExpectedStep(actor="server", performative="PROPOSE", round=1),
        ExpectedStep(actor="client", performative="ABORT", round=1),
        ExpectedStep(actor="server", performative="MESSAGE", round=None),
    ),
    expected_final_state=FINAL_STATE_NEGOTIATION_ENDED,
    scripted_client_decisions=(
        NegotiationDecision(
            action="abort",
            rationale="The client cannot choose a served area; the negotiation is aborted.",
        ),
    ),
    description=(
        "The client ends the information negotiation with ABORT when it cannot provide a served area."
    ),
)

SCENARIO_OUTAGE_SPEC = Scenario(
    name=SCENARIO_OUTAGE,
    expected_kind=KIND_NONE,
    user_request=EXAMPLE_REQUESTS[SCENARIO_OUTAGE],
    expected_sequence=(),
    expected_final_state=FINAL_STATE_TASK_COMPLETED,
    scripted_client_decisions=(),
    description=(
        "A two-site baseline request in the planned-outage use case; the Modify phase is the "
        "separate selected-Task request."
    ),
)

#: All scenarios keyed by name, in design order.
SCENARIOS: dict[str, Scenario] = {
    SCENARIO_NIGHT_SPEC.name: SCENARIO_NIGHT_SPEC,
    SCENARIO_INFORMATION_SPEC.name: SCENARIO_INFORMATION_SPEC,
    SCENARIO_UNKNOWN_AREA_SPEC.name: SCENARIO_UNKNOWN_AREA_SPEC,
    SCENARIO_TARGET_SPEC.name: SCENARIO_TARGET_SPEC,
    SCENARIO_FEASIBILITY_SPEC.name: SCENARIO_FEASIBILITY_SPEC,
    SCENARIO_REJECT_SPEC.name: SCENARIO_REJECT_SPEC,
    SCENARIO_ABORT_SPEC.name: SCENARIO_ABORT_SPEC,
    SCENARIO_OUTAGE_SPEC.name: SCENARIO_OUTAGE_SPEC,
}

#: Supported scenario names, in design order.
SUPPORTED_SCENARIO_NAMES = tuple(SCENARIOS)

#: Scenarios the plain-A2A comparison supports: one complete request and the missing-parameter
#: request whose negotiation A2A-T exists for.
PLAIN_PROTOCOL_SCENARIOS: tuple[str, ...] = (SCENARIO_NIGHT, SCENARIO_INFORMATION)

#: Scenarios the pure A2A mode supports. The pure form mirrors the A2A-T request list: the text
#: is sent unchanged, and the peer's policy decides whether that text triggers a negotiation.
PURE_PROTOCOL_SCENARIOS: tuple[str, ...] = SUPPORTED_SCENARIO_NAMES


def get_scenario(name: str) -> Scenario:
    """Return the scenario with the given name.

    Raises:
        KeyError: when the name is not one of the supported scenarios.
    """
    try:
        return SCENARIOS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown scenario '{name}'. Supported: {', '.join(SUPPORTED_SCENARIO_NAMES)}") from exc


def resolve_user_request(scenario: Scenario, language: str) -> str:
    """Return the scenario's natural-language request for one language."""
    if language not in scenario.user_request:
        raise KeyError(f"Scenario '{scenario.name}' has no request for language '{language}'")
    return scenario.user_request[language]


def resolve_example_request(name: str, language: str) -> str:
    """Return the example request the presentation shows for one scenario."""
    return resolve_user_request(get_scenario(name), language)


def expected_negotiation_target(
    scenario: Scenario,
    language: str = LANGUAGE_ZH,
) -> str | None:
    """Return the guaranteed-throughput target a completed scenario must agree on.

    Only a negotiation scenario agrees on a target; a complete request already carries it in the
    request text. Scenarios that do not complete (reject/abort) have no agreed target either.
    """
    del language
    if scenario.expected_final_state != FINAL_STATE_TASK_COMPLETED or scenario.expected_kind == KIND_NONE:
        return None
    for decision in reversed(scenario.scripted_client_decisions):
        if decision.action != "accept":
            continue
        value = (decision.filled_params or {}).get("guaranteedThroughputTarget")
        if isinstance(value, str) and value:
            return value
    return None
