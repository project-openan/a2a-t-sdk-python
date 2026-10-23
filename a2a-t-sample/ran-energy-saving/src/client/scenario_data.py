"""Sample scenario input data for the ran-energy-saving e2e flow.

The client uses the template-directed ``generate_task_prompt_from_text`` API against the
``Task-T/network-layer/ran-energy-saving/v1`` template, so no scenario recognition is needed:
the natural-language input below directly drives the slot extraction.

The prompt input is selected by the configured language (``A2AT_LANGUAGE`` in ``.env``), so the
input language always matches the mock/template language tree (``zh-CN`` / ``en-US``), by
the sample scenario (``A2AT_SAMPLE_SCENARIO``): the ``default`` scenario keeps the existing
complete input, while the ``negotiation`` scenario uses the missing-region input that drives the
information-negotiation variant (the client input omits the region, the server opens the
negotiation, the client answers with ``negotiation_answers``), and by the negotiation kind
(``A2AT_SAMPLE_NEGOTIATION_KIND``): ``target`` / ``feasibility`` select their own complete
inputs whose Task-T validation fails with the value-not-allowed / rule-violation content error
that triggers the target / feasibility negotiation (the server stays data-driven on the error
code). The transport
(``A2AT_SAMPLE_TRANSPORT``) switches how the client receives the task events: the default SSE
stream or the embedded webhook receiver (``common.webhook_receiver``).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

#: Task-T template addressed by this case.
ENERGY_SAVING_TEMPLATE_URI = "Task-T/network-layer/ran-energy-saving/v1"

#: Sample scenario variants selected by ``A2AT_SAMPLE_SCENARIO``.
SCENARIO_DEFAULT = "default"
SCENARIO_NEGOTIATION = "negotiation"
SUPPORTED_SCENARIOS = (SCENARIO_DEFAULT, SCENARIO_NEGOTIATION)

#: ``.env`` key selecting the sample scenario variant.
SCENARIO_ENV_KEY = "A2AT_SAMPLE_SCENARIO"

#: Event transport variants selected by ``A2AT_SAMPLE_TRANSPORT``: ``sse`` consumes the streamed
#: response as before, ``webhook`` embeds the client-side receiver
#: (``common.webhook_receiver.WebhookReceiver``) and receives the task events as push-notification
#: callbacks (non-blocking ``message:send``).
TRANSPORT_SSE = "sse"
TRANSPORT_WEBHOOK = "webhook"
SUPPORTED_TRANSPORTS = (TRANSPORT_SSE, TRANSPORT_WEBHOOK)

#: ``.env`` key selecting the event transport.
TRANSPORT_ENV_KEY = "A2AT_SAMPLE_TRANSPORT"

#: ``.env`` keys of the webhook transport: the receiver bind port (``0`` = OS-assigned ephemeral
#: port), the push token checked against the ``X-A2A-Notification-Token`` header, and the idle
#: backstop timeout (seconds) that ends the default scenario when no push arrives.
WEBHOOK_PORT_ENV_KEY = "A2AT_SAMPLE_WEBHOOK_PORT"
WEBHOOK_TOKEN_ENV_KEY = "A2AT_SAMPLE_WEBHOOK_TOKEN"
WEBHOOK_IDLE_TIMEOUT_ENV_KEY = "A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT"

#: Negotiation kinds selecting the client scenario input; the server stays data-driven on the
#: content-validation error code (``content.param_missing`` -> information,
#: ``content.value_not_allowed`` -> target, ``content.rule_violation`` -> feasibility).
NEGOTIATION_KIND_INFORMATION = "information"
NEGOTIATION_KIND_TARGET = "target"
NEGOTIATION_KIND_FEASIBILITY = "feasibility"
SUPPORTED_NEGOTIATION_KINDS = (NEGOTIATION_KIND_INFORMATION, NEGOTIATION_KIND_TARGET, NEGOTIATION_KIND_FEASIBILITY)

#: ``.env`` key selecting the negotiation kind.
NEGOTIATION_KIND_ENV_KEY = "A2AT_SAMPLE_NEGOTIATION_KIND"

#: Chinese natural-language task request used for prompt generation (UC1).
NATURAL_LANGUAGE_PROMPT_INPUT_ZH = (
    "在松山湖管委会创建无线网络节能任务：在满足任务上下文的前提下最大化节能；"
    "分时段保障速率目标为 {00:00~07:00,2Mbps}、{07:00~17:30,10Mbps}、"
    "{17:30~23:00,20Mbps}、{23:00~24:00,2Mbps}。"
)

#: English natural-language task request used for prompt generation (UC1).
NATURAL_LANGUAGE_PROMPT_INPUT_EN = (
    "Create a RAN energy saving task in the Songshanhu Administration Committee area to maximize "
    "energy saving. The time-segment-based guaranteed throughput target is {00:00~07:00,2Mbps}, "
    "{07:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,2Mbps}."
)

#: Chinese natural-language task request of the negotiation scenario: the region phrase is
#: omitted, so the mock extracts the region placeholder for the required Task Object slot
#: (resources/mock_llm/<lang>/slot_extraction_missing.json) and the server opens the negotiation.
NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_ZH = (
    "创建无线网络节能任务：在满足任务上下文的前提下最大化节能；"
    "分时段保障速率目标为 {00:00~07:00,2Mbps}、{07:00~17:30,10Mbps}、"
    "{17:30~23:00,20Mbps}、{23:00~24:00,2Mbps}。"
)

#: English natural-language task request of the negotiation scenario (region omitted).
NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN = (
    "Create a RAN energy saving task to maximize energy saving. The time-segment-based "
    "guaranteed throughput target is {00:00~07:00,2Mbps}, {07:00~17:30,10Mbps}, "
    "{17:30~23:00,20Mbps}, {23:00~24:00,2Mbps}."
)

#: Chinese natural-language task request of the target negotiation: the guaranteed throughput
#: target has two overlapping time segments ({17:30~23:00,20Mbps} and {20:00~00:00,10Mbps}), so
#: the Task-T validation reports the value-not-allowed content error that triggers the target
#: negotiation (spec case 2: a time conflict within the segmented target).
NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_ZH = (
    "在松山湖管委会创建无线网络节能任务：在满足任务上下文的前提下最大化节能；"
    "分时段保障速率目标为 {00:00~07:00,2Mbps}、{07:00~09:00,10Mbps}、{09:00~17:30,10Mbps}、"
    "{17:30~23:00,20Mbps}、{20:00~00:00,10Mbps}。"
)

#: English natural-language task request of the target negotiation (overlapping segments).
NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN = (
    "Create a RAN energy saving task in the Songshanhu Administration Committee area to maximize "
    "energy saving. The time-segment-based guaranteed throughput target is {00:00~07:00,2Mbps}, "
    "{07:00~09:00,10Mbps}, {09:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {20:00~00:00,10Mbps}."
)

#: Chinese natural-language task request of the feasibility negotiation: two stations must keep
#: a guaranteed rate through an 18-hour power outage, so the Task-T validation reports the
#: rule-violation content error that triggers the feasibility negotiation (spec case 3: the
#: guaranteed rate cannot sustain the outage duration).
NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_ZH = (
    "在松山湖管委会创建无线网络节能任务：站点列表1为[站点名称1, 站点名称2]；"
    "分时段保障速率目标为 {00:00~06:00,2Mbps}、{06:00~18:00,10Mbps}；"
    "背景事件为在00:00~18:00停电；在满足任务上下文的前提下最大化节能。"
)

#: English natural-language task request of the feasibility negotiation (outage case).
NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_EN = (
    "Create a RAN energy saving task in the Songshanhu Administration Committee area to maximize "
    "energy saving. Station list 1 is [Station 1, Station 2]; the time-segment-based guaranteed "
    "throughput target is {00:00~06:00,2Mbps}, {06:00~18:00,10Mbps}; the background event is a "
    "power outage from 00:00 to 18:00."
)

#: Natural-language task requests keyed by negotiation kind, each as ``(en-US, zh-CN)``. The
#: ``information`` kind uses the missing-region input of the ``negotiation`` scenario.
NEGOTIATION_INPUT_KEYS: dict[str, tuple[str, str]] = {
    NEGOTIATION_KIND_INFORMATION: (
        NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN,
        NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_ZH,
    ),
    NEGOTIATION_KIND_TARGET: (NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN, NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_ZH),
    NEGOTIATION_KIND_FEASIBILITY: (
        NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_EN,
        NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_ZH,
    ),
}

#: Region display string per language; the scripted mock LLM routes the content-validation
#: variant on its presence in the user-message head, and the negotiation accept answers carry it.
NEGOTIATION_REGION_STRINGS: dict[str, str] = {
    "en-US": "Songshanhu Administration Committee",
    "zh-CN": "松山湖管委会",
}

#: Filled Task-T parameters of the negotiation scenario per language: the four TASK_PARAM_SCHEMA
#: params with the region resolved. Mirrors the ``params`` of the complete
#: ``resources/mock_llm/<lang>/content_validation.json`` of each language (the test suite pins the
#: equality, so the two cannot drift).
_NEGOTIATION_FILLED_PARAMS: dict[str, dict[str, object]] = {
    "en-US": {
        "operationType": "create",
        "region": NEGOTIATION_REGION_STRINGS["en-US"],
        "energySavingGoal": "Maximize energy saving under the condition that the task context is met.",
        "guaranteedThroughputTarget": (
            "{00:00~07:00,2Mbps}, {07:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,2Mbps}"
        ),
    },
    "zh-CN": {
        "operationType": "create",
        "region": NEGOTIATION_REGION_STRINGS["zh-CN"],
        "energySavingGoal": "在满足任务上下文的前提下最大化节能。",
        "guaranteedThroughputTarget": (
            "{00:00~07:00,2Mbps}、{07:00~17:30,10Mbps}、{17:30~23:00,20Mbps}、{23:00~24:00,2Mbps}"
        ),
    },
}


def resolve_language(*, env_path: Path | None = None) -> str:
    """Resolve the configured ``A2AT_LANGUAGE`` (defaults to ``zh-CN``)."""
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    return str(values.get("A2AT_LANGUAGE", "")).strip() or "zh-CN"


#: Negotiation-T text content per language, each mirroring the canned extraction response of the
#: same stage (``resources/mock_llm/<lang>/negotiation_*.json``): the server-side propose texts
#: feed the from-data propose builders, and the client-side accept texts must equal the canned
#: accept-extraction content so the from-data and from-text channels produce equivalent messages.
_NEGOTIATION_TARGET_DESCRIPTIONS: dict[str, str] = {
    "en-US": (
        "Target negotiation for the RAN energy saving task: the guaranteed throughput target has "
        "two overlapping time segments, {17:30~23:00,20Mbps} and {20:00~00:00,10Mbps}, so the "
        "guaranteed rate for the overlapping period cannot be determined uniquely."
    ),
    "zh-CN": (
        "无线网络节能任务的目标协商：分时段保障速率目标中 {17:30~23:00,20Mbps} 与 "
        "{20:00~00:00,10Mbps} 两个时段存在时间重叠，无法唯一确定重叠时段的速率保障目标。"
    ),
}

#: Restatement of the original intent carried by the target propose (``intent_understanding``).
_NEGOTIATION_TARGET_INTENT_UNDERSTANDINGS: dict[str, list[tuple[str, str]]] = {
    "en-US": [
        ("Region", "Songshanhu Administration Committee"),
        (
            "Time-segment-based guaranteed throughput target",
            "{00:00~07:00,2Mbps}, {07:00~09:00,10Mbps}, {09:00~17:30,10Mbps}, "
            "{17:30~23:00,20Mbps}, {20:00~00:00,10Mbps}",
        ),
    ],
    "zh-CN": [
        ("区域", "松山湖管委会"),
        (
            "分时段保障速率目标",
            "{00:00~07:00,2Mbps}、{07:00~09:00,10Mbps}、{09:00~17:30,10Mbps}、"
            "{17:30~23:00,20Mbps}、{20:00~00:00,10Mbps}",
        ),
    ],
}

#: Clarification requests carried by the target propose (``request_for_clarification``).
_NEGOTIATION_TARGET_CLARIFICATIONS: dict[str, list[tuple[str, str]]] = {
    "en-US": [
        (
            "Time-segment-based guaranteed throughput target",
            "The two segments {17:30~23:00,20Mbps} and {20:00~00:00,10Mbps} overlap, so the "
            "guaranteed rate for the overlapping period is ambiguous; please adjust the time.",
        )
    ],
    "zh-CN": [
        (
            "分时段保障速率目标",
            "如下两个时段存在时间重叠，无法唯一确定重叠时段的速率保障目标，请调整时间："
            "{17:30~23:00,20Mbps}、{20:00~00:00,10Mbps}",
        )
    ],
}

#: Confirmed intent of the target negotiation accept (the corrected target) per language.
_NEGOTIATION_TARGET_CONFIRMATIONS: dict[str, str] = {
    "en-US": (
        "The energy saving target is corrected to {00:00~07:00,2Mbps}, {07:00~09:00,10Mbps}, "
        "{09:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~00:00,10Mbps}."
    ),
    "zh-CN": (
        "节能目标修正为{00:00~07:00,2Mbps}、{07:00~09:00,10Mbps}、{09:00~17:30,10Mbps}、"
        "{17:30~23:00,20Mbps}、{23:00~00:00,10Mbps}。"
    ),
}

#: Feasibility description of the feasibility negotiation propose per language.
_NEGOTIATION_FEASIBILITY_DESCRIPTIONS: dict[str, str] = {
    "en-US": (
        "The feasibility assessment for the station-level energy saving task is complete: the "
        "current guaranteed throughput target cannot satisfy the power-outage duration "
        "requirement; see the infeasibility details and proposal."
    ),
    "zh-CN": (
        "站点级节能任务的目标达成可行性评估已完成，当前速率目标无法同时满足停电时长要求，"
        "详见评估不可行时的详情和提案，请确认。"
    ),
}

#: Infeasibility details and proposal of the feasibility propose
#: (``infeasibility_details_and_proposal``).
_NEGOTIATION_FEASIBILITY_INFEASIBILITY: dict[str, list[tuple[str, str]]] = {
    "en-US": [
        ("Conclusion", "Infeasible."),
        (
            "Proposal",
            "Lower the guaranteed throughput target for 06:00~18:00 from 10Mbps to 2Mbps, which "
            "extends the supportable outage duration to the required 18 hours. If lowering the "
            "rate is not acceptable, shorten the required outage duration instead. One of the two "
            "must be adjusted; no third option satisfies both original requirements.",
        ),
    ],
    "zh-CN": [
        ("评估结论", "不可行。"),
        (
            "提案",
            "建议将06:00~18:00时段的速率保障目标由10Mbps下调至2Mbps，以延长可支撑的停电时长至"
            "满足要求的18小时。若无法接受下调速率，则需相应缩短停电时长保障要求。两者需二选一"
            "调整，暂无第三种可同时满足原有两项要求的方案。",
        ),
    ],
}

#: Feasibility summary of the feasibility negotiation accept (the accepted proposal) per language.
_NEGOTIATION_FEASIBILITY_SUMMARIES: dict[str, str] = {
    "en-US": (
        "Agreed to lower the guaranteed throughput target for 06:00~18:00 from 10Mbps to 2Mbps; "
        "the negotiation is confirmed as complete."
    ),
    "zh-CN": "同意将06:00~18:00时段的速率保障目标由10Mbps下调至2Mbps，本次协商确认结束。",
}


def _localized_text(values: dict[str, str], *, env_path: Path | None = None) -> str:
    """Return the text of the resolved language, falling back to ``zh-CN``."""
    language = resolve_language(env_path=env_path)
    return values.get(language, values["zh-CN"])


def resolve_scenario(*, env_path: Path | None = None) -> str:
    """Resolve the configured ``A2AT_SAMPLE_SCENARIO`` (defaults to ``default``).

    The ``.env`` value wins over the OS environment (the sample's ``resolve_server_url``
    convention); an unset or unknown value resolves to the ``default`` scenario.
    """
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    raw_value = str(values.get(SCENARIO_ENV_KEY, "")).strip() or os.environ.get(SCENARIO_ENV_KEY, "").strip()
    return raw_value if raw_value in SUPPORTED_SCENARIOS else SCENARIO_DEFAULT



def resolve_negotiation_kind(*, env_path: Path | None = None) -> str:
    """Resolve the configured ``A2AT_SAMPLE_NEGOTIATION_KIND`` (defaults to ``information``).

    The kind selects the client scenario input; the server stays data-driven on the
    content-validation error code. An unset or unknown value resolves to ``information``.
    """
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    raw_value = (
        str(values.get(NEGOTIATION_KIND_ENV_KEY, "")).strip() or os.environ.get(NEGOTIATION_KIND_ENV_KEY, "").strip()
    )
    return raw_value if raw_value in SUPPORTED_NEGOTIATION_KINDS else NEGOTIATION_KIND_INFORMATION


def resolve_transport(*, env_path: Path | None = None) -> str:
    """Resolve the configured ``A2AT_SAMPLE_TRANSPORT`` (defaults to ``sse``).

    The transport switches how the client receives the task events: ``sse`` consumes the streamed
    response, ``webhook`` embeds the ``common.webhook_receiver.WebhookReceiver`` and receives the
    events as push-notification callbacks (non-blocking ``message:send``). An unset or unknown
    value resolves to ``sse``.
    """
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    raw_value = str(values.get(TRANSPORT_ENV_KEY, "")).strip() or os.environ.get(TRANSPORT_ENV_KEY, "").strip()
    return raw_value if raw_value in SUPPORTED_TRANSPORTS else TRANSPORT_SSE


def resolve_webhook_port(*, env_path: Path | None = None) -> int:
    """Resolve the configured ``A2AT_SAMPLE_WEBHOOK_PORT`` (defaults to ``9000``).

    The port binds the embedded webhook receiver; ``0`` selects an OS-assigned ephemeral port.
    An unset, blank, non-integer or out-of-range value resolves to the ``9000`` default.
    """
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    raw_value = str(values.get(WEBHOOK_PORT_ENV_KEY, "")).strip() or os.environ.get(WEBHOOK_PORT_ENV_KEY, "").strip()
    try:
        port = int(raw_value)
    except ValueError:
        return 9000
    return port if 0 <= port <= 65535 else 9000


def resolve_webhook_token(*, env_path: Path | None = None) -> str:
    """Resolve the configured ``A2AT_SAMPLE_WEBHOOK_TOKEN`` (defaults to ``demo-webhook-token``).

    The token is the expected value of the ``X-A2A-Notification-Token`` header the server's push
    sender carries on every webhook POST; the receiver answers 401 on a mismatch.
    """
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    raw_value = str(values.get(WEBHOOK_TOKEN_ENV_KEY, "")).strip() or os.environ.get(WEBHOOK_TOKEN_ENV_KEY, "").strip()
    return raw_value or "demo-webhook-token"


def resolve_webhook_idle_timeout(*, env_path: Path | None = None) -> float:
    """Resolve the configured ``A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT`` (defaults to ``30.0``).

    The timeout is the webhook-mode backstop that ends the default scenario when no push arrives
    (reset per received event). An unset, blank, non-numeric or non-positive value resolves to
    the ``30.0`` default.
    """
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    raw_value = (
        str(values.get(WEBHOOK_IDLE_TIMEOUT_ENV_KEY, "")).strip()
        or os.environ.get(WEBHOOK_IDLE_TIMEOUT_ENV_KEY, "").strip()
    )
    try:
        timeout = float(raw_value)
    except ValueError:
        return 30.0
    return timeout if timeout > 0 else 30.0


def build_prompt_input(
    *,
    env_path: Path | None = None,
    scenario: str | None = None,
    negotiation_kind: str | None = None,
) -> str:
    """Return the prompt generation input matching the configured language, scenario and kind.

    The explicit ``scenario`` / ``negotiation_kind`` overrides beat the resolvers; unknown values
    fall back like the resolvers do. A ``target`` / ``feasibility`` kind selects its own input
    regardless of the scenario; otherwise the ``negotiation`` scenario keeps the missing-region
    information input and the ``default`` scenario the complete input (v1 behavior).
    """
    is_english = resolve_language(env_path=env_path) == "en-US"
    selected_kind = negotiation_kind if negotiation_kind is not None else resolve_negotiation_kind(env_path=env_path)
    if selected_kind in (NEGOTIATION_KIND_TARGET, NEGOTIATION_KIND_FEASIBILITY):
        en_input, zh_input = NEGOTIATION_INPUT_KEYS[selected_kind]
        return en_input if is_english else zh_input
    selected_scenario = scenario if scenario is not None else resolve_scenario(env_path=env_path)
    if selected_scenario == SCENARIO_NEGOTIATION:
        en_input, zh_input = NEGOTIATION_INPUT_KEYS[NEGOTIATION_KIND_INFORMATION]
        return en_input if is_english else zh_input
    return NATURAL_LANGUAGE_PROMPT_INPUT_EN if is_english else NATURAL_LANGUAGE_PROMPT_INPUT_ZH


#: Guaranteed-throughput target of the Task-T refill between negotiation kinds: the target case
#: accepts the corrected non-overlapping segments, the feasibility case the lowered rate. The
#: information case keeps the complete input's target (the missing parameter is the region only).
_NEGOTIATION_FILLED_TARGETS: dict[str, dict[str, str]] = {
    NEGOTIATION_KIND_TARGET: {
        "en-US": (
            "{00:00~07:00,2Mbps}, {07:00~09:00,10Mbps}, {09:00~17:30,10Mbps}, "
            "{17:30~23:00,20Mbps}, {23:00~00:00,10Mbps}"
        ),
        "zh-CN": (
            "{00:00~07:00,2Mbps}、{07:00~09:00,10Mbps}、{09:00~17:30,10Mbps}、"
            "{17:30~23:00,20Mbps}、{23:00~00:00,10Mbps}"
        ),
    },
    NEGOTIATION_KIND_FEASIBILITY: {
        "en-US": "{00:00~06:00,2Mbps}, {06:00~18:00,2Mbps}",
        "zh-CN": "{00:00~06:00,2Mbps}、{06:00~18:00,2Mbps}",
    },
}

#: Site-level context of the feasibility refill: the station list and the power outage period the
#: accepted lower rate applies to. The base refill is region-level, so these two optional schema
#: fields are added only for the feasibility kind.
_FEASIBILITY_REFILL_CONTEXT: dict[str, dict[str, str]] = {
    "en-US": {
        "siteList": "Station 1,Station 2",
        "backgroundEvent": "power outage from 00:00 to 18:00",
    },
    "zh-CN": {
        "siteList": "站点名称1,站点名称2",
        "backgroundEvent": "00:00~18:00 停电",
    },
}


def negotiation_filled_params(
    negotiation_kind: str = NEGOTIATION_KIND_INFORMATION,
    *,
    env_path: Path | None = None,
) -> dict[str, object]:
    """Return the four Task-T parameters for the refill, adjusted to what the negotiation accepted.

    The target case refills the corrected (non-overlapping) segments and the feasibility case the
    lowered guaranteed rate, so the re-validated Task-T prompt carries the agreed target; the
    information case only fills the region and keeps the complete input's target.
    """
    language = resolve_language(env_path=env_path)
    filled = dict(_NEGOTIATION_FILLED_PARAMS.get(language, _NEGOTIATION_FILLED_PARAMS["zh-CN"]))
    kind_targets = _NEGOTIATION_FILLED_TARGETS.get(negotiation_kind)
    if kind_targets is not None:
        filled["guaranteedThroughputTarget"] = kind_targets.get(language, kind_targets["zh-CN"])
    if negotiation_kind == NEGOTIATION_KIND_FEASIBILITY:
        filled.update(_FEASIBILITY_REFILL_CONTEXT.get(language, _FEASIBILITY_REFILL_CONTEXT["zh-CN"]))
    return filled


def negotiation_answers(*, env_path: Path | None = None) -> dict[str, str]:
    """Return the negotiation accept answers: the region value of the resolved language."""
    language = resolve_language(env_path=env_path)
    return {"region": NEGOTIATION_REGION_STRINGS.get(language, NEGOTIATION_REGION_STRINGS["zh-CN"])}


def negotiation_target_description(*, env_path: Path | None = None) -> str:
    """Return the target negotiation propose description (mirrors the canned propose extraction)."""
    return _localized_text(_NEGOTIATION_TARGET_DESCRIPTIONS, env_path=env_path)


def negotiation_target_intent_understanding(*, env_path: Path | None = None) -> list[tuple[str, str]]:
    """Return the intent restatement of the target propose (canned propose extraction)."""
    language = resolve_language(env_path=env_path)
    items = _NEGOTIATION_TARGET_INTENT_UNDERSTANDINGS.get(
        language, _NEGOTIATION_TARGET_INTENT_UNDERSTANDINGS["zh-CN"]
    )
    return list(items)


def negotiation_target_clarifications(*, env_path: Path | None = None) -> list[tuple[str, str]]:
    """Return the clarification requests of the target propose (canned propose extraction)."""
    language = resolve_language(env_path=env_path)
    items = _NEGOTIATION_TARGET_CLARIFICATIONS.get(language, _NEGOTIATION_TARGET_CLARIFICATIONS["zh-CN"])
    return list(items)


def negotiation_target_confirmation(*, env_path: Path | None = None) -> str:
    """Return the confirmed intent of the target accept (equals the canned accept extraction)."""
    return _localized_text(_NEGOTIATION_TARGET_CONFIRMATIONS, env_path=env_path)


def negotiation_feasibility_description(*, env_path: Path | None = None) -> str:
    """Return the feasibility negotiation propose description (mirrors the canned propose extraction)."""
    return _localized_text(_NEGOTIATION_FEASIBILITY_DESCRIPTIONS, env_path=env_path)


def negotiation_feasibility_infeasibility(*, env_path: Path | None = None) -> list[tuple[str, str]]:
    """Return the infeasibility details and proposal of the feasibility propose (canned extraction)."""
    language = resolve_language(env_path=env_path)
    items = _NEGOTIATION_FEASIBILITY_INFEASIBILITY.get(
        language, _NEGOTIATION_FEASIBILITY_INFEASIBILITY["zh-CN"]
    )
    return list(items)


def negotiation_feasibility_summary(*, env_path: Path | None = None) -> str:
    """Return the feasibility summary of the feasibility accept (equals the canned accept extraction)."""
    return _localized_text(_NEGOTIATION_FEASIBILITY_SUMMARIES, env_path=env_path)


def build_task_request() -> dict[str, object]:
    """Build the sample Task-T request input (scenario name + agent query)."""
    return {
        # The "scenario" key is the request LABEL ("create ran energy saving task") rendered as
        # the message text part — unrelated to the A2AT_SAMPLE_SCENARIO variant switch above.
        "scenario": "create ran energy saving task",
        "agent_card_query": {
            "name": "RAN Domain Agent",
            "organization": "Huawei",
        },
    }
