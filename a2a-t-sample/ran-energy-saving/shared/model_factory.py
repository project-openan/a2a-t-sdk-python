"""ADK model construction.

Configuration stays a pure value object; building a model always targets the configured
OpenAI-compatible endpoint.
"""

from __future__ import annotations

from typing import Any

from google.adk.models.base_llm import BaseLlm

from shared.config import (
    ADK_MODEL_NAME_ENV_KEY,
    ADK_OUTPUT_SCHEMA_WITH_TOOLS_FALSE,
    ADK_OUTPUT_SCHEMA_WITH_TOOLS_TRUE,
    SampleConfig,
    load_config,
)

#: LiteLlm subclasses keyed by the forced schema+tools capability.
_CAPABILITY_MODEL_CLASSES: dict[bool, Any] = {}


def _capability_model_class(allow_schema_with_tools: bool) -> Any:
    """Return a ``LiteLlm`` subclass whose schema+tools capability is fixed.

    ADK decides the request shape from ``model.capabilities.output_schema_and_tools``: when it is
    false, the response schema is exposed as the synthetic ``set_model_response`` tool instead of a
    ``response_format`` field. Some OpenAI-compatible endpoints (Qwen) accept tools only when no
    response format is present, so the deployment can force the fallback path.
    """
    cached = _CAPABILITY_MODEL_CLASSES.get(allow_schema_with_tools)
    if cached is not None:
        return cached
    from google.adk.models import LlmCapabilities
    from google.adk.models.lite_llm import LiteLlm

    class _CapabilityLiteLlm(LiteLlm):
        @property
        def capabilities(self) -> LlmCapabilities:
            return LlmCapabilities(output_schema_and_tools=allow_schema_with_tools)

    _CAPABILITY_MODEL_CLASSES[allow_schema_with_tools] = _CapabilityLiteLlm
    return _CapabilityLiteLlm


def build_adk_model(config: SampleConfig | None = None) -> BaseLlm:
    """Build the ADK model from the sample configuration.

    Args:
        config: resolved sample configuration; defaults to :func:`load_config`.

    Returns:
        The configured OpenAI-compatible ADK model.

    Raises:
        ValueError: when live mode has no model name.
    """
    resolved = config or load_config()
    if not resolved.adk_model_name:
        raise ValueError(f"{ADK_MODEL_NAME_ENV_KEY} must be non-empty")
    from google.adk.models.lite_llm import LiteLlm

    kwargs: dict[str, Any] = {
        # LiteLlm forwards these to litellm. Keep every ADK turn inside the same per-call budget
        # as the SDK structured calls, and disable LiteLLM's own retries so the client budget
        # stays predictable (one call cannot silently become three attempts).
        "timeout": resolved.llm_timeout_seconds,
        "num_retries": 0,
    }
    if resolved.adk_model_api_base:
        kwargs["api_base"] = resolved.adk_model_api_base
    if resolved.adk_model_api_key:
        kwargs["api_key"] = resolved.adk_model_api_key
    model_class: Any = LiteLlm
    if resolved.adk_model_output_schema_with_tools == ADK_OUTPUT_SCHEMA_WITH_TOOLS_FALSE:
        model_class = _capability_model_class(False)
    elif resolved.adk_model_output_schema_with_tools == ADK_OUTPUT_SCHEMA_WITH_TOOLS_TRUE:
        model_class = _capability_model_class(True)
    return model_class(model=resolved.adk_model_name, **kwargs)


def build_traced_adk_model(
    config: SampleConfig | None = None,
    *,
    model: BaseLlm | None = None,
) -> BaseLlm:
    """Build the ADK model wrapped in the Run Explorer tracer.

    Production entry points use this builder so every ADK turn produces an ``llm`` span. The
    wrapped model still reports the same ``model`` id and ``capabilities``; an explicit ``model``
    override (tests) is wrapped as-is, and an already-traced model is returned unchanged.

    Args:
        config: resolved sample configuration; defaults to :func:`load_config`.
        model: optional model override (tests).
    """
    from shared.adk_tracing import TracingLlm

    resolved = (
        model
        if model is not None
        else build_adk_model(config)
    )
    if isinstance(resolved, TracingLlm):
        return resolved
    return TracingLlm.wrap(resolved)
