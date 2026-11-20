"""Metadata model of generated A2A-T messages.

Port of the Java ``core/model`` triple :class:`MetadataContent`, :class:`NegotiationContext` and
``NegotiationPerformative``, plus the extension URI constants of Java ``ExtensionUriConstants``. A
generated message travels in A2A-T metadata under its extension URI
together with the template it was rendered from and, for negotiation messages, the session context
of the negotiation it belongs to; :meth:`MetadataContent.build_metadata_content` builds that map.

The performative carries the communicative intent of a negotiation message (plan 7.5: a performative
is a speech act, not a state-machine phase). How a performative maps to a prompt template is a
concern of the negotiation layer, not of this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final

__all__ = [
    "AUTHORIZATION_T_EXTENSION_URI",
    "NEGOTIATION_CONTEXT_METADATA_KEY",
    "NEGOTIATION_T_EXTENSION_URI",
    "NEGOTIATION_T_EXTENSION_URI_NL",
    "NOTIFICATION_T_EXTENSION_URI",
    "TASK_T_EXTENSION_URI",
    "TEMPLATE_URI_METADATA_KEY",
    "MetadataContent",
    "NegotiationContext",
    "NegotiationPerformative",
    "decode_extension_value",
    "extension_value",
]

#: Metadata key carrying the template URI alongside the message itself.
TEMPLATE_URI_METADATA_KEY: Final[str] = "templateUri"

#: Metadata key carrying the negotiation session context alongside the message itself.
NEGOTIATION_CONTEXT_METADATA_KEY: Final[str] = "negotiationContext"

#: URI of the Task-T extension (Java ``ExtensionUriConstants``): the extension under which
#: generated task prompts travel in A2A-T metadata.
TASK_T_EXTENSION_URI: Final[str] = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1"

#: URI of the Authorization-T extension (Java ``ExtensionUriConstants``).
AUTHORIZATION_T_EXTENSION_URI: Final[str] = (
    "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Authorization-T/v1"
)

#: URI of the Notification-T extension (Java ``ExtensionUriConstants``).
NOTIFICATION_T_EXTENSION_URI: Final[str] = (
    "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1"
)

#: Canonical URI of the Negotiation-T extension (Java ``ExtensionUriConstants`` negotiation slice):
#: the URI under which generated negotiation messages travel in A2A-T metadata.
NEGOTIATION_T_EXTENSION_URI: Final[str] = (
    "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1"
)

#: Legacy alias URI of the Negotiation-T extension, kept for runtime compatibility reads of
#: messages emitted under the ``NL`` naming; new metadata emission uses
#: :data:`NEGOTIATION_T_EXTENSION_URI`.
NEGOTIATION_T_EXTENSION_URI_NL: Final[str] = (
    "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/NL/v1"
)


def decode_extension_value(value: object) -> object:
    """Decode one A2A-T extension metadata value carrying an embedded JSON document.

    A2A-T emits the extension value as rendered text, but peers in the field also send a JSON
    document serialized into that string, sometimes with a list root (``[{...}]``) rather than an
    object. Callers that need the structured payload should read it through this helper instead of
    calling :func:`json.loads` themselves.

    Only a string whose whole content parses as a JSON object or array is decoded: rendered prompt
    text, malformed or truncated JSON and JSON scalars are returned unchanged. Nothing is decoded
    recursively, so a value that was already structured passes through untouched. The helper never
    raises.

    Args:
        value: raw extension metadata value; any type is accepted.

    Returns:
        the decoded ``dict``/``list`` when the value is a JSON object/array string, otherwise
        ``value`` unchanged.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "{[":
        return value
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        return value
    return parsed if isinstance(parsed, (dict, list)) else value


def extension_value(metadata: object | None, extension_uri: str) -> object | None:
    """Return one extension value of a metadata container, decoded by :func:`decode_extension_value`.

    Accepts anything keyed by URI: a plain mapping, or the protobuf ``Struct`` that an inbound
    ``a2a.types.Message`` carries (a ``Struct`` registers as a ``Mapping`` but has no ``get``, so
    the value is read by item access, never by ``.get``). The read never raises.

    Args:
        metadata: metadata container, or ``None``.
        extension_uri: extension URI whose value is read, for example
            :data:`NEGOTIATION_T_EXTENSION_URI`.

    Returns:
        the decoded extension value, or ``None`` when the container or the key is missing.
    """
    if metadata is None:
        return None
    try:
        value = metadata[extension_uri]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return None
    return decode_extension_value(value)


class NegotiationPerformative(StrEnum):
    """Performative (negotiation primitive) of a negotiation message.

    A performative expresses the communicative intent of a message within a negotiation session:
    ``PROPOSE`` puts a proposal on the table, ``ACCEPT`` and ``REJECT`` respond to a proposal, and
    ``ABORT`` terminates the session. The wire value of every constant is its upper-case name.
    """

    #: Puts a proposal on the table for the counterpart to evaluate.
    PROPOSE = "PROPOSE"

    #: Accepts the counterpart's latest proposal.
    ACCEPT = "ACCEPT"

    #: Rejects the counterpart's latest proposal.
    REJECT = "REJECT"

    #: Terminates the negotiation session before an agreement is reached.
    ABORT = "ABORT"

    @classmethod
    def try_parse(cls, value: str | None) -> NegotiationPerformative | None:
        """Parse the wire representation of a performative.

        Only the exact upper-case names of the four constants are accepted; lower-case, ``None``
        and unknown values yield ``None`` rather than raising (Java ``Optional`` parity).

        Args:
            value: candidate wire value, may be ``None``.

        Returns:
            the matching performative, or ``None`` when the value is not an exact match.
        """
        if value is None:
            return None
        for performative in cls:
            if performative.value == value:
                return performative
        return None


@dataclass(frozen=True, slots=True)
class NegotiationContext:
    """Session context carried by every negotiation message.

    The context identifies one negotiation session, tracks the current round, and bounds how many
    rounds the session may run. A context is immutable; advancing to the next round produces a new
    instance.

    The ``performative`` carries the communicative intent of the message the context travels with.
    On an incoming message it is the performative of that message; the generation pipeline stamps
    the performative of the operation it performs on the output context, mirroring the nested
    ``negotiationContext`` structure of the A2A-T Negotiation-T specification.

    Attributes:
        id: unique identifier of the negotiation session, expected to be a UUID.
        round: current negotiation round, 1-based.
        max_rounds: maximum number of rounds before the negotiation must end.
        performative: communicative intent of the message this context travels with.
    """

    id: str
    round: int
    max_rounds: int
    performative: NegotiationPerformative

    #: Default round budget applied when a caller does not specify one.
    DEFAULT_MAX_ROUNDS: ClassVar[int] = 5

    def __post_init__(self) -> None:
        """Validate the context fields.

        Raises:
            ValueError: when the id is blank, the round is below 1, ``max_rounds`` is below 1, or
                the performative is ``None``.
        """
        if self.id is None or not self.id.strip():
            raise ValueError("Negotiation context id must not be blank.")
        if self.round < 1:
            raise ValueError(f"Negotiation context round must be a positive integer but was {self.round}.")
        if self.max_rounds < 1:
            raise ValueError(f"Negotiation context maxRounds must be a positive integer but was {self.max_rounds}.")
        if self.performative is None:
            raise ValueError("Negotiation context performative must not be null.")

    @classmethod
    def of(cls, id: str, round: int, performative: NegotiationPerformative) -> NegotiationContext:
        """Create a context using the default round budget.

        Args:
            id: unique identifier of the negotiation session.
            round: current negotiation round, 1-based.
            performative: communicative intent of the message this context travels with.

        Returns:
            a new negotiation context with :attr:`DEFAULT_MAX_ROUNDS` as the round budget.
        """
        return cls(id, round, cls.DEFAULT_MAX_ROUNDS, performative)

    def next_round(self) -> NegotiationContext:
        """Return the context for the next round, leaving this context unchanged.

        The returned context keeps this context's performative: the performative of an outbound
        message is decided by the generation pipeline, which stamps the operation's performative
        on the context it emits.

        Returns:
            a new negotiation context whose round is one greater than the current round.
        """
        return NegotiationContext(self.id, self.round + 1, self.max_rounds, self.performative)

    def with_performative(self, performative: NegotiationPerformative) -> NegotiationContext:
        """Return a copy of this context stamped with the given performative.

        Args:
            performative: communicative intent to stamp on the copy.

        Returns:
            a new negotiation context with the same ``id``, ``round`` and ``max_rounds`` as this
            context.

        Raises:
            ValueError: when the performative is ``None``.
        """
        return NegotiationContext(self.id, self.round, self.max_rounds, performative)

    def is_exhausted(self) -> bool:
        """Report whether the round budget has been exceeded.

        A context whose round equals ``max_rounds`` is not exhausted yet; only rounds strictly
        beyond the budget are.

        Returns:
            ``True`` when the current round is greater than ``max_rounds``.
        """
        return self.round > self.max_rounds


@dataclass(frozen=True, slots=True)
class MetadataContent:
    """Successful result of an A2A-T prompt generation call.

    Attributes:
        template_uri: URI of the template the message was generated from; ``None`` when unknown.
        prompt_text: rendered prompt text; ``None`` when not yet rendered.
        extension_uri: TMF extension URI under which the message travels in A2A-T metadata.
        negotiation_context: negotiation session context; ``None`` for non-negotiation messages.
    """

    template_uri: str | None
    prompt_text: str | None
    extension_uri: str
    negotiation_context: NegotiationContext | None = None

    def build_metadata_content(self) -> dict[str, object]:
        """Build the A2A-T metadata map for this generated message.

        The returned map always contains the extension URI mapping to the rendered message and
        ``templateUri`` mapping to the template URI, in that order. When a negotiation context is
        present it additionally carries ``negotiationContext`` mapping to a nested map with the
        ``id``, ``round``, ``maxRounds`` and ``performative`` fields, in that order, the
        performative holding its upper-case wire name. Non-negotiation messages omit the
        ``negotiationContext`` key entirely. Repeated calls return equal maps.

        Returns:
            a newly built metadata map with the extension URI, ``templateUri`` and, for negotiation
            messages, ``negotiationContext`` keys.
        """
        metadata: dict[str, object] = {
            self.extension_uri: self.prompt_text,
            TEMPLATE_URI_METADATA_KEY: self.template_uri,
        }
        context = self.negotiation_context
        if context is not None:
            metadata[NEGOTIATION_CONTEXT_METADATA_KEY] = {
                "id": context.id,
                "round": context.round,
                "maxRounds": context.max_rounds,
                "performative": context.performative.value,
            }
        return metadata
