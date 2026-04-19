from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.common.models import PromptReference


class A2ATTaskPromptCommonTest(unittest.TestCase):
    def test_render_and_parse_a2a_t_task_prompt_share_one_protocol(self) -> None:
        from a2a_t.prompt.common.task_prompt_format import (
            TaskPromptMetadata,
            format_task_prompt,
            parse_task_prompt_metadata,
        )

        prompt_text = format_task_prompt(
            body="Site: Site A",
            metadata=TaskPromptMetadata(
                scenario_code="energy_saving",
                language="en-US",
                version="0.0.1",
                description="Used for energy saving analysis.",
            ),
        )
        metadata = parse_task_prompt_metadata(prompt_text)

        self.assertEqual(
            metadata,
            TaskPromptMetadata(
                scenario_code="energy_saving",
                language="en-US",
                version="0.0.1",
                description="Used for energy saving analysis.",
            ),
        )
        self.assertEqual(
            metadata.to_prompt_reference(),
            PromptReference(scenario_code="energy_saving", language="en-US", version="0.0.1"),
        )

    def test_parse_rejects_missing_language(self) -> None:
        from a2a_t.prompt.common.task_prompt_format import TaskPromptFormatError, parse_task_prompt_metadata

        with self.assertRaises(TaskPromptFormatError) as context:
            parse_task_prompt_metadata(
                "---\n"
                "scenario_code: energy_saving\n"
                "version: 0.0.1\n"
                "description: Used for energy saving analysis.\n"
                "---\n\n"
                "Site: Site A"
            )

        self.assertEqual(context.exception.field, "language")

    def test_parse_rejects_missing_description(self) -> None:
        from a2a_t.prompt.common.task_prompt_format import TaskPromptFormatError, parse_task_prompt_metadata

        with self.assertRaises(TaskPromptFormatError) as context:
            parse_task_prompt_metadata(
                "---\n"
                "scenario_code: energy_saving\n"
                "language: en-US\n"
                "version: 0.0.1\n"
                "---\n\n"
                "Site: Site A"
            )

        self.assertEqual(context.exception.field, "description")

    def test_parse_rejects_blank_language(self) -> None:
        from a2a_t.prompt.common.task_prompt_format import TaskPromptFormatError, parse_task_prompt_metadata

        with self.assertRaises(TaskPromptFormatError) as context:
            parse_task_prompt_metadata(
                "---\n"
                "scenario_code: energy_saving\n"
                "language:    \n"
                "version: 0.0.1\n"
                "description: Used for energy saving analysis.\n"
                "---\n\n"
                "Site: Site A"
            )

        self.assertEqual(context.exception.field, "language")

    def test_parse_rejects_blank_description(self) -> None:
        from a2a_t.prompt.common.task_prompt_format import TaskPromptFormatError, parse_task_prompt_metadata

        with self.assertRaises(TaskPromptFormatError) as context:
            parse_task_prompt_metadata(
                "---\n"
                "scenario_code: energy_saving\n"
                "language: en-US\n"
                "version: 0.0.1\n"
                "description:    \n"
                "---\n\n"
                "Site: Site A"
            )

        self.assertEqual(context.exception.field, "description")


if __name__ == "__main__":
    unittest.main()
