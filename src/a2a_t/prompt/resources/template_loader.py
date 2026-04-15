from __future__ import annotations

from a2a_t.prompt.common.models import PromptReference

from ._base import BasePromptResourceLoader


class TemplateLoader(BasePromptResourceLoader):
    def load(self, *, reference: PromptReference) -> str:
        path = f"templates/{reference.scenario_code}/{reference.version}/{reference.language}/template.md"
        return self._read_text(path)
