"""Business scripts of the eight RAN energy-saving demonstration scenarios.

Every scenario carries the client-side natural-language request (both languages), the expected
negotiation kind, the exact performative/round sequence from the design, the expected final state,
and the decisions a scripted (offline) client returns. Tests assert the sequence and the rounds
instead of log text.

``night`` and ``outage`` are complete requests: the RAN Agent creates the task without a pre-task
negotiation. ``night`` uses the area-level complete request, while ``outage`` carries the Site 1 /
Site 2 planned-outage context of use case 2.

The ``reject`` scenario reuses the missing-throughput request: the server opens an ``information``
negotiation and the client rejects because it cannot provide the missing content. The ``abort``
scenario uses the same information request and keeps countering with the requested region instead
of the missing target until the server aborts at ``maxRounds``. This mirrors the real NMS, which
opens an information negotiation for the unresolved region before any target check.

The information request deliberately omits the time-segmented guaranteed throughput target
(``task_context``): the Task-T template marks that slot optional, so the client can still render
the prompt, while the server's caller schema marks it required and opens the information
negotiation. Omitting the region would instead fail the client's required-slot validation before
any A2A message is sent, because the template marks ``task_object`` required.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from shared.models import NegotiationDecision
from shared.run_constants import (
    FINAL_STATE_NEGOTIATION_ABORTED,
    FINAL_STATE_NEGOTIATION_ENDED,
    FINAL_STATE_TASK_COMPLETED,
    LANGUAGE_EN,
    LANGUAGE_ZH,
    FinalState,
)

from demo.served_areas import REGION_STRINGS

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

#: Chinese natural-language task request used for prompt generation (UC1, complete input).
NATURAL_LANGUAGE_PROMPT_INPUT_ZH = (
    "在区域 A 创建无线网络节能任务：基于任务对象和任务上下文，输出无线网络节能方案，"
    "达成任务目标中定义的无线网络节能目标，并按照预期输出中定义的格式返回无线网络节能结果；"
    "在满足任务上下文的前提下最大化节能；分时段保障速率目标为 {00:00~17:30,10Mbps}、"
    "{17:30~23:00,20Mbps}、{23:00~23:59,10Mbps}。"
    "预期输出为无线网络节能任务的意图报告。"
)

#: English natural-language task request used for prompt generation (UC1, complete input).
NATURAL_LANGUAGE_PROMPT_INPUT_EN = (
    "Create a RAN energy saving task in Region A to maximize "
    "energy saving. Based on the task object and task context, output a wireless network energy "
    "saving plan that achieves the energy saving goal defined in the task target and returns the "
    "energy saving result in the format defined in the expected output. The time-segment-based "
    "guaranteed throughput target is {00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, "
    "{23:00~23:59,10Mbps}."
    "The expected output is the intent report of the wireless network energy saving task."
)

#: Chinese natural-language task request of use case 2 (planned power outage, complete input).
NATURAL_LANGUAGE_PROMPT_INPUT_OUTAGE_ZH = (
    "修改区域 A 运行中的无线网络节能任务：基于任务对象和任务上下文，输出无线网络节能方案，"
    "达成任务目标中定义的无线网络节能目标；站点列表1为[站点1, 站点2]；明天计划停电，"
    "停电期间 03:00~05:00 将分时段保障速率下调到 2Mbps 以节省电池，停电之外保持 "
    "{00:00~03:00,10Mbps}、{05:00~24:00,10Mbps}。预期输出为无线网络节能任务的意图报告。"
)

#: English natural-language task request of use case 2 (planned power outage, complete input).
NATURAL_LANGUAGE_PROMPT_INPUT_OUTAGE_EN = (
    "Modify the running RAN energy saving task in Region A. "
    "Based on the task object and task context, output a wireless network energy saving plan that "
    "achieves the energy saving goal defined in the task target. Station list 1 is "
    '["Site 1", "Site 2"]. Planned power outage tomorrow; during the outage, 03:00~05:00, '
    "lower the guaranteed throughput to 2Mbps to save battery; outside the outage, keep "
    "{00:00~03:00,10Mbps} and {05:00~24:00,10Mbps}."
    "The expected output is the intent report of the wireless network energy saving task."
)

#: Chinese natural-language task request of the information negotiation: the region is present but
#: the time-segmented guaranteed throughput target is omitted. The Task-T template's
#: ``task_context`` slot is optional, so the client renders the prompt; the server's caller schema
#: requires ``guaranteedThroughputTarget`` and reports the missing-parameter content error.
NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_ZH = (
    "在区域 A 创建无线网络节能任务：基于任务对象和任务上下文，输出无线网络节能方案，"
    "达成任务目标中定义的无线网络节能目标，并按照预期输出中定义的格式返回无线网络节能结果；"
    "在满足任务上下文的前提下最大化节能。"
    "预期输出为无线网络节能任务的意图报告。"
)

#: English natural-language task request of the information negotiation (throughput target
#: omitted, region present). The explicit task-description clause keeps the request rich enough for
#: a live model to fill the template's required ``task_description`` slot; the omitted slot remains
#: only the time-segmented guaranteed throughput target.
NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN = (
    "Create a RAN energy saving task in Region A. Based on the "
    "task object and task context, output a wireless network energy saving plan that achieves the "
    "energy saving goal defined in the task target and returns the energy saving result in the "
    "format defined in the expected output; maximize energy saving under the task context."
    "The expected output is the intent report of the wireless network energy saving task."
)

#: Chinese natural-language task request of the unknown-area negotiation: the area does not exist
#: in the RAN dataset, so the server asks the client to confirm a served area.
NATURAL_LANGUAGE_PROMPT_INPUT_UNKNOWN_ZH = (
    "在未知区域创建无线网络节能任务：基于任务对象和任务上下文，输出无线网络节能方案，"
    "达成任务目标中定义的无线网络节能目标，并按照预期输出中定义的格式返回无线网络节能结果；"
    "在满足任务上下文的前提下最大化节能；分时段保障速率目标为 {00:00~07:00,2Mbps}、"
    "{07:00~23:00,20Mbps}、{23:00~24:00,2Mbps}。"
    "预期输出为无线网络节能任务的意图报告。"
)

#: English natural-language task request of the unknown-area negotiation.
NATURAL_LANGUAGE_PROMPT_INPUT_UNKNOWN_EN = (
    "Create a RAN energy saving task in Region Z. Based on the task object and task context, "
    "output a wireless network energy saving plan that achieves the energy saving goal defined "
    "in the task target and returns the energy saving result in the format defined in the "
    "expected output. The time-segment-based guaranteed throughput target is "
    "{00:00~07:00,2Mbps}, {07:00~23:00,20Mbps}, {23:00~24:00,2Mbps}. "
    "The expected output is the intent report of the wireless network energy saving task."
)

#: Chinese natural-language task request of the target negotiation: the guaranteed throughput
#: target has two overlapping time segments, so the Task-T validation reports the
#: value-not-allowed content error.
NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_ZH = (
    "在区域 A 创建无线网络节能任务：基于任务对象和任务上下文，输出无线网络节能方案，"
    "达成任务目标中定义的无线网络节能目标，并按照预期输出中定义的格式返回无线网络节能结果；"
    "在满足任务上下文的前提下最大化节能；分时段保障速率目标为 {00:00~07:00,2Mbps}、"
    "{07:00~09:00,10Mbps}、{09:00~17:30,10Mbps}、{17:30~23:00,20Mbps}、{20:00~00:00,10Mbps}。"
    "预期输出为无线网络节能任务的意图报告。"
)

#: English natural-language task request of the target negotiation (overlapping segments).
NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN = (
    "Create a RAN energy saving task in Region A to maximize "
    "energy saving. Based on the task object and task context, output a wireless network energy "
    "saving plan that achieves the energy saving goal defined in the task target and returns the "
    "energy saving result in the format defined in the expected output. The time-segment-based "
    "guaranteed throughput target is {00:00~07:00,2Mbps}, {07:00~09:00,10Mbps}, "
    "{09:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {20:00~00:00,10Mbps}."
    "The expected output is the intent report of the wireless network energy saving task."
)

#: Chinese natural-language task request of the feasibility negotiation: two stations must keep a
#: guaranteed rate through an 18-hour power outage, so the Task-T validation reports the
#: rule-violation content error.
NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_ZH = (
    "在区域 A 创建无线网络节能任务：基于任务对象和任务上下文，输出无线网络节能方案，"
    "达成任务目标中定义的无线网络节能目标，并按照预期输出中定义的格式返回无线网络节能结果；"
    "站点列表1为[站点名称1, 站点名称2]；分时段保障速率目标为 {00:00~06:00,2Mbps}、"
    "{06:00~18:00,10Mbps}；背景事件为在00:00~18:00停电；在满足任务上下文的前提下最大化节能。"
    "预期输出为无线网络节能任务的意图报告。"
)

#: English natural-language task request of the feasibility negotiation (outage case).
NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_EN = (
    "Create a RAN energy saving task in Region A to maximize "
    "energy saving. Based on the task object and task context, output a wireless network energy "
    "saving plan that achieves the energy saving goal defined in the task target and returns the "
    "energy saving result in the format defined in the expected output. Station list 1 is "
    "[Station 1, Station 2]; the time-segment-based guaranteed throughput target is "
    "{00:00~06:00,2Mbps}, {06:00~18:00,10Mbps}; the background event is a power outage from "
    "00:00 to 18:00."
    "The expected output is the intent report of the wireless network energy saving task."
)

#: Presentation-only requests. The wire prompt keeps the longer, template-oriented wording because
#: a live model fills the Task-T slots from it; the page shows what a network engineer would
#: actually type, in the reference demo's concise style. The UI omits ``userRequest`` while the
#: textarea still matches this text, so the run keeps using the rich prompt.
PRESENTATION_REQUESTS: dict[str, dict[str, str]] = {
    SCENARIO_NIGHT: {
        LANGUAGE_EN: (
            "Start energy saving in Region A with these guaranteed throughputs: "
            "00:00–17:30 at 10 Mbps, 17:30–23:00 at 20 Mbps, 23:00–24:00 at 10 Mbps."
        ),
        LANGUAGE_ZH: ("在区域 A 开启节能，保障速率为：00:00–17:30 10 Mbps，17:30–23:00 20 Mbps，23:00–24:00 10 Mbps。"),
    },
    SCENARIO_INFORMATION: {
        LANGUAGE_EN: "Start energy saving in Region A.",
        LANGUAGE_ZH: "在区域 A 开启节能。",
    },
    SCENARIO_UNKNOWN_AREA: {
        LANGUAGE_EN: (
            "Start energy saving in Region Z with these guaranteed throughputs: "
            "00:00–07:00 at 2 Mbps, 07:00–23:00 at 20 Mbps, 23:00–24:00 at 2 Mbps."
        ),
        LANGUAGE_ZH: ("在未知区域开启节能，保障速率为：00:00–07:00 2 Mbps，07:00–23:00 20 Mbps，23:00–24:00 2 Mbps。"),
    },
    SCENARIO_TARGET: {
        LANGUAGE_EN: (
            "Start energy saving in Region A with these guaranteed throughputs: "
            "00:00–07:00 at 2 Mbps, 07:00–09:00 at 10 Mbps, 09:00–17:30 at 10 Mbps, "
            "17:30–23:00 at 20 Mbps, 20:00–00:00 at 10 Mbps."
        ),
        LANGUAGE_ZH: (
            "在区域 A 开启节能，保障速率为：00:00–07:00 2 Mbps，07:00–09:00 10 Mbps，"
            "09:00–17:30 10 Mbps，17:30–23:00 20 Mbps，20:00–00:00 10 Mbps。"
        ),
    },
    SCENARIO_FEASIBILITY: {
        LANGUAGE_EN: (
            "Start energy saving for Site 1 and Site 2 in Region A: "
            "00:00–06:00 at 2 Mbps and 06:00–18:00 at 10 Mbps. A power outage runs "
            "from 00:00–18:00."
        ),
        LANGUAGE_ZH: (
            "在区域 A 为站点 1 和站点 2 开启节能：00:00–06:00 2 Mbps，06:00–18:00 10 Mbps；00:00–18:00 有停电事件。"
        ),
    },
    SCENARIO_OUTAGE: {
        LANGUAGE_EN: (
            "Create a baseline energy-saving task for Site 1 and Site 2 in Region A, "
            "then modify the running task for tomorrow's 03:00–05:00 planned outage: keep 2 Mbps "
            "during the outage and 10 Mbps otherwise."
        ),
        LANGUAGE_ZH: (
            "在区域 A 为站点 1 和站点 2 创建节能基线任务，然后修改运行中的任务以应对明天 "
            "03:00–05:00 的计划停电：停电期间保障 2 Mbps，其余时间 10 Mbps。"
        ),
    },
    SCENARIO_REJECT: {
        LANGUAGE_EN: "Start energy saving in Region A.",
        LANGUAGE_ZH: "在区域 A 开启节能。",
    },
    SCENARIO_ABORT: {
        LANGUAGE_EN: (
            "Start energy saving in Region A with these guaranteed throughputs: "
            "00:00–07:00 at 2 Mbps, 07:00–09:00 at 10 Mbps, 09:00–17:30 at 10 Mbps, "
            "17:30–23:00 at 20 Mbps, 20:00–00:00 at 10 Mbps."
        ),
        LANGUAGE_ZH: (
            "在区域 A 开启节能，保障速率为：00:00–07:00 2 Mbps，07:00–09:00 10 Mbps，"
            "09:00–17:30 10 Mbps，17:30–23:00 20 Mbps，20:00–00:00 10 Mbps。"
        ),
    },
}

#: Request the presentation puts in the editor after the operator selects a running Task. It is a
#: Modify-only request: unlike :data:`PRESENTATION_REQUESTS` for ``outage`` it never asks for a
#: baseline Create, because the baseline Task already exists on the NMS.
MODIFY_REQUEST: dict[str, str] = {
    LANGUAGE_EN: (
        "Modify the running energy-saving task for tomorrow's 03:00–05:00 planned outage: "
        "keep 2 Mbps during the outage and 10 Mbps otherwise."
    ),
    LANGUAGE_ZH: (
        "修改运行中的节能任务以应对明天 03:00–05:00 的计划停电："
        "停电期间保障 2 Mbps，其余时间 10 Mbps。"
    ),
}


def resolve_modify_request(language: str) -> str:
    """Return the Modify-only request shown in the presentation for one language."""
    return MODIFY_REQUEST.get(language, MODIFY_REQUEST[LANGUAGE_ZH])


#: Task-T parameters the client refills on accept, per language. The base set is the complete,
#: non-overlapping request; the information kind uses it to supply the throughput target it omitted
#: on purpose, while the target and feasibility kinds override the guaranteed-throughput target with
#: the value their negotiation agrees on.
_ACCEPT_BASE_PARAMS: dict[str, dict[str, object]] = {
    LANGUAGE_EN: {
        "operationType": "Create",
        # Task-T requires task_description; data-based generation validates the same slot schema
        # as text-based generation, so the fixed Create description must be supplied explicitly.
        "task_description": (
            "Based on <Task Object> and <Task Context>, output a wireless network energy saving "
            "plan that achieves the wireless network energy saving goal defined in <Task Target>, "
            "and return the wireless network energy saving result in the format defined in "
            "<Expected Output>."
        ),
        "region": REGION_STRINGS[LANGUAGE_EN],
        "energySavingGoal": "Maximize energy saving under the condition that the task context is met.",
        "guaranteedThroughputTarget": ("{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~23:59,10Mbps}"),
        "expectedOutput": "Output the intent report of the wireless network energy saving task.",
    },
    LANGUAGE_ZH: {
        "operationType": "Create",
        "task_description": (
            "基于任务对象和任务上下文，输出无线网络节能方案，达成任务目标中定义的无线网络节能目标，"
            "并按照预期输出中定义的格式返回无线网络节能结果。"
        ),
        "region": REGION_STRINGS[LANGUAGE_ZH],
        "energySavingGoal": "在满足任务上下文的前提下最大化节能。",
        "guaranteedThroughputTarget": ("{00:00~17:30,10Mbps}、{17:30~23:00,20Mbps}、{23:00~23:59,10Mbps}"),
        "expectedOutput": "输出无线网络节能任务的意图报告。",
    },
}

#: Task-T data schema used by the structured initial generation of complete requests.
TASK_T_DATA_SCHEMA: dict[str, dict[str, object]] = {
    LANGUAGE_EN: {
        "operationType": "operation type: Create or Modify",
        "task_description": "task description",
        "region": "region name",
        "energySavingGoal": "energy saving goal",
        "guaranteedThroughputTarget": ("time-segmented guaranteed throughput target, format {HH:MM~HH:MM,NMbps}"),
        "expectedOutput": "expected output of the task",
        "siteList": "site list",
        "backgroundEvent": "background event, for example a planned power outage",
    },
    LANGUAGE_ZH: {
        "operationType": "操作类型：创建或修改",
        "task_description": "任务描述",
        "region": "区域名称",
        "energySavingGoal": "节能目标",
        "guaranteedThroughputTarget": "分时段保障速率目标，格式 {HH:MM~HH:MM,NMbps}",
        "expectedOutput": "任务的预期输出",
        "siteList": "站点列表",
        "backgroundEvent": "背景事件，例如计划停电",
    },
}

#: Values a complete scenario supplies on top of the base parameters, per language.
_COMPLETE_INITIAL_OVERRIDES: dict[str, dict[str, dict[str, str]]] = {
    SCENARIO_UNKNOWN_AREA: {
        LANGUAGE_EN: {
            "region": "Region Z",
            "guaranteedThroughputTarget": ("{00:00~07:00,2Mbps}, {07:00~23:00,20Mbps}, {23:00~24:00,2Mbps}"),
        },
        LANGUAGE_ZH: {
            "region": "未知区域",
            "guaranteedThroughputTarget": ("{00:00~07:00,2Mbps}、{07:00~23:00,20Mbps}、{23:00~24:00,2Mbps}"),
        },
    },
    SCENARIO_OUTAGE: {
        # Phase 1 creates the normal two-site baseline; the planned outage is introduced by the
        # follow-up Modify Task-T, so this baseline carries no background event.
        LANGUAGE_EN: {
            "siteList": "Site 1,Site 2",
            "guaranteedThroughputTarget": "{00:00~24:00,10Mbps}",
        },
        LANGUAGE_ZH: {
            "siteList": "站点1,站点2",
            "guaranteedThroughputTarget": "{00:00~24:00,10Mbps}",
        },
    },
}

#: Values the follow-up Modify Task-T supplies on top of the same base parameters, per language.
#: Only the planned-outage scenario has a follow-up; the other complete scenarios stay single-phase.
_FOLLOW_UP_OVERRIDES: dict[str, dict[str, dict[str, str]]] = {
    SCENARIO_OUTAGE: {
        LANGUAGE_EN: {
            "operationType": "Modify",
            "task_description": (
                "Based on <Task Object> and <Task Context>, update the running wireless network "
                "energy saving task and output a plan that achieves the energy saving goal defined "
                "in <Task Target> for the planned outage."
            ),
            "siteList": "Site 1,Site 2",
            "backgroundEvent": "planned power outage tomorrow 03:00~05:00",
            "guaranteedThroughputTarget": ("{00:00~03:00,10Mbps}, {03:00~05:00,2Mbps}, {05:00~24:00,10Mbps}"),
        },
        LANGUAGE_ZH: {
            "operationType": "Modify",
            "task_description": (
                "基于任务对象和任务上下文，修改运行中的无线网络节能任务，并输出在计划停电期间"
                "达成任务目标中定义的无线网络节能目标的方案。"
            ),
            "siteList": "站点1,站点2",
            "backgroundEvent": "明天 03:00~05:00 计划停电",
            "guaranteedThroughputTarget": ("{00:00~03:00,10Mbps}、{03:00~05:00,2Mbps}、{05:00~24:00,10Mbps}"),
        },
    },
}


def complete_initial_params(name: str, language: str = LANGUAGE_ZH) -> dict[str, object] | None:
    """Return the complete Task-T parameters of a complete scenario, or ``None``.

    Complete scenarios (``night`` and ``outage``) must never open a pre-task negotiation. Their
    Task-T is generated from these explicit values instead of relying on a live model to extract
    every required slot from free text; negotiation scenarios keep the text path so their flawed
    input can trigger the intended negotiation. ``unknown_area`` also uses explicit values so the
    only flawed slot is the area, not a model-extracted target. For ``outage`` these are the
    baseline Create values; :func:`follow_up_initial_params` returns the Modify values.
    """
    if name not in (SCENARIO_NIGHT, SCENARIO_OUTAGE, SCENARIO_UNKNOWN_AREA):
        return None
    params = dict(_ACCEPT_BASE_PARAMS.get(language, _ACCEPT_BASE_PARAMS[LANGUAGE_ZH]))
    overrides = _COMPLETE_INITIAL_OVERRIDES.get(name, {})
    if overrides:
        params.update(overrides.get(language, overrides[LANGUAGE_ZH]))
    return params


def follow_up_initial_params(name: str, language: str = LANGUAGE_ZH) -> dict[str, object] | None:
    """Return the follow-up Modify Task-T parameters of a multi-phase scenario, or ``None``.

    Only the planned-outage scenario is two-phase: the first Task-T creates a baseline on the
    existing Task, then this Modify Task-T continues that same Task through a real A2A follow-up.
    """
    overrides = _FOLLOW_UP_OVERRIDES.get(name)
    if overrides is None:
        return None
    params = dict(_ACCEPT_BASE_PARAMS.get(language, _ACCEPT_BASE_PARAMS[LANGUAGE_ZH]))
    params.update(overrides.get(language, overrides[LANGUAGE_ZH]))
    return params


def task_t_data_schema(language: str = LANGUAGE_ZH) -> dict[str, object]:
    """Return the Task-T data schema used with :func:`complete_initial_params`."""
    return dict(TASK_T_DATA_SCHEMA.get(language, TASK_T_DATA_SCHEMA[LANGUAGE_ZH]))


#: Guaranteed-throughput target kept when the unknown-area negotiation confirms the served area.
UNKNOWN_AREA_TARGET: dict[str, str] = {
    LANGUAGE_EN: "{00:00~07:00,2Mbps}, {07:00~23:00,20Mbps}, {23:00~24:00,2Mbps}",
    LANGUAGE_ZH: "{00:00~07:00,2Mbps}、{07:00~23:00,20Mbps}、{23:00~24:00,2Mbps}",
}

#: Corrected guaranteed-throughput targets agreed by each negotiation kind, per language.
_ACCEPT_TARGET_OVERRIDES: dict[str, dict[str, str]] = {
    KIND_TARGET: {
        LANGUAGE_EN: ("{00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~23:59,10Mbps}"),
        LANGUAGE_ZH: ("{00:00~17:30,10Mbps}、{17:30~23:00,20Mbps}、{23:00~23:59,10Mbps}"),
    },
    KIND_FEASIBILITY: {
        LANGUAGE_EN: "{00:00~06:00,2Mbps}, {06:00~18:00,2Mbps}",
        LANGUAGE_ZH: "{00:00~06:00,2Mbps}、{06:00~18:00,2Mbps}",
    },
}

#: Site-level context the feasibility accept carries in addition to the base parameters.
_ACCEPT_FEASIBILITY_CONTEXT: dict[str, dict[str, str]] = {
    LANGUAGE_EN: {
        "siteList": "Station 1,Station 2",
        "backgroundEvent": "power outage from 00:00 to 18:00",
    },
    LANGUAGE_ZH: {
        "siteList": "站点名称1,站点名称2",
        "backgroundEvent": "00:00~18:00 停电",
    },
}

#: Confirmed intent of the target negotiation accept, per language.
TARGET_ACCEPT_CONFIRMATIONS: dict[str, str] = {
    LANGUAGE_EN: (
        "The energy saving target is corrected to {00:00~07:00,2Mbps}, {07:00~09:00,10Mbps}, "
        "{09:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~00:00,10Mbps}."
    ),
    LANGUAGE_ZH: (
        "节能目标修正为{00:00~07:00,2Mbps}、{07:00~09:00,10Mbps}、{09:00~17:30,10Mbps}、"
        "{17:30~23:00,20Mbps}、{23:00~00:00,10Mbps}。"
    ),
}

#: Feasibility summary of the feasibility negotiation accept, per language.
FEASIBILITY_ACCEPT_SUMMARIES: dict[str, str] = {
    LANGUAGE_EN: (
        "Agreed to lower the guaranteed throughput target for 06:00~18:00 from 10Mbps to 2Mbps; "
        "the negotiation is confirmed as complete."
    ),
    LANGUAGE_ZH: "同意将06:00~18:00时段的速率保障目标由10Mbps下调至2Mbps，本次协商确认结束。",
}


def _localized(values: dict[str, str], language: str) -> str:
    """Return the value of one language, falling back to ``zh-CN``."""
    return values.get(language, values[LANGUAGE_ZH])


def accept_filled_params(kind: str, language: str = LANGUAGE_ZH) -> dict[str, object]:
    """Return the Task-T parameters the client refills when it accepts one proposal.

    Args:
        kind: Negotiation kind (``information`` / ``target`` / ``feasibility``).
        language: Process-level language selecting the localized values.

    Returns:
        A fresh parameter map with the kind's agreed guaranteed-throughput target (the information
        kind uses the base complete target) and the feasibility site context when applicable.
    """
    params = dict(_ACCEPT_BASE_PARAMS.get(language, _ACCEPT_BASE_PARAMS[LANGUAGE_ZH]))
    override = _ACCEPT_TARGET_OVERRIDES.get(kind)
    if override is not None:
        params["guaranteedThroughputTarget"] = _localized(override, language)
    if kind == KIND_FEASIBILITY:
        params.update(_ACCEPT_FEASIBILITY_CONTEXT.get(language, _ACCEPT_FEASIBILITY_CONTEXT[LANGUAGE_ZH]))
    return params


def unknown_area_accept_params(language: str = LANGUAGE_ZH) -> dict[str, object]:
    """Return the accept refill of the unknown-area scenario: a served area and the same windows."""
    params = dict(_ACCEPT_BASE_PARAMS.get(language, _ACCEPT_BASE_PARAMS[LANGUAGE_ZH]))
    params["region"] = REGION_STRINGS.get(language, REGION_STRINGS[LANGUAGE_ZH])
    params["guaranteedThroughputTarget"] = _localized(UNKNOWN_AREA_TARGET, language)
    return params


def accept_confirmation_text(kind: str, language: str = LANGUAGE_ZH) -> str | None:
    """Return the natural-language confirmation an accept of ``kind`` carries, when it has one.

    Args:
        kind: Negotiation kind (``information`` / ``target`` / ``feasibility``).
        language: Process-level language selecting the localized text.

    Returns:
        The corrected target (``target``) or the agreed feasibility summary (``feasibility``);
        ``None`` for the information kind, whose accept lists the filled parameters instead.
    """
    if kind == KIND_TARGET:
        return _localized(TARGET_ACCEPT_CONFIRMATIONS, language)
    if kind == KIND_FEASIBILITY:
        return _localized(FEASIBILITY_ACCEPT_SUMMARIES, language)
    return None


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
        user_request: Client natural-language request per language.
        expected_sequence: Exact performative/round sequence from the design.
        expected_final_state: Terminal state the run must reach.
        scripted_client_decisions: Decisions returned by the offline scripted decision provider,
            in order.
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
    user_request={
        LANGUAGE_EN: NATURAL_LANGUAGE_PROMPT_INPUT_EN,
        LANGUAGE_ZH: NATURAL_LANGUAGE_PROMPT_INPUT_ZH,
    },
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
    user_request={
        LANGUAGE_EN: NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN,
        LANGUAGE_ZH: NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_ZH,
    },
    expected_sequence=(
        ExpectedStep(actor="server", performative="PROPOSE", round=1),
        ExpectedStep(actor="client", performative="ACCEPT", round=1),
    ),
    expected_final_state=FINAL_STATE_TASK_COMPLETED,
    scripted_client_decisions=(
        NegotiationDecision(
            action="accept",
            rationale=(
                "The missing time-segmented guaranteed throughput target is supplied, so the "
                "energy-saving task can run."
            ),
        ),
    ),
    description=(
        "Task-T misses the guaranteed throughput target; the client accepts the server's missing-parameter proposal."
    ),
)

SCENARIO_UNKNOWN_AREA_SPEC = Scenario(
    name=SCENARIO_UNKNOWN_AREA,
    expected_kind=KIND_INFORMATION,
    user_request={
        LANGUAGE_EN: NATURAL_LANGUAGE_PROMPT_INPUT_UNKNOWN_EN,
        LANGUAGE_ZH: NATURAL_LANGUAGE_PROMPT_INPUT_UNKNOWN_ZH,
    },
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
        ),
    ),
    description=(
        "The request names an unknown area; the RAN Agent asks the client to confirm a served area before executing."
    ),
)

SCENARIO_TARGET_SPEC = Scenario(
    name=SCENARIO_TARGET,
    expected_kind=KIND_TARGET,
    user_request={
        LANGUAGE_EN: NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN,
        LANGUAGE_ZH: NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_ZH,
    },
    expected_sequence=(
        ExpectedStep(actor="server", performative="PROPOSE", round=1),
        ExpectedStep(actor="client", performative="PROPOSE", round=1),
        ExpectedStep(actor="server", performative="PROPOSE", round=2),
        ExpectedStep(actor="client", performative="ACCEPT", round=2),
    ),
    expected_final_state=FINAL_STATE_TASK_COMPLETED,
    scripted_client_decisions=(
        NegotiationDecision(
            action="counter",
            rationale="The first revision still overlaps the 17:30~23:00 segment.",
            counter_proposal=(
                "Move the extra capacity to a non-overlapping window: keep {17:30~20:00,20Mbps} "
                "and {20:00~00:00,10Mbps}."
            ),
        ),
        NegotiationDecision(
            action="accept",
            rationale="The revised plan has non-overlapping segments and meets the throughput target.",
        ),
    ),
    description="Overlapping throughput segments; the client counter-proposes once, then accepts the revision.",
)

SCENARIO_FEASIBILITY_SPEC = Scenario(
    name=SCENARIO_FEASIBILITY,
    expected_kind=KIND_FEASIBILITY,
    user_request={
        LANGUAGE_EN: NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_EN,
        LANGUAGE_ZH: NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_ZH,
    },
    expected_sequence=(
        ExpectedStep(actor="server", performative="PROPOSE", round=1),
        ExpectedStep(actor="client", performative="ACCEPT", round=1),
    ),
    expected_final_state=FINAL_STATE_TASK_COMPLETED,
    scripted_client_decisions=(
        NegotiationDecision(
            action="accept",
            rationale="The outage-aware alternative plan is feasible and keeps the SLA margin.",
        ),
    ),
    description="A guaranteed rate conflicts with a long power outage; the client accepts the alternative plan.",
)

SCENARIO_REJECT_SPEC = Scenario(
    name=SCENARIO_REJECT,
    expected_kind=KIND_INFORMATION,
    user_request={
        LANGUAGE_EN: NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN,
        LANGUAGE_ZH: NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_ZH,
    },
    expected_sequence=(
        ExpectedStep(actor="server", performative="PROPOSE", round=1),
        ExpectedStep(actor="client", performative="REJECT", round=1),
        ExpectedStep(actor="server", performative="MESSAGE", round=None),
    ),
    expected_final_state=FINAL_STATE_NEGOTIATION_ENDED,
    scripted_client_decisions=(
        NegotiationDecision(
            action="reject",
            rationale="The requested guaranteed throughput target is not available from the client side.",
        ),
    ),
    description=("The client cannot provide the missing throughput target; the negotiation ends without a task."),
)

SCENARIO_ABORT_SPEC = Scenario(
    name=SCENARIO_ABORT,
    # The real NMS opens an information negotiation for the unresolved region before it ever
    # reaches the target check. The abort exercise therefore counters inside that information
    # negotiation: it keeps repeating the requested region instead of supplying the missing
    # throughput target, and the server aborts at maxRounds. This matches the real NMS behavior
    # better than forcing a target negotiation the vendor does not open.
    expected_kind=KIND_INFORMATION,
    user_request={
        LANGUAGE_EN: NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN,
        LANGUAGE_ZH: NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_ZH,
    },
    expected_sequence=(
        ExpectedStep(actor="server", performative="PROPOSE", round=1),
        ExpectedStep(actor="client", performative="PROPOSE", round=1),
        ExpectedStep(actor="server", performative="PROPOSE", round=2),
        ExpectedStep(actor="client", performative="PROPOSE", round=2),
        ExpectedStep(actor="server", performative="PROPOSE", round=3),
        ExpectedStep(actor="client", performative="PROPOSE", round=3),
        ExpectedStep(actor="server", performative="ABORT", round=3),
    ),
    expected_final_state=FINAL_STATE_NEGOTIATION_ABORTED,
    scripted_client_decisions=(
        NegotiationDecision(
            action="counter",
            rationale="The client repeats the requested region instead of supplying the missing target.",
            counter_proposal="region: Region A",
        ),
        NegotiationDecision(
            action="counter",
            rationale="The client still cannot supply the missing target and repeats its region.",
            counter_proposal="region: Region A",
        ),
        NegotiationDecision(
            action="counter",
            rationale="No further information is available; the client repeats its position.",
            counter_proposal="region: Region A",
        ),
    ),
    description=(
        "The information negotiation does not converge because the client keeps countering with "
        "the requested region instead of the missing target; the server aborts at maxRounds."
    ),
)

SCENARIO_OUTAGE_SPEC = Scenario(
    name=SCENARIO_OUTAGE,
    expected_kind=KIND_NONE,
    user_request={
        LANGUAGE_EN: NATURAL_LANGUAGE_PROMPT_INPUT_OUTAGE_EN,
        LANGUAGE_ZH: NATURAL_LANGUAGE_PROMPT_INPUT_OUTAGE_ZH,
    },
    expected_sequence=(),
    expected_final_state=FINAL_STATE_TASK_COMPLETED,
    scripted_client_decisions=(),
    description=(
        "A planned power outage tomorrow on two sites; the demo creates a baseline task, then "
        "sends a real Modify Task-T on the same Task without a pre-task negotiation."
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

#: Scenarios the pure A2A mode supports: complete, missing-parameter and conflicting-target.
PURE_PROTOCOL_SCENARIOS: tuple[str, ...] = (
    SCENARIO_NIGHT,
    SCENARIO_INFORMATION,
    SCENARIO_TARGET,
)


def get_scenario(name: str) -> Scenario:
    """Return the scenario with the given name.

    Raises:
        KeyError: when the name is not one of the five supported scenarios.
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


def resolve_presentation_request(name: str, language: str) -> str:
    """Return the concise request shown in the presentation for one scenario."""
    by_language = PRESENTATION_REQUESTS.get(name)
    if by_language is not None and language in by_language:
        return by_language[language]
    return resolve_user_request(get_scenario(name), language)


def expected_negotiation_target(
    scenario: Scenario,
    language: str = LANGUAGE_ZH,
) -> str | None:
    """Return the guaranteed-throughput target a completed scenario must agree on.

    Only a negotiation scenario agrees on a target; a complete request already carries it in the
    user request, so it is not a negotiation accuracy check. Scenarios that do not complete
    (reject/abort) have no agreed target either.
    """
    if scenario.expected_final_state != FINAL_STATE_TASK_COMPLETED or scenario.expected_kind == KIND_NONE:
        return None
    if scenario.name == SCENARIO_UNKNOWN_AREA:
        return _localized(UNKNOWN_AREA_TARGET, language)
    params = accept_filled_params(scenario.expected_kind, language)
    value = params.get("guaranteedThroughputTarget") if params else None
    return value if isinstance(value, str) else None
