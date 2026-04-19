from __future__ import annotations

from a2a_t.prompt.common.models import PromptReference
from a2a_t.common.prompt_resources.models import ScenarioDefinition, SlotSchema


class AnalysisMessageBuilder:
    def build_scenario_recognition_messages(
        self,
        *,
        normalized_input: str,
        scenarios: list[ScenarioDefinition],
        language: str,
        system_prompt: str,
        user_prompt: str,
    ) -> list[dict[str, str]]:
        scenario_lines = [
            (
                f"- scenario_code: {scenario.scenario_code}\n"
                f"  scenario_name: {scenario.scenario_name}\n"
                f"  description: {scenario.description}\n"
                f"  example: {scenario.example}"
            )
            for scenario in scenarios
        ]
        content = "\n\n".join(
            [
                f"[user_prompt]\n{user_prompt}",
                f"[language]\n{language}",
                f"[input]\n{normalized_input}",
                "[scenarios]\n" + "\n".join(scenario_lines),
            ]
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

    def build_slot_extraction_messages(
        self,
        *,
        normalized_input: str,
        reference: PromptReference,
        template_text: str,
        slot_schema: SlotSchema,
        system_prompt: str,
        user_prompt: str,
    ) -> list[dict[str, str]]:
        slot_lines = [
            (
                f"- name: {slot.name}\n"
                f"  required: {slot.required}\n"
                f"  description: {slot.description}\n"
                f"  example: {slot.example}\n"
                f"  value_constraint: {slot.value_constraint}"
            )
            for slot in slot_schema.slots
        ]
        content = "\n\n".join(
            [
                f"[user_prompt]\n{user_prompt}",
                f"[scenario_code]\n{reference.scenario_code}",
                f"[version]\n{reference.version}",
                f"[language]\n{reference.language}",
                f"[input]\n{normalized_input}",
                f"[template]\n{template_text}",
                "[slots]\n" + "\n".join(slot_lines),
            ]
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]
