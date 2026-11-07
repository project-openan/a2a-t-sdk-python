"""Scripted ``LLMClient`` provider for offline and test runs.

The provider is registered with the official SDK factory as ``scripted`` and serves the canned
responses under ``resources/mock_llm/<language>/``. Calls are routed by the **output schema
signature** plus a marker from the user message — never by call order, because the client and the
server processes each start their own call sequence at zero and a sequenced script would serve the
wrong response.

The routing rules and the canned content are the ones of the legacy sample mock, adapted to run as
a regular provider: no SDK method is monkey-patched and no module outside the sample root is
imported. The target-propose and abort extraction schemas are identical in every round, so those
two stages additionally synthesize their extraction from the policy's round marker instead of
replaying one canned response. Use :func:`register_scripted_provider` before constructing
``A2ATClient`` / ``A2ATServer``; the registration is idempotent.
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path
from typing import Any

from a2a_t.llm.factory import LLMClientFactory
from a2a_t.llm.models import LLMClientConfig, LLMResponse
from dotenv import dotenv_values
from shared.run_constants import LANGUAGE_ZH, MAX_ROUNDS

from demo.scenario import (
    EXAMPLE_REQUESTS,
    MODIFY_REQUEST,
    SCENARIO_FEASIBILITY,
    SCENARIO_INFORMATION,
    SCENARIO_NIGHT,
    SCENARIO_OUTAGE,
    SCENARIO_TARGET,
    SCENARIO_UNKNOWN_AREA,
)
from demo.served_areas import (
    REGION_STRINGS,
    build_area_proposal_text,
    match_served_areas,
)

#: Provider name registered with ``LLMClientFactory``.
SCRIPTED_PROVIDER_NAME = "scripted"

#: Default response tree, relative to the sample root.
_RESOURCES_DIR = Path(__file__).resolve().parents[1] / "resources" / "mock_llm"

#: Routed stage names (``llm_calls`` entries). The complete variants keep their historical names.
STAGE_SLOT_EXTRACTION = "slot_extraction"
STAGE_SLOT_EXTRACTION_MISSING = "slot_extraction_missing"
STAGE_SLOT_EXTRACTION_TARGET = "slot_extraction_target"
STAGE_SLOT_EXTRACTION_FEASIBILITY = "slot_extraction_feasibility"
STAGE_SLOT_EXTRACTION_TARGET_REFILL = "slot_extraction_target_refill"
STAGE_SLOT_EXTRACTION_FEASIBILITY_REFILL = "slot_extraction_feasibility_refill"
STAGE_SLOT_EXTRACTION_OUTAGE = "slot_extraction_outage"
STAGE_SLOT_EXTRACTION_OUTAGE_BASELINE = "slot_extraction_outage_baseline"
STAGE_SLOT_EXTRACTION_UNKNOWN = "slot_extraction_unknown"
STAGE_CONTENT_VALIDATION = "content_validation"
STAGE_CONTENT_VALIDATION_MISSING = "content_validation_missing"
STAGE_CONTENT_VALIDATION_TARGET = "content_validation_target"
STAGE_CONTENT_VALIDATION_FEASIBILITY = "content_validation_feasibility"
STAGE_CONTENT_VALIDATION_TARGET_REFILL = "content_validation_target_refill"
STAGE_CONTENT_VALIDATION_FEASIBILITY_REFILL = "content_validation_feasibility_refill"
STAGE_CONTENT_VALIDATION_OUTAGE = "content_validation_outage"
STAGE_CONTENT_VALIDATION_OUTAGE_BASELINE = "content_validation_outage_baseline"
STAGE_NEGOTIATION_PROPOSE_EXTRACTION = "negotiation_propose_extraction"
STAGE_NEGOTIATION_ACCEPT_EXTRACTION = "negotiation_accept_extraction"
STAGE_NEGOTIATION_REJECT_EXTRACTION = "negotiation_reject_extraction"
STAGE_NEGOTIATION_TARGET_PROPOSE_EXTRACTION = "negotiation_target_propose_extraction"
STAGE_NEGOTIATION_TARGET_ACCEPT_EXTRACTION = "negotiation_target_accept_extraction"
STAGE_NEGOTIATION_TARGET_REJECT_EXTRACTION = "negotiation_target_reject_extraction"
STAGE_NEGOTIATION_FEASIBILITY_PROPOSE_EXTRACTION = "negotiation_feasibility_propose_extraction"
STAGE_NEGOTIATION_FEASIBILITY_ACCEPT_EXTRACTION = "negotiation_feasibility_accept_extraction"
STAGE_NEGOTIATION_FEASIBILITY_REJECT_EXTRACTION = "negotiation_feasibility_reject_extraction"
STAGE_NEGOTIATION_ABORT_EXTRACTION = "negotiation_abort_extraction"
#: Synthetic stage of the negotiation semantic-validation call; no canned file is loaded because the
#: response is derived from the declared template of the user prompt (see
#: :func:`negotiation_validation_response`).
STAGE_NEGOTIATION_SEMANTIC_VALIDATION = "negotiation_semantic_validation"

#: Stage name -> response file name.
_STAGE_FILES: dict[str, str] = {
    STAGE_SLOT_EXTRACTION: "slot_extraction.json",
    STAGE_SLOT_EXTRACTION_MISSING: "slot_extraction_missing.json",
    STAGE_SLOT_EXTRACTION_TARGET: "slot_extraction_target.json",
    STAGE_SLOT_EXTRACTION_FEASIBILITY: "slot_extraction_feasibility.json",
    STAGE_SLOT_EXTRACTION_TARGET_REFILL: "slot_extraction_target_refill.json",
    STAGE_SLOT_EXTRACTION_FEASIBILITY_REFILL: "slot_extraction_feasibility_refill.json",
    STAGE_SLOT_EXTRACTION_OUTAGE: "slot_extraction_outage.json",
    STAGE_SLOT_EXTRACTION_OUTAGE_BASELINE: "slot_extraction_outage_baseline.json",
    STAGE_SLOT_EXTRACTION_UNKNOWN: "slot_extraction_unknown.json",
    STAGE_CONTENT_VALIDATION: "content_validation.json",
    STAGE_CONTENT_VALIDATION_MISSING: "content_validation_missing.json",
    STAGE_CONTENT_VALIDATION_TARGET: "content_validation_target.json",
    STAGE_CONTENT_VALIDATION_FEASIBILITY: "content_validation_feasibility.json",
    STAGE_CONTENT_VALIDATION_TARGET_REFILL: "content_validation_target_refill.json",
    STAGE_CONTENT_VALIDATION_FEASIBILITY_REFILL: "content_validation_feasibility_refill.json",
    STAGE_CONTENT_VALIDATION_OUTAGE: "content_validation_outage.json",
    STAGE_CONTENT_VALIDATION_OUTAGE_BASELINE: "content_validation_outage_baseline.json",
    STAGE_NEGOTIATION_PROPOSE_EXTRACTION: "negotiation_propose_extraction.json",
    STAGE_NEGOTIATION_ACCEPT_EXTRACTION: "negotiation_accept_extraction.json",
    STAGE_NEGOTIATION_REJECT_EXTRACTION: "negotiation_reject_extraction.json",
    STAGE_NEGOTIATION_TARGET_PROPOSE_EXTRACTION: "negotiation_target_propose_extraction.json",
    STAGE_NEGOTIATION_TARGET_ACCEPT_EXTRACTION: "negotiation_target_accept_extraction.json",
    STAGE_NEGOTIATION_TARGET_REJECT_EXTRACTION: "negotiation_target_reject_extraction.json",
    STAGE_NEGOTIATION_FEASIBILITY_PROPOSE_EXTRACTION: "negotiation_feasibility_propose_extraction.json",
    STAGE_NEGOTIATION_FEASIBILITY_ACCEPT_EXTRACTION: "negotiation_feasibility_accept_extraction.json",
    STAGE_NEGOTIATION_FEASIBILITY_REJECT_EXTRACTION: "negotiation_feasibility_reject_extraction.json",
    STAGE_NEGOTIATION_ABORT_EXTRACTION: "negotiation_abort_extraction.json",
}

#: Complete scenario inputs; checked before every other variant so a complete request never falls
#: through to a negotiation variant.
_COMPLETE_SCENARIO_INPUTS = tuple(EXAMPLE_REQUESTS[SCENARIO_NIGHT].values())

#: Planned-outage baseline request: a site-level Create with no background event, so it needs its
#: own create-shaped canned slot extraction.
_OUTAGE_BASELINE_SCENARIO_INPUTS = tuple(EXAMPLE_REQUESTS[SCENARIO_OUTAGE].values())

#: Planned-outage Modify request: the selected Task is continued with a Modify Task-T.
_OUTAGE_SCENARIO_INPUTS = tuple(MODIFY_REQUEST.values())

#: Target / feasibility negotiation inputs: complete inputs served by their own slot-extraction
#: variants, whose canned slots preserve the distinguishing values the rendered prompt needs. They
#: are checked before the missing inputs so the shared region/goal wording of an ambiguous or
#: infeasible request is never mistaken for the intentionally incomplete information request.
_TARGET_SCENARIO_INPUTS = tuple(EXAMPLE_REQUESTS[SCENARIO_TARGET].values())
_FEASIBILITY_SCENARIO_INPUTS = tuple(EXAMPLE_REQUESTS[SCENARIO_FEASIBILITY].values())
_UNKNOWN_SCENARIO_INPUTS = tuple(EXAMPLE_REQUESTS[SCENARIO_UNKNOWN_AREA].values())

#: Missing-throughput scenario inputs that flip the slot-extraction routing to the missing variant.
_MISSING_SCENARIO_INPUTS = tuple(EXAMPLE_REQUESTS[SCENARIO_INFORMATION].values())

#: Distinguishing values the target/feasibility rendered prompts carry, so the content-validation
#: routing also fires on real rendered-prompt heads and not only on the scenario natural-language
#: inputs. Each marker is a value the failing input carries and the negotiated refill does not.
_TARGET_VALIDATION_MARKER = "20:00~00:00,10Mbps"
_FEASIBILITY_VALIDATION_MARKERS = ("06:00~18:00,10Mbps",)
#: The planned-outage request and its rendered Task-T both carry the 03:00~05:00 outage and the
#: lower 2 Mbps guarantee. Either marker is enough to route to the outage canned validation.
_OUTAGE_VALIDATION_MARKERS = (
    "03:00~05:00,2Mbps",
    "lower the guaranteed throughput to 2Mbps",
    "将分时段保障速率下调到 2Mbps",
    "power outage from 03:00 to 05:00",
    "03:00~05:00停电",
)

#: Markers of the two-site baseline Task-T (phase 1 of the planned-outage run). It has no outage
#: wording, so it needs its own create-shaped validation fixture.
_OUTAGE_BASELINE_VALIDATION_MARKERS = ("00:00~24:00,10Mbps", "00:00~24:00, 10Mbps")

#: Throughput segment of the complete (information-accept) target. A rendered Task-T prompt that
#: carries the region but none of this marker omits the time-segmented guaranteed throughput
#: target, i.e. it is the information scenario's intentionally incomplete prompt.
_COMPLETE_THROUGHPUT_MARKER = "07:00~17:30,10Mbps"

#: Throughput marker of the unknown-area scenario. The area is the only invalid slot, so its
#: Task-T must validate successfully and reach the sample precheck instead of being treated as the
#: intentionally incomplete information request.
_UNKNOWN_AREA_VALIDATION_MARKER = "07:00~23:00,20Mbps"

#: Requested area inside the precheck proposal text (localized).
_REQUESTED_AREA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'The requested area "([^"]+)"'),
    re.compile(r"无法识别区域“([^”]+)”"),
)


def _requested_area(user_content: str) -> str | None:
    """Return the area named by an unknown-area proposal text, or ``None`` for another proposal."""
    for pattern in _REQUESTED_AREA_PATTERNS:
        match = pattern.search(user_content)
        if match is not None:
            return match.group(1)
    return None


#: From-data Task-T refill markers (the accepted values are the only distinguishing text).
#:
#: The feasibility refill carries the outage in both spellings: the filled parameter uses a space
#: (``00:00~18:00 停电``), while the canned slot extraction renders the task context without one
#: (``在00:00~18:00停电``). Content validation reads the rendered Task-T, so it needs the second
#: spelling; the failing input still routes to its own feasibility stage first through the
#: ``06:00~18:00,10Mbps`` marker.
_TARGET_REFILL_MARKERS = ("23:00~23:59,10Mbps",)
_FEASIBILITY_REFILL_MARKERS = (
    "power outage from 00:00 to 18:00",
    "00:00~18:00 停电",
    "00:00~18:00停电",
)

#: Negotiation-T extraction signatures routed most-specific property set first, pinned to the
#: property sets of ``a2a_t.negotiation.generation.json_schema_builder.build_extraction_schema``.
#:
#: Accept and reject share the terminal schema of a negotiation type, so the accept stage is the
#: default and :data:`_NEGOTIATION_REJECT_STAGES` overrides it when the user prompt declares the
#: reject phase.
_NEGOTIATION_EXTRACTION_SIGNATURES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"conclusion", "feasibility_summary"}), STAGE_NEGOTIATION_FEASIBILITY_ACCEPT_EXTRACTION),
    (frozenset({"conclusion", "confirmed_intent", "failure_reason"}), STAGE_NEGOTIATION_TARGET_ACCEPT_EXTRACTION),
    (frozenset({"conclusion", "items"}), STAGE_NEGOTIATION_ACCEPT_EXTRACTION),
    (
        frozenset({"feasibility_negotiation_description", "action"}),
        STAGE_NEGOTIATION_FEASIBILITY_PROPOSE_EXTRACTION,
    ),
    (frozenset({"target_negotiation_description"}), STAGE_NEGOTIATION_TARGET_PROPOSE_EXTRACTION),
    (frozenset({"items", "relationship"}), STAGE_NEGOTIATION_PROPOSE_EXTRACTION),
    (frozenset({"termination_reason"}), STAGE_NEGOTIATION_ABORT_EXTRACTION),
)

#: Reject-phase extraction stage per shared terminal signature.
_NEGOTIATION_REJECT_STAGES: dict[frozenset[str], str] = {
    frozenset({"conclusion", "feasibility_summary"}): STAGE_NEGOTIATION_FEASIBILITY_REJECT_EXTRACTION,
    frozenset({"conclusion", "confirmed_intent", "failure_reason"}): STAGE_NEGOTIATION_TARGET_REJECT_EXTRACTION,
    frozenset({"conclusion", "items"}): STAGE_NEGOTIATION_REJECT_EXTRACTION,
}

#: Negotiation kinds the semantic-validation response may declare.
_NEGOTIATION_KINDS: tuple[str, ...] = ("information", "target", "feasibility")

#: Template-URI segment identifying one negotiation kind.
_KIND_BY_TEMPLATE_SEGMENT: dict[str, str] = {
    "information-negotiation": "information",
    "target-negotiation": "target",
    "feasibility-negotiation": "feasibility",
}

#: Labels of the negotiation semantic-validation user prompt fields the routing reads.
#:
#: The labels are localized by the SDK prompt resources, but the values stay English: the phase is
#: a lower-case performative name and the declared type is the type name (``common`` for the abort
#: template). Both label spellings must be parsed so the sample's default ``zh-CN`` offline mode
#: routes exactly like ``en-US``.
_PHASE_LABELS: tuple[str, ...] = ("Negotiation phase:", "协商阶段：")
_DECLARED_TYPE_LABELS: tuple[str, ...] = ("Declared negotiation type:", "声明的协商类型：")
_TEMPLATE_URI_LABELS: tuple[str, ...] = ("Declared template identifier:", "声明的模板标识：")

#: Localized labels starting the template-content block of the content validation user prompt.
_TEMPLATE_CONTENT_LABELS = ("Template Content:", "模板正文：")

#: Sentence the information-propose input carries when more than one item is missing; the canned
#: propose-extraction response has no relationship by default, so the provider re-adds it.
_PROPOSE_RELATIONSHIP_MARKER = "Multiple items must be provided together."

#: Localized labels starting the free-text input block of an extraction user prompt.
_INPUT_LABELS: tuple[str, ...] = ("Input text:", "输入文本：")

#: Server revised-proposal marker the deterministic policy emits from round 2 on. A single canned
#: extraction cannot serve every round: the policy's text is the only round-specific input the
#: fromText call carries, so the provider synthesizes the extraction from it.
_REVISED_PROPOSAL_MARKER = "Revised proposal for round "

#: Initial target-proposal wording of the policy; its canned extraction is already faithful.
_TARGET_INITIAL_MARKER = "target negotiation needs clarification"

#: Server-abort wording of the policy. A client abort carries a different reason, so the canned
#: client-abort extraction stays correct for that path.
_SERVER_ABORT_MARKER = "maximum number of rounds"

#: Round budget carried by the policy's server-abort reason.
_ROUND_BUDGET_PATTERN = re.compile(r"\((?P<rounds>\d+)\)")

#: Fixed confirmation request of a clarified target, per language (the prompt forbids rephrasing).
_TARGET_CONFIRM_REQUESTS: dict[str, str] = {
    "en-US": "The target has been clarified. Do you agree to proceed with this target?",
    "zh-CN": "目标已经澄清，是否同意按照此目标继续执行？",
}

#: Label prefix and entry name of a client counter proposal, per language.
_CLIENT_COUNTER_LABELS: dict[str, str] = {
    "en-US": "Client counter proposal",
    "zh-CN": "客户端反提议",
}

#: Server-abort termination reason templates; ``{rounds}`` is the round budget.
_SERVER_ABORT_REASONS: dict[str, str] = {
    "en-US": (
        "The server ended the negotiation because the maximum number of rounds ({rounds}) was "
        "reached without convergence; the RAN energy saving task cannot start."
    ),
    "zh-CN": "服务端因达到最大协商轮次（{rounds}）仍未收敛而终止本次协商；无线网络节能任务无法启动。",
}

#: Routed stage names of every served structured call (module-level test hook).
llm_calls: list[str] = []


def reset_call_log() -> None:
    """Clear the module-level routed stage log (test hook)."""
    llm_calls.clear()


def resolve_language(*, env_path: Path | None = None) -> str:
    """Resolve the configured ``A2AT_LANGUAGE`` (defaults to ``zh-CN``)."""
    raw_value = os.environ.get("A2AT_LANGUAGE")
    if not raw_value:
        resolved = env_path or Path.cwd() / ".env"
        values = dotenv_values(resolved) if resolved.exists() else {}
        raw_value = values.get("A2AT_LANGUAGE")
    return str(raw_value or "").strip() or LANGUAGE_ZH


def load_script(language: str, *, resources_dir: Path | None = None) -> dict[str, str]:
    """Load the canned responses of one language as serialized JSON strings.

    Raises:
        FileNotFoundError: when the language tree does not exist.
    """
    language_dir = (resources_dir or _RESOURCES_DIR) / language
    if not language_dir.exists():
        raise FileNotFoundError(f"Scripted responses not found for language: {language} (expected at {language_dir})")
    script: dict[str, str] = {}
    for stage, file_name in _STAGE_FILES.items():
        with (language_dir / file_name).open(encoding="utf-8") as handle:
            script[stage] = json.dumps(json.load(handle), ensure_ascii=False)
    return script


def _head_before_markers(content: str, markers: tuple[str, ...]) -> str:
    """Return the leading part of one message, cut at the earliest of the given markers."""
    cut = len(content)
    for marker in markers:
        index = content.find(marker)
        if 0 <= index < cut:
            cut = index
    return content[:cut]


def _first_user_content(messages: list[dict[str, str]]) -> str:
    """Return the content of the first user message of one structured call."""
    return next(message["content"] for message in messages if message.get("role") == "user")


def _prompt_field(user_content: str, labels: tuple[str, ...]) -> str:
    """Return the value of one localized ``Label: value`` line of a user prompt, or ``""``."""
    for line in user_content.splitlines():
        for label in labels:
            if line.startswith(label):
                return line[len(label) :].strip()
    return ""


def _declared_negotiation_kind(user_content: str) -> str | None:
    """Return the negotiation kind declared by one semantic-validation user prompt.

    The prompt carries the declared type (``common`` for the type-independent abort template), the
    declared template URI and the phase. The common abort template yields ``None`` so the response
    satisfies the abort contract; a typed template yields its kind, falling back to the template-URI
    segment when the declared type line is absent.
    """
    phase = _prompt_field(user_content, _PHASE_LABELS).lower()
    declared_type = _prompt_field(user_content, _DECLARED_TYPE_LABELS).lower()
    if phase == "abort" or declared_type == "common":
        return None
    if declared_type in _NEGOTIATION_KINDS:
        return declared_type
    template_uri = _prompt_field(user_content, _TEMPLATE_URI_LABELS)
    segments = template_uri.split("/")
    if len(segments) >= 2:
        return _KIND_BY_TEMPLATE_SEGMENT.get(segments[1])
    return None


def _localized(values: dict[str, str], language: str) -> str:
    """Return one localized value, falling back to ``en-US``."""
    return values.get(language, values["en-US"])


def _extraction_input(user_content: str) -> str:
    """Return the free-text input block of one negotiation extraction user prompt."""
    for label in _INPUT_LABELS:
        index = user_content.find(label)
        if index < 0:
            continue
        block = user_content[index + len(label) :].lstrip("\r\n")
        end = block.find("\n\n")
        return (block[:end] if end >= 0 else block).strip()
    return user_content.strip()


def target_propose_extraction_response(user_content: str, language: str) -> str | None:
    """Synthesize the target propose extraction of one round, or ``None`` to use the canned file.

    The extraction schema is the same in every round, so a single canned response would render
    round 1's clarification text again for the server's revisions and for the client's counter
    proposals. The policy's revised-plan text (``Revised proposal for round N: ...``) is
    round-specific and authoritative, so it is served verbatim as the description; a client
    counter proposal is labeled as the initiating side's text. The policy's first-round text keeps
    the canned extraction, which already reflects it.

    Args:
        user_content: User prompt of the structured extraction call.
        language: Process-level language selecting the fixed confirmation request.

    Returns:
        Serialized JSON extraction, or ``None`` when the canned response is faithful.
    """
    text = _extraction_input(user_content)
    if _REVISED_PROPOSAL_MARKER in text:
        return json.dumps(
            {
                "target_negotiation_description": text,
                "intent_understanding": None,
                "alignment_and_clarification": None,
                "request_for_clarification": None,
                "target_confirm_request": _localized(_TARGET_CONFIRM_REQUESTS, language),
            },
            ensure_ascii=False,
        )
    if _TARGET_INITIAL_MARKER in text.lower() or not text:
        return None
    label = _localized(_CLIENT_COUNTER_LABELS, language)
    return json.dumps(
        {
            "target_negotiation_description": f"{label}: {text}",
            "intent_understanding": None,
            "alignment_and_clarification": [{"name": label, "value": text}],
            "request_for_clarification": None,
            "target_confirm_request": None,
        },
        ensure_ascii=False,
    )


def abort_extraction_response(user_content: str, language: str) -> str | None:
    """Synthesize the server abort extraction, or ``None`` for a client-initiated abort.

    The canned abort extraction reports that the client aborted. That is correct for a client abort,
    but the server aborts at the round limit; its reason must name the server and the round budget
    (design §8: the abort is the initiator's terminal action at ``maxRounds``).

    Args:
        user_content: User prompt of the structured extraction call.
        language: Process-level language of the synthesized reason.

    Returns:
        Serialized JSON extraction for a server abort, ``None`` otherwise.
    """
    text = _extraction_input(user_content)
    if _SERVER_ABORT_MARKER not in text.lower():
        return None
    match = _ROUND_BUDGET_PATTERN.search(text)
    rounds = match.group("rounds") if match is not None else str(MAX_ROUNDS)
    reason = _localized(_SERVER_ABORT_REASONS, language).format(rounds=rounds)
    return json.dumps({"termination_reason": reason}, ensure_ascii=False)


def negotiation_validation_response(user_content: str) -> str:
    """Build the canned semantic-validation response of one negotiation validation call.

    Offline mode accepts every negotiation message the client and the server exchange. The
    ``negotiation_type`` must match the declared template: the typed templates declare their kind
    and the common abort template declares ``null``. The gate validates with an empty parameter
    schema, so ``params`` is an empty object.

    Args:
        user_content: User prompt of the structured validation call.

    Returns:
        Serialized JSON response: ``semantic_verdict``, ``negotiation_type``, ``errors`` and
        ``params``.
    """
    return json.dumps(
        {
            "semantic_verdict": True,
            "negotiation_type": _declared_negotiation_kind(user_content),
            "errors": [],
            "params": {},
        },
        ensure_ascii=False,
    )


def _is_target_validation_head(head: str) -> bool:
    """Report whether one content-validation head carries the target negotiation input."""
    return _TARGET_VALIDATION_MARKER in head or any(
        scenario_input in head for scenario_input in _TARGET_SCENARIO_INPUTS
    )


def _is_feasibility_validation_head(head: str) -> bool:
    """Report whether one content-validation head carries the feasibility negotiation input."""
    return all(marker in head for marker in _FEASIBILITY_VALIDATION_MARKERS) or any(
        scenario_input in head for scenario_input in _FEASIBILITY_SCENARIO_INPUTS
    )


def route_structured_call(
    messages: list[dict[str, str]],
    json_schema: dict[str, Any] | None = None,
    *,
    language: str | None = None,
) -> str:
    """Return the stage name one structured call is routed to, keyed on its output schema.

    The schema signature picks the stage family: slot extraction by the ``slots``/``slot_errors``
    properties, content validation by ``semantic_verdict``, and the Negotiation-T extraction
    stages by the most-specific matching signature. The scenario variant is told apart from the
    user message.

    Raises:
        ValueError: when no signature matches the call.
    """
    user_content = _first_user_content(messages)
    properties = json_schema.get("properties") if isinstance(json_schema, dict) else None
    property_names = set(properties) if isinstance(properties, dict) else set()
    phase = _prompt_field(user_content, _PHASE_LABELS).lower()

    if "semantic_verdict" in property_names and "negotiation_type" in property_names:
        return STAGE_NEGOTIATION_SEMANTIC_VALIDATION
    if "slots" in property_names and "slot_errors" in property_names:
        # A structured (data + schema) initial request carries the outage markers but not the raw
        # scenario request; route it to the outage variant as well.
        if any(marker in user_content for marker in _OUTAGE_VALIDATION_MARKERS):
            return STAGE_SLOT_EXTRACTION_OUTAGE
        if any(marker in user_content for marker in _OUTAGE_BASELINE_VALIDATION_MARKERS):
            return STAGE_SLOT_EXTRACTION_OUTAGE_BASELINE
        if any(scenario_input in user_content for scenario_input in _OUTAGE_BASELINE_SCENARIO_INPUTS):
            return STAGE_SLOT_EXTRACTION_OUTAGE_BASELINE
        if any(scenario_input in user_content for scenario_input in _OUTAGE_SCENARIO_INPUTS):
            return STAGE_SLOT_EXTRACTION_OUTAGE
        if any(scenario_input in user_content for scenario_input in _COMPLETE_SCENARIO_INPUTS):
            return STAGE_SLOT_EXTRACTION
        if any(scenario_input in user_content for scenario_input in _TARGET_SCENARIO_INPUTS):
            return STAGE_SLOT_EXTRACTION_TARGET
        if any(scenario_input in user_content for scenario_input in _FEASIBILITY_SCENARIO_INPUTS):
            return STAGE_SLOT_EXTRACTION_FEASIBILITY
        if any(scenario_input in user_content for scenario_input in _UNKNOWN_SCENARIO_INPUTS):
            return STAGE_SLOT_EXTRACTION_UNKNOWN
        if any(marker in user_content for marker in _FEASIBILITY_REFILL_MARKERS):
            return STAGE_SLOT_EXTRACTION_FEASIBILITY_REFILL
        if any(marker in user_content for marker in _TARGET_REFILL_MARKERS):
            return STAGE_SLOT_EXTRACTION_TARGET_REFILL
        if any(scenario_input in user_content for scenario_input in _MISSING_SCENARIO_INPUTS):
            return STAGE_SLOT_EXTRACTION_MISSING
        return STAGE_SLOT_EXTRACTION
    if "semantic_verdict" in property_names:
        head = _head_before_markers(user_content, _TEMPLATE_CONTENT_LABELS)
        if any(marker in head for marker in _OUTAGE_BASELINE_VALIDATION_MARKERS):
            return STAGE_CONTENT_VALIDATION_OUTAGE_BASELINE
        if any(marker in head for marker in _OUTAGE_VALIDATION_MARKERS):
            return STAGE_CONTENT_VALIDATION_OUTAGE
        if _is_target_validation_head(head):
            return STAGE_CONTENT_VALIDATION_TARGET
        if _is_feasibility_validation_head(head):
            return STAGE_CONTENT_VALIDATION_FEASIBILITY
        if any(scenario_input in head for scenario_input in _COMPLETE_SCENARIO_INPUTS):
            return STAGE_CONTENT_VALIDATION
        if any(marker in head for marker in _FEASIBILITY_REFILL_MARKERS):
            return STAGE_CONTENT_VALIDATION_FEASIBILITY_REFILL
        if any(marker in head for marker in _TARGET_REFILL_MARKERS):
            return STAGE_CONTENT_VALIDATION_TARGET_REFILL
        if any(scenario_input in head for scenario_input in _MISSING_SCENARIO_INPUTS):
            return STAGE_CONTENT_VALIDATION_MISSING
        if _UNKNOWN_AREA_VALIDATION_MARKER in head:
            return STAGE_CONTENT_VALIDATION
        region_string = REGION_STRINGS.get(language or "", "")
        if region_string and region_string not in head:
            return STAGE_CONTENT_VALIDATION_MISSING
        # A rendered Task-T prompt is not the raw scenario request: the information scenario's
        # prompt carries the region but no time-segmented throughput target. Complete, target and
        # feasibility refills are matched above, so an absent complete-throughput marker is the
        # intentionally incomplete information prompt.
        if _COMPLETE_THROUGHPUT_MARKER not in head:
            return STAGE_CONTENT_VALIDATION_MISSING
        return STAGE_CONTENT_VALIDATION
    for signature, stage in _NEGOTIATION_EXTRACTION_SIGNATURES:
        if signature <= property_names:
            if phase == "reject":
                return _NEGOTIATION_REJECT_STAGES.get(signature, stage)
            return stage
    raise ValueError(f"The scripted LLM cannot route this structured call: {sorted(property_names)}")


#: Canonical Task-T task description the model may derive from the operation type.
_TASK_DESCRIPTIONS: dict[bool, dict[str, str]] = {
    False: {
        "Create": (
            "Based on <Task Object> and <Task Context>, output a wireless network energy saving plan "
            "that achieves the wireless network energy saving goal defined in <Task Target>, and "
            "return the wireless network energy saving result in the format defined in <Expected Output>."
        ),
        "Modify": (
            "Based on <Task Object> and <Task Context>, output a wireless network energy saving plan "
            "that achieves the wireless network energy saving goal defined in <Task Target>."
        ),
    },
    True: {
        "Create": (
            "基于<任务对象>和<任务上下文>，输出无线网络节能方案，达成<任务目标>中定义的无线网络节能目标，"
            "并按照<预期输出>中定义的格式返回无线网络节能结果。"
        ),
        "Modify": "基于<任务对象>和<任务上下文>，输出无线网络节能方案，达成<任务目标>中定义的无线网络节能目标。",
    },
}


def _task_description(value: object, operation: str, zh: bool) -> str:
    """Return the data map's description, or the canonical one for its operation type.

    A refill data map only carries the negotiated values; the Task-T template still requires a
    description, and the deterministic provider renders the same canonical text a model derives
    from the template's value constraint.
    """
    if isinstance(value, str) and value.strip():
        return value.strip()
    descriptions = _TASK_DESCRIPTIONS[zh]
    return descriptions.get(operation, descriptions["Create"])


#: Vendor region token in a free-text request (``A2ATestRegion``, ``RegionZ``, ...).
_REQUESTED_REGION_PATTERN = re.compile(r"\b([A-Za-z][A-Za-z0-9_-]*Region[A-Za-z0-9_-]*)\b")
_REQUESTED_WINDOW_PATTERN = re.compile(
    r"\{\s*(\d{1,2}:\d{2})\s*~\s*(\d{1,2}:\d{2})\s*,\s*(\d+(?:\.\d+)?)\s*Mbps\s*\}"
)


def _operator_input(user_content: str) -> str:
    """Return the ``[input]`` section of one slot-extraction prompt.

    The SDK prompt also carries the slot schema, whose examples mention ``A2ATestRegion``. Looking
    for a region in the whole prompt would pick that example instead of the operator's request.
    """
    marker = user_content.find("[input]")
    if marker < 0:
        return user_content
    end = user_content.find("[slots]", marker)
    return user_content[marker:end] if end >= 0 else user_content[marker:]


def _apply_requested_profile(content: str, user_content: str, language: str) -> str:
    """Align a canned slot response with the region and windows of the operator's free text."""
    operator_input = _operator_input(user_content)
    match = _REQUESTED_REGION_PATTERN.search(operator_input)
    if match is not None:
        region = match.group(1)
        if region not in {"Region", "RegionA", "RegionZ"}:
            # Only the neutral placeholder is rewritten; the unknown-area scenario keeps ``Region Z``.
            content = content.replace("Region A", region).replace("区域 A", region)
    windows = _REQUESTED_WINDOW_PATTERN.findall(operator_input)
    if not windows:
        return content
    lowered = operator_input.lower()
    if any(marker in lowered for marker in ("outage", "power", "停电", "背景事件")):
        # The feasibility canned response carries the background event after the target; keep it
        # instead of flattening the Task Context to the window list.
        return content
    target = ", ".join(f"{{{start}~{end},{rate}Mbps}}" for start, end, rate in windows)
    context = (
        f"分时段保障速率目标：{target}。"
        if language == LANGUAGE_ZH
        else f"Time-segment-based guaranteed throughput target: {target}."
    )
    try:
        document = json.loads(content)
    except ValueError:
        return content
    slots = document.get("slots")
    if not isinstance(slots, dict):
        return content
    key = "任务上下文" if language == LANGUAGE_ZH else "task_context"
    slots[key] = context
    return json.dumps(document, ensure_ascii=False)


def data_slot_extraction_response(user_content: str, language: str) -> str | None:
    """Echo the explicit ``[input]`` values of a data-based slot extraction.

    Complete requests and the unknown-area scenario render their Task-T from a data map instead of
    free text. The canned slot responses assume the default Region A request, so without this
    echo ``Region Z`` was silently replaced by Region A before the server precheck could see it.
    """
    marker = user_content.find("[input]")
    if marker < 0:
        return None
    line_start = user_content.find("\n", marker)
    line_end = user_content.find("\n", line_start + 1)
    if line_start < 0 or line_end < 0:
        return None
    raw = user_content[line_start + 1 : line_end].strip()
    if not raw.startswith("{"):
        return None
    try:
        data = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    region = data.get("region")
    target = data.get("guaranteedThroughputTarget")
    if not isinstance(region, str) or not isinstance(target, str):
        return None
    zh = language == LANGUAGE_ZH
    task_object = f"区域为{region}。" if zh else f"The area is the {region}."
    sites = data.get("siteList")
    if isinstance(sites, str) and sites.strip():
        items = [item.strip() for item in sites.split(",") if item.strip()]
        if zh:
            task_object += f" 站点列表1为[{'、'.join(items)}]。"
        else:
            quoted = ", ".join(f'"{item}"' for item in items)
            task_object += f" Site list 1: [{quoted}]."
    context = (
        f"分时段保障速率目标为 {target}。" if zh else f"Time-segment-based guaranteed throughput target: {target}."
    )
    background = data.get("backgroundEvent")
    if isinstance(background, str) and background.strip():
        context += f" 背景事件为{background}。" if zh else f" Background event: {background}."
    operation = str(data.get("operationType") or "Create")
    description = _task_description(data.get("task_description"), operation, zh)
    if zh:
        operation = {"Create": "创建", "Modify": "修改"}.get(operation, operation)
        slots = {
            "操作类型": operation,
            "任务描述": description,
            "任务对象": task_object,
            "任务目标": data.get("energySavingGoal") or "",
            "任务上下文": context,
            "预期输出": data.get("expectedOutput") or "",
        }
    else:
        slots = {
            "operation_type": operation,
            "task_description": description,
            "task_object": task_object,
            "task_target": data.get("energySavingGoal") or "",
            "task_context": context,
            "expected_output": data.get("expectedOutput") or "",
        }
    return json.dumps({"slots": slots, "slot_errors": []}, ensure_ascii=False)


class ScriptedLLMClient:
    """Deterministic ``LLMClient`` serving the canned response tree.

    Attributes:
        language: Language tree the responses are loaded from.
        calls: Routed stage names of every served call on this instance (test hook).
    """

    def __init__(
        self,
        config: LLMClientConfig,
        *,
        logger: Any | None = None,
        language: str | None = None,
        resources_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self.language = language or resolve_language()
        self._script = load_script(self.language, resources_dir=resources_dir)
        self.calls: list[str] = []

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Return the canned response routed by the output schema and scenario marker."""
        del temperature, max_tokens
        user_content = _first_user_content(messages)
        stage = route_structured_call(messages, json_schema, language=self.language)
        self.calls.append(stage)
        llm_calls.append(stage)
        if stage == STAGE_NEGOTIATION_SEMANTIC_VALIDATION:
            return LLMResponse(
                content=negotiation_validation_response(user_content),
                model="scripted",
                usage={"prompt_tokens": 0, "completion_tokens": 0},
                metadata={"scripted_stage": stage, "language": self.language},
            )
        content: str | None = None
        if stage == STAGE_NEGOTIATION_TARGET_PROPOSE_EXTRACTION:
            content = target_propose_extraction_response(user_content, self.language)
        elif stage == STAGE_NEGOTIATION_ABORT_EXTRACTION:
            content = abort_extraction_response(user_content, self.language)
        if content is None and stage.startswith("slot_extraction"):
            content = data_slot_extraction_response(user_content, self.language)
        if content is None and stage == STAGE_NEGOTIATION_PROPOSE_EXTRACTION:
            requested = _requested_area(user_content)
            if requested is not None:
                value = build_area_proposal_text(
                    requested,
                    match_served_areas(requested),
                    self.language,
                )
                content = json.dumps(
                    {"items": [{"name": "region", "value": value}], "relationship": None},
                    ensure_ascii=False,
                )
        if content is None:
            content = self._script[stage]
        if stage.startswith("slot_extraction"):
            # Free-text requests now carry the vendor region (for example ``A2ATestRegion``); the
            # canned slot responses were written for the neutral ``Region A`` example. Reconcile
            # them so the offline simulator sees the same Task Object as the page.
            content = _apply_requested_profile(content, user_content, self.language)
        if stage == STAGE_NEGOTIATION_PROPOSE_EXTRACTION and _PROPOSE_RELATIONSHIP_MARKER in user_content:
            extracted = json.loads(content)
            extracted["relationship"] = _PROPOSE_RELATIONSHIP_MARKER
            content = json.dumps(extracted, ensure_ascii=False)
        return LLMResponse(
            content=content,
            model="scripted",
            usage={"prompt_tokens": 0, "completion_tokens": 0},
            metadata={"scripted_stage": stage, "language": self.language},
        )


def register_scripted_provider() -> None:
    """Register :class:`ScriptedLLMClient` with the SDK factory under ``scripted``.

    Idempotent: a second call is a no-op.
    """
    if SCRIPTED_PROVIDER_NAME in LLMClientFactory.available_providers():
        return
    LLMClientFactory.register(SCRIPTED_PROVIDER_NAME, ScriptedLLMClient)


def is_scripted_provider_registered() -> bool:
    """Return whether the ``scripted`` provider is registered."""
    return SCRIPTED_PROVIDER_NAME in LLMClientFactory.available_providers()
