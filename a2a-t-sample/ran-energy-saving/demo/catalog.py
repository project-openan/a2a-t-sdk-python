"""Scenario catalog and client policy of the demonstration.

The core only knows :class:`protocol.run_spec.RunSpec`; this module resolves one of the sample
scenarios to it, including the business values filled into an accepted Task-T.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from protocol.run_spec import ClientPolicy, ExpectedStep, RunSpec
from shared.run_constants import LANGUAGE_ZH

from demo.scenario import (
    SCENARIO_UNKNOWN_AREA,
    SUPPORTED_SCENARIO_NAMES,
    Scenario,
    accept_confirmation_text,
    accept_filled_params,
    complete_initial_params,
    expected_negotiation_target,
    get_scenario,
    resolve_user_request,
    task_t_data_schema,
    unknown_area_accept_params,
)

#: Business values of the sample OSS: the negotiated Task-T parameters and its accept text.
DEMO_CLIENT_POLICY: ClientPolicy = ClientPolicy(
    filled_params=accept_filled_params,
    accept_text=accept_confirmation_text,
)


def to_run_spec(scenario: Scenario, *, language: str = LANGUAGE_ZH) -> RunSpec:
    """Convert one demonstration scenario into the core run specification."""
    initial = complete_initial_params(scenario.name, language)
    policy = DEMO_CLIENT_POLICY
    if initial is not None:
        # A complete request must never open a negotiation. Generate its Task-T from explicit
        # values (still through the SDK's LLM extraction) instead of free-text slot extraction,
        # which a live model can leave incomplete.
        schema = task_t_data_schema(language)
        values = dict(initial)
        policy = replace(DEMO_CLIENT_POLICY, initial_data=lambda _language: (values, schema))
    if scenario.name == SCENARIO_UNKNOWN_AREA:
        # Only the area was wrong. The accept confirms a served area and keeps the requested
        # throughput windows instead of replacing them with the generic base target.
        policy = replace(
            policy,
            filled_params=lambda _kind, language: unknown_area_accept_params(language),
        )
    return RunSpec(
        name=scenario.name,
        description=scenario.description,
        user_request=resolve_user_request(scenario, language),
        scripted_decisions=tuple(scenario.scripted_client_decisions),
        expected_sequence=tuple(
            ExpectedStep(actor=step.actor, performative=step.performative, round=step.round)
            for step in scenario.expected_sequence
        ),
        policy=policy,
        expected_final_state=scenario.expected_final_state,
        expected_negotiation_target=expected_negotiation_target(scenario, language),
    )


@dataclass(frozen=True)
class DemoRunCatalog:
    """Catalog over the five canned scenarios of :mod:`demo.scenario`."""

    @property
    def names(self) -> tuple[str, ...]:
        """Supported scenario names, in display order."""
        return SUPPORTED_SCENARIO_NAMES

    def spec(self, name: str, *, language: str = LANGUAGE_ZH) -> RunSpec:
        """Resolve one scenario name to its run specification."""
        return to_run_spec(get_scenario(name), language=language)
