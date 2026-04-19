from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class FakePromptResourceLoader:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def load(self, *, analysis_action: str, version: str, language: str):
        from a2a_t.prompt.resources.models import PromptMessages

        self.calls.append(
            {
                "analysis_action": analysis_action,
                "version": version,
                "language": language,
            }
        )
        return PromptMessages(
            system_prompt="System side={role}",
            user_prompt="User status={status} content={content_text} facts={facts_json}",
        )


class FallbackPromptResourceLoader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def load(self, *, analysis_action: str, version: str, language: str):
        from a2a_t.prompt.resources.errors import PromptResourceNotFoundError
        from a2a_t.prompt.resources.models import PromptMessages

        self.calls.append((analysis_action, language))
        if language != "en-US":
            raise PromptResourceNotFoundError(f"missing {language}")
        return PromptMessages(
            system_prompt="Fallback system",
            user_prompt="Fallback user {content_text}",
        )


class NegotiationPromptRendererTest(unittest.TestCase):
    def _context(self):
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext

        return NegotiationContext(
            negotiation_type=NegotiationType.CLARIFICATION,
            negotiation_id="neg-1",
            role=NegotiationRole.CLIENT,
            round=1,
            status=NegotiationStatus.IN_PROGRESS,
            extra={},
        )

    def test_render_start_loads_type_prompt_resource_and_formats_text(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationType
        from a2a_t.negotiation.common.models import StartNegotiationInput
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer

        loader = FakePromptResourceLoader()
        renderer = NegotiationPromptRenderer(
            prompt_resource_loader=loader,
            version="0.0.1",
            language="en-US",
        )

        text = renderer.render_start(
            input=StartNegotiationInput(
                type=NegotiationType.CLARIFICATION,
                content_text="Please clarify the request.",
                facts={"clarificationItems": [{"name": "intent"}]},
            ),
            context=self._context(),
        )

        self.assertEqual(loader.calls[0]["analysis_action"], "clarification_negotiation")
        self.assertIn("System side=client", text)
        self.assertIn("User status=in-progress content=Please clarify the request.", text)
        self.assertIn("```negotiation-json", text)

    def test_render_continue_loads_prompt_resource_by_negotiation_type(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationStatus, NegotiationType
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer

        loader = FakePromptResourceLoader()
        renderer = NegotiationPromptRenderer(
            prompt_resource_loader=loader,
            version="0.0.1",
            language="en-US",
        )

        text = renderer.render_continue(
            negotiation_type=NegotiationType.FULFILLMENT,
            context=self._context(),
            status=NegotiationStatus.AGREED,
            content_text="The result is accepted.",
            facts={},
        )

        self.assertEqual(loader.calls[0]["analysis_action"], "fulfillment_negotiation")
        self.assertIn("User status=agreed content=The result is accepted.", text)

    def test_render_start_falls_back_to_en_us_when_requested_language_is_missing(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationType
        from a2a_t.negotiation.common.models import StartNegotiationInput
        from a2a_t.negotiation.rendering.negotiation_prompt_renderer import NegotiationPromptRenderer

        loader = FallbackPromptResourceLoader()
        renderer = NegotiationPromptRenderer(
            prompt_resource_loader=loader,
            version="0.0.1",
            language="zh-CN",
        )

        text = renderer.render_start(
            input=StartNegotiationInput(
                type=NegotiationType.CLARIFICATION,
                content_text="Please clarify the request.",
                facts={},
            ),
            context=self._context(),
        )

        self.assertEqual(
            loader.calls,
            [
                ("clarification_negotiation", "zh-CN"),
                ("clarification_negotiation", "en-US"),
            ],
        )
        self.assertIn("Fallback system", text)
