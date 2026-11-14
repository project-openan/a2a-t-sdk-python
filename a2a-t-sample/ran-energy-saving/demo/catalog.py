"""Scenario catalog of the demonstration.

The core only knows :class:`protocol.run_spec.RunSpec`; this module resolves one of the sample
scenarios to it. The example request is the single source of business values: this module only
applies the configured vendor profile to the text the page shows and the client sends.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from protocol.run_spec import ExpectedStep, RunSpec
from shared.config import DISCOVERY_NMS, SampleConfig
from shared.run_constants import LANGUAGE_EN, LANGUAGE_ZH

from demo.intent import apply_region_profile_text
from demo.scenario import (
    SCENARIO_ABORT,
    SCENARIO_REJECT,
    SCENARIO_UNKNOWN_AREA,
    SUPPORTED_SCENARIO_NAMES,
    Scenario,
    expected_negotiation_target,
    get_scenario,
    resolve_user_request,
)

#: Neutral region each scenario's example request names, per language.
_DEFAULT_NEUTRAL_REGION: Final[dict[str, str]] = {
    LANGUAGE_EN: "Region A",
    LANGUAGE_ZH: "区域 A",
}
_NEUTRAL_REGIONS: Final[dict[str, dict[str, str]]] = {
    SCENARIO_UNKNOWN_AREA: {LANGUAGE_EN: "Region Z", LANGUAGE_ZH: "未知区域"},
}

#: Default site names the site-level example requests use. They are sites the vendor NMS serves
#: (the NMS rejects any other site), so an unconfigured real-NMS run still sends a valid request.
_DEFAULT_SITES: Final[dict[str, tuple[str, ...]]] = {
    LANGUAGE_EN: ("NRES_XCS_6102937", "LTE_26441_LNR_OCL"),
    LANGUAGE_ZH: ("NRES_XCS_6102937", "LTE_26441_LNR_OCL"),
}


def profile_request_text(
    text: str,
    *,
    language: str,
    region: str = "",
    sites: tuple[str, ...] = (),
    neutral_region: str = "",
) -> str:
    """Apply the vendor profile to one example request.

    The page serves the profiled text and the client sends exactly that text, so a real-NMS run
    never shows the neutral demo names next to a vendor Task-T.
    """
    if not neutral_region:
        neutral_region = _DEFAULT_NEUTRAL_REGION.get(language, "")
    return apply_region_profile_text(
        text,
        old_region=neutral_region,
        region=region,
        old_sites=_DEFAULT_SITES.get(language, ()),
        sites=sites,
        language=language,
    )


def to_run_spec(
    scenario: Scenario,
    *,
    language: str = LANGUAGE_ZH,
    region: str = "",
    sites: tuple[str, ...] = (),
) -> RunSpec:
    """Convert one demonstration scenario into the core run specification.

    ``region``/``sites`` apply the vendor profile to the example request. The Web UI serves the
    same profiled text, and the client sends exactly that text, so the page and the wire agree.
    """
    # These scenarios deliberately name an area the network does not serve; profiling that
    # placeholder to the vendor region would turn the negotiation case into a valid request.
    # Every other scenario keeps the vendor region.
    unknown_region_scenarios = {SCENARIO_UNKNOWN_AREA, SCENARIO_REJECT, SCENARIO_ABORT}
    user_request = profile_request_text(
        resolve_user_request(scenario, language),
        language=language,
        region="" if scenario.name in unknown_region_scenarios else region,
        sites=sites,
        neutral_region=_NEUTRAL_REGIONS.get(scenario.name, _DEFAULT_NEUTRAL_REGION).get(language, ""),
    )
    return RunSpec(
        name=scenario.name,
        description=scenario.description,
        user_request=user_request,
        scripted_decisions=tuple(scenario.scripted_client_decisions),
        expected_sequence=tuple(
            ExpectedStep(actor=step.actor, performative=step.performative, round=step.round)
            for step in scenario.expected_sequence
        ),
        expected_final_state=scenario.expected_final_state,
        expected_negotiation_target=expected_negotiation_target(scenario, language),
    )


@dataclass(frozen=True)
class DemoRunCatalog:
    """Catalog over the canned scenarios of :mod:`demo.scenario`.

    Attributes:
        region: vendor region applied to every scenario request; empty keeps the neutral demo
            name.
        sites: vendor sites applied when the scenario carries a site list.
    """

    region: str = ""
    sites: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, config: SampleConfig) -> DemoRunCatalog:
        """Build the catalog with the vendor profile selected by one configuration."""
        if config.discovery != DISCOVERY_NMS:
            return cls()
        return cls(region=config.nms_region, sites=config.nms_sites)

    @property
    def names(self) -> tuple[str, ...]:
        """Supported scenario names, in display order."""
        return SUPPORTED_SCENARIO_NAMES

    def spec(self, name: str, *, language: str = LANGUAGE_ZH) -> RunSpec:
        """Resolve one scenario name to its run specification."""
        return to_run_spec(
            get_scenario(name), language=language, region=self.region, sites=self.sites
        )
