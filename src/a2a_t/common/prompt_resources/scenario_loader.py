from __future__ import annotations

from .local_resources import BasePromptResourceLoader
from .models import ScenarioDefinition


class ScenarioLoader(BasePromptResourceLoader):
    """Load scenario definitions for scenario recognition."""

    def load(self, *, language: str) -> list[ScenarioDefinition]:
        """Return all scenario definitions for the requested language."""
        path = f"scenarios/{language}/scenarios.json"
        data = self._read_json_with_fallback(path)
        scenarios = data.get("scenarios") or []

        return [
            ScenarioDefinition(
                scenario_code=str(item["scenario_code"]),
                scenario_name=str(item["scenario_name"]),
                description=str(item["description"]),
                example=str(item["example"]),
            )
            for item in scenarios
        ]
