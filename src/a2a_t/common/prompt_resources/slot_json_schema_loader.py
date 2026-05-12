from __future__ import annotations

from a2a_t.prompt.common.models import PromptReference

from .local_resources import BasePromptResourceLoader
from .errors import PromptResourceParseError


class SlotJsonSchemaLoader(BasePromptResourceLoader):
    """Load slot validation schemas used by server-side compliance validation."""

    def load(self, *, reference: PromptReference) -> dict[str, object]:
        """Return a JSON Schema object for the referenced scenario resource."""
        path = f"slots/{reference.scenario_code}/{reference.language}/slot.json"
        data = self._read_json_with_fallback(path)
        if self._looks_like_json_schema(data):
            return dict(data)
        raise PromptResourceParseError(
            "slot.json must be a JSON Schema object.",
            path=path,
            scenario_code=reference.scenario_code,
            language=reference.language,
        )

    @staticmethod
    def _looks_like_json_schema(data: dict[str, object]) -> bool:
        """Return whether the loaded payload already looks like a JSON Schema object."""
        return "slots" not in data and data.get("type") == "object" and "properties" in data
