from __future__ import annotations

from ._base import BasePromptResourceLoader
from .models import ScenarioDefinition


class ScenarioLoader(BasePromptResourceLoader):
    def load(self, *, version: str, language: str) -> list[ScenarioDefinition]:
        path = f"scenarios/{version}/{language}/scenarios.json"
        data = self._read_json(path)
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
