"""Run specification and the demo/production seams of the client runtime.

A client run needs three things the core cannot own:

* the natural-language request and the expected negotiation sequence (a scenario catalog);
* the deterministic decision script used in offline mode;
* the business values filled into an accepted Task-T (a client policy).

The composition root (the demo launcher, the Web UI, the gateway CLI or a customer service) supplies
them; ``protocol`` and ``shared`` only see these interfaces.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from shared.models import NegotiationDecision
from shared.run_constants import LANGUAGE_ZH

from protocol.intent import ProcessedIntent


@dataclass(frozen=True)
class ExpectedStep:
    """One expected performative of a run, used by the UI and the acceptance tests."""

    actor: str
    performative: str
    round: int | None = None


def _no_filled_params(kind: str, language: str) -> dict[str, object]:
    """Default policy: an accept carries no business values."""
    del kind, language
    return {}


def _no_accept_text(kind: str, language: str) -> str:
    """Default policy: the accept text is derived from the decision rationale."""
    del kind, language
    return ""


def _no_initial_data(
    language: str,
) -> tuple[Mapping[str, object], Mapping[str, object]] | None:
    """Default policy: generate the initial Task-T from the natural-language request."""
    del language
    return None


def _no_follow_up_data(
    language: str,
) -> tuple[Mapping[str, object], Mapping[str, object]] | None:
    """Default policy: the run is single-phase and sends no Modify follow-up Task-T."""
    del language
    return None


@dataclass(frozen=True)
class ClientPolicy:
    """Business values of one OSS integration, supplied by the composition root.

    Attributes:
        filled_params: values filled into the Task-T when the client accepts a proposal.
        accept_text: natural-language accept confirmation, when the integration provides one.
        initial_data: structured Task-T values plus their data schema for integrations whose
            initial request is already complete; returning ``None`` keeps the text-extraction path.
        follow_up_data: structured Task-T values plus their data schema for a real Modify follow-up
            on the Task created by the initial phase; returning ``None`` keeps the run single-phase.
    """

    filled_params: Callable[[str, str], dict[str, object]] = _no_filled_params
    accept_text: Callable[[str, str], str | None] = _no_accept_text
    initial_data: Callable[[str], tuple[Mapping[str, object], Mapping[str, object]] | None] = (
        _no_initial_data
    )
    follow_up_data: Callable[
        [str], tuple[Mapping[str, object], Mapping[str, object]] | None
    ] = _no_follow_up_data


DEFAULT_CLIENT_POLICY: ClientPolicy = ClientPolicy()


@dataclass(frozen=True)
class RunSpec:
    """Everything the core needs to know about one named run.

    Attributes:
        name: scenario/run name used by the UI and the CLI.
        description: one-line description shown by the UI.
        user_request: natural-language request resolved for the process language.
        scripted_decisions: decisions replayed by the scripted provider and the offline model.
        expected_sequence: expected performative sequence (UI hints and acceptance tests).
        policy: business values filled on accept.
        expected_final_state: terminal state the run must reach, when the catalog provides one.
        expected_negotiation_target: guaranteed-throughput target the negotiation must agree on,
            when the scenario ends with a completed task.
        intent: built-in business intent of the scenario, used when the operator did not override
            the canned request.
    """

    name: str
    description: str
    user_request: str
    scripted_decisions: tuple[NegotiationDecision, ...] = ()
    expected_sequence: tuple[ExpectedStep, ...] = ()
    policy: ClientPolicy = DEFAULT_CLIENT_POLICY
    expected_final_state: str | None = None
    expected_negotiation_target: str | None = None
    intent: ProcessedIntent | None = None


@runtime_checkable
class RunCatalog(Protocol):
    """Catalog of the named runs a deployment exposes."""

    @property
    def names(self) -> tuple[str, ...]:
        """Supported run names, in display order."""
        ...

    def spec(self, name: str, *, language: str = LANGUAGE_ZH) -> RunSpec:
        """Resolve one run name to its specification.

        Raises:
            KeyError: when the name is unknown.
        """
        ...


@dataclass(frozen=True)
class EmptyRunCatalog:
    """Catalog used when a deployment did not configure one; every lookup fails loudly."""

    @property
    def names(self) -> tuple[str, ...]:
        """No run is available."""
        return ()

    def spec(self, name: str, *, language: str = LANGUAGE_ZH) -> RunSpec:
        """Reject every lookup with the missing-catalog explanation."""
        del language
        raise KeyError(f"Unknown run {name!r}: no run catalog is configured.")
