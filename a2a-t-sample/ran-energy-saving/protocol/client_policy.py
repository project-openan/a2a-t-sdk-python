"""Deterministic client acceptance policy of an automatic negotiation decision.

The client owns exactly one kind of fact: what it is willing to concede. It does not own network
facts. Region, site and cell names, and whether a goal is achievable, are the server's authority:
the client passes the operator's values through, uses a value the server itself offers, and never
invents, corrects or rejects one on its own.

This module is the deterministic side of that boundary. It runs only for an automatic (live ADK)
decision; a scripted decision is a recorded operator answer and a manual/confirm decision is the
operator's own, so neither is filtered. It never calls a model and never sends a message.

The policy answers three questions:

* may the model change an entity value? only when the server proposal offers that exact value, or
  the operator's own parsed intent states it;
* does the agreed plan meet the client's throughput floor?
* may an automatic ``reject``/``abort`` of a feasible alternative be turned into an ``accept``?
  Yes, unless the throughput floor vetoes it: in automatic mode the client accepts the server's
  feasible alternative by default.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from shared.models import NegotiationAreaOption, NegotiationDecision, NegotiationProposeView

from protocol.grounding import normalize, present, server_entity_values, server_target_rates

#: Task-T fields the server owns. The model may only restate a value the server itself offers, or
#: a value the operator's own intent already states.
ENTITY_FIELDS: Final[tuple[str, ...]] = ("operationType", "region", "siteList")

#: Task-T fields the client may agree to change as part of a plan.
MUTABLE_FIELDS: Final[tuple[str, ...]] = ("guaranteedThroughputTarget", "backgroundEvent")

#: One ``{HH:MM~HH:MM,NMbps}`` target segment (the workbook also uses a hyphen separator).
_TARGET_SEGMENT: Final[re.Pattern[str]] = re.compile(
    r"\{\s*([0-9]{1,2}:[0-9]{2})\s*[-~–]\s*([0-9]{1,2}:[0-9]{2})\s*,\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*Mbps\s*\}",
    re.IGNORECASE,
)

#: One plain rate in an affirmative sentence.
_RATE: Final[re.Pattern[str]] = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*Mbps", re.IGNORECASE)

#: One ``HH:MM`` clock value.
_CLOCK: Final[re.Pattern[str]] = re.compile(r"([0-9]{1,2}):([0-9]{2})")


@dataclass(frozen=True)
class PolicyOutcome:
    """What the deterministic policy changed in one automatic decision.

    Attributes:
        notes: Human-readable explanation of every change, for the trace and the log.
        dropped_fields: Entity fields the model supplied without a source; the value was removed.
        forced_accept: Whether a model ``reject``/``abort`` was turned into an ``accept``.
    """

    notes: tuple[str, ...] = ()
    dropped_fields: frozenset[str] = frozenset()
    forced_accept: bool = False


@dataclass(frozen=True)
class ClientPolicy:
    """The client-side acceptance limits of one run.

    Attributes:
        min_throughput_mbps: Lowest guaranteed throughput the client accepts without a human;
            ``None`` means no floor. Configured by ``A2AT_SAMPLE_CLIENT_MIN_THROUGHPUT_MBPS``.
    """

    min_throughput_mbps: float | None = None

    @classmethod
    def from_config(cls, config: object) -> ClientPolicy:
        """Build the policy from a :class:`shared.config.SampleConfig` (or any object with the key)."""
        value = getattr(config, "client_min_throughput_mbps", None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return cls()
        return cls(min_throughput_mbps=float(value))

    def view(self) -> dict[str, object]:
        """Return the policy as the JSON-serializable view the decision agent's tool returns."""
        return {
            "min_throughput_mbps": self.min_throughput_mbps,
            "mutable_fields": list(MUTABLE_FIELDS),
            "entity_fields": list(ENTITY_FIELDS),
            "rules": [
                "The server is authoritative for region, site and cell names; never add, rename, "
                "correct or remove them. Use a value the original request states or a value the "
                "server's proposal offers.",
                "In automatic mode, accept a server alternative that only changes the rate target, "
                "the time window or the background event, unless it is below min_throughput_mbps.",
                "Answer an information item only with a value from the original request or from "
                "the server's own options.",
            ],
        }


def apply_automated_decision(
    decision: NegotiationDecision,
    *,
    kind: str,
    propose: NegotiationProposeView,
    baseline: Mapping[str, object],
    intent_fields: Mapping[str, object] | None,
    policy: ClientPolicy,
) -> tuple[NegotiationDecision, PolicyOutcome]:
    """Return the decision the client is allowed to send, plus what the policy changed.

    The input is the model's automatic decision. The function never invents an entity value: an
    entity field the model supplies is kept only when the server proposal offers that exact value
    or the operator's parsed intent already states it; otherwise it is dropped. When the proposal
    offers a structured entity option and the model did not use one, the recommended (or only)
    option is adopted.

    Args:
        decision: Automatic decision produced by the decision agent.
        kind: Negotiation kind of the proposal (``information`` / ``target`` / ``feasibility``).
        propose: Inbound server proposal.
        baseline: Task-T values already accepted by the server (the extraction of the sent prompt).
        intent_fields: Operator-derived business values, when the intent layer ran.
        policy: Acceptance limits of this client.

    Returns:
        The decision to send and a :class:`PolicyOutcome` describing what changed.
    """
    notes: list[str] = []
    dropped: set[str] = set()
    forced_accept = False
    filled = dict(decision.filled_params or {})
    offered = server_entity_values(propose)
    intent = intent_fields or {}

    for field in ENTITY_FIELDS:
        value = filled.get(field)
        if field not in filled or not present(value):
            continue
        if (
            normalize(value) in offered.get(field, set())
            or normalize(value) == normalize(baseline.get(field, ""))
            or normalize(value) == normalize(intent.get(field, ""))
        ):
            continue
        filled.pop(field, None)
        dropped.add(field)
        notes.append(f"dropped {field}={value!r}: neither the server nor the operator states that value")

    for field, options in _structured_entity_options(propose):
        if field in filled and present(filled.get(field)):
            continue
        chosen = _preferred_option(options)
        if chosen is None:
            continue
        filled[field] = chosen
        notes.append(f"adopted the server's {field}={chosen!r}")

    if kind == "feasibility" and decision.action in ("reject", "abort"):
        violation = throughput_floor_violation(propose, policy)
        if violation is None:
            decision = decision.model_copy(
                update={
                    "action": "accept",
                    "rationale": (
                        "Automatic mode accepts the server's feasible alternative: it changes only "
                        "the rate target or time window and stays within the client policy."
                    ),
                    "filled_params": None,
                    "counter_params": None,
                    "counter_proposal": None,
                    "confirmed_intent": None,
                    "feasibility_summary": None,
                }
            )
            forced_accept = True
            notes.append("turned the model's reject of a feasible alternative into an accept")
        else:
            notes.append(f"kept the model's {decision.action}: {violation}")

    if decision.action == "accept" and kind == "feasibility":
        violation = throughput_floor_violation(propose, policy)
        if violation is not None:
            notes.append(f"refused the accept: {violation}")
            decision = decision.model_copy(
                update={
                    "action": "reject",
                    "rationale": violation,
                    "filled_params": None,
                    "confirmed_intent": None,
                    "feasibility_summary": None,
                }
            )
            filled = {}
        elif not present(filled.get("guaranteedThroughputTarget")):
            agreed = agreed_target_value(propose, baseline)
            if agreed is not None:
                filled["guaranteedThroughputTarget"] = agreed
                notes.append(f"derived the agreed target {agreed!r} from the proposal")

    if decision.action == "counter":
        violation = counter_floor_violation(decision, policy)
        if violation is not None:
            notes.append(f"refused the counter: {violation}")
            decision = decision.model_copy(
                update={
                    "action": "reject",
                    "rationale": violation,
                    "filled_params": filled or None,
                    "counter_params": None,
                    "counter_proposal": None,
                }
            )
            filled = {}

    if decision.action == "accept":
        decision = decision.model_copy(update={"filled_params": filled or None})
    return decision, PolicyOutcome(
        notes=tuple(notes),
        dropped_fields=frozenset(dropped),
        forced_accept=forced_accept,
    )


def throughput_floor_violation(
    propose: NegotiationProposeView,
    policy: ClientPolicy,
) -> str | None:
    """Return the reason the server's alternative is below the client floor, else ``None``."""
    floor = policy.min_throughput_mbps
    if floor is None:
        return None
    rates = [value for item in server_target_rates(propose) for value in _rate_values(item)]
    if not rates:
        return None
    lowest = min(rates)
    if lowest >= floor:
        return None
    return (
        f"The server's alternative ({lowest:g} Mbps) is below the client's configured minimum "
        f"throughput of {floor:g} Mbps."
    )


def counter_floor_violation(
    decision: NegotiationDecision,
    policy: ClientPolicy,
) -> str | None:
    """Return the reason the counter proposes a rate below the floor, else ``None``."""
    floor = policy.min_throughput_mbps
    if floor is None:
        return None
    texts = [decision.counter_proposal or ""]
    texts.extend(str(value) for value in (decision.counter_params or {}).values())
    rates = [value for text in texts for value in _rate_values(text)]
    if not rates or min(rates) >= floor:
        return None
    return (
        f"The counter proposal ({min(rates):g} Mbps) is below the client's configured minimum "
        f"throughput of {floor:g} Mbps."
    )


def agreed_target_value(
    propose: NegotiationProposeView,
    baseline: Mapping[str, object],
) -> str | None:
    """Return the agreed target the client should carry on a feasibility accept.

    A feasibility proposal states its alternative in prose ("reduce the rate target to 2Mbps").
    The value is applied to the operator's own window, so the accept names a concrete segment
    instead of leaving the vendor to keep the pre-negotiation rate.

    Args:
        propose: Inbound feasibility proposal.
        baseline: Task-T values already accepted by the server.

    Returns:
        A time-segmented target such as ``{08:00~18:00,2Mbps}``, or ``None`` when no concrete rate
        can be derived.
    """
    rates = [value for item in server_target_rates(propose) for value in _rate_values(item)]
    if not rates:
        return None
    rate_text = f"{min(rates):g}Mbps"
    target = baseline.get("guaranteedThroughputTarget")
    segments = _TARGET_SEGMENT.findall(target) if isinstance(target, str) else []
    if not segments:
        offered = [match.group(0) for match in _TARGET_SEGMENT.finditer(propose.content)]
        return ", ".join(offered) if offered else None
    event_window = _event_window(baseline.get("backgroundEvent"))
    agreed: list[str] = []
    for start, end, current_rate in segments:
        if event_window is None or _overlaps(start, end, *event_window):
            agreed.append(f"{{{start}~{end},{rate_text}}}")
        else:
            agreed.append(f"{{{start}~{end},{current_rate}Mbps}}")
    return ", ".join(agreed)


def _structured_entity_options(
    propose: NegotiationProposeView,
) -> list[tuple[str, Sequence[NegotiationAreaOption]]]:
    """Return the entity fields with structured options the proposal offers."""
    options = propose.options
    if options is None or options.field not in ENTITY_FIELDS:
        return []
    values = [option for option in options.options if option.value]
    if not values:
        return []
    return [(options.field, values)]


def _preferred_option(options: Sequence[NegotiationAreaOption]) -> str | None:
    """Return the recommended option, or the only option, of one structured field."""
    recommended = [option.value for option in options if option.recommended]
    if recommended:
        return recommended[0]
    if len(options) == 1:
        return options[0].value
    return None


def _rate_values(text: str) -> list[float]:
    """Return the numeric Mbps values of one text."""
    values: list[float] = []
    for match in _RATE.finditer(text):
        try:
            values.append(float(match.group(1)))
        except ValueError:  # pragma: no cover - the regex only matches numbers
            continue
    return values


def _event_window(value: object) -> tuple[str, str] | None:
    """Return the ``HH:MM~HH:MM`` window named by a background event, if any."""
    if not isinstance(value, str):
        return None
    match = re.search(
        r"\b([0-9]{1,2}:[0-9]{2})\s*(?:~|[-–]|to|至)\s*([0-9]{1,2}:[0-9]{2})\b",
        value,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return match.group(1), match.group(2)


def _minutes(clock: str) -> int:
    """Return one ``HH:MM`` clock value in minutes."""
    match = _CLOCK.fullmatch(clock.strip())
    if match is None:  # pragma: no cover - callers only pass regex-extracted clocks
        return 0
    return int(match.group(1)) * 60 + int(match.group(2))


def _overlaps(start: str, end: str, event_start: str, event_end: str) -> bool:
    """Return whether two clock windows overlap."""
    return _minutes(start) < _minutes(event_end) and _minutes(event_start) < _minutes(end)


__all__ = [
    "ClientPolicy",
    "ENTITY_FIELDS",
    "MUTABLE_FIELDS",
    "PolicyOutcome",
    "agreed_target_value",
    "apply_automated_decision",
    "counter_floor_violation",
    "throughput_floor_violation",
]
