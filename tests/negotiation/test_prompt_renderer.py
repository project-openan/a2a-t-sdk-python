from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class NegotiationPromptRendererTest(unittest.TestCase):
    def test_render_start_passthroughs_content_text(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationType
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer

        renderer = NegotiationPromptRenderer()

        text = renderer.render_start(
            negotiation_type=NegotiationType.CLARIFICATION,
            message="Please clarify the request.",
        )

        self.assertEqual(text, "Please clarify the request.")

    def test_render_continue_passthroughs_content_text(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationType
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer

        renderer = NegotiationPromptRenderer()

        text = renderer.render_continue(
            negotiation_type=NegotiationType.FULFILLMENT,
            message="The result is accepted.",
        )

        self.assertEqual(text, "The result is accepted.")
