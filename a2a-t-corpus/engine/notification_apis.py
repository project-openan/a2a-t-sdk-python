"""Notification-T API registration (port of the Java ``ClientApis``/``ServerApis`` notification entries).

JSON step arguments are bound to the production components assembled by the corpus runtime:

- from-text generation: ``{"text", "templateUri"}`` -> ``PromptGenerationOrchestrator.
  generate_notification_prompt_from_text``;
- from-data generation: ``{"data", "schema", "templateUri"}`` -> ``PromptGenerationOrchestrator.
  generate_notification_prompt_from_data_with_schema``;
- validation: ``{"promptText", "schema", "templateUri"}`` -> the Notification-T content validator
  (``InputLimitedContentValidator`` built with ``extension_name="Notification-T"``).

The wire ``api`` names stay the Java-side camelCase facade names (shared corpus contract); this
module maps them to the snake_case orchestrator/validator methods.
"""

from __future__ import annotations

from engine.client_apis import Args, metadata_to_map
from engine.registry import ApiRegistry

from a2a_t.client.prompt_generation.prompt_generation_orchestrator import PromptGenerationOrchestrator
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.server.prompt_compliance.content_validator import InputLimitedContentValidator

__all__ = ["register_notification_apis"]


def register_notification_apis(
    registry: ApiRegistry,
    client: PromptGenerationOrchestrator,
    notification_validator: InputLimitedContentValidator,
) -> None:
    """Register the full Notification-T phase-1 API set of one assembled runtime."""
    registry.register(
        "generateNotificationPromptFromText",
        lambda args: metadata_to_map(
            client.generate_notification_prompt_from_text(
                Args.text(args, "text"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )
    registry.register(
        "generateNotificationPromptFromDataWithSchema",
        lambda args: metadata_to_map(
            client.generate_notification_prompt_from_data_with_schema(
                Args.map(args, "data"),
                Args.map(args, "schema"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )
    registry.register(
        "validateNotificationPromptAndDataFilling",
        lambda args: _filled_to_map(
            notification_validator.validate(
                Args.text(args, "promptText"),
                Args.map(args, "schema"),
                Args.template_uri(args, "templateUri"),
            )
        ),
    )


def _filled_to_map(filled: FilledParamData) -> dict[str, object]:
    """Serialize one filled-parameter result into the transcript payload shape."""
    return {"data": dict(filled.data)}
