from __future__ import annotations

from collections import UserDict


class PromptRenderError(Exception):
    pass


class _StrictSlotMap(UserDict[str, str]):
    def __missing__(self, key: str) -> str:
        raise PromptRenderError(f"Template references unknown slot: {key}")


class PromptRenderer:
    def render(
        self,
        *,
        template_text: str,
        slots: dict[str, str | None],
        scenario_code: str,
        language: str,
        version: str,
        description: str,
    ) -> str:
        normalized_slots = _StrictSlotMap({key: "" if value is None else value for key, value in slots.items()})
        try:
            body = template_text.format_map(normalized_slots)
        except KeyError as error:
            raise PromptRenderError(f"Template references unknown slot: {error.args[0]}") from error

        return (
            "---\n"
            f"scenario_code: {scenario_code}\n"
            f"language: {language}\n"
            f"version: {version}\n"
            f"description: {description}\n"
            "---\n\n"
            f"{body}"
        )
