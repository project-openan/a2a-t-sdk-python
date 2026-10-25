"""Scenario discovery of the corpus (port of the Java ``ScenarioScanner``).

A scenario is any directory under the extension's ``resources/`` folder that carries the requested
case file (``input_case_from_text.json`` or ``input_case_from_data.json``). New scenarios therefore
require zero code changes. Python resolves from the source tree only: there is no classpath
concept, so discovery is a plain directory scan.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Scenario", "ScenarioScanner", "corpus_root"]

#: The corpus module directory (``a2a-t-corpus/``), the anchor of every path convention.
def corpus_root() -> Path:
    """Return the corpus module directory."""
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Scenario:
    """One discovered scenario: directory name plus writable source directory carrying the case files."""

    name: str
    source_dir: Path

    def input_file(self, flow_file_name: str) -> Path:
        """Return the path of the requested case file inside this scenario directory."""
        return self.source_dir / flow_file_name


class ScenarioScanner:
    """Discovers corpus scenarios by directory convention and filters them by glob."""

    @staticmethod
    def discover_optional(extension_folder: str, flow_file_name: str) -> list[Scenario]:
        """List matching scenarios, returning an empty list when the case file exists nowhere.

        The optional variant serves the language-specific case selection: a language without any
        committed case file is a visible skip, not a collection failure.
        """
        source_tree = corpus_root() / "suites" / extension_folder / "resources"
        scenarios: list[Scenario] = []
        if source_tree.is_dir():
            for directory in source_tree.iterdir():
                if directory.is_dir() and (directory / flow_file_name).is_file():
                    scenarios.append(Scenario(directory.name, directory))
        scenarios.sort(key=lambda scenario: scenario.name)
        return scenarios

    @staticmethod
    def discover(extension_folder: str, flow_file_name: str) -> list[Scenario]:
        """List every scenario under ``suites/<extension>/resources/`` carrying the requested case file."""
        source_tree = corpus_root() / "suites" / extension_folder / "resources"
        scenarios = ScenarioScanner.discover_optional(extension_folder, flow_file_name)
        if not scenarios:
            raise RuntimeError(
                "No corpus scenario directories found: expected "
                f"suites/{extension_folder}/resources/<scenario>/{flow_file_name} (source tree "
                f"{source_tree} does not contain any match)"
            )
        return scenarios

    @staticmethod
    def filter_scenarios(scenarios: list[Scenario], filter_value: str | None) -> list[Scenario]:
        """Keep the scenarios whose name matches any of the comma-separated glob patterns."""
        if not filter_value:
            return scenarios
        filtered: list[Scenario] = []
        for pattern in filter_value.split(","):
            for scenario in scenarios:
                if fnmatch.fnmatch(scenario.name, pattern.strip()) and scenario not in filtered:
                    filtered.append(scenario)
        return filtered

    @staticmethod
    def matches_case_filter(case_filter: str | None, case_id: str) -> bool:
        """Return whether one case id matches the optional glob filter (no filter matches everything)."""
        if not case_filter:
            return True
        return fnmatch.fnmatch(case_id, case_filter.strip())
