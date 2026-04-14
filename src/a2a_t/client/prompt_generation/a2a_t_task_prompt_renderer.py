from __future__ import annotations

from collections import UserDict

from a2a_t.prompt.common.a2a_t_task_prompt import A2ATTaskPromptMetadata, render_a2a_t_task_prompt


class A2ATTaskPromptRenderError(Exception):
    pass


class _StrictSlotMap(UserDict[str, str]):
    def __missing__(self, key: str) -> str:
        raise A2ATTaskPromptRenderError(f"Template references unknown slot: {key}")


class A2ATTaskPromptRenderer:
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
            raise A2ATTaskPromptRenderError(f"Template references unknown slot: {error.args[0]}") from error

        return render_a2a_t_task_prompt(
            body=body,
            metadata=A2ATTaskPromptMetadata(
                scenario_code=scenario_code,
                language=language,
                version=version,
                description=description,
            ),
        )
