from __future__ import annotations

from ._base import BasePromptResourceLoader


class TemplateLoader(BasePromptResourceLoader):
    def load(self, *, scenario_code: str, version: str, language: str) -> str:
        path = self.root_dir / "templates" / scenario_code / version / language / "template.md"
        return self._read_text(path)
