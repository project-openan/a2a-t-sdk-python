from __future__ import annotations

from a2a_t.server.prompt_compliance.errors import ProcessedPromptParseError
from a2a_t.server.prompt_compliance.models import PromptIdentity


class ProcessedPromptParser:
    """Parse prompt identity from processed prompt front matter."""

    def parse(self, processed_prompt_text: str) -> PromptIdentity:
        if not processed_prompt_text.startswith("---\n"):
            raise ProcessedPromptParseError("Processed prompt must start with front matter.")

        closing_index = processed_prompt_text.find("\n---\n", 4)
        if closing_index == -1:
            raise ProcessedPromptParseError("Processed prompt front matter is not closed.")

        header = processed_prompt_text[4:closing_index]
        metadata: dict[str, str] = {}

        for line in header.splitlines():
            if not line.strip():
                continue
            if ":" not in line:
                raise ProcessedPromptParseError(f"Invalid front matter line: {line}")

            key, value = line.split(":", 1)
            normalized_key = key.strip()
            normalized_value = value.strip()
            if not normalized_key or not normalized_value:
                raise ProcessedPromptParseError(f"Invalid front matter line: {line}")
            metadata[normalized_key] = normalized_value

        scenario_code = metadata.get("scenario_code")
        if not scenario_code:
            raise ProcessedPromptParseError(
                "Processed prompt is missing required field: scenario_code.",
                field="scenario_code",
            )

        version = metadata.get("version")
        if not version:
            raise ProcessedPromptParseError("Processed prompt is missing required field: version.", field="version")

        language = metadata.get("language") or "default"
        return PromptIdentity(scenario_code=scenario_code, language=language, version=version)
