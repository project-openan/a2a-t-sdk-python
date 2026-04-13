from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PromptRendererTest(unittest.TestCase):
    def test_render_builds_markdown_prompt_with_front_matter(self) -> None:
        from a2a_t.client.prompt.renderer import PromptRenderer

        renderer = PromptRenderer()
        prompt_text = renderer.render(
            template_text="Site: {site}\nNotes: {additional_notes}",
            slots={"site": "Site A", "additional_notes": None},
            scenario_code="energy_saving",
            language="en-US",
            version="0.0.1",
            description="Used for energy saving analysis.",
        )

        self.assertIn("---\nscenario_code: energy_saving\nlanguage: en-US\nversion: 0.0.1\ndescription: Used for energy saving analysis.\n---\n", prompt_text)
        self.assertTrue(prompt_text.endswith("Site: Site A\nNotes: "))

    def test_render_raises_when_template_references_unknown_slot(self) -> None:
        from a2a_t.client.prompt.renderer import PromptRenderer
        from a2a_t.client.prompt.renderer import PromptRenderError

        renderer = PromptRenderer()

        with self.assertRaises(PromptRenderError):
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
