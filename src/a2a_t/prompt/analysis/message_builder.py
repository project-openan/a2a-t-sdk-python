from __future__ import annotations

import json
from collections.abc import Mapping

from a2a_t.common.prompt_resources.models import ScenarioDefinition, SlotSchema
from a2a_t.prompt.common.models import PromptReference


class AnalysisMessageBuilder:
    """Build LLM message payloads for prompt analysis workflows."""

    def build_scenario_recognition_messages(
        self,
        *,
        normalized_input: str,
        scenarios: list[ScenarioDefinition],
        language: str,
        system_prompt: str,
        user_prompt: str,
    ) -> list[dict[str, str]]:
        """Build the structured message list used for scenario recognition."""
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
        slot_schema: SlotSchema,
        system_prompt: str,
        user_prompt: str,
        template_text: str | None = None,
        data_schema: Mapping[str, object] | None = None,
    ) -> list[dict[str, str]]:
        """Build the structured message list used for slot extraction.

        The optional ``data_schema`` (field name to description) appends one ``[data_schema]``
        section describing the meaning of each structured input field — the schema-guided
        extraction variant of the Java ``DefaultStructuredPromptSlotValueExtractor``.

        The optional ``template_text`` carries the scenario template body; its
        ``## Terminology Explanation`` section is extracted and appended as a
        ``[terminology]`` section so the model can resolve synonyms without bloating
        each slot's ``value_constraint``.
        """
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
        sections = [
            f"[user_prompt]\n{user_prompt}",
            f"[scenario_code]\n{reference.scenario_code}",
            f"[language]\n{reference.language}",
            f"[input]\n{normalized_input}",
            "[slots]\n" + "\n".join(slot_lines),
        ]
        if template_text:
            terminology = self._extract_terminology(template_text)
            if terminology:
                sections.append(f"[terminology]\n{terminology}")
        if data_schema:
            sections.append(f"[data_schema]\n{json.dumps(dict(data_schema), ensure_ascii=False)}")
        content = "\n\n".join(sections)
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

    @staticmethod
    def _extract_terminology(template_text: str) -> str:
        """Extract the Terminology Explanation section from the template body.

        Returns the raw section text (header included) or an empty string when
        the section is absent.
        """
        marker = "## Terminology Explanation"
        start = template_text.find(marker)
        if start == -1:
            return ""
        rest = template_text[start:]
        next_header = rest.find("\n## ", 1)
        if next_header != -1:
            rest = rest[:next_header]
        return rest.strip()
