"""Negotiation terminal-rounds metric (spec 5.5): ``a2at.negotiation.total_rounds`` Histogram.

Reported automatically when the A2ATClient/A2ATServer constructs a terminal
negotiation message (accept/reject/abort): ``report_negotiation_rounds`` records
one histogram sample with the round value extracted from the message's
``negotiationContext``. A Histogram (not a Counter) keeps the round-value
distribution queryable (avg/max/p90). Attributes: ``negotiation.id``,
``outcome`` (accept/reject/abort), ``extension.name=Negotiation-T``. Both ends
may report; only the terminal-message constructor reports, once per negotiation.

Instrumentation scope matches the rest of the SDK: meter name ``a2at-observability``
(via ``_otel_compat.get_meter``). Metric failures are swallowed with a WARNING
and never break the business flow (spec 8.2).
"""

from __future__ import annotations

import logging
from typing import Any

from a2a_t.observability import _otel_compat
from a2a_t.observability.attributes import ATTR_EXTENSION_NAME, ATTR_NEGOTIATION_ID

logger = logging.getLogger("a2at.observability")

METRIC_NEGOTIATION_TOTAL_ROUNDS = "a2at.negotiation.total_rounds"
NEGOTIATION_EXTENSION = "Negotiation-T"
_OUTCOMES = frozenset({"accept", "reject", "abort"})

_METRIC_INSTRUMENTS: dict[str, Any] = {}


def report_terminal_negotiation_rounds(negotiation_context: Any) -> None:
    """Report ``a2at.negotiation.total_rounds`` from a terminal NegotiationContext (spec 5.5).

    Terminal means the context performative is ACCEPT/REJECT/ABORT; PROPOSE (and any other
    value) reports nothing. Structural access (``id`` / ``round`` / ``performative``); never
    raises (spec 8.2).
    """
    try:
        if negotiation_context is None:
            return
        performative = getattr(negotiation_context, "performative", None)
        outcome = getattr(performative, "value", performative)
        if not isinstance(outcome, str) or outcome.lower() not in _OUTCOMES:
            return
        report_negotiation_rounds(
            negotiation_id=getattr(negotiation_context, "id", None),
            outcome=outcome,
            rounds=getattr(negotiation_context, "round", None),
        )
    except Exception:  # noqa: BLE001 - metrics must never break the flow
        logger.warning("a2at: failed to report negotiation rounds", exc_info=True)


def _metric_histogram(name: str, unit: str, description: str) -> Any | None:
    """Lazily create and cache one histogram per metric name (shared v2 pattern)."""
    instrument = _METRIC_INSTRUMENTS.get(name)
    if instrument is None:
        meter: Any = _otel_compat.get_meter()
        instrument = meter.create_histogram(name, unit=unit, description=description)
        _METRIC_INSTRUMENTS[name] = instrument
    return instrument


def report_negotiation_rounds(negotiation_id: str | None, outcome: str | None, rounds: int | None) -> None:
    """Record one ``a2at.negotiation.total_rounds`` histogram sample; never raises."""
    try:
        if not _otel_compat.is_metric_enabled():
            return
        if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 0:
            return
        attributes: dict[str, Any] = {ATTR_EXTENSION_NAME: NEGOTIATION_EXTENSION}
        if isinstance(negotiation_id, str) and negotiation_id:
            attributes[ATTR_NEGOTIATION_ID] = negotiation_id
        if isinstance(outcome, str) and outcome.lower() in _OUTCOMES:
            attributes["outcome"] = outcome.lower()
        histogram = _metric_histogram(
            METRIC_NEGOTIATION_TOTAL_ROUNDS, "{round}", "Negotiation terminal message round count"
        )
        if histogram is not None:
            histogram.record(rounds, attributes=attributes)
    except Exception:  # noqa: BLE001 - metrics must never break the flow
        logger.warning("a2at: failed to record negotiation metric", exc_info=True)
