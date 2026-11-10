"""A2A client flow of the RAN energy-saving sample (SSE and webhook, multi-round negotiation).

The flow owns the protocol correctness of the client side:

* it generates the initial Task-T prompt with the SDK ``fromText`` API and sends it as one A2A
  ``Message`` (the text part is the short request label; the prompt travels under ``Task-T/v1``);
* it consumes the response events — the SSE stream or the webhook pushes plus the non-blocking RPC
  reply — through one normalized event shape;
* whenever the server sends a pre-task ``Propose``, it asks the configured
  :class:`~protocol.decision_provider.NegotiationDecisionProvider` for a decision and renders the
  matching protocol message with the SDK ``fromText`` generators;
* an ``accept`` refills the Task-T prompt with ``fromDataWithSchema``, a ``counter`` keeps the
  inbound round and carries ``metadata["sampleProposal"]``, and ``reject`` / ``abort`` are terminal;
* the server owns round counting: the client always uses ``session.with_performative(...)`` and
  never calls ``next_round()``.

A negotiation ends on a plain end ``Message`` ("协商已结束"), a server ``Abort`` message, or a
terminal Task state. Reject and abort never create a Task.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol

from a2a.client.client import ClientCallContext
from a2a.types import (
    CancelTaskRequest,
    Role,
    SendMessageRequest,
    StreamResponse,
    TaskPushNotificationConfig,
    TaskState,
)
from a2a_t.core.errors.exceptions import ContentValidationError, PromptGenerationError
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
    MetadataContent,
    NegotiationContext,
    NegotiationPerformative,
    decode_extension_value,
)
from a2a_t.core.standard_templates import (
    FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI,
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    NEGOTIATION_ABORT_URI,
    TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from a2a_t.negotiation.content import (
    FeasibilityEndingContent,
    InformationEndingContent,
    NegotiationConclusion,
    NegotiationEndingContent,
    NegotiationEndingData,
    NegotiationItem,
    TargetEndingContent,
)
from google.protobuf.json_format import ParseDict
from shared.config import DEFAULT_HTTP_TIMEOUT_SECONDS, DEFAULT_WEBHOOK_IDLE_TIMEOUT
from shared.logging_setup import log_context
from shared.models import NegotiationDecision, NegotiationOptions, NegotiationProposeView
from shared.run_constants import (
    FINAL_STATE_DECISION_UNGROUNDED,
    FINAL_STATE_INPUT_INVALID,
    FINAL_STATE_INTENT_NOT_CONFIRMED,
    FINAL_STATE_NEGOTIATION_ABORTED,
    FINAL_STATE_NEGOTIATION_ENDED,
    FINAL_STATE_TASK_COMPLETED,
    LANGUAGE_ZH,
    RUN_MODE_CREATE,
    RUN_MODE_MODIFY,
    reports_failed_completion,
)
from shared.run_source import now_iso
from shared.sse_event_consumer import normalize_event, stream_response_payload
from shared.task_schema import TASK_PARAM_SCHEMA
from shared.trace import (
    PHASE_NEGOTIATION,
    PHASE_RESOLUTION,
    PHASE_TASK_MODIFY,
    PHASE_TASK_PROMPT,
    PHASE_TASK_VALIDATION,
    TraceRecorder,
    current_trace,
    elapsed_ms,
    emit_activity,
    infer_decision_phase,
    is_report_artifact,
    producer_span_id,
    trace_stage,
)

from protocol.client_policy import ClientPolicy, agreed_target_value, apply_automated_decision
from protocol.decision_provider import (
    AdkDecisionProvider,
    DecisionTimeoutError,
    NegotiationDecisionProvider,
    ScriptedDecisionProvider,
)
from protocol.grounding import UngroundedValue, find_ungrounded, grounding_reask_text, normalize
from protocol.messages import (
    END_MESSAGE_TEXT,
    SAMPLE_NEGOTIATION_OPTIONS_METADATA_KEY,
    SAMPLE_PHASE_METADATA_KEY,
    SAMPLE_PHASE_MODIFY,
    SAMPLE_PROPOSAL_METADATA_KEY,
    TASK_TEMPLATE_URI,
    is_negotiation_message,
    metadata_to_dict,
    negotiation_context_metadata,
    normalize_negotiation_metadata,
    parse_negotiation_context,
    vendor_negotiation_value,
)
from protocol.negotiation_policy import (
    NegotiationKind,
    negotiation_kind_for_errors,
    negotiation_kind_for_negotiation_type,
    negotiation_kind_for_propose_content,
    negotiation_kind_for_template_uri,
)
from protocol.target_grammar import find_segments, normalize_text

_LOGGER = logging.getLogger("ran_energy_saving.client_flow")

#: Short label carried in the text part of every message (the protocol content lives in metadata).
TASK_REQUEST_LABEL: Final[str] = "create ran energy saving task"

#: Short label of the follow-up Task-T that modifies an existing Task.
TASK_MODIFY_LABEL: Final[str] = "modify ran energy saving task"

#: Final state when a terminal Task state other than COMPLETED is reached.
FINAL_STATE_TASK_REJECTED: Final[str] = "task_rejected"
FINAL_STATE_TASK_FAILED: Final[str] = "task_failed"

#: Final state when the event stream ends without a terminal event.
FINAL_STATE_INCOMPLETE: Final[str] = "incomplete"

#: Terminal Task states of the A2A SDK request handler.
_TERMINAL_STATE_NAMES: Final[frozenset[str]] = frozenset(
    TaskState.Name(state)
    for state in (
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_REJECTED,
    )
)

#: Default maximum number of client sends; a backstop against a server that never terminates.
DEFAULT_MAX_EXCHANGES: Final[int] = 16

#: Default HTTP budget of one A2A send; the client runtime passes the configured value. It reuses
#: the sample configuration default so the flow and the httpx client cannot drift.
DEFAULT_CALL_TIMEOUT_SECONDS: Final[float] = DEFAULT_HTTP_TIMEOUT_SECONDS

#: Negotiation-T accept/reject template URI per kind.
_ACCEPT_REJECT_TEMPLATE_URI_BY_KIND: Final[dict[str, str]] = {
    NegotiationKind.INFORMATION.value: INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    NegotiationKind.TARGET.value: TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
    NegotiationKind.FEASIBILITY.value: FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI,
}

#: Negotiation-T propose template URI per kind.
_PROPOSE_TEMPLATE_URI_BY_KIND: Final[dict[str, str]] = {
    NegotiationKind.INFORMATION.value: INFORMATION_NEGOTIATION_PROPOSE_URI,
    NegotiationKind.TARGET.value: TARGET_NEGOTIATION_PROPOSE_URI,
    NegotiationKind.FEASIBILITY.value: FEASIBILITY_NEGOTIATION_PROPOSE_URI,
}

#: Marker of the server's information-propose text that lists the missing parameter names.
_MISSING_ITEMS_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:missing items?|provide the following items?)\s*:\s*([^.]+)\.", re.IGNORECASE
)

# A Task-T throughput target is parsed by :mod:`protocol.target_grammar`. The flow used to own a
# tilde-only copy here, which silently dropped the hyphen/colon form the English slot overlay and
# the vendor Task-T actually carry.

#: Heading opening the Task-T context section (the template writes both language labels).
_CONTEXT_HEADING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^##\s+[^\n]*(?:任务上下文|Task Context)[^\n]*$", re.MULTILINE
)

#: Client-side errors.
_ERROR_NO_TEMPLATE_URI: Final[str] = "The negotiation propose carries no template URI."
_ERROR_NO_SESSION: Final[str] = "The negotiation propose carries no valid negotiationContext."
_ERROR_UNKNOWN_KIND: Final[str] = "The negotiation propose template is not a sample negotiation template."
_ERROR_NO_PROPOSE: Final[str] = "The server sent a negotiation message that is not a Propose or Abort."
_ERROR_DECISION_WAIT: Final[str] = (
    "The decision provider stopped waiting without a decision; nothing was sent and the run "
    "failed (wiring error)."
)


class ClientFlowError(RuntimeError):
    """Raised when the server sends content the client flow cannot handle."""


#: SDK validation codes that mean the validator itself failed rather than the content being invalid.
#: Those calls degrade to ``unavailable`` so a model outage never blocks a demo run.
_VALIDATION_UNAVAILABLE_CODES: Final[frozenset[str]] = frozenset(
    {
        "llm.not_configured",
        "llm.invocation_failed",
        "llm.response_invalid",
        "template.load_failed",
        "infra.resource_read_failed",
        "infra.internal_error",
    }
)

#: Deterministic validation failures the user can act on; they block regardless of the LLM verdict.
_VALIDATION_BLOCKING_CODES: Final[frozenset[str]] = frozenset(
    {
        "input.text_too_long",
        "template.not_found",
        "negotiation.invalid_input",
    }
)


@dataclass(frozen=True)
class TaskValidation:
    """Outcome of one pre-send Task-T validation call.

    ``filled`` carries the parameters the SDK extracted from the prompt. The accept refill uses
    them as its baseline, so a raw-intent run keeps the operator's values instead of falling back
    to a hardcoded default.
    """

    verdict: str
    negotiation_kind: NegotiationKind | None
    errors: list[dict[str, object]]
    reason: str | None
    duration_ms: int
    filled: Mapping[str, object] = field(default_factory=dict)
    #: How many SDK validation attempts this result consumed (1 when no retry was needed).
    attempts: int = 1


def _elapsed_ms(started: float) -> int:
    """Return the whole milliseconds elapsed since ``started``."""
    return max(0, int((time.monotonic() - started) * 1000))


def _validation_errors(error: ContentValidationError) -> list[dict[str, object]]:
    """Render the SDK slot errors as JSON-ready documents for the trace and the UI."""
    return [
        {
            "slotName": item.slot_name,
            "code": str(item.code),
            "message": item.message,
            "facts": dict(item.facts or {}),
        }
        for item in error.errors or []
    ]


def _generation_task_validation(error: PromptGenerationError) -> TaskValidation | None:
    """Turn a business slot-extraction failure into a blocked validation the operator can repair.

    A ``slot.*`` failure happens before any Task-T exists, so there is nothing to send and no
    protocol negotiation to start; the operator edits the request and retries. Infrastructure
    failures (no failed slot parameters) keep propagating instead of being misreported as input
    problems the operator could fix.
    """
    errors = [
        {
            "slotName": item.slot_name,
            "code": str(item.code),
            "message": item.message,
            "facts": dict(item.facts or {}),
        }
        for item in error.failed_parameters
    ]
    if not errors:
        return None
    return TaskValidation("blocked", None, errors, str(error.code_str), 0)


def _classify_validation_error(
    error: ContentValidationError,
) -> tuple[str, NegotiationKind | None]:
    """Map one SDK validation failure to ``passed``-adjacent, negotiable or blocking."""
    code = error.code_str
    if code in _VALIDATION_UNAVAILABLE_CODES:
        return "unavailable", None
    if code in _VALIDATION_BLOCKING_CODES:
        return "blocked", None
    kind = negotiation_kind_for_errors(error.errors)
    if kind is not None:
        # The server maps the same errors to a negotiation kind, so the request must still be sent.
        return "negotiable", kind
    return "blocked", None


#: Business labels of the caller-schema required fields, used by the OSS-agent interception.
_REQUIRED_FIELD_LABELS: Final[dict[str, str]] = {
    "operationType": "Operation Type",
    "region": "Area",
    "guaranteedThroughputTarget": "Rate guarantee goal",
}

#: Business labels of the same required fields in Chinese.
_REQUIRED_FIELD_LABELS_ZH: Final[dict[str, str]] = {
    "operationType": "操作类型",
    "region": "区域",
    "guaranteedThroughputTarget": "速率保障目标",
}


def _required_field_label(
    name: str,
    schema: Mapping[str, object],
    language: str,
) -> str:
    """Return the business label of one required field.

    The schema's own ``title`` (and ``x-title-zh-CN``) is authoritative, so adding a required field
    also adds its label instead of leaking the camelCase property name. The static maps remain a
    fallback for schemas that predate the titles.
    """
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        prop = properties.get(name)
        if isinstance(prop, Mapping):
            key = "x-title-zh-CN" if language == LANGUAGE_ZH else "title"
            value = prop.get(key) or prop.get("title")
            if isinstance(value, str) and value.strip():
                return value.strip()
    labels = _REQUIRED_FIELD_LABELS_ZH if language == LANGUAGE_ZH else _REQUIRED_FIELD_LABELS
    return labels.get(name, name)


#: Caller-schema field a site-level request must state. The Task-T template requires a
#: time-segmented rate target for a site-level configuration; the caller schema does not list it
#: in ``required`` because an area-level request may omit it.
RATE_TARGET_FIELD: Final[str] = "guaranteedThroughputTarget"


def _is_missing_value(value: object) -> bool:
    """Return whether one extracted parameter carries no value."""
    return value is None or (isinstance(value, str) and not value.strip())


def _missing_required_fields(
    data: Mapping[str, object],
    schema: Mapping[str, object],
    language: str,
    *,
    site_level_rate_optional: bool = False,
) -> list[dict[str, object]]:
    """Return the caller-schema required fields the OSS agent must ask the operator to supply.

    The schema's ``required`` list is the only source of mandatory fields. The guaranteed rate is
    deliberately not on it: an area-level request without one is valid (the vendor treats an empty
    target list as the default non-disruptive mode, and our Task-T template marks the rate target
    optional). A site-level request still needs it, because the Task-T template requires a
    time-segmented rate target for site-level configurations; enforcing that rule here keeps
    area-level requests sendable while the site-level contract stays intact.

    The returned errors use the same ``content.param_missing`` shape as the SDK, including a
    business ``section_label`` that the presentation and the negotiation proposal can display.
    """
    required = schema.get("required")
    names = [name for name in required if isinstance(name, str)] if isinstance(required, list) else []
    site_level = any(
        data.get(field) not in (None, "")
        for field in ("siteList", "backgroundEvent")
    )
    if not site_level_rate_optional and RATE_TARGET_FIELD not in names:
        names.append(RATE_TARGET_FIELD)
    errors: list[dict[str, object]] = []
    for name in names:
        if name == RATE_TARGET_FIELD and not site_level:
            # Area-level: the rate guarantee target is optional.
            continue
        if not _is_missing_value(data.get(name)):
            continue
        label = _required_field_label(name, schema, language)
        message = (
            f"缺少必填项「{label}」，请补充后重试"
            if language == LANGUAGE_ZH
            else f"'{label}' is empty; please provide a value"
        )
        errors.append(
            {
                "slotName": name,
                "code": "content.param_missing",
                "message": message,
                "facts": {"section_label": label},
            }
        )
    return errors


def _validation_names_a_missing_required(
    error: ContentValidationError,
    schema: Mapping[str, object],
) -> bool:
    """Return whether a failed validation reports a required value as missing.

    A semantic conflict (for example overlapping target windows) nulls the affected slot too, but
    that value is negotiable and must still reach the server. A structured error on a caller-
    required slot triggers the local business gate; the gate itself then decides whether the value
    is actually absent.
    """
    required = schema.get("required")
    if isinstance(required, list):
        required_names = {name for name in required if isinstance(name, str)}
        for item in error.errors or ():
            if item.slot_name in required_names and str(item.code) in {
                "content.param_missing",
                "content.entry_field_missing",
            }:
                return True
    return str(error.code_str) in {"content.param_missing", "content.entry_field_missing"}


#: Extra attempts the sample makes when the SDK reports ``llm.response_invalid``. The SDK already
#: retries that code, but the sample pins ``A2AT_LLM_MAX_ATTEMPTS=1`` to keep one LLM call inside
#: the A2A HTTP budget. Retrying the contract failure here is cheap (the model returned in seconds);
#: transport timeouts (``llm.invocation_failed``) are deliberately not retried, because a second
#: 120 s wait would exhaust the same budget.
_SEMANTIC_VALIDATION_RETRIES: Final[int] = 1


async def _validate_task_prompt(
    prompt_client: PromptClient,
    prompt: str,
    schema: Mapping[str, object],
    template_uri: str,
    *,
    language: str,
    enforce_required_fields: bool = True,
    site_level_rate_optional: bool = False,
) -> TaskValidation:
    """Run the SDK content validation without blocking the event loop.

    The extracted parameters are always returned, even when semantic validation fails, because the
    accept refill and the required-field gate both need the operator-derived baseline.
    """
    started = time.monotonic()
    total_attempts = _SEMANTIC_VALIDATION_RETRIES + 1
    for attempt in range(1, total_attempts + 1):
        try:
            filled = await asyncio.to_thread(
                prompt_client.validate_task_prompt_and_data_filling,
                prompt,
                schema,
                template_uri,
            )
        except ContentValidationError as error:
            if error.code_str == "llm.response_invalid" and attempt < total_attempts:
                _LOGGER.warning(
                    "Task-T semantic validation returned an invalid response (attempt %s/%s); retrying",
                    attempt,
                    total_attempts,
                )
                continue
            params = error.params if isinstance(error.params, Mapping) else {}
            if enforce_required_fields and _validation_names_a_missing_required(error, schema):
                missing = _missing_required_fields(
                    params,
                    schema,
                    language,
                    site_level_rate_optional=site_level_rate_optional,
                )
                if missing:
                    return TaskValidation(
                        "blocked",
                        None,
                        missing,
                        "content.param_missing",
                        _elapsed_ms(started),
                        params,
                        attempt,
                    )
            verdict, kind = _classify_validation_error(error)
            return TaskValidation(
                verdict,
                kind,
                _validation_errors(error),
                error.code_str,
                _elapsed_ms(started),
                params,
                attempt,
            )
        except Exception as error:  # noqa: BLE001 - validation must never break the send path
            _LOGGER.warning("Task-T validation unavailable: %s", error)
            reason = f"{type(error).__name__}: {error}"
            return TaskValidation("unavailable", None, [], reason, _elapsed_ms(started), {}, attempt)
        extracted = getattr(filled, "data", filled)
        data = dict(extracted) if isinstance(extracted, Mapping) else {}
        if enforce_required_fields:
            missing = _missing_required_fields(
                data,
                schema,
                language,
                site_level_rate_optional=site_level_rate_optional,
            )
            if missing:
                # The OSS agent owns the business-required fields: do not send an incomplete Task-T
                # and rely on the network's negotiation for a value the operator can supply here.
                return TaskValidation(
                    "blocked",
                    None,
                    missing,
                    "content.param_missing",
                    _elapsed_ms(started),
                    data,
                    attempt,
                )
        return TaskValidation("passed", None, [], None, _elapsed_ms(started), data, attempt)
    raise RuntimeError("unreachable")  # pragma: no cover - every loop path returns


async def _resolve_intent_fields(
    intent_fields: Mapping[str, object] | Awaitable[Mapping[str, object]] | None,
) -> Mapping[str, object] | None:
    """Resolve the intent layer's fields, awaiting a concurrent intent read when one is passed.

    The intent layer is best effort: a read that failed or timed out leaves the run without
    intent fields, never with a failed run. The runtime's own processor already degrades to a raw
    reading, so this guard only covers a task that was cancelled or raised.
    """
    if intent_fields is None or isinstance(intent_fields, Mapping):
        return intent_fields
    if not inspect.isawaitable(intent_fields):
        return None
    try:
        return await intent_fields
    except Exception as error:  # noqa: BLE001 - the intent layer must not break the run
        _LOGGER.warning("intent fields unavailable; continuing without them: %s", error)
        return None


def _merge_initial_filled(
    validation: TaskValidation | None,
    intent_fields: Mapping[str, object] | None,
) -> dict[str, object]:
    """Return the operator-derived baseline of an accept refill.

    The SDK extraction is authoritative; the intent layer fills the values the extraction could
    not provide (a raw-intent run has no intent, so the extraction is the only source).
    """
    merged = dict(intent_fields or {})
    if validation is not None:
        for key, value in validation.filled.items():
            if value is not None and not (isinstance(value, str) and not value.strip()):
                merged[key] = value
    return merged


def _region_consistency_errors(
    extracted: Mapping[str, object],
    intent_fields: Mapping[str, object] | None,
    language: str,
) -> list[dict[str, object]]:
    """Return one error when the extracted region disagrees with the intent's verbatim region.

    The intent agent is instructed to keep the region verbatim. A disagreement means the Task-T
    extraction was polluted (for example by a schema example), so the request must not be sent.
    """
    extracted_region = extracted.get("region")
    intent_region = (intent_fields or {}).get("region")
    if not (isinstance(extracted_region, str) and extracted_region.strip()):
        return []
    if not (isinstance(intent_region, str) and intent_region.strip()):
        return []
    if normalize(extracted_region) == normalize(intent_region):
        return []
    label = "区域" if language == LANGUAGE_ZH else "Area"
    if language == LANGUAGE_ZH:
        message = (
            f"提取出的区域「{extracted_region}」与操作员原文「{intent_region}」不一致；"
            "请修改请求后重试"
        )
    else:
        message = (
            f"The extracted region '{extracted_region}' does not match the operator's text "
            f"'{intent_region}'; edit the request and retry."
        )
    return [
        {
            "slotName": "region",
            "code": "content.value_mismatch",
            "message": message,
            "facts": {"section_label": label},
        }
    ]


class PromptClient(Protocol):
    """The ``A2ATClient`` surface the flow uses (also implemented by test fakes)."""

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> MetadataContent: ...

    def generate_task_prompt_from_data_with_schema(
        self,
        data: Mapping[str, object],
        schema: Mapping[str, object],
        template_uri: str,
    ) -> MetadataContent: ...

    def generate_negotiation_accept_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent: ...

    def generate_negotiation_accept_prompt_from_data(
        self, data: NegotiationEndingData, template_uri: str
    ) -> MetadataContent: ...

    def generate_negotiation_reject_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent: ...

    def generate_negotiation_abort_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent: ...

    def generate_negotiation_propose_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent: ...

    def validate_task_prompt_and_data_filling(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> object: ...


class SendMessageClient(Protocol):
    """Minimal a2a-sdk client surface: one async-iterator ``send_message`` call per exchange."""

    def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext,
    ) -> AsyncIterator[StreamResponse]: ...


#: Observer invoked immediately after a provider returns its decision (Run Explorer §6).
DecisionMadeCallback = Callable[[NegotiationDecision, NegotiationProposeView, NegotiationContext], None]


class ClientTransport(Protocol):
    """Send one request and return its normalized events up to the first stop point."""

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]: ...


@dataclass
class ClientFlowResult:
    """Outcome of one client flow run.

    Attributes:
        events: Every normalized event received, in arrival order.
        final_state: Terminal state name (``task_completed``, the two no-task states, a task
            failure name, or ``incomplete``).
        decisions: Decisions returned by the provider, in order.
        context_id: A2A context id of the conversation, when observed.
        task_id: Task id of the created task, when one was created.
        report: Last report artifact payload/text of a completed task, when observed.
        reports: Report artifact payloads in arrival order (a two-phase run has a baseline report
            and the modified report).
        error: Operator-facing reason of a terminal state that is not a task outcome.
    """

    events: list[dict[str, object]] = field(default_factory=list)
    final_state: str = FINAL_STATE_INCOMPLETE
    decisions: list[NegotiationDecision] = field(default_factory=list)
    context_id: str | None = None
    task_id: str | None = None
    report: dict[str, object] | None = None
    reports: list[dict[str, object]] = field(default_factory=list)
    error: str | None = None


def is_terminal_event(event: Mapping[str, object]) -> bool:
    """Report whether one normalized event is a terminal Task state."""
    return event.get("kind") == "status" and event.get("state") in _TERMINAL_STATE_NAMES


def is_stop_event(event: Mapping[str, object]) -> bool:
    """Report whether one normalized event ends a send phase (message or terminal state)."""
    return event.get("kind") == "message" or is_terminal_event(event)


def normalize_webhook_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Convert one webhook push JSON body into the normalized event shape.

    The receiver hands over the ``MessageToDict`` of a ``StreamResponse``; parsing it back into the
    protobuf type keeps the SSE and webhook paths on exactly one normalization implementation.
    """
    try:
        stream_response = StreamResponse()
        ParseDict(stream_response_payload(payload), stream_response)
        return normalize_event(stream_response)
    except Exception as exc:
        raise ClientFlowError(f"Unsupported webhook payload: {exc}") from exc


class SseClientTransport:
    """SSE transport: stream one ``message:send`` and stop at the first message/terminal event.

    The optional event sink is invoked for every normalized event as soon as the stream yields it,
    which lets the Web UI show a round while the flow is still waiting for the next one.
    """

    def __init__(self, client: SendMessageClient) -> None:
        """Create one SSE transport over an a2a-sdk client."""
        self._client = client
        self._event_sink: Callable[[dict[str, object]], None] | None = None

    def set_event_sink(self, event_sink: Callable[[dict[str, object]], None] | None) -> None:
        """Attach the live event sink used by :func:`run_client_flow`."""
        self._event_sink = event_sink

    def _emit(self, event: dict[str, object]) -> None:
        """Forward one normalized event to the attached sink, when any."""
        if self._event_sink is not None:
            self._event_sink(event)

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        """Send one message and consume its stream until a stop point."""
        events: list[dict[str, object]] = []
        async for stream_response in self._client.send_message(request, context=call_context):
            event = normalize_event(stream_response)
            events.append(event)
            self._emit(event)
            if is_stop_event(event):
                break
        _LOGGER.debug("sse phase finished: events=%d", len(events))
        return events

    async def cancel(self, task_id: str, call_context: ClientCallContext | None = None) -> None:
        """Cancel the named Task (used when a two-phase run cannot send its Modify)."""
        await _cancel_task(self._client, task_id, call_context)


class WebhookClientTransport:
    """Webhook transport: non-blocking send with inline push registration.

    The RPC reply is inspected first because a pre-task negotiation message is never pushed; task
    events then arrive on the embedded receiver's queue. The idle timeout is the backstop that ends
    a phase when the server pushes nothing.
    """

    def __init__(
        self,
        client: SendMessageClient,
        *,
        queue: asyncio.Queue[dict[str, object]],
        push_url: str,
        push_token: str,
        idle_timeout: float = float(DEFAULT_WEBHOOK_IDLE_TIMEOUT),
        bind: Callable[[str | None, str | None], None] | None = None,
        context_id: str = "",
    ) -> None:
        """Create one webhook transport.

        Args:
            client: a2a-sdk client whose ``send_message`` is an async generator.
            queue: Queue fed by the webhook router for this run only.
            push_url: Receiver callback URL registered inline on every request.
            push_token: Push token checked by the receiver.
            idle_timeout: Backstop timeout waiting for pushed events.
            bind: Optional callback binding ``(task_id, context_id)`` to this run's queue. The
                router uses it to demultiplex a process-level receiver; a push that arrives before
                the RPC reply is buffered and flushed when the bind lands.
            context_id: Optional client-assigned A2A context id. Setting it before the first send
                makes every pushed event routable from its ``contextId`` alone, even when the
                server answers with a bare message instead of a Task.
        """
        self._client = client
        self._queue = queue
        self._push_url = push_url
        self._push_token = push_token
        self._idle_timeout = idle_timeout
        self._bind = bind
        self._context_id = context_id
        self._event_sink: Callable[[dict[str, object]], None] | None = None

    def set_event_sink(self, event_sink: Callable[[dict[str, object]], None] | None) -> None:
        """Attach the live event sink used by :func:`run_client_flow`."""
        self._event_sink = event_sink

    def _emit(self, event: dict[str, object]) -> None:
        """Forward one normalized event to the attached sink, when any."""
        if self._event_sink is not None:
            self._event_sink(event)

    def _bind_context(self, request: SendMessageRequest) -> None:
        """Stamp the run's A2A context on the message and bind it before the request leaves.

        The server echoes the context on every pushed event, so binding before the send removes
        the pre-reply window for the context-keyed subset. The task id is still bound from the
        reply, which is the stronger key for status/artifact events.
        """
        if not self._context_id:
            return
        if not request.message.context_id:
            request.message.context_id = self._context_id
        if self._bind is not None:
            self._bind(None, request.message.context_id)

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        """Send one non-blocking message, register push inline, and collect its events."""
        self._bind_context(request)
        request.configuration.return_immediately = True
        # This transport serves our own server, where the Task is created when the negotiation is
        # accepted: the negotiation reply is the create send and must carry the registration. The
        # vendor NMS creates the Task on the initial Task-T and rejects a push configuration on a
        # negotiation reply — that rule lives in ``NmsWebhookTransport``.
        request.configuration.task_push_notification_config.CopyFrom(
            TaskPushNotificationConfig(url=self._push_url, token=self._push_token)
        )
        events: list[dict[str, object]] = []
        reply: StreamResponse | None = None
        loop = asyncio.get_running_loop()
        send_started = loop.time()
        async for stream_response in self._client.send_message(request, context=call_context):
            reply = stream_response
        # The reply is the last event of the phase, so the backstop starts when it lands and the
        # send duration is not charged as silence. Without a reply the send itself is the only
        # reference point.
        phase_started = loop.time() if reply is not None else send_started
        if reply is not None:
            reply_event = normalize_event(reply)
            events.append(reply_event)
            self._emit(reply_event)
            if self._bind is not None:
                # The router can only route this run's pushes once it knows the identities the
                # server assigned to it; bind them before waiting on the shared receiver.
                self._bind(
                    _string_field(reply_event, "task_id"),
                    _string_field(reply_event, "context_id"),
                )
            if is_stop_event(reply_event):
                return events
        # One budget per phase, counted from the reply: a server that keeps pushing must not
        # restart the clock, or the phase (and the caller waiting on it) never ends.
        deadline = phase_started + self._idle_timeout
        while True:
            remaining = max(deadline - asyncio.get_running_loop().time(), 0.0)
            try:
                payload = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except TimeoutError:
                _LOGGER.info("webhook phase idle for %gs; treating it as complete", self._idle_timeout)
                return events
            event = normalize_webhook_payload(payload)
            events.append(event)
            self._emit(event)
            # The report artifact ends a webhook phase: the underlying energy-saving task is
            # long-running and stays WORKING after it (design §7.1 / old webhook parity). The ADK
            # executor also emits one artifact per tool call, so only the named/stamped report
            # artifact ends the phase; stopping at the first tool artifact would lose the report.
            if is_stop_event(event) or is_report_artifact(event):
                return events

    async def cancel(self, task_id: str, call_context: ClientCallContext | None = None) -> None:
        """Cancel the named Task (used when a two-phase run cannot send its Modify)."""
        await _cancel_task(self._client, task_id, call_context)


async def _cancel_task(
    client: SendMessageClient,
    task_id: str,
    call_context: ClientCallContext | None,
) -> None:
    """Cancel one Task through the SDK client, failing clearly when it cannot."""
    cancel_task = getattr(client, "cancel_task", None)
    if cancel_task is None:
        raise ClientFlowError("The A2A client does not support cancelling a Task.")
    await cancel_task(CancelTaskRequest(id=task_id), context=call_context)


async def run_client_flow(
    *,
    prompt_client: PromptClient,
    transport: ClientTransport,
    user_request: str,
    provider: NegotiationDecisionProvider,
    label: str = TASK_REQUEST_LABEL,
    task_template_uri: str = TASK_TEMPLATE_URI,
    language: str = LANGUAGE_ZH,
    intent_fields: Mapping[str, object] | Awaitable[Mapping[str, object]] | None = None,
    max_exchanges: int = DEFAULT_MAX_EXCHANGES,
    call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT_SECONDS,
    event_sink: Callable[[dict[str, object]], None] | None = None,
    on_decision_requested: Callable[[NegotiationProposeView, NegotiationContext], None] | None = None,
    on_decision_made: DecisionMadeCallback | None = None,
    on_intent_ready: Callable[[str], Awaitable[bool]] | None = None,
    trace_sink: Callable[[dict[str, object]], None] | None = None,
    conversation_context: str | None = None,
    single_extension_per_message: bool = False,
    infer_missing_negotiation_kind: bool = False,
    client_policy: ClientPolicy | None = None,
    validate_before_send: bool = False,
    enforce_required_fields: bool = False,
    site_level_rate_optional: bool = False,
    run_mode: str = RUN_MODE_CREATE,
    target_task_id: str | None = None,
    target_context_id: str | None = None,
) -> ClientFlowResult:
    """Run one complete client flow: initial Task-T, multi-round negotiation, task or end.

    Args:
        prompt_client: A2A-T SDK client facade (or a test double).
        transport: SSE or webhook transport.
        user_request: Natural-language task request.
        provider: Negotiation decision provider.
        label: Short text part of every message.
        task_template_uri: Task-T template the initial and accept prompts address.
        language: Process-level language of the refilled Task-T values.
        intent_fields: Business values parsed from the request by the intent layer, keyed by their
            Task-T parameter name. They are the base of an accept refill; a decision's
            ``filled_params`` overrides them. ``None`` means the run has no intent layer. An
            awaitable is also accepted so the runtime can overlap the intent read with Task-T
            generation; it is resolved once, after the prompt is rendered.
        max_exchanges: Backstop on the number of client sends.
        call_timeout_seconds: HTTP budget of every send, carried in ``ClientCallContext.timeout``.
            It must outlast a live-mode server gate that performs several LLM calls before it
            emits any event.
        event_sink: Optional observer invoked for every normalized event. The SSE and webhook
            transports forward each event as it arrives; a transport without a ``set_event_sink``
            hook has its returned batch forwarded instead, so the observer sees every event exactly
            once.
        on_decision_requested: Optional observer invoked immediately before the provider is asked
            for a decision, carrying the proposal view and its negotiation session (the Web UI
            uses it to display the manual decision form).
        on_decision_made: Optional observer invoked immediately after the provider returns, before
            the response is built or sent. It lets the Web UI and the transcript record a decision
            causally, i.e. before the task events it causes.
        on_intent_ready: Optional async approval gate invoked with the rendered Task-T before the
            initial message is sent. Returning ``False`` ends the run before any task is created.
        trace_sink: Optional observer of synthetic Run Explorer spans (``task_prompt_rendered``,
            ``message_sent``, ``decision_requested``, ``decision_made``). Protocol events keep using
            ``event_sink``; the two streams share the Web UI sequence numbering.
        conversation_context: Optional recent-turn block prepended to the natural-language request
            before Task-T generation, so a follow-up can refer to an earlier run. The wire prompt
            still contains only the current request plus this delimited context.
        single_extension_per_message: When true (the real NMS), an accept carries only the
            Negotiation-T key because that server rejects a message with more than one ``-T``
            extension key. The local demo server instead requires the agreed Task-T on the accept,
            so it keeps the default ``False``.
        infer_missing_negotiation_kind: When true (the real NMS), a propose that carries no
            ``templateUri`` is typed from the opening heading of its body. The vendor omits the
            field on the proposal that continues a negotiation; without this the run fails hard.
            A body without a known heading still fails, because guessing the kind would pick the
            wrong decision prompt and reply template.
        client_policy: Client acceptance limits applied to an automatic decision. The flow never
            invents an entity value and never accepts a plan below the configured throughput floor;
            in automatic mode it also accepts a feasible server alternative the model rejected.
            ``None`` means no floor.
        validate_before_send: Validate each rendered Task-T with the SDK content pipeline before
            sending it. Negotiable errors still go to the server for negotiation; unmapped errors
            end the flow with ``input_invalid`` and no message is sent.
        enforce_required_fields: Check the caller schema's required parameters against the
            extracted data and stop before sending when one is empty. The SDK extraction runs even
            when ``validate_before_send`` is false, so the gate does not depend on the semantic
            validation switch.
        run_mode: ``create`` starts a new A2A Task; ``modify`` sends the scenario's Modify Task-T
            as the first message so an existing running Task is continued.
        target_task_id: Task the Modify-only run continues; required by ``run_mode="modify"``.
        target_context_id: Context of the target Task; required by ``run_mode="modify"``.

    Returns:
        The collected events, provider decisions and terminal state.

    Raises:
        ClientFlowError: when the server sends an unanswerable negotiation message or the stream
            exceeds the exchange budget.
    """
    policy = client_policy or ClientPolicy()
    carries_task_t = not single_extension_per_message
    transport_emits_events = False
    if event_sink is not None:
        set_event_sink = getattr(transport, "set_event_sink", None)
        if callable(set_event_sink):
            set_event_sink(event_sink)
            transport_emits_events = True

    def collect(batch: list[dict[str, object]]) -> None:
        """Store one send phase's events and forward them when the transport does not."""
        result.events.extend(batch)
        if event_sink is not None and not transport_emits_events:
            for event in batch:
                event_sink(event)

    recorder = TraceRecorder(current_trace().run_id or "", id_prefix="flow")

    def emit_trace(
        kind: str,
        *,
        phase: str,
        title: str,
        direction: str = "internal",
        round_number: int | None = None,
        context_id: str | None = None,
        detail: Mapping[str, object] | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Build one synthetic span and forward it to the optional trace sink."""
        if trace_sink is None:
            return
        span = recorder.emit(
            kind=kind,
            phase=phase,
            title=title,
            actor="client",
            side="client",
            direction=direction,
            round=round_number,
            context_id=context_id,
            detail=detail,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
        )
        trace_sink(span.to_dict())

    async def send_with_wait_activity(
        request: SendMessageRequest,
        call_context: ClientCallContext,
        *,
        stage: str,
    ) -> list[dict[str, object]]:
        """Send one request while publishing what the page is waiting for."""
        activity_id = producer_span_id("activity")
        emit_activity(
            activity_id=activity_id,
            activity_kind="peer",
            status="running",
            label_key="waitingPeer",
            stage=stage,
            detail={"producer": "client_flow"},
        )
        try:
            return await transport.send(request, call_context)
        finally:
            emit_activity(
                activity_id=activity_id,
                activity_kind="peer",
                status="done",
                label_key="waitingPeer",
                stage=stage,
            )

    def emit_validation(
        validation: TaskValidation,
        prompt_text: str,
        template_uri: str,
        phase: str,
    ) -> None:
        """Publish one pre-send validation result for the timeline and the presentation card."""
        emit_trace(
            "task_validation",
            phase=phase,
            title=f"Task-T validation {validation.verdict}",
            detail={
                "verdict": validation.verdict,
                "templateUri": template_uri,
                "promptText": prompt_text[:4000],
                "blocking": validation.verdict == "blocked",
                "errors": validation.errors,
                "negotiationKind": (
                    validation.negotiation_kind.value if validation.negotiation_kind is not None else None
                ),
                "durationMs": validation.duration_ms,
                "reason": validation.reason,
                "attempts": validation.attempts,
            },
        )

    def emit_message_sent(
        request: SendMessageRequest,
        *,
        phase: str,
        title: str,
        performative: str,
        round_number: int | None,
        context_id: str | None,
    ) -> None:
        """Emit one outbound ``message_sent`` span for an A2A request."""
        metadata = metadata_to_dict(request.message.metadata)
        template_uri = metadata.get(TEMPLATE_URI_METADATA_KEY)
        label_text = request.message.parts[0].text if request.message.parts else ""
        metadata_keys = sorted(str(key) for key in metadata)
        extension_uri = next(
            (key for key in metadata_keys if "Task-T" in key),
            None,
        )
        negotiation_uri = next(
            (key for key in metadata_keys if "Negotiation-T" in key),
            None,
        )
        # The refilled Task-T is the authoritative record of the values the client actually sent
        # (the accept decision itself carries no filled params). Keep a bounded copy so the wire
        # view can show the structured payload rather than the short text-part label.
        task_t = next(
            (
                value
                for key, value in metadata.items()
                if isinstance(key, str) and "Task-T" in key and isinstance(value, str)
            ),
            None,
        )
        negotiation_t = next(
            (
                value
                for key, value in metadata.items()
                if isinstance(key, str) and "Negotiation-T" in key and isinstance(value, str)
            ),
            None,
        )
        detail: dict[str, object] = {
            "performative": performative,
            "summary": label_text,
            "metadataKeys": metadata_keys,
            "templateUri": template_uri if isinstance(template_uri, str) else None,
            "extensionUri": extension_uri,
            "negotiationUri": negotiation_uri,
            "messageId": getattr(request.message, "message_id", None),
            "taskId": getattr(request.message, "task_id", None),
        }
        if task_t is not None:
            detail["taskT"] = task_t[:4000]
        if negotiation_t is not None:
            detail["negotiationT"] = negotiation_t[:4000]
        sample_phase = metadata.get(SAMPLE_PHASE_METADATA_KEY)
        if isinstance(sample_phase, str) and sample_phase:
            detail["samplePhase"] = sample_phase
        emit_trace(
            "message_sent",
            phase=phase,
            title=title,
            direction="client->server",
            round_number=round_number,
            context_id=context_id or (request.message.context_id or None),
            detail=detail,
        )

    if run_mode not in (RUN_MODE_CREATE, RUN_MODE_MODIFY):
        raise ClientFlowError(f"Unsupported run mode: {run_mode}")
    modify_only = run_mode == RUN_MODE_MODIFY
    if modify_only and (not target_task_id or not target_context_id):
        raise ClientFlowError("A Modify-only run needs both the target Task id and its context id.")
    if not modify_only and (target_task_id or target_context_id):
        raise ClientFlowError("A target Task can only be used by a Modify-only run.")
    resolved_label = TASK_MODIFY_LABEL if modify_only else label
    initial_phase = PHASE_TASK_MODIFY if modify_only else PHASE_TASK_PROMPT
    validation_schema: Mapping[str, object] = TASK_PARAM_SCHEMA
    # OSP `AL`: the moment OSS calls the A2A-T SDK. The span is written after the Task-T exists,
    # so the SDK call's own start/end is carried on it (the LLM spans inside are separate).
    sdk_started = time.perf_counter()
    sdk_started_at = now_iso()
    try:
        with trace_stage("task_t_modify_extraction" if modify_only else "task_t_slot_extraction"):
            # Every run, Create and Modify alike, extracts its Task-T from the operator's text.
            # There is no structured shortcut: the text is the only source of business values.
            # The SDK call is synchronous and can take minutes on a slow endpoint; run it off the
            # event loop so the page keeps receiving activity frames while it waits.
            prompt_content = await asyncio.to_thread(
                prompt_client.generate_task_prompt_from_text,
                _request_with_context(user_request, conversation_context),
                task_template_uri,
            )
    except PromptGenerationError as error:
        validation = _generation_task_validation(error)
        if validation is None:
            raise
        # No Task-T exists, so there is nothing to send and no protocol negotiation to start.
        # Reuse the pre-send validation surface: the operator edits the request and retries.
        emit_validation(validation, "", task_template_uri, initial_phase)
        blocked = ClientFlowResult()
        blocked.final_state = FINAL_STATE_INPUT_INVALID
        _fill_result(blocked, target_context_id if modify_only else None)
        return blocked
    prompt_text = _require_prompt_text(prompt_content, "the initial Task-T prompt")
    # A concurrent intent read (see the runtime) is awaited here, after generation, so the two
    # model turns overlap instead of running one after the other. Every later use of the fields
    # sees a plain mapping.
    intent_fields = await _resolve_intent_fields(intent_fields)
    emit_trace(
        "task_prompt_rendered",
        phase=initial_phase,
        title="Modify Task-T prompt rendered" if modify_only else "Task-T prompt rendered",
        started_at=sdk_started_at,
        ended_at=now_iso(),
        duration_ms=elapsed_ms(sdk_started),
        detail={
            "promptText": prompt_text,
            "templateUri": prompt_content.template_uri or task_template_uri,
            "length": len(prompt_text),
            **({"samplePhase": SAMPLE_PHASE_MODIFY} if modify_only else {}),
        },
    )
    initial_filled: dict[str, object] = _merge_initial_filled(None, intent_fields)
    validation: TaskValidation | None = None
    required_unknown = False
    if validate_before_send or enforce_required_fields:
        validation = await _validate_task_prompt(
            prompt_client,
            prompt_text,
            validation_schema,
            task_template_uri,
            language=language,
            enforce_required_fields=enforce_required_fields,
            site_level_rate_optional=site_level_rate_optional,
        )
        initial_filled = _merge_initial_filled(validation, intent_fields)
        if enforce_required_fields and validation.verdict == "unavailable":
            # The semantic pipeline is unavailable (for example a contract-breaking model
            # response). Keep the real validator reason: reporting "field is empty" would blame
            # the operator for a validator failure. The missing caller-required values stay in the
            # error list as secondary context, and the gate still blocks.
            missing = _missing_required_fields(
                initial_filled,
                validation_schema,
                language,
                site_level_rate_optional=site_level_rate_optional,
            )
            if missing:
                required_unknown = True
                validation = TaskValidation(
                    "blocked",
                    None,
                    [*validation.errors, *missing],
                    validation.reason or "content.param_missing",
                    validation.duration_ms,
                    initial_filled,
                    validation.attempts,
                )
        if enforce_required_fields and validation.filled:
            mismatch = _region_consistency_errors(validation.filled, intent_fields, language)
            if mismatch:
                validation = TaskValidation(
                    "blocked",
                    None,
                    mismatch,
                    "content.value_mismatch",
                    validation.duration_ms,
                    initial_filled,
                    validation.attempts,
                )
        if validate_before_send:
            emit_validation(validation, prompt_text, task_template_uri, initial_phase)
            if validation.verdict == "blocked":
                # Nothing is sent and no negotiation starts: the operator repairs the request first.
                blocked = ClientFlowResult()
                blocked.final_state = FINAL_STATE_INPUT_INVALID
                _fill_result(blocked, None)
                return blocked
        elif required_unknown or validation.reason in {"content.param_missing", "content.value_mismatch"}:
            # Only the business gate runs when semantic validation is off; negotiable content
            # still goes to the server.
            emit_validation(validation, prompt_text, task_template_uri, initial_phase)
            blocked = ClientFlowResult()
            blocked.final_state = FINAL_STATE_INPUT_INVALID
            _fill_result(blocked, None)
            return blocked
    if on_intent_ready is not None and not await on_intent_ready(prompt_text):
        # The engineer did not confirm the structured intent, so the Task-T never leaves the OSS
        # side. No message is sent and no task is created.
        blocked = ClientFlowResult()
        blocked.final_state = FINAL_STATE_INTENT_NOT_CONFIRMED
        _fill_result(blocked, None)
        return blocked

    initial_metadata: dict[str, object] = {
        prompt_content.extension_uri or TASK_T_EXTENSION_URI: prompt_text,
        TEMPLATE_URI_METADATA_KEY: prompt_content.template_uri or task_template_uri,
    }
    # ``samplePhase`` is sample-only bookkeeping and is not part of the A2A-T interface, so it
    # never travels on the wire. The server still derives Modify from the Task-T operation type
    # and keeps reading the marker for older external clients.
    initial_request = _build_message_request(
        metadata=initial_metadata,
        label=resolved_label,
        context_id=target_context_id if modify_only else None,
        task_id=target_task_id if modify_only else None,
    )
    # Declare Negotiation-T alongside Task-T: the vendor NMS only returns a structured negotiation
    # (Negotiation-T metadata plus negotiationContext) when the negotiation extension is declared.
    # Declaring Task-T alone makes it answer with a bare Message whose empty metadata the client
    # cannot distinguish from a negotiation-ended message.
    initial_context = _call_context((TASK_T_EXTENSION_URI, NEGOTIATION_T_EXTENSION_URI), call_timeout_seconds)

    result = ClientFlowResult()
    context_id: str | None = None
    # The Task every negotiation reply continues. A Modify run starts from the selected Task; a
    # create run learns it from the server's own events. A reply without it makes the server open
    # (or look for) a new Task instead of continuing this one.
    task_id: str | None = target_task_id if modify_only else None
    exchanges = 0
    # One grounded decision turn per negotiation round; a second ungrounded accept is terminal.
    reasked_rounds: set[tuple[str, int]] = set()
    # Counts the inbound proposals of this run; a vendor frame without a negotiationContext gets
    # this number as its round.
    negotiation_rounds = 0
    # Only used when a vendor frame names no context either; the reply still needs an id.
    inferred_negotiation_id = str(uuid.uuid4())
    emit_message_sent(
        initial_request,
        phase=PHASE_TASK_MODIFY if modify_only else PHASE_TASK_VALIDATION,
        title="Modify Task-T sent" if modify_only else "Task-T sent",
        performative="TASK_T",
        round_number=None,
        context_id=target_context_id if modify_only else None,
    )
    _LOGGER.info("client flow started: transport=%s", type(transport).__name__)
    batch = await send_with_wait_activity(
        initial_request,
        initial_context,
        stage="task_t_modify_send" if modify_only else "task_t_send",
    )
    exchanges += 1
    collect(batch)
    context_id = context_id or _context_id_of(batch)
    task_id = task_id or _task_id_of(batch)

    def finish(state: str) -> ClientFlowResult:
        """Record the terminal state, fill the result and log the outcome."""
        result.final_state = state
        _fill_result(result, context_id)
        _LOGGER.info(
            "client flow finished: state=%s task_id=%s decisions=%d",
            result.final_state,
            result.task_id or "-",
            len(result.decisions),
        )
        return result

    while True:
        inbound = _find_negotiation_message(batch)
        if inbound is None:
            # Only the last phase can terminate the flow: an earlier round's proposal must not be
            # mistaken for the end message (webhook phases end at the report artifact).
            return finish(_final_state(batch))

        event, metadata = inbound
        context_id = context_id or _string_field(event, "context_id")
        task_id = _string_field(event, "task_id") or task_id
        negotiation_rounds += 1
        session = parse_negotiation_context(metadata)
        if session is None:
            session = _inferred_negotiation_session(
                metadata,
                round_number=negotiation_rounds,
                fallback_id=context_id or inferred_negotiation_id,
            )
        if session is None:
            raise ClientFlowError(_ERROR_NO_SESSION)
        context_id = context_id or session.id

        if session.performative is NegotiationPerformative.ABORT:
            _LOGGER.info("negotiation aborted by the server at round %s", session.round)
            return finish(FINAL_STATE_NEGOTIATION_ABORTED)
        if session.performative is not NegotiationPerformative.PROPOSE:
            raise ClientFlowError(_ERROR_NO_PROPOSE)

        template_uri, kind, inferred_kind = _resolve_propose_template(
            metadata, infer_missing_template_uri=infer_missing_negotiation_kind
        )
        propose = _propose_view(event, metadata, session, kind, template_uri, context_id)
        emit_trace(
            "decision_requested",
            phase=PHASE_NEGOTIATION,
            title=f"Decision requested · {kind.value} R{session.round}",
            round_number=session.round,
            context_id=context_id,
            detail={
                "negotiationKind": kind.value,
                "round": session.round,
                "maxRounds": session.max_rounds,
                "templateUri": template_uri,
                "content": propose.content,
                # Only the inferred case is flagged; a conformant propose keeps its frame unchanged.
                **({"templateUriInferred": True} if inferred_kind else {}),
            },
        )
        _LOGGER.info(
            "proposal received: kind=%s round=%s/%s",
            kind.value,
            session.round,
            session.max_rounds,
        )
        if on_decision_requested is not None:
            on_decision_requested(propose, session)
        with log_context(
            negotiation_id=session.id or context_id,
            round=session.round,
            context_id=context_id,
        ):
            try:
                decision = await provider.respond(propose, session)
            except DecisionTimeoutError as exc:
                # The provider loop normally only notifies the page. Reaching here means the wait
                # sink was never installed; refuse to send an abort the operator never chose.
                raise ClientFlowError(_ERROR_DECISION_WAIT) from exc
            policy_outcome = None
            if _requires_auto_verification(provider):
                decision, policy_outcome = apply_automated_decision(
                    decision,
                    kind=kind.value,
                    propose=propose,
                    baseline=initial_filled,
                    intent_fields=intent_fields,
                    policy=policy,
                )
                if policy_outcome.notes:
                    emit_trace(
                        "decision_policy_applied",
                        phase=PHASE_NEGOTIATION,
                        title="Client policy applied to the automatic decision",
                        round_number=session.round,
                        context_id=context_id,
                        detail={
                            "action": decision.action,
                            "notes": list(policy_outcome.notes),
                            "forcedAccept": policy_outcome.forced_accept,
                            "droppedFields": sorted(policy_outcome.dropped_fields),
                        },
                    )
            if (
                policy_outcome is not None
                and decision.action == "accept"
                and kind is NegotiationKind.INFORMATION
                and policy_outcome.dropped_fields
            ):
                # The server asked for a value and the model supplied one it could not source.
                # Dropping it silently would send an accept that does not answer the question.
                ungrounded = [
                    UngroundedValue(
                        field,
                        "",
                        "the model supplied an entity value neither the server nor the operator states",
                    )
                    for field in sorted(policy_outcome.dropped_fields)
                ]
                decision = None
            else:
                decision, ungrounded = await _ensure_grounded_decision(
                    provider,
                    decision,
                    propose,
                    session,
                    kind=kind,
                    baseline=initial_filled,
                    intent_fields=intent_fields,
                    carries_task_t=carries_task_t,
                    reasked=reasked_rounds,
                    trace_sink=trace_sink,
                )
        if decision is None:
            fields = ", ".join(f"{item.field}={item.value or '<missing>'}" for item in ungrounded)
            reason = f"The automatic decision for round {session.round} has no grounded value: {fields}."
            emit_trace(
                "decision_ungrounded",
                phase=PHASE_NEGOTIATION,
                title="Decision ungrounded",
                round_number=session.round,
                context_id=context_id,
                detail={
                    "fields": [item.field for item in ungrounded],
                    "values": {item.field: item.value for item in ungrounded},
                    "reason": reason,
                },
            )
            # An automatic decision that cannot be grounded is a refusal, not a request to end
            # the negotiation on the operator's behalf. In automatic mode there is no human
            # channel to surface it, so the client rejects and names the reason.
            reject_decision = NegotiationDecision(action="reject", rationale=reason)
            request, call_context = await _build_terminal_request(
                prompt_client=prompt_client,
                decision=reject_decision,
                kind=kind,
                session=session,
                context_id=context_id,
                label=resolved_label,
                propose_content=propose.content,
                performative=NegotiationPerformative.REJECT,
                call_timeout_seconds=call_timeout_seconds,
                task_id=task_id,
            )
            if on_decision_made is not None:
                on_decision_made(reject_decision, propose, session)
            result.decisions.append(reject_decision)
            emit_message_sent(
                request,
                phase=PHASE_RESOLUTION,
                title="Reject sent",
                performative=NegotiationPerformative.REJECT.value,
                round_number=session.round,
                context_id=context_id,
            )
            batch = await send_with_wait_activity(
                request, call_context, stage="negotiation_send"
            )
            exchanges += 1
            collect(batch)
            task_id = task_id or _task_id_of(batch)
            result.error = reason
            return finish(FINAL_STATE_DECISION_UNGROUNDED)
        _LOGGER.info("decision made: action=%s round=%s", decision.action, session.round)
        # Exactly one timeline row per decision: a consumer callback (Web UI/transcript) records
        # the decision itself, so the synthetic span would be an adjacent duplicate. Without a
        # callback (CLI), the flow emits the span so the decision still reaches the trace.
        if on_decision_made is not None:
            on_decision_made(decision, propose, session)
        else:
            emit_trace(
                "decision_made",
                phase=infer_decision_phase(decision.action),
                title=f"Decision made · {decision.action}",
                round_number=session.round,
                context_id=context_id,
                detail={
                    "action": decision.action,
                    "rationale": decision.rationale,
                    "filledParams": dict(decision.filled_params) if decision.filled_params else None,
                    "counterProposal": decision.counter_proposal,
                    "counterParams": dict(decision.counter_params) if decision.counter_params else None,
                    "confirmedIntent": decision.confirmed_intent,
                    "feasibilitySummary": decision.feasibility_summary,
                },
            )
        result.decisions.append(decision)

        if exchanges >= max_exchanges:
            raise ClientFlowError(f"The client flow exceeded {max_exchanges} exchanges.")

        if decision.action == "accept":
            dropped_values: list[str] = []
            request, call_context = await _build_accept_request(
                prompt_client=prompt_client,
                decision=decision,
                kind=kind,
                session=session,
                propose=propose,
                context_id=context_id,
                label=resolved_label,
                task_template_uri=task_template_uri,
                baseline=initial_filled,
                intent_fields=intent_fields,
                call_timeout_seconds=call_timeout_seconds,
                single_extension_per_message=single_extension_per_message,
                task_id=task_id,
                dropped=dropped_values,
            )
            if dropped_values:
                # The accept carried values the flow could not send; surface them instead of
                # letting the difference between the decision and the wire go unrecorded.
                emit_trace(
                    "decision_value_dropped",
                    phase=PHASE_RESOLUTION,
                    title="Accepted value dropped before sending",
                    round_number=session.round,
                    context_id=context_id,
                    detail={"fields": list(dropped_values), "action": decision.action},
                )
            send_phase = PHASE_RESOLUTION
            send_performative = NegotiationPerformative.ACCEPT.value
            send_title = "Accept sent"
        elif decision.action == "counter":
            request, call_context = await _build_counter_request(
                prompt_client=prompt_client,
                decision=decision,
                kind=kind,
                session=session,
                context_id=context_id,
                label=resolved_label,
                call_timeout_seconds=call_timeout_seconds,
                include_sample_proposal=carries_task_t,
                task_id=task_id,
            )
            send_phase = PHASE_NEGOTIATION
            send_performative = NegotiationPerformative.PROPOSE.value
            send_title = f"Counter proposal sent · R{session.round}"
        elif decision.action in ("reject", "abort"):
            # Only an explicit operator/agent decision may end the negotiation. Any other action
            # is a wiring error: sending ABORT as the fallback would terminate the remote Task on
            # behalf of a human who never chose it.
            performative = (
                NegotiationPerformative.REJECT
                if decision.action == "reject"
                else NegotiationPerformative.ABORT
            )
            request, call_context = await _build_terminal_request(
                prompt_client=prompt_client,
                decision=decision,
                kind=kind,
                session=session,
                context_id=context_id,
                label=resolved_label,
                propose_content=propose.content,
                performative=performative,
                call_timeout_seconds=call_timeout_seconds,
                task_id=task_id,
            )
            send_phase = PHASE_RESOLUTION
            send_performative = performative.value
            send_title = f"{performative.value.capitalize()} sent"
        else:
            raise ClientFlowError(f"Unsupported decision action: {decision.action}")

        emit_message_sent(
            request,
            phase=send_phase,
            title=send_title,
            performative=send_performative,
            round_number=session.round,
            context_id=context_id,
        )
        batch = await send_with_wait_activity(
            request, call_context, stage="negotiation_send"
        )
        exchanges += 1
        collect(batch)
        task_id = task_id or _task_id_of(batch)
        if decision.action in ("reject", "abort"):
            return finish(FINAL_STATE_NEGOTIATION_ENDED)
        if decision.action == "accept":
            # The vendor may open a new negotiation id on the same context id right after the
            # accept. That new PROPOSE is part of the accept response stream and must be decided;
            # only an accept batch without another proposal finishes the flow.
            if _find_negotiation_message(batch) is None:
                return finish(_final_state(batch))


# ----------------------------------------------------------------------
# Request construction
# ----------------------------------------------------------------------


def _build_message_request(
    *,
    metadata: Mapping[str, object],
    label: str,
    context_id: str | None = None,
    task_id: str | None = None,
) -> SendMessageRequest:
    """Build one user-role A2A message request with an optional metadata map.

    ``task_id`` turns the request into a real A2A continuation of the named Task; without it the
    server creates a new Task.
    """
    request = SendMessageRequest()
    request.message.message_id = str(uuid.uuid4())
    request.message.role = Role.ROLE_USER
    request.message.parts.add().text = label
    if context_id:
        request.message.context_id = context_id
    if task_id:
        request.message.task_id = task_id
    for key, value in metadata.items():
        if value is not None:
            # The bundled protobuf stubs do not model the dynamic Struct mapping.
            request.message.metadata[key] = value  # ty: ignore[invalid-assignment]
    return request


def _call_context(extension_uris: tuple[str, ...], timeout: float) -> ClientCallContext:
    """Build the call context carrying the ``A2A-Extensions`` header and the request budget.

    The SDK applies ``timeout`` to the HTTP request of every send (``get_http_args`` builds an
    ``httpx.Timeout`` from it), so the configured budget wins over the httpx client default.
    """
    return ClientCallContext(
        timeout=timeout,
        service_parameters={"A2A-Extensions": ",".join(sorted(set(extension_uris)))},
    )


def _negotiation_request(
    *,
    prompt_client: PromptClient,
    content: MetadataContent,
    label: str,
    context_id: str,
    session: NegotiationContext,
    call_timeout_seconds: float,
    extra_metadata: Mapping[str, object] | None = None,
    task_id: str | None = None,
) -> tuple[SendMessageRequest, ClientCallContext]:
    """Build one negotiation response request from generated protocol content."""
    metadata = {key: value for key, value in content.build_metadata_content().items() if value is not None}
    # The negotiation id is the server's own handle for the negotiation and differs from the
    # context id. The vendor NMS rejects a reply that carries the context id in its place with
    # ``negotiation id missing or mismatched``; the context id still travels in ``contextId``.
    # A continuing negotiation may also start a new negotiation id on the same context id.
    metadata[NEGOTIATION_CONTEXT_METADATA_KEY] = negotiation_context_metadata(
        NegotiationContext(
            id=session.id or context_id,
            round=session.round,
            max_rounds=session.max_rounds,
            performative=session.performative,
        )
    )
    if extra_metadata:
        metadata.update(extra_metadata)
    request = _build_message_request(
        metadata=metadata,
        label=label,
        context_id=context_id,
        task_id=task_id,
    )
    return request, _call_context((TASK_T_EXTENSION_URI, NEGOTIATION_T_EXTENSION_URI), call_timeout_seconds)


def _accept_filled_values(
    kind: NegotiationKind,
    decision: NegotiationDecision,
    baseline: Mapping[str, object] | None,
    intent_fields: Mapping[str, object] | None,
    *,
    dropped: list[str] | None = None,
) -> dict[str, object]:
    """Merge the operator-derived baseline and the decision into the agreed Task-T parameters.

    The baseline is the initial Task-T extraction; the intent layer fills what the extraction did
    not provide. The decision may add or change a value, but the grounding verifier decides
    whether that change is allowed. There is no configured fallback and no default operation type:
    an absent value stays absent and the verifier blocks the ACCEPT.

    A target is normalized to the canonical ``{HH:MM~HH:MM,NMbps}`` form rather than validated
    against one notation. The old tilde-only filter dropped the hyphen/colon form the English slot
    overlay and the vendor Task-T use, and did so without a trace; a value that still cannot be
    parsed is now recorded in ``dropped`` so the caller can surface it.
    """
    del kind
    filled: dict[str, object] = {}
    for source in (intent_fields or {}, baseline or {}):
        for key, value in source.items():
            if value is not None and not (isinstance(value, str) and not value.strip()):
                filled[key] = value
    allowed = TASK_PARAM_SCHEMA.get("properties")
    if isinstance(allowed, Mapping):
        for key, value in (decision.filled_params or {}).items():
            if key not in allowed or value is None:
                _record_dropped(dropped, key, "not a Task-T parameter or empty")
                continue
            if not (isinstance(value, (str, int, float)) and str(value).strip()):
                _record_dropped(dropped, key, "empty value")
                continue
            if key == RATE_TARGET_FIELD:
                normalized = normalize_text(value)
                if normalized is None:
                    _record_dropped(dropped, key, "not a time-segmented target")
                    continue
                filled[key] = normalized
                continue
            filled[key] = value
    target = filled.get(RATE_TARGET_FIELD)
    if isinstance(target, str):
        normalized = normalize_text(target)
        if normalized is None:
            _record_dropped(dropped, RATE_TARGET_FIELD, "baseline target is not time-segmented")
            filled.pop(RATE_TARGET_FIELD, None)
        else:
            filled[RATE_TARGET_FIELD] = normalized
    return filled


def _record_dropped(dropped: list[str] | None, field: str, reason: str) -> None:
    """Record one silently-dropped accept value so the caller can trace it."""
    _LOGGER.warning("accept value dropped: %s (%s)", field, reason)
    if dropped is not None:
        dropped.append(f"{field}: {reason}")


def _requires_auto_verification(provider: NegotiationDecisionProvider) -> bool:
    """Return whether this provider's ACCEPT is an automatic decision that must be verified.

    A scripted provider is a recorded operator answer. An offline ADK provider is the
    deterministic fake model used by the demo/tests. Manual and confirm providers return the
    operator's submitted decision. Only a live automatic ADK decision is verified.
    """
    if isinstance(provider, ScriptedDecisionProvider):
        return False
    if isinstance(provider, AdkDecisionProvider):
        return not provider.is_offline
    return getattr(provider, "requires_grounding", False) is True


async def _ensure_grounded_decision(
    provider: NegotiationDecisionProvider,
    decision: NegotiationDecision,
    propose: NegotiationProposeView,
    session: NegotiationContext,
    *,
    kind: NegotiationKind,
    baseline: Mapping[str, object],
    intent_fields: Mapping[str, object] | None,
    carries_task_t: bool,
    reasked: set[tuple[str, int]],
    trace_sink: Callable[[dict[str, object]], None] | None,
) -> tuple[NegotiationDecision | None, list[UngroundedValue]]:
    """Return a grounded decision, or ``None`` and the values that could not be grounded.

    Only values that actually travel are judged. An information accept carries the values in its
    text; a target/feasibility accept on the real NMS carries no Task-T at all and only agrees with
    the server's proposal, so there is nothing to ground. The local demo server does carry the
    refilled Task-T, and then the changed values are checked.

    One corrective decision turn per negotiation round is allowed. The corrective turn shows the
    model the server proposal and its options again and states the grounding rule; if the model
    still returns an ungrounded ACCEPT, the flow refuses to send it.
    """
    if decision.action != "accept" or not _requires_auto_verification(provider):
        return decision, []
    if kind is not NegotiationKind.INFORMATION and not carries_task_t:
        return decision, []
    if kind is NegotiationKind.INFORMATION and not decision.filled_params:
        return None, [
            UngroundedValue("information", "", "accept does not answer the requested items")
        ]
    filled = _accept_filled_values(kind, decision, baseline, intent_fields)
    ungrounded = _ungrounded_values(
        filled,
        baseline=baseline,
        intent_fields=intent_fields,
        propose=propose,
    )
    if not ungrounded:
        return decision, []
    key = (propose.negotiation_id, session.round)
    if key in reasked:
        return None, ungrounded
    reasked.add(key)
    if trace_sink is not None:
        recorder = TraceRecorder(current_trace().run_id or "", id_prefix="grounding")
        span = recorder.emit(
            kind="decision_reask",
            phase=PHASE_NEGOTIATION,
            title="Decision ungrounded; asking the model again",
            actor="client",
            side="client",
            direction="internal",
            round=session.round,
            context_id=propose.context_id,
            detail={
                "fields": [item.field for item in ungrounded],
                "values": {item.field: item.value for item in ungrounded},
            },
        )
        trace_sink(span.to_dict())
    corrected = propose.model_copy(update={"content": grounding_reask_text(propose, ungrounded)})
    corrected_decision = await provider.respond(corrected, session)
    if corrected_decision.action != "accept":
        return corrected_decision, []
    if kind is NegotiationKind.INFORMATION and not corrected_decision.filled_params:
        return None, [
            UngroundedValue("information", "", "accept does not answer the requested items")
        ]
    filled = _accept_filled_values(kind, corrected_decision, baseline, intent_fields)
    still_ungrounded = _ungrounded_values(
        filled,
        baseline=baseline,
        intent_fields=intent_fields,
        # Verify against the real server proposal, not our corrective instruction: the reask text
        # repeats the ungrounded value and must never become its own grounding source.
        propose=propose,
    )
    if still_ungrounded:
        return None, still_ungrounded
    return corrected_decision, []


def _ungrounded_values(
    filled: Mapping[str, object],
    *,
    baseline: Mapping[str, object],
    intent_fields: Mapping[str, object] | None,
    propose: NegotiationProposeView,
) -> list[UngroundedValue]:
    """Run the grounding verifier with the flow's value shapes."""
    return find_ungrounded(
        filled,
        baseline=baseline,
        intent=intent_fields or {},
        propose=propose,
    )


def _ensure_agreed_target(task_text: str, filled: Mapping[str, object]) -> str:
    """Keep the agreed throughput target in a refilled Task-T prompt that dropped it.

    The refill is a model call even though the accepted value is already known. A live model can
    render the template without the negotiated target (typically by leaving the Task Context
    section empty), and the server then rejects the accept for the very value the client supplied.
    Every time segment of the agreed target must appear in the prompt; when one is missing, the
    target is written into the Task Context section per the template layout.

    Args:
        task_text: prompt rendered from the accepted Task-T parameters.
        filled: merged Task-T parameters the accept carries.

    Returns:
        The prompt unchanged when it already carries every segment, otherwise the repaired prompt.
    """
    target = filled.get("guaranteedThroughputTarget")
    if not isinstance(target, str) or not target.strip():
        return task_text
    segments = find_segments(target)
    if not segments:
        return task_text
    rendered = {(segment.start, segment.end, segment.rate_mbps) for segment in find_segments(task_text)}
    if all((segment.start, segment.end, segment.rate_mbps) in rendered for segment in segments):
        return task_text
    _LOGGER.warning("the refilled Task-T prompt dropped the agreed throughput target; writing it into the task context")
    heading = _CONTEXT_HEADING_PATTERN.search(task_text)
    if heading is None:
        return f"{task_text.rstrip()}\n\n{target}\n"
    start = heading.end()
    following = re.search(r"^##\s", task_text[start:], re.MULTILINE)
    end = start + following.start() if following is not None else len(task_text)
    existing = task_text[start:end].strip()
    body = f"\n\n{existing}\n\n{target}\n\n" if existing else f"\n\n{target}\n\n"
    return f"{task_text[:start]}{body}{task_text[end:]}"


async def _build_accept_request(
    *,
    prompt_client: PromptClient,
    decision: NegotiationDecision,
    kind: NegotiationKind,
    session: NegotiationContext,
    propose: NegotiationProposeView,
    context_id: str,
    label: str,
    task_template_uri: str,
    baseline: Mapping[str, object] | None,
    intent_fields: Mapping[str, object] | None,
    call_timeout_seconds: float,
    single_extension_per_message: bool,
    task_id: str | None = None,
    dropped: list[str] | None = None,
) -> tuple[SendMessageRequest, ClientCallContext]:
    """Refill the Task-T prompt and generate the Negotiation-T accept of the proposal type.

    The accept content is built from typed data and rendered deterministically (the SDK
    ``generate_accept_from_data`` path). The old from-text path ran the client's own accept text
    through an LLM extraction; a degenerate text made the extraction return an empty required
    field, which the SDK classifies as a non-retryable failure and which aborted live runs. The
    from-text path stays only as a fallback for content this flow cannot type.
    """
    filled = _accept_filled_values(kind, decision, baseline, intent_fields, dropped=dropped)
    agreed_target = _prefer_the_agreed_target(kind, filled, decision, propose, baseline)
    task_content = None
    task_text = ""
    if not single_extension_per_message:
        # Only the local demo server reads a refilled Task-T from the accept. The real NMS rejects
        # a second ``-T`` key, so it never receives one; generating it there was a pure LLM call
        # whose result was discarded (and whose failure could abort a valid accept).
        with trace_stage("task_t_slot_extraction_refill"):
            task_content = await asyncio.to_thread(
                prompt_client.generate_task_prompt_from_data_with_schema,
                filled,
                TASK_PARAM_SCHEMA,
                task_template_uri,
            )
        task_text = _ensure_agreed_target(
            _require_prompt_text(task_content, "the refilled Task-T prompt"), filled
        )

    accept_context = session.with_performative(NegotiationPerformative.ACCEPT)
    template_uri = _ACCEPT_REJECT_TEMPLATE_URI_BY_KIND[kind.value]
    typed = _typed_accept_content(kind, filled, decision, baseline, agreed_target)
    if typed is not None:
        content = await asyncio.to_thread(
            prompt_client.generate_negotiation_accept_prompt_from_data,
            NegotiationEndingData(accept_context, typed),
            template_uri,
        )
    else:
        accept_text = _accept_text(kind, filled, decision, baseline=baseline)
        with trace_stage("negotiation_accept_extraction"):
            content = await asyncio.to_thread(
                prompt_client.generate_negotiation_accept_prompt_from_text,
                accept_text,
                accept_context,
                template_uri,
            )
    # The real NMS rejects a message whose metadata holds more than one ``-T`` extension key, so
    # the NMS path sends the accept with the negotiation key only; the local demo server reads the
    # refilled Task-T prompt from the accept message and keeps receiving it.
    metadata = None if task_content is None else {
        task_content.extension_uri or TASK_T_EXTENSION_URI: task_text
    }
    return _negotiation_request(
        prompt_client=prompt_client,
        content=content,
        label=label,
        context_id=context_id,
        session=accept_context,
        call_timeout_seconds=call_timeout_seconds,
        extra_metadata=metadata,
        task_id=task_id,
    )


async def _build_counter_request(
    *,
    prompt_client: PromptClient,
    decision: NegotiationDecision,
    kind: NegotiationKind,
    session: NegotiationContext,
    context_id: str,
    label: str,
    call_timeout_seconds: float,
    include_sample_proposal: bool = True,
    task_id: str | None = None,
) -> tuple[SendMessageRequest, ClientCallContext]:
    """Generate a counter proposal that keeps the inbound round (same-round ``Propose``).

    ``include_sample_proposal`` is false on the real NMS path: ``counter_params`` is a sample-only
    plan over the demo dataset, and sending a fabricated ``{cellId, window, targetRate}`` to a
    vendor that has no such cell would be exactly the client-side network fact this flow bans.
    """
    text = decision.counter_proposal or decision.rationale
    if not text.strip():
        raise ClientFlowError("A counter decision carries neither a counter proposal nor a rationale.")
    propose_context = session.with_performative(NegotiationPerformative.PROPOSE)
    with trace_stage("negotiation_propose_extraction"):
        content = await asyncio.to_thread(
            prompt_client.generate_negotiation_propose_prompt_from_text,
            text,
            propose_context,
            _PROPOSE_TEMPLATE_URI_BY_KIND[kind.value],
        )
    extra: dict[str, object] = {}
    if include_sample_proposal and decision.counter_params:
        extra[SAMPLE_PROPOSAL_METADATA_KEY] = dict(decision.counter_params)
    return _negotiation_request(
        prompt_client=prompt_client,
        content=content,
        label=label,
        context_id=context_id,
        session=propose_context,
        call_timeout_seconds=call_timeout_seconds,
        extra_metadata=extra or None,
        task_id=task_id,
    )


async def _build_terminal_request(
    *,
    prompt_client: PromptClient,
    decision: NegotiationDecision,
    kind: NegotiationKind,
    session: NegotiationContext,
    context_id: str,
    label: str,
    propose_content: str,
    performative: NegotiationPerformative,
    call_timeout_seconds: float,
    task_id: str | None = None,
) -> tuple[SendMessageRequest, ClientCallContext]:
    """Generate a terminal reject or abort message (no new Task is created)."""
    terminal_context = session.with_performative(performative)
    if performative is NegotiationPerformative.REJECT:
        text = _reject_text(kind, propose_content, decision)
        with trace_stage("negotiation_reject_extraction"):
            content = await asyncio.to_thread(
                prompt_client.generate_negotiation_reject_prompt_from_text,
                text,
                terminal_context,
                _ACCEPT_REJECT_TEMPLATE_URI_BY_KIND[kind.value],
            )
    else:
        text = decision.rationale or "The client aborted the negotiation."
        with trace_stage("negotiation_abort_extraction"):
            content = await asyncio.to_thread(
                prompt_client.generate_negotiation_abort_prompt_from_text,
                text,
                terminal_context,
                NEGOTIATION_ABORT_URI,
            )
    return _negotiation_request(
        prompt_client=prompt_client,
        content=content,
        label=label,
        context_id=context_id,
        session=terminal_context,
        call_timeout_seconds=call_timeout_seconds,
        task_id=task_id,
    )


# ----------------------------------------------------------------------
# Event helpers
# ----------------------------------------------------------------------


def _request_with_context(user_request: str, conversation_context: str | None) -> str:
    """Prepend a delimited recent-turn block to the natural-language request."""
    if not conversation_context or not conversation_context.strip():
        return user_request
    return (
        "Conversation context (earlier turns of this client conversation):\n"
        f"{conversation_context.strip()}\n\n"
        "Current request:\n"
        f"{user_request}"
    )


def _require_prompt_text(content: MetadataContent, what: str) -> str:
    """Return the rendered prompt text of generated content, raising when it is missing."""
    text = content.prompt_text
    if not isinstance(text, str) or not text.strip():
        raise ClientFlowError(f"The SDK did not generate {what}.")
    return text


def _string_field(event: Mapping[str, object], key: str) -> str | None:
    """Return one string field of a normalized event, ``None`` when absent or not a string."""
    value = event.get(key)
    return value if isinstance(value, str) and value else None


def _task_id_of(events: list[dict[str, object]]) -> str | None:
    """Return the last task id carried by a batch of normalized events."""
    for event in reversed(events):
        task_id = _string_field(event, "task_id")
        if task_id:
            return task_id
    return None


def _task_id_of(events: list[dict[str, object]]) -> str | None:
    """Return the first Task id carried by a batch of normalized events."""
    for event in events:
        task_id = _string_field(event, "task_id")
        if task_id:
            return task_id
    return None


def _context_id_of(events: list[dict[str, object]]) -> str | None:
    """Return the first context id carried by a batch of normalized events."""
    for event in events:
        context_id = _string_field(event, "context_id")
        if context_id:
            return context_id
        metadata = event.get("metadata")
        if isinstance(metadata, Mapping):
            session = parse_negotiation_context(metadata)
            if session is not None:
                return session.id
    return None


def _inferred_negotiation_session(
    metadata: Mapping[str, object],
    *,
    round_number: int,
    fallback_id: str,
) -> NegotiationContext | None:
    """Build a session for a vendor frame that carries no ``negotiationContext``.

    The vendor NMS sometimes sends only the Negotiation-T body (short key, JSON envelope) without
    the context object. The typed performative inside the body is used when present; the round is
    counted by the caller; the negotiation id falls back to the A2A context id, which is the only
    identity the server gave us.
    """
    value = vendor_negotiation_value(metadata)
    if value is None:
        return None
    performative = NegotiationPerformative.PROPOSE
    match = _VENDOR_PERFORMATIVE_PATTERN.search(str(value))
    if match is not None:
        parsed = NegotiationPerformative.try_parse(match.group(1))
        if parsed is not None:
            performative = parsed
    _LOGGER.warning(
        "the negotiation frame carries no negotiationContext; inferring performative=%s round=%s",
        performative.value,
        round_number,
    )
    return NegotiationContext(
        id=fallback_id,
        round=round_number,
        max_rounds=_DEFAULT_INFERRED_MAX_ROUNDS,
        performative=performative,
    )


def _find_negotiation_message(
    events: list[dict[str, object]],
) -> tuple[dict[str, object], dict[str, object]] | None:
    """Return the last inbound Negotiation-T proposal of a batch and its metadata.

    A conformant A2A-T server sends the proposal as a standalone ``Message``. The vendor NMS
    instead delivers it inside ``statusUpdate.status.message``, which normalization keeps as a
    ``status`` event; only ``INPUT_REQUIRED`` hands the turn back to the client, so any other
    state that carries the same metadata is an observation, not a proposal to answer.
    """
    input_required = TaskState.Name(TaskState.TASK_STATE_INPUT_REQUIRED)
    for event in reversed(events):
        metadata = event.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        # The vendor writes the short ``Negotiation-T`` key; normalize it so every consumer below
        # sees the canonical extension URI.
        metadata_dict = normalize_negotiation_metadata(metadata_to_dict(metadata))
        if not is_negotiation_message(metadata_dict):
            continue
        kind = event.get("kind")
        if kind == "message":
            return event, metadata_dict
        if kind == "status" and event.get("state") == input_required:
            return event, metadata_dict
    return None


#: Key of the vendor's nested typed metadata inside a Negotiation-T JSON body.
_RESPONSE_METADATA_KEY: Final[str] = "responseMetadata"

#: Typed fields a vendor Negotiation-T body spells on the wire. A body whose envelope the client
#: cannot parse still contains them verbatim, so reading them from the raw value is the last-resort
#: signal. They are only ever read, never interpreted here.
_VENDOR_NEGOTIATION_TYPE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'"negotiationType"\s*:\s*"([^"\\]+)"'
)
_VENDOR_TEMPLATE_URI_PATTERN: Final[re.Pattern[str]] = re.compile(r'"templateUri"\s*:\s*"([^"\\]+)"')
_VENDOR_TEXT_PATTERN: Final[re.Pattern[str]] = re.compile(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"')

#: The performative typed inside a vendor Negotiation-T body (``responseMetadata``).
_VENDOR_PERFORMATIVE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'"performative"\s*:\s*"([A-Za-z]+)"'
)

#: Round budget of a negotiation whose inbound frame carries no ``negotiationContext``.
_DEFAULT_INFERRED_MAX_ROUNDS: Final[int] = 5

#: Markers that identify an unparsable value as a vendor message envelope. Reading a field out of
#: the raw string is only allowed inside the ``responseMetadata`` block of such an envelope, so a
#: field quoted in prose or under an unrelated sibling key can never type the proposal.
_VENDOR_MESSAGE_PATTERN: Final[re.Pattern[str]] = re.compile(r'"message"\s*:')
_VENDOR_RESPONSE_METADATA_PATTERN: Final[re.Pattern[str]] = re.compile(r'"responseMetadata"\s*:\s*\{')

#: Characters that open a JSON document; anything else is rendered text the heading fallback reads.
_JSON_OPENERS: Final[frozenset[str]] = frozenset({"{", "["})


def _vendor_documents(content: object) -> list[Mapping[str, object]]:
    """Return the envelope objects of one vendor Negotiation-T value, whatever its wrapper.

    The vendor sends the value as a JSON string. Depending on the endpoint it decodes to a bare
    object or to a one-element list of objects, so both roots are accepted, as is a value a peer
    already delivered structured. Anything else yields an empty list.
    """
    decoded = decode_extension_value(content)
    if isinstance(decoded, Mapping):
        return [decoded]
    if isinstance(decoded, list):
        return [item for item in decoded if isinstance(item, Mapping)]
    return []


def _vendor_message_entries(document: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Return the message entries of one vendor envelope (``message`` is a list on the wire)."""
    message = document.get("message")
    if isinstance(message, list):
        return [entry for entry in message if isinstance(entry, Mapping)]
    if isinstance(message, Mapping):
        return [message]
    return []


def _vendor_response_metadatas(content: object) -> list[Mapping[str, object]]:
    """Return every ``responseMetadata`` mapping of one vendor Negotiation-T JSON body.

    The vendor NMS sends the extension value as a JSON string, not rendered markdown:
    ``{"message": [{"metadata": {"responseMetadata": {...}}}]}``, wrapped in a list on some
    endpoints. The document root and every message entry are inspected. All matches are returned so
    the caller can skip an empty block instead of letting it shadow a later typed one; a value that
    is not a readable document yields an empty list.
    """
    found: list[Mapping[str, object]] = []
    for document in _vendor_documents(content):
        candidates: list[object] = [document]
        candidates.extend(entry.get("metadata") for entry in _vendor_message_entries(document))
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            response_metadata = candidate.get(_RESPONSE_METADATA_KEY)
            if isinstance(response_metadata, Mapping):
                found.append(response_metadata)
    return found


def _vendor_typed_fields(content: object) -> tuple[str | None, str | None]:
    """Return the typed fields of a vendor message envelope the client could not parse.

    Reads the unparsed extension string, so it still works when the envelope is truncated. The
    scan is scoped to the ``responseMetadata`` block of a value that carries a ``message``
    envelope: a field quoted in prose, or one sitting under an unrelated sibling key, must never
    type a proposal. Returns ``None`` for each absent field and never interprets the value.
    """
    if not isinstance(content, str):
        return None, None
    if content.lstrip()[:1] not in _JSON_OPENERS:
        return None, None
    if _VENDOR_MESSAGE_PATTERN.search(content) is None:
        return None, None
    marker = _VENDOR_RESPONSE_METADATA_PATTERN.search(content)
    if marker is None:
        return None, None
    scope = content[marker.end() :]
    negotiation_type = _VENDOR_NEGOTIATION_TYPE_PATTERN.search(scope)
    template_uri = _VENDOR_TEMPLATE_URI_PATTERN.search(scope)
    return (
        negotiation_type.group(1) if negotiation_type is not None else None,
        template_uri.group(1) if template_uri is not None else None,
    )


def _kind_from_vendor_fields(negotiation_type: object, template_uri: object) -> NegotiationKind | None:
    """Return the kind named by a vendor ``negotiationType`` or nested ``templateUri``.

    ``negotiationType`` is the vendor's own typed field and outranks ``templateUri``, whose path
    does not follow the sample's template naming. A disagreement is logged, never silently
    resolved.
    """
    kind = negotiation_kind_for_negotiation_type(negotiation_type)
    uri_kind = negotiation_kind_for_template_uri(template_uri if isinstance(template_uri, str) else None)
    if kind is not None:
        if uri_kind is not None and uri_kind is not kind:
            _LOGGER.warning(
                "vendor propose negotiationType=%s disagrees with its templateUri kind=%s; "
                "keeping negotiationType",
                negotiation_type,
                uri_kind.value,
            )
        return kind
    return uri_kind


def _vendor_propose_kind(content: object) -> NegotiationKind | None:
    """Return the kind named by a vendor propose body, or ``None`` when it names none.

    A readable document is typed only by its own ``responseMetadata`` blocks, never by a field
    found elsewhere in the document. When the envelope does not parse at all (a root the client
    does not know, truncated JSON), the typed fields are read from the raw value but only inside
    its ``responseMetadata`` block, so a typed proposal is still typed instead of guessed from
    prose. ``None`` keeps the caller on its heading fallback or its loud failure.
    """
    decoded = decode_extension_value(content)
    if isinstance(decoded, (Mapping, list)):
        kinds: list[NegotiationKind] = []
        for response_metadata in _vendor_response_metadatas(content):
            kind = _kind_from_vendor_fields(
                response_metadata.get("negotiationType"), response_metadata.get("templateUri")
            )
            if kind is not None:
                kinds.append(kind)
        if len(set(kinds)) > 1:
            _LOGGER.warning(
                "vendor propose carries disagreeing typed metadata (%s); keeping the first",
                ", ".join(kind.value for kind in kinds),
            )
        return kinds[0] if kinds else None
    negotiation_type, template_uri = _vendor_typed_fields(content)
    if negotiation_type is None and template_uri is None:
        return None
    _LOGGER.warning(
        "vendor propose envelope was not readable as a document; typed fields read from its raw "
        "value (negotiationType=%s, templateUri=%s)",
        negotiation_type or "-",
        template_uri or "-",
    )
    return _kind_from_vendor_fields(negotiation_type, template_uri)


def _vendor_message_text(content: object) -> str | None:
    """Return the rendered prose of one Negotiation-T value, or ``None`` when it carries none.

    A readable document is unwrapped to its first non-empty ``message[*].text``. A value that is
    not a document is returned unchanged: rendered prose is prose even when it happens to start
    with ``[`` or ``{``. Only a JSON-looking value that also carries a ``message`` envelope is read
    from the raw string, which covers a truncated vendor body. ``None`` means "no prose": callers
    must not read a JSON envelope as if it were a proposal body.
    """
    decoded = decode_extension_value(content)
    if isinstance(decoded, (Mapping, list)):
        for document in _vendor_documents(content):
            for entry in _vendor_message_entries(document):
                text = entry.get("text")
                if isinstance(text, str) and text.strip():
                    return text
        return None
    if isinstance(content, str):
        if content.lstrip()[:1] in _JSON_OPENERS and _VENDOR_MESSAGE_PATTERN.search(content):
            return _vendor_raw_text(content)
        return content
    return None


def _vendor_raw_text(content: str) -> str | None:
    """Read the ``message[*].text`` field out of a JSON body the client could not parse.

    The value is the vendor's own field, JSON-escaped inside the envelope, so it is unescaped with
    the JSON rules before use. A body with no such field yields ``None``, never the envelope.
    """
    match = _VENDOR_TEXT_PATTERN.search(content)
    if match is None:
        return None
    try:
        return json.loads(f'"{match.group(1)}"')
    except ValueError:
        return match.group(1)


def _propose_diagnostics(
    metadata: Mapping[str, object],
    *,
    outer_template_uri: object = None,
) -> str:
    """Describe what one inbound propose actually carried, for the log and the raised error."""
    keys = ", ".join(sorted(str(key) for key in metadata)) or "<none>"
    content = metadata.get(NEGOTIATION_T_EXTENSION_URI)
    if isinstance(content, str):
        shape = "json" if content.strip()[:1] in _JSON_OPENERS else "text"
    elif isinstance(content, Mapping):
        shape = "mapping"
    elif isinstance(content, list):
        shape = "list"
    else:
        shape = "absent"
    parts = [f"metadata keys: {keys}", f"Negotiation-T value: {shape}"]
    if outer_template_uri is not None:
        parts.append(f"outer templateUri: {outer_template_uri!r}")
    return "; ".join(parts)


def _resolve_propose_template(
    metadata: Mapping[str, object],
    *,
    infer_missing_template_uri: bool,
) -> tuple[str, NegotiationKind, bool]:
    """Return one inbound propose's template URI, negotiation kind and whether it was inferred.

    A conformant propose carries a sample ``templateUri``. The vendor NMS instead names its own
    template (or omits it) and types the body through ``responseMetadata``, so with
    ``infer_missing_template_uri`` on, the typed fields of the body supply the kind and the
    canonical propose URI of that kind stands in for the unusable value. The resolution order is
    nested ``negotiationType``, nested ``templateUri``, the typed fields read from the raw value,
    then the opening heading of the unwrapped body. An untyped body still fails: guessing the kind
    would select the wrong decision prompt and the wrong reply template.

    Args:
        metadata: A2A metadata map of the inbound propose.
        infer_missing_template_uri: whether an unusable ``templateUri`` may be recovered from the
            propose body. Only the real-NMS client path turns this on.

    Returns:
        The template URI to report, the negotiation kind, and whether the URI was inferred.

    Raises:
        ClientFlowError: when the propose carries no usable template URI and its kind is not
            readable from the body. The message lists the metadata keys, the value shape and the
            outer URI so a field report is diagnosable without the raw frame.
    """
    value = metadata.get(TEMPLATE_URI_METADATA_KEY)
    if isinstance(value, str) and value.strip():
        kind = negotiation_kind_for_template_uri(value)
        if kind is not None:
            return value, kind, False
        if not infer_missing_template_uri:
            raise ClientFlowError(
                f"{_ERROR_UNKNOWN_KIND} templateUri={value} "
                f"({_propose_diagnostics(metadata, outer_template_uri=value)})"
            )
        _LOGGER.warning(
            "the negotiation propose template %r is not a sample negotiation template; "
            "inferring its kind from the body",
            value,
        )
    elif not infer_missing_template_uri:
        raise ClientFlowError(f"{_ERROR_NO_TEMPLATE_URI} ({_propose_diagnostics(metadata)})")

    content = metadata.get(NEGOTIATION_T_EXTENSION_URI)
    kind = _vendor_propose_kind(content)
    if kind is None:
        kind = negotiation_kind_for_propose_content(_vendor_message_text(content))
    if kind is not None:
        _LOGGER.warning(
            "the negotiation propose carries no usable outer template URI; inferred kind=%s "
            "from its responseMetadata, typed fields or heading",
            kind.value,
        )
        return _PROPOSE_TEMPLATE_URI_BY_KIND[kind.value], kind, True
    diagnostics = _propose_diagnostics(metadata, outer_template_uri=value)
    if isinstance(value, str) and value.strip():
        # The frame did carry a template URI; inference just could not type it. Reporting "no
        # template URI" here would misdirect the field investigation.
        _LOGGER.warning(
            "the negotiation propose template %r names no sample kind and its body names none (%s)",
            value,
            diagnostics,
        )
        raise ClientFlowError(f"{_ERROR_UNKNOWN_KIND} templateUri={value} ({diagnostics})")
    _LOGGER.warning("the negotiation propose carries no template URI (%s)", diagnostics)
    raise ClientFlowError(f"{_ERROR_NO_TEMPLATE_URI} ({diagnostics})")


def _propose_view(
    event: Mapping[str, object],
    metadata: Mapping[str, object],
    session: NegotiationContext,
    kind: NegotiationKind,
    template_uri: str,
    context_id: str,
) -> NegotiationProposeView:
    """Build the provider-facing view of one inbound server proposal.

    ``content`` is the rendered prose of the proposal, never the vendor's JSON envelope: the
    decision prompt, the grounding checks, the transcript and the approval card all consume it as
    the human-readable proposal body.
    """
    content = _vendor_message_text(metadata.get(NEGOTIATION_T_EXTENSION_URI)) or ""
    return NegotiationProposeView(
        negotiation_id=session.id,
        kind=kind.value,
        round=session.round,
        max_rounds=session.max_rounds,
        content=content,
        performative=session.performative.value,
        template_uri=template_uri,
        context_id=_string_field(event, "context_id") or context_id,
        options=_proposal_options(metadata.get(SAMPLE_NEGOTIATION_OPTIONS_METADATA_KEY)),
    )


def _proposal_options(value: object) -> NegotiationOptions | None:
    """Parse the sample-only option payload of one proposal, ignoring malformed metadata."""
    if not isinstance(value, Mapping):
        return None
    try:
        return NegotiationOptions.model_validate(value)
    except ValueError:
        return None


def _prefer_the_agreed_target(
    kind: NegotiationKind,
    filled: dict[str, object],
    decision: NegotiationDecision,
    propose: NegotiationProposeView,
    baseline: Mapping[str, object] | None,
) -> str | None:
    """Return (and apply) the target the accept actually agrees to.

    A feasibility/target accept must name the alternative, not the baseline the server rejected.
    When the decision supplies a target that value wins; otherwise the proposal's own alternative
    plus the operator's windows yields it deterministically (``agreed_target_value``). When neither
    names a value the baseline target is left untouched: it is still the task's throughput goal,
    so the refilled Task-T must keep it, and the accept text must simply not present it as a new
    agreement (see ``_agreed_values_line``).
    """
    if kind is NegotiationKind.INFORMATION:
        return None
    supplied = normalize_text((decision.filled_params or {}).get(RATE_TARGET_FIELD))
    if supplied is not None:
        filled[RATE_TARGET_FIELD] = supplied
        return supplied
    if kind is not NegotiationKind.FEASIBILITY:
        # ``agreed_target_value`` re-rates the operator's windows from the proposal's prose. That is
        # a feasibility derivation; a target negotiation's accepted plan must come from the
        # decision, never from a rate the proposal happened to mention.
        return None
    chosen_event = (decision.filled_params or {}).get("backgroundEvent")
    if isinstance(chosen_event, str) and chosen_event.strip():
        # The decision took the non-rate alternative (a shorter outage). Naming the rate cut would
        # claim a choice the client did not make.
        return None
    agreed = normalize_text(agreed_target_value(propose, filled)) or normalize_text(
        agreed_target_value(propose, {**(baseline or {}), **filled})
    )
    if agreed is not None:
        filled[RATE_TARGET_FIELD] = agreed
    return agreed


def _typed_accept_content(
    kind: NegotiationKind,
    filled: Mapping[str, object],
    decision: NegotiationDecision,
    baseline: Mapping[str, object] | None,
    agreed_target: str | None,
) -> NegotiationEndingContent | None:
    """Build the typed ending content of an accept, or ``None`` when the flow cannot type it.

    ``None`` keeps the from-text fallback for content the flow does not own (an operator's free
    answer the sample cannot map onto Task-T parameters). Every branch here is deterministic: the
    values come from the decision, the agreed Task-T parameters or the proposal, never from a model
    re-reading the client's own accept text.
    """
    if kind is NegotiationKind.INFORMATION:
        allowed = TASK_PARAM_SCHEMA.get("properties")
        items: list[NegotiationItem] = []
        for name, raw in (decision.filled_params or {}).items():
            if not isinstance(allowed, Mapping) or name not in allowed:
                continue
            # The item value is the decision's own answer, never the merged Task-T value: a value
            # the flow could not refill (for example a loosely written rate) must not be replaced
            # by the baseline the request already carried.
            if raw is None or not str(raw).strip():
                continue
            value = normalize_text(raw) if name == RATE_TARGET_FIELD else None
            items.append(NegotiationItem(name, value or str(raw)))
        return InformationEndingContent(NegotiationConclusion.ACCEPT, items) if items else None
    if kind is NegotiationKind.TARGET:
        confirmation = (decision.confirmed_intent or "").strip()
        if confirmation and _contradicts_agreed_target(confirmation, agreed_target, baseline):
            _LOGGER.warning(
                "the confirmed intent names a target the accept does not agree to; using the "
                "deterministic confirmation instead"
            )
            confirmation = ""
        if confirmation:
            confirmation = _with_agreed_target(confirmation, agreed_target)
        else:
            confirmation = _agreed_values_line(filled, baseline=baseline)
        if not confirmation and agreed_target is not None:
            # The decision explicitly named this target even though it equals the baseline; an
            # accept that names nothing would fall back to a model extraction for no reason.
            confirmation = f"Rate guarantee target: {agreed_target}."
        if not confirmation:
            # A plain Accept click still confirms the server's proposal: the decision wording is
            # the confirmation. Never route this through the extractor, which has nothing to read.
            confirmation = (decision.rationale or "").strip() or "Accept the server's proposed adjustment."
        return TargetEndingContent(NegotiationConclusion.ACCEPT, confirmation, None)
    supplied_summary = (decision.feasibility_summary or "").strip()
    if supplied_summary and _contradicts_agreed_target(supplied_summary, agreed_target, baseline):
        _LOGGER.warning(
            "the decision summary names a target the accept does not agree to; using the "
            "deterministic summary instead"
        )
        supplied_summary = ""
    summary = (
        _with_agreed_target(supplied_summary, agreed_target)
        if supplied_summary
        else _feasibility_accept_summary(filled, baseline, agreed_target)
    )
    return FeasibilityEndingContent(NegotiationConclusion.ACCEPT, summary)


def _contradicts_agreed_target(
    text: str,
    agreed_target: str | None,
    baseline: Mapping[str, object] | None,
) -> bool:
    """Return whether one text names a target segment the accept does not agree to.

    A summary may mention the previous rate ("reduced from 10Mbps to 2Mbps"); that rate is the
    baseline and is legitimate context. Only a segment in neither the agreement nor the baseline
    is an invention that must not go out as the result.
    """
    if agreed_target is None:
        return False
    allowed = {(segment.start, segment.end, segment.rate_mbps) for segment in find_segments(agreed_target)}
    allowed |= {
        (segment.start, segment.end, segment.rate_mbps)
        for segment in find_segments((baseline or {}).get(RATE_TARGET_FIELD))
    }
    claimed = {(segment.start, segment.end, segment.rate_mbps) for segment in find_segments(text)}
    return bool(claimed - allowed)


def _with_agreed_target(summary: str, agreed_target: str | None) -> str:
    """Put the deterministic agreed value in front of a hand-written confirmation.

    The result confirmation is the only thing the vendor reads from an accept; a summary that
    omits the agreed rate would leave the vendor executing the pre-negotiation rate. A summary
    that already names every agreed segment is left alone, so the value is never repeated.
    """
    if agreed_target is None:
        return summary
    agreed = find_segments(agreed_target)
    rendered = {(segment.start, segment.end, segment.rate_mbps) for segment in find_segments(summary)}
    if agreed and all((segment.start, segment.end, segment.rate_mbps) in rendered for segment in agreed):
        return summary
    return f"Rate guarantee target: {agreed_target}. {summary}"


def _feasibility_accept_summary(
    filled: Mapping[str, object],
    baseline: Mapping[str, object] | None,
    agreed_target: str | None,
) -> str:
    """Render the feasibility result confirmation from the agreed values, never from a model.

    Only a target the accept actually agrees to is named: the untouched baseline rate is the goal
    the server rejected, so it must not appear as part of the agreement.
    """
    if agreed_target is not None:
        return f"Accepted the server's proposal: rate guarantee target {agreed_target}."
    values = _agreed_values_line(filled, baseline=baseline)
    if values:
        return f"Accepted the server's proposal: {values}"
    return "Accepted the server's proposed adjustment."


def _accept_text(
    kind: NegotiationKind,
    filled: Mapping[str, object],
    decision: NegotiationDecision,
    *,
    baseline: Mapping[str, object] | None = None,
) -> str:
    """Render the natural-language input of an accept for the proposal type.

    The agreed values are rendered by the flow, not carried by the model's prose. A real-NMS
    accept has no Task-T metadata: its text is the only thing the vendor reads, and a feasibility
    accept that does not name the alternative leaves the vendor executing the pre-negotiation
    rate. Only values the negotiation actually changed are included, because repeating the whole
    Task-T makes the Negotiation-T validator treat unrelated text as result content.
    """
    if kind is NegotiationKind.INFORMATION:
        # The information accept result must answer the requested item only. Repeating every base
        # Task-T field (region, description, ...) makes the Negotiation-T validator treat unrelated
        # text as result content and, on live models, reject the accept. The decision only answers
        # the items it actually filled; nothing is appended from configuration.
        allowed = TASK_PARAM_SCHEMA.get("properties")
        requested = [
            name for name in (decision.filled_params or {}) if isinstance(allowed, Mapping) and name in allowed
        ]
        items = " ".join(f"{name}: {filled.get(name, '')}." for name in requested)
        return f"Accept. {items}".strip()
    confirmation = decision.confirmed_intent if kind is NegotiationKind.TARGET else decision.feasibility_summary
    values = _agreed_values_line(filled, baseline=baseline)
    body = " ".join(part for part in (values, confirmation or "") if part).strip()
    if not body:
        # A bare ``Accept.`` gives the extraction nothing to read and used to kill the run
        # (D12: the fixed fallback was designed but never implemented). Prefer the decision's own
        # wording, then the fixed sentence; never the performative alone.
        body = (decision.rationale or "").strip() or "Accept the server's proposed adjustment."
    return f"Accept. {body}".strip()


def _agreed_values_line(
    filled: Mapping[str, object],
    *,
    baseline: Mapping[str, object] | None,
) -> str:
    """Render the Task-T values the accept changes, as one deterministic sentence group."""
    parts: list[str] = []
    target = filled.get("guaranteedThroughputTarget")
    if isinstance(target, str) and target.strip():
        baseline_target = normalize_text((baseline or {}).get("guaranteedThroughputTarget"))
        # The baseline is what the server rejected; repeating it as the agreed value is worse
        # than naming nothing.
        if normalize_text(target) != baseline_target:
            parts.append(f"Rate guarantee target: {target.strip()}.")
    for name, label in (
        ("region", "Area"),
        ("siteList", "Site list"),
        ("backgroundEvent", "Background event"),
    ):
        value = filled.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        if baseline is not None and normalize(value) == normalize(baseline.get(name, "")):
            continue
        parts.append(f"{label}: {value.strip()}.")
    return " ".join(parts)


def _reject_text(kind: NegotiationKind, propose_content: str, decision: NegotiationDecision) -> str:
    """Render the natural-language input of a reject; information rejects itemize every item."""
    rationale = decision.rationale or "The requested information cannot be provided."
    if kind is not NegotiationKind.INFORMATION:
        return f"Reject. {rationale}"
    match = _MISSING_ITEMS_PATTERN.search(propose_content)
    items = [item.strip() for item in match.group(1).split(",")] if match else []
    items = [item for item in items if item] or ["requested information"]
    return "Reject. " + " ".join(f"{item}: {rationale}" for item in items)


def _final_state(batch: list[dict[str, object]], *, message_ends_flow: bool = True) -> str:
    """Derive the terminal state from one send phase's events.

    Only the last send phase may terminate the flow: scanning every accumulated event would let a
    prior round's ``Propose`` look like the end message after a webhook phase stops at the report
    artifact. A plain non-negotiation message (or the explicit end text) is the negotiation-end
    signal; a negotiation message is not.

    ``message_ends_flow=False`` keeps the explicit end text as an end signal but treats every
    other plain message as an observation. A direct vendor hop answers a request with a standalone
    ROLE_AGENT message even while its Task keeps working, so that message must not end the run.

    A downstream ``COMPLETED`` is not automatically a success: the vendor NMS reports a refused
    request with ``TASK_STATE_COMPLETED`` plus an error sentence (W-PURE-3). Such an artifact-less
    completion is classified ``task_failed`` so the outer Task and the UI never claim success.
    """
    report_seen = False
    artifact_seen = any(event.get("kind") == "artifact" for event in batch)
    for event in reversed(batch):
        if event.get("kind") == "artifact":
            report_seen = True
            continue
        if event.get("kind") == "message":
            text = event.get("text")
            metadata = event.get("metadata")
            if text == END_MESSAGE_TEXT:
                return FINAL_STATE_NEGOTIATION_ENDED
            if not message_ends_flow:
                # A direct vendor hop answers with a standalone message while its Task is still
                # working; only a Task state (or the explicit end text) ends that flow.
                continue
            if isinstance(metadata, Mapping) and is_negotiation_message(metadata_to_dict(metadata)):
                continue
            return FINAL_STATE_NEGOTIATION_ENDED
        if event.get("kind") != "status":
            continue
        state = event.get("state")
        if state == TaskState.Name(TaskState.TASK_STATE_COMPLETED):
            if _reports_failed_completion(event, artifact_seen=artifact_seen):
                return FINAL_STATE_TASK_FAILED
            return FINAL_STATE_TASK_COMPLETED
        if state == TaskState.Name(TaskState.TASK_STATE_REJECTED):
            return FINAL_STATE_TASK_REJECTED
        if state in _TERMINAL_STATE_NAMES:
            return FINAL_STATE_TASK_FAILED
    return FINAL_STATE_INCOMPLETE if not report_seen else FINAL_STATE_TASK_COMPLETED


def _reports_failed_completion(event: Mapping[str, object], *, artifact_seen: bool) -> bool:
    """Whether one ``COMPLETED`` status actually reports a refused request.

    The vendor NMS expresses "I cannot do this" with ``TASK_STATE_COMPLETED`` and an error sentence
    rather than ``FAILED``. A completed Task that published an artifact produced a result, so only
    an artifact-less completion whose status text carries a refusal phrase is downgraded. Everything
    else keeps the protocol state the downstream sent.
    """
    if artifact_seen:
        return False
    return reports_failed_completion(event.get("text"))


def _fill_result(result: ClientFlowResult, context_id: str | None) -> None:
    """Fill the context id, task id and report payload of one result.

    The ADK executor emits one artifact per tool call and a final artifact named after the report;
    the report artifact wins, the first artifact is only the fallback so a Task that produced no
    named report still exposes something useful.
    """
    result.context_id = result.context_id or context_id
    report_events: list[dict[str, object]] = []
    fallback_event: dict[str, object] | None = None
    for event in result.events:
        task_id = _string_field(event, "task_id")
        if task_id:
            result.task_id = task_id
        if event.get("kind") == "artifact":
            fallback_event = fallback_event or event
            if "report" in (_string_field(event, "name") or "").lower():
                report_events.append(event)
    # A rejected change still carries tool artifacts (the profile and the evaluation), but it has
    # no report. Never expose the first tool result as if it were the energy-saving report.
    if result.final_state != FINAL_STATE_TASK_COMPLETED:
        return
    selected = report_events or ([fallback_event] if fallback_event is not None else [])
    for event in selected:
        data = event.get("data")
        if isinstance(data, Mapping):
            result.reports.append(dict(data))
        else:
            result.reports.append(
                {key: value for key, value in event.items() if key in ("text", "name", "artifact_id")}
            )
    if result.reports:
        # A two-phase run has a baseline report and the modified report; the latter is the result.
        result.report = result.reports[-1]
