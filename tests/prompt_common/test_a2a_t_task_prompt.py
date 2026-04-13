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
        from a2a_t.prompt.common.a2a_t_task_prompt import (
            A2ATTaskPromptMetadata,
            parse_a2a_t_task_prompt_metadata,
            render_a2a_t_task_prompt,
        )

        prompt_text = render_a2a_t_task_prompt(
            body="Site: Site A",
            metadata=A2ATTaskPromptMetadata(
                scenario_code="energy_saving",
                language="en-US",
                version="0.0.1",
                description="Used for energy saving analysis.",
            ),
        )
        metadata = parse_a2a_t_task_prompt_metadata(prompt_text)

        self.assertEqual(
            metadata,
            A2ATTaskPromptMetadata(
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


if __name__ == "__main__":
    unittest.main()
