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
inputs whose Task-T validation fails with the value-not-allowed / semantic-conflict content
error that triggers the target / feasibility negotiation (the server stays data-driven on the
error code). The generation channel (``A2AT_SAMPLE_CHANNEL``) switches the negotiation
propose/accept generation between typed data and natural-language text. The transport
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

#: Generation channel variants selected by ``A2AT_SAMPLE_CHANNEL``: ``data`` generates the
#: negotiation propose/accept from typed content (no extraction LLM call), ``text`` from a
#: natural-language rendering plus one extraction LLM call.
CHANNEL_DATA = "data"
CHANNEL_TEXT = "text"
SUPPORTED_CHANNELS = (CHANNEL_DATA, CHANNEL_TEXT)

#: ``.env`` key selecting the generation channel.
CHANNEL_ENV_KEY = "A2AT_SAMPLE_CHANNEL"

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
#: ``content.value_not_allowed`` -> target, ``content.semantic_conflict`` -> feasibility).
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

#: Chinese natural-language task request of the target negotiation: the complete input with a
#: 100Mbps throughput target beyond the cell capability, so the Task-T validation reports the
#: value-not-allowed content error that triggers the target negotiation.
NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_ZH = (
    "在松山湖管委会创建无线网络节能任务：在满足任务上下文的前提下最大化节能；"
    "分时段保障速率目标为 {00:00~07:00,100Mbps}。"
)

#: English natural-language task request of the target negotiation (100Mbps target).
NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN = (
    "Create a RAN energy saving task in the Songshanhu Administration Committee area to maximize "
    "energy saving. The time-segment-based guaranteed throughput target is {00:00~07:00,100Mbps}."
)

#: Chinese natural-language task request of the feasibility negotiation: a 30% energy saving goal
#: conflicting with the 50Mbps guaranteed throughput target, so the Task-T validation reports the
#: semantic-conflict content error that triggers the feasibility negotiation.
NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_ZH = (
    "在松山湖管委会创建无线网络节能任务：节能目标为30%；分时段保障速率目标为 {00:00~06:00,50Mbps}。"
)

#: English natural-language task request of the feasibility negotiation (30% goal, 50Mbps target).
NATURAL_LANGUAGE_PROMPT_INPUT_FEASIBILITY_EN = (
    "Create a RAN energy saving task in the Songshanhu Administration Committee area with a 30% "
    "energy saving goal. The time-segment-based guaranteed throughput target is {00:00~06:00,50Mbps}."
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
        "Target negotiation for the RAN energy saving task: the submitted guaranteed throughput "
        "target of 100Mbps exceeds the cell capability, so the clarified target needs confirmation."
    ),
    "zh-CN": "无线网络节能任务的目标协商：用户提交的保障速率目标100Mbps超出小区能力，需对澄清后的目标进行确认。",
}

#: Clarified-target confirm request of the target negotiation propose per language.
_NEGOTIATION_TARGET_CONFIRM_REQUESTS: dict[str, str] = {
    "en-US": "The clarified target is a guaranteed throughput of 20Mbps for the region; please confirm.",
    "zh-CN": "澄清后的目标为该区域保障速率20Mbps，请确认。",
}

#: Confirmed intent of the target negotiation accept (the confirmed-target sentence) per language.
_NEGOTIATION_TARGET_CONFIRMATIONS: dict[str, str] = {
    "en-US": "The clarified target is a guaranteed throughput of 20Mbps for the region.",
    "zh-CN": "澄清后的目标为该区域保障速率20Mbps。",
}

#: Feasibility description of the feasibility negotiation propose per language.
_NEGOTIATION_FEASIBILITY_DESCRIPTIONS: dict[str, str] = {
    "en-US": (
        "Feasibility negotiation for the RAN energy saving task: the 30% energy saving goal "
        "conflicts with the 50Mbps guaranteed throughput target, so the feasibility needs a joint "
        "evaluation."
    ),
    "zh-CN": "无线网络节能任务的可行性协商：节能目标30%与保障速率目标50Mbps存在冲突，需对可行性进行联合评估。",
}

#: Contents to evaluate of the feasibility negotiation propose per language (corpus
#: "Target achievement" / "Resource status" style).
_NEGOTIATION_FEASIBILITY_CONTENTS: dict[str, list[tuple[str, str]]] = {
    "en-US": [
        (
            "Target achievement",
            "The area runs energy saving from 00:00 to 06:00 while guaranteeing a throughput target of 50Mbps.",
        ),
        (
            "Resource status",
            "The cells in the area are NR with carrier shutdown available during the low-traffic period.",
        ),
    ],
    "zh-CN": [
        ("目标达成", "区域在00:00~06:00时段执行节能，同时保障速率目标为50Mbps。"),
        ("能源现状", "区域内小区为NR制式，低话务时段具备关断载频条件。"),
    ],
}

#: Feasibility summary of the feasibility negotiation accept per language.
_NEGOTIATION_FEASIBILITY_SUMMARIES: dict[str, str] = {
    "en-US": "The energy saving target is achievable; the negotiation is confirmed.",
    "zh-CN": "节能目标可达成，本次协商确认结束。",
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


def resolve_channel(*, env_path: Path | None = None) -> str:
    """Resolve the configured ``A2AT_SAMPLE_CHANNEL`` (defaults to ``data``).

    The channel switches only the negotiation propose/accept generation (typed data vs an
    NL rendering plus one extraction LLM call); an unset or unknown value resolves to ``data``.
    """
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    raw_value = str(values.get(CHANNEL_ENV_KEY, "")).strip() or os.environ.get(CHANNEL_ENV_KEY, "").strip()
    return raw_value if raw_value in SUPPORTED_CHANNELS else CHANNEL_DATA


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


def negotiation_filled_params(*, env_path: Path | None = None) -> dict[str, object]:
    """Return the four Task-T parameters with the region filled (negotiation round 2 input)."""
    language = resolve_language(env_path=env_path)
    filled = _NEGOTIATION_FILLED_PARAMS.get(language, _NEGOTIATION_FILLED_PARAMS["zh-CN"])
    return dict(filled)


def negotiation_answers(*, env_path: Path | None = None) -> dict[str, str]:
    """Return the negotiation accept answers: the region value of the resolved language."""
    language = resolve_language(env_path=env_path)
    return {"region": NEGOTIATION_REGION_STRINGS.get(language, NEGOTIATION_REGION_STRINGS["zh-CN"])}


def negotiation_target_description(*, env_path: Path | None = None) -> str:
    """Return the target negotiation propose description (mirrors the canned propose extraction)."""
    return _localized_text(_NEGOTIATION_TARGET_DESCRIPTIONS, env_path=env_path)


def negotiation_target_confirm_request(*, env_path: Path | None = None) -> str:
    """Return the clarified-target confirm request (mirrors the canned propose extraction)."""
    return _localized_text(_NEGOTIATION_TARGET_CONFIRM_REQUESTS, env_path=env_path)


def negotiation_target_confirmation(*, env_path: Path | None = None) -> str:
    """Return the confirmed intent of the target accept (equals the canned accept extraction)."""
    return _localized_text(_NEGOTIATION_TARGET_CONFIRMATIONS, env_path=env_path)


def negotiation_feasibility_description(*, env_path: Path | None = None) -> str:
    """Return the feasibility negotiation propose description (mirrors the canned propose extraction)."""
    return _localized_text(_NEGOTIATION_FEASIBILITY_DESCRIPTIONS, env_path=env_path)


def negotiation_feasibility_contents(*, env_path: Path | None = None) -> list[tuple[str, str]]:
    """Return the contents to evaluate of the feasibility propose (canned propose extraction)."""
    language = resolve_language(env_path=env_path)
    contents = _NEGOTIATION_FEASIBILITY_CONTENTS.get(language, _NEGOTIATION_FEASIBILITY_CONTENTS["zh-CN"])
    return list(contents)


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
