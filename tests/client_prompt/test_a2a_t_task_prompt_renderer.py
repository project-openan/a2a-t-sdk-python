from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class A2ATTaskPromptRendererTest(unittest.TestCase):
    def test_shared_task_prompt_renderer_builds_markdown_prompt_with_front_matter(self) -> None:
        from a2a_t.prompt.common.task_prompt_format import TaskPromptMetadata, format_task_prompt
        from a2a_t.prompt.task_rendering import TaskPromptRenderer

        renderer = TaskPromptRenderer()
        prompt_text = renderer.render(
            template_text="Site: {site}\nNotes: {additional_notes}",
            slots={"site": "Site A", "additional_notes": None},
            scenario_code="energy_saving",
            language="en-US",
            version="0.0.1",
            description="Used for energy saving analysis.",
        )

        self.assertEqual(
            prompt_text,
            format_task_prompt(
                body="Site: Site A\nNotes: ",
                metadata=TaskPromptMetadata(
                    scenario_code="energy_saving",
                    language="en-US",
                    version="0.0.1",
                    description="Used for energy saving analysis.",
                ),
            ),
        )

    def test_render_raises_when_template_references_unknown_slot(self) -> None:
        from a2a_t.prompt.task_rendering import TaskPromptRenderError
        from a2a_t.prompt.task_rendering import TaskPromptRenderer

        renderer = TaskPromptRenderer()

        with self.assertRaises(TaskPromptRenderError):
            renderer.render(
                template_text="Site: {site}\nTime Range: {time_range}",
                slots={"site": "Site A"},
                scenario_code="energy_saving",
                language="en-US",
                version="0.0.1",
                description="Used for energy saving analysis.",
            )

if __name__ == "__main__":
    unittest.main()
