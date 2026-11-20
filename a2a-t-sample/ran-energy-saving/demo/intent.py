"""Business intent understanding of the RAN energy-saving demonstration.

The operator's request is the single input: the client sends exactly that text to the A2A-T
pipeline. This module only *reads* it — the ADK intent agent parses the business fields for the
intent panel and the approval step, and never rewrites the request. There is no built-in intent
table and no text-to-intent mapping: known scenarios are ordinary example requests, and their
deliberate gaps or conflicts come from the text itself.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Final, Protocol

from google.adk.apps import App
from google.adk.events import Event as AdkEvent
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types
from protocol.intent import (
    INTENT_SOURCE_MODEL,
    IntentProcessor,
    OperatorRequest,
    ProcessedIntent,
)
from shared.config import SampleConfig
from shared.json_text import iter_json_objects
from shared.model_factory import build_traced_adk_model
from shared.run_constants import LANGUAGE_EN, LANGUAGE_ZH

from demo.adk_fake_model import build_fake_model
from demo.agents.intent_agent import BusinessIntent, build_intent_agent

_LOGGER = logging.getLogger("ran_energy_saving.intent")

#: ADK application name of the intent agent.
INTENT_APP_NAME: Final[str] = "ran_energy_intent"

#: ADK user id used for every intent turn.
INTENT_USER_ID: Final[str] = "ran-energy-operator"

#: Generic site tokens a raw request may use for the n-th site, per language. The default example
#: requests name real vendor sites, but a replayed or structured request may still say "Site 1" or
#: "Station 1"; both must follow the configured profile.
_SITE_TOKENS: Final[dict[str, tuple[str, ...]]] = {
    LANGUAGE_EN: ("Station {n}", "station {n}", "Site {n}", "site {n}"),
    LANGUAGE_ZH: ("站点名称{n}", "站点 {n}", "站点{n}"),
}


class IntentExtractionError(RuntimeError):
    """Raised when the intent agent returns no parseable business intent."""


class IntentExtractor(Protocol):
    """One business-intent reading of an operator request (LLM/ADK backed in production)."""

    async def extract(self, request: OperatorRequest) -> ProcessedIntent:
        """Return the business reading of ``request`` without changing its text."""
        ...


def apply_region_profile_text(
    text: str,
    *,
    old_region: str,
    region: str,
    old_sites: tuple[str, ...] = (),
    sites: tuple[str, ...] = (),
    language: str = LANGUAGE_EN,
) -> str:
    """Replace the neutral demo region/site names with the configured vendor profile.

    The example request and the Task-T must name the same vendor values. This helper only renders
    the example text once; the client then sends that exact text, so the page and the wire agree.
    """
    if not text:
        return text
    updated = text
    if old_region and region and old_region != region:
        updated = updated.replace(old_region, region)
    if old_sites and sites and old_sites != sites:
        tokens = _SITE_TOKENS.get(language, _SITE_TOKENS[LANGUAGE_EN])
        for index, (old_site, new_site) in enumerate(zip(old_sites, sites), start=1):
            if old_site and old_site != new_site:
                updated = updated.replace(old_site, new_site)
            for token in sorted(tokens, key=len, reverse=True):
                updated = updated.replace(token.format(n=index), new_site)
    return updated


class AdkIntentExtractor:
    """Parse one operator request with the ADK intent agent."""

    def __init__(
        self,
        agent: Any,
        *,
        app_name: str = INTENT_APP_NAME,
        user_id: str = INTENT_USER_ID,
    ) -> None:
        """Create one extractor over a built intent agent.

        Args:
            agent: ADK agent whose ``output_schema`` is :class:`BusinessIntent`.
            app_name: ADK application name.
            user_id: ADK user id of every intent turn.
        """
        app = App(name=app_name, root_agent=agent)
        self._runner = Runner(
            app=app,
            app_name=app_name,
            session_service=InMemorySessionService(),
            auto_create_session=True,
        )
        self._user_id = user_id

    async def extract(self, request: OperatorRequest) -> ProcessedIntent:
        """Run the intent agent over the operator's text and return its business reading.

        Raises:
            IntentExtractionError: when the agent produces no parseable :class:`BusinessIntent`.
        """
        final_event: AdkEvent | None = None
        async for event in self._runner.run_async(
            user_id=self._user_id,
            session_id=f"intent-{uuid.uuid4().hex}",
            new_message=types.Content(role="user", parts=[types.Part(text=request.text)]),
        ):
            if event.is_final_response():
                final_event = event
        intent = _intent_from_event(final_event)
        if intent is None:
            raise IntentExtractionError("The intent agent returned no parseable BusinessIntent.")
        return _processed_intent(request.text, intent)


class DemoIntentProcessor:
    """Sample intent processor: an ADK/LLM reading of the operator's request."""

    def __init__(
        self,
        *,
        language: str,
        extractor: IntentExtractor | None = None,
    ) -> None:
        """Create one processor.

        Args:
            language: process-level language.
            extractor: ADK intent extractor; ``None`` (offline mode) keeps the raw text.
        """
        self._language = language
        self._extractor = extractor

    async def process(self, request: OperatorRequest) -> ProcessedIntent:
        """Understand one operator request without changing its text."""
        if self._extractor is None:
            return ProcessedIntent.raw(
                request.text,
                reason="No live model is configured for intent extraction.",
            )
        try:
            return await self._extractor.extract(request)
        except Exception as exc:  # noqa: BLE001 - the intent layer must not break the run
            _LOGGER.warning("intent extraction failed; falling back to raw text: %s", exc)
            return ProcessedIntent.raw(
                request.text,
                reason=f"Intent extraction failed: {type(exc).__name__}: {exc}",
            )


def build_demo_intent_processor(config: SampleConfig) -> IntentProcessor:
    """Build the intent processor of one demo run, or of the OSS Agent."""
    model = build_traced_adk_model(config, offline_model_factory=build_fake_model)
    return DemoIntentProcessor(
        language=config.language,
        extractor=AdkIntentExtractor(build_intent_agent(model)),
    )


def _intent_from_event(event: AdkEvent | None) -> BusinessIntent | None:
    """Read a :class:`BusinessIntent` from one ADK final event, ``None`` when absent."""
    if event is None:
        return None
    output = event.output
    if isinstance(output, BusinessIntent):
        return output
    if isinstance(output, dict):
        try:
            return BusinessIntent.model_validate(output)
        except ValueError:
            return None
    content = event.content
    if content is None:
        return None
    for part in content.parts or ():
        if not part.text:
            continue
        for payload, _raw in iter_json_objects(part.text):
            try:
                return BusinessIntent.model_validate(payload)
            except (ValueError, TypeError):
                continue
    return None


def _processed_intent(text: str, intent: BusinessIntent) -> ProcessedIntent:
    """Convert one parsed business intent into the protocol-neutral reading.

    ``normalized_text`` / ``plain_text`` are the operator's own text: the intent panel shows the
    parsed fields separately, and the client sends exactly what the operator wrote.
    """
    return ProcessedIntent(
        raw_request=text,
        normalized_text=text,
        plain_text=text,
        operation_type=intent.operation_type,
        region=intent.region.strip(),
        sites=_strings(intent.sites),
        windows=_strings(intent.windows),
        energy_saving_goal=intent.energy_saving_goal.strip(),
        task_description=intent.task_description.strip(),
        expected_output=intent.expected_output.strip(),
        background_event=(intent.background_event or "").strip() or None,
        missing_fields=_strings(intent.missing_fields),
        conflicts=_strings(intent.conflicts),
        source=INTENT_SOURCE_MODEL,
    )


def _strings(values: object) -> tuple[str, ...]:
    """Return a tuple of non-empty stripped strings."""
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(value.strip() for value in values if isinstance(value, str) and value.strip())


__all__ = [
    "AdkIntentExtractor",
    "DemoIntentProcessor",
    "IntentExtractionError",
    "IntentExtractor",
    "apply_region_profile_text",
    "build_demo_intent_processor",
]
