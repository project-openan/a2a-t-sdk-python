from __future__ import annotations

from dataclasses import dataclass

from .models import PromptReference

_FRONT_MATTER_OPEN = "---\n"
_FRONT_MATTER_CLOSE = "\n---\n"


class A2ATTaskPromptFormatError(ValueError):
    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


@dataclass(slots=True)
class A2ATTaskPromptMetadata:
    scenario_code: str
    language: str
    version: str
    description: str

    def to_prompt_reference(self) -> PromptReference:
        return PromptReference(
            scenario_code=self.scenario_code,
            language=self.language,
            version=self.version,
        )


def render_a2a_t_task_prompt(*, body: str, metadata: A2ATTaskPromptMetadata) -> str:
    return (
        _FRONT_MATTER_OPEN +
        f"scenario_code: {metadata.scenario_code}\n"
        f"language: {metadata.language}\n"
        f"version: {metadata.version}\n"
        f"description: {metadata.description}\n"
        "---\n\n"
        f"{body}"
    )


def parse_a2a_t_task_prompt_metadata(prompt_text: str) -> A2ATTaskPromptMetadata:
    if not prompt_text.startswith(_FRONT_MATTER_OPEN):
        raise A2ATTaskPromptFormatError("A2A-T task prompt must start with front matter.")

    closing_index = prompt_text.find(_FRONT_MATTER_CLOSE, len(_FRONT_MATTER_OPEN))
    if closing_index == -1:
        raise A2ATTaskPromptFormatError("A2A-T task prompt front matter is not closed.")

    header = prompt_text[len(_FRONT_MATTER_OPEN) : closing_index]
    metadata: dict[str, str] = {}

    for line in header.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise A2ATTaskPromptFormatError(f"Invalid front matter line: {line}")

        key, value = line.split(":", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if not normalized_key or not normalized_value:
            raise A2ATTaskPromptFormatError(f"Invalid front matter line: {line}")
        metadata[normalized_key] = normalized_value

    scenario_code = metadata.get("scenario_code")
    if not scenario_code:
        raise A2ATTaskPromptFormatError(
            "A2A-T task prompt is missing required field: scenario_code.",
            field="scenario_code",
        )

    version = metadata.get("version")
    if not version:
        raise A2ATTaskPromptFormatError(
            "A2A-T task prompt is missing required field: version.",
            field="version",
        )

    return A2ATTaskPromptMetadata(
        scenario_code=scenario_code,
        language=metadata.get("language") or "default",
        version=version,
        description=metadata.get("description") or "",
    )
