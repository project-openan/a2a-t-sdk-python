"""Run specification and the demo/production seams of the client runtime.

A client run needs the natural-language request and the expected negotiation sequence (a scenario
catalog) plus the deterministic decision script used in offline mode. The composition root (the
demo launcher, the Web UI, the gateway CLI or a customer service) supplies them; ``protocol`` and
``shared`` only see these interfaces.

Business values are never part of the specification: the operator's text is the request, and both
the business intent and the negotiation values are parsed from it by the model/agent layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from shared.models import NegotiationDecision
from shared.run_constants import LANGUAGE_ZH


@dataclass(frozen=True)
class ExpectedStep:
    """One expected performative of a run, used by the UI and the acceptance tests."""

    actor: str
    performative: str
    round: int | None = None


@dataclass(frozen=True)
class RunSpec:
    """Everything the core needs to know about one named run.

    Attributes:
        name: scenario/run name used by the UI and the CLI.
        description: one-line description shown by the UI.
        user_request: natural-language request resolved for the process language. This text is
            both what the UI shows and what the client sends.
        scripted_decisions: decisions replayed by the scripted provider and the offline model
            (test doubles for the ADK decision agent).
        expected_sequence: expected performative sequence (UI hints and acceptance tests).
        expected_final_state: terminal state the run must reach, when the catalog provides one.
        expected_negotiation_target: guaranteed-throughput target the negotiation must agree on,
            when the scenario ends with a completed task.
    """

    name: str
    description: str
    user_request: str
    scripted_decisions: tuple[NegotiationDecision, ...] = ()
    expected_sequence: tuple[ExpectedStep, ...] = ()
    expected_final_state: str | None = None
    expected_negotiation_target: str | None = None


@runtime_checkable
class RunCatalog(Protocol):
    """Catalog of the named runs a deployment exposes."""

    @property
    def names(self) -> tuple[str, ...]:
        """Supported run names, in display order."""
        ...

    def spec(self, name: str, *, language: str = LANGUAGE_ZH) -> RunSpec:
        """Resolve one run name to its specification."""
        ...
