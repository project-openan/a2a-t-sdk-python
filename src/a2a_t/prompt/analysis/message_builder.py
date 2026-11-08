from __future__ import annotations

import json
from collections.abc import Mapping

from a2a_t.common.prompt_resources.models import ScenarioDefinition, SlotSchema
from a2a_t.prompt.common.models import PromptReference

#: Localized headings of the template's terminology section.
#:
#: The template headings follow the template language, while the slot constraints that
#: reference this section are written against the same language; recognizing only the
#: English heading silently dropped the section for every other language and left the
#: synonyms referenced by ``[terminology]`` unresolved.
_TERMINOLOGY_HEADINGS: tuple[str, ...] = ("## Terminology Explanation", "## 术语解释")


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
        terminology section is extracted and appended as a ``[terminology]`` section
        so the model can resolve synonyms without bloating each slot's
        ``value_constraint``. The section heading is localized, so every known
        heading in :data:`_TERMINOLOGY_HEADINGS` is accepted.
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
        """Extract the terminology section from the template body.

        Returns the raw section text (header included) or an empty string when no
        known heading is present. When several headings occur, the first one wins.
        """
        starts = [index for index in (template_text.find(heading) for heading in _TERMINOLOGY_HEADINGS) if index != -1]
        if not starts:
            return ""
        rest = template_text[min(starts) :]
        next_header = rest.find("\n## ", 1)
        if next_header != -1:
            rest = rest[:next_header]
        return rest.strip()
