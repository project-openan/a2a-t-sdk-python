"""A2A-T metadata and message helpers shared by the server protocol layer.

Every generated A2A-T message travels in A2A metadata under its extension URI, together with the
``templateUri`` it was rendered from and, for negotiation messages, the nested
``negotiationContext``. This module is the single place that turns an SDK ``MetadataContent`` into
that metadata map and reads it back, so the gate, the policy and the tests never hand-build
extension keys.

Sample convention: the negotiation session id is the A2A ``contextId`` (design §17), so every
``negotiationContext.id`` built here equals the message ``context_id``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Final

from a2a.types import Message, Role
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
    MetadataContent,
    NegotiationContext,
    NegotiationPerformative,
)
from a2a_t.core.standard_templates import ENERGY_SAVING_URI
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

#: Task-T template addressed by the sample.
TASK_TEMPLATE_URI: Final[str] = ENERGY_SAVING_URI

#: Metadata key carrying the sample-only counter proposal (``{cellId, window, targetRate}``).
#:
#: This is a sample convention, not an A2A-T protocol field: it lets the deterministic server policy
#: evaluate a client counter proposal against the in-memory RAN dataset. The protocol still works
#: when it is absent (the policy then advances through its preset revision sequence).
SAMPLE_PROPOSAL_METADATA_KEY: Final[str] = "sampleProposal"

#: Metadata key carrying sample-only choices on an information proposal.
#:
#: This is a sample convention, not an A2A-T protocol field: the unknown-area negotiation lists the
#: served areas and their fuzzy-match scores so a Web UI can render the areas as selectable choices.
#: The plain protocol content still carries the same question, so clients that ignore this key keep
#: working.
SAMPLE_NEGOTIATION_OPTIONS_METADATA_KEY: Final[str] = "sampleNegotiationOptions"

#: Metadata key carrying the sample-only Run Explorer run id on the initial Task-T message.
#:
#: The server reads it to tag its own spans with the client run id, so the Web UI can merge the
#: cross-process server trace into the run timeline. It is a sample convention, not an A2A-T field;
#: without it the server simply emits no trace (the CLI and plain A2A clients keep working).
SAMPLE_RUN_ID_METADATA_KEY: Final[str] = "sampleRunId"

#: Metadata key of the legacy sample-only two-phase marker of a Task-T message.
#:
#: This is a sample convention, not an A2A-T protocol field, and the client no longer sends it:
#: Modify is derived from the Task-T ``Operation Type``. The server still reads it so an older
#: external client keeps working: ``baseline`` asks the server to keep the Task ``WORKING`` after
#: the first report, and ``modify`` marks the follow-up as the Task-T modification.
SAMPLE_PHASE_METADATA_KEY: Final[str] = "samplePhase"
SAMPLE_PHASE_BASELINE: Final[str] = "baseline"
SAMPLE_PHASE_MODIFY: Final[str] = "modify"

#: Metadata key of the machine-readable structured errors on a REJECTED Task status message.
#:
#: Sample convention, not an A2A-T protocol field: the underlying ``ContentValidationError`` carries
#: ``SlotValidationError`` entries, and flattening them into prose would lose the codes clients need
#: for programmatic handling. The value is ``{"code": <overall code>, "errors": [<slot errors>]}``.
STRUCTURED_ERRORS_METADATA_KEY: Final[str] = "structuredErrors"

#: Text part of the standalone Negotiation-T propose message (the content itself lives in metadata).
PROPOSE_MESSAGE_TEXT: Final[str] = "Negotiation proposal from the RAN energy saving service"

#: Text part of the standalone Negotiation-T abort message.
ABORT_MESSAGE_TEXT: Final[str] = "The RAN energy saving negotiation was aborted"

#: Text of the plain A2A message that terminates a reject/abort negotiation.
END_MESSAGE_TEXT: Final[str] = "协商已结束"


def format_rate_percent(value: object) -> str:
    """Render one energy-saving rate as ``NN%`` without doubling an existing percent sign.

    The planner may produce either ``35`` or ``35%`` depending on the model, so both the local
    approval gate and the A2A MIE request normalize the value before showing it.
    """
    text = str(value).strip()
    return text if text.endswith("%") else f"{text}%"


def metadata_to_dict(metadata: Struct | Mapping[str, object] | None) -> dict[str, object]:
    """Return A2A message metadata as a plain Python mapping.

    Args:
        metadata: protobuf ``Struct`` of an A2A message, a plain mapping, or ``None``.

    Returns:
        A plain dict with nested structs converted to dicts; an empty dict for ``None``.
    """
    if metadata is None:
        return {}
    if isinstance(metadata, Struct):
        return MessageToDict(metadata)
    return dict(metadata)


def task_prompt_from_metadata(metadata: Mapping[str, object]) -> str:
    """Return the Task-T prompt carried under the Task-T extension URI, or an empty string."""
    value = metadata.get(TASK_T_EXTENSION_URI)
    return value if isinstance(value, str) else ""


def negotiation_prompt_from_metadata(metadata: Mapping[str, object]) -> str:
    """Return the Negotiation-T prompt carried under the Negotiation-T extension URI, or ``""``."""
    value = metadata.get(NEGOTIATION_T_EXTENSION_URI)
    return value if isinstance(value, str) else ""


def is_negotiation_message(metadata: Mapping[str, object] | None) -> bool:
    """Report whether one message metadata map marks an A2A-T negotiation message."""
    if not metadata:
        return False
    return NEGOTIATION_T_EXTENSION_URI in metadata or NEGOTIATION_CONTEXT_METADATA_KEY in metadata


def parse_negotiation_context(metadata: Mapping[str, object] | None) -> NegotiationContext | None:
    """Parse the nested ``negotiationContext`` carried by one A2A-T message.

    A missing or malformed context yields ``None`` instead of raising, mirroring the SDK's
    ``NegotiationPerformative.try_parse`` leniency at the wire boundary.

    Args:
        metadata: A2A message metadata map.

    Returns:
        The parsed session context, or ``None`` when the message carries no usable context.
    """
    if not metadata:
        return None
    raw = metadata.get(NEGOTIATION_CONTEXT_METADATA_KEY)
    if not isinstance(raw, Mapping):
        return None
    performative = NegotiationPerformative.try_parse(str(raw.get("performative", "") or ""))
    if performative is None:
        return None
    try:
        return NegotiationContext(
            id=str(raw.get("id", "") or ""),
            round=int(raw.get("round", 0)),
            max_rounds=int(raw.get("maxRounds", 0)),
            performative=performative,
        )
    except (TypeError, ValueError):
        return None


def negotiation_context_metadata(context: NegotiationContext) -> dict[str, object]:
    """Return the wire form of one negotiation context, as stored in message metadata."""
    return {
        "id": context.id,
        "round": context.round,
        "maxRounds": context.max_rounds,
        "performative": context.performative.value,
    }


def missing_extensions(requested_extensions: Sequence[str] | set[str], required: Sequence[str]) -> tuple[str, ...]:
    """Return the required extension URIs absent from the request header.

    Args:
        requested_extensions: extension URIs of the inbound ``A2A-Extensions`` header.
        required: extension URIs the caller requires.

    Returns:
        The required URIs that are not requested, in the order given by ``required``.
    """
    requested = set(requested_extensions)
    return tuple(uri for uri in required if uri not in requested)


def build_negotiation_metadata(content: MetadataContent, *, context_id: str) -> dict[str, object]:
    """Build the metadata map of one generated Negotiation-T message.

    The negotiation id is the server's own handle. The local sample server uses the context id as
    that handle, while the vendor NMS opens a new negotiation id on the same context id; using the
    generated session id covers both. ``None`` values (for example a generated message without a
    template URI) are omitted because the protobuf metadata struct cannot carry them.

    Args:
        content: generated negotiation content returned by the SDK.
        context_id: A2A context id of the conversation.

    Returns:
        Metadata entries: the Negotiation-T prompt, ``templateUri`` and ``negotiationContext``.

    Raises:
        ValueError: when ``content`` carries no negotiation context.
    """
    session = content.negotiation_context
    if session is None:
        raise ValueError("Negotiation content carries no negotiation context")
    metadata = content.build_metadata_content()
    metadata[NEGOTIATION_CONTEXT_METADATA_KEY] = negotiation_context_metadata(
        NegotiationContext(
            id=session.id or context_id,
            round=session.round,
            max_rounds=session.max_rounds,
            performative=session.performative,
        )
    )
    return {key: value for key, value in metadata.items() if value is not None}


def build_agent_message(
    *,
    context_id: str | None,
    text: str,
    metadata: Mapping[str, object] | None = None,
) -> Message:
    """Build one agent-role A2A message with an optional metadata map.

    Args:
        context_id: conversation context id; ``None`` keeps the message context-less.
        text: text part of the message.
        metadata: metadata entries to store on the message.
    """
    message = Message(message_id=str(uuid.uuid4()), role=Role.ROLE_AGENT)
    if context_id is not None:
        message.context_id = context_id
    message.parts.add(text=text)
    for key, value in (metadata or {}).items():
        if value is not None:
            # The bundled protobuf stubs do not model the dynamic Struct mapping.
            message.metadata[key] = value  # ty: ignore[invalid-assignment]
    return message


def build_negotiation_message(
    content: MetadataContent,
    *,
    context_id: str,
    text: str,
    extra_metadata: Mapping[str, object] | None = None,
) -> Message:
    """Build one standalone Negotiation-T message (no task id, no Task).

    Args:
        content: generated negotiation content returned by the SDK.
        context_id: A2A context id of the conversation.
        text: label text of the message part.
        extra_metadata: optional extra metadata (for example a sample-only convention).
    """
    metadata = build_negotiation_metadata(content, context_id=context_id)
    if extra_metadata:
        metadata.update(extra_metadata)
    return build_agent_message(context_id=context_id, text=text, metadata=metadata)


def build_end_message(context_id: str | None) -> Message:
    """Build the plain message that terminates a rejected or aborted negotiation.

    The message deliberately carries no Negotiation-T metadata and no task id, so clients end the
    flow instead of answering another round.
    """
    return build_agent_message(context_id=context_id, text=END_MESSAGE_TEXT)


def build_error_message(context_id: str | None, text: str) -> Message:
    """Build a plain protocol error message (no Task, no Negotiation-T metadata)."""
    return build_agent_message(context_id=context_id, text=text)
