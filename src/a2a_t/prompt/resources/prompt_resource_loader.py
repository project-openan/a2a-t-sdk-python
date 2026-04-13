from __future__ import annotations

from ._base import BasePromptResourceLoader
from .models import PromptMessages


class PromptResourceLoader(BasePromptResourceLoader):
    def load(self, *, analysis_action: str, version: str, language: str) -> PromptMessages:
        prompt_dir = self.root_dir / "prompts" / analysis_action / version / language
        return PromptMessages(
            system_prompt=self._read_text(prompt_dir / "system.md"),
            user_prompt=self._read_text(prompt_dir / "user.md"),
        )
