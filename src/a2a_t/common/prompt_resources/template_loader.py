from __future__ import annotations

from a2a_t.prompt.common.models import PromptReference

from .local_resources import BasePromptResourceLoader


class TemplateLoader(BasePromptResourceLoader):
    """Load scenario-specific task prompt templates."""

    def load(self, *, reference: PromptReference) -> str:
        """Return the template text for the referenced scenario resource."""
        path = f"templates/{reference.scenario_code}/{reference.language}/template.md"
        return self._read_text_with_fallback(path)
