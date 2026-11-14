"""Core addressing and validation primitives (port of the Java ``a2a-t-core`` module).

Provides template URI addressing (:class:`~a2a_t.core.template_uri.TemplateUri`
and the dual-spelled built-in template constants), the path-segment guards used
by every resource lookup, prompt resource keys, the metadata model of generated
messages and the content validation pipeline. The error catalog, message
rendering and exception tree live in the subpackage :mod:`a2a_t.core.errors`
and are intentionally not re-exported here.
"""

from __future__ import annotations

from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    TEMPLATE_URI_METADATA_KEY,
    MetadataContent,
    NegotiationContext,
    NegotiationPerformative,
    decode_extension_value,
    extension_value,
)
from a2a_t.core.path_segments import (
    is_simple_segment,
    require_simple_relative_path,
    require_simple_segment,
)
from a2a_t.core.prompt_resource_key import PromptResourceKey
from a2a_t.core.template_uri import DEFAULT_TEMPLATE_VERSION, TemplateUri
from a2a_t.core.validation_pipeline import (
    RETRYABLE_ERROR_CODES,
    ContentValidationError,
    ContentValidator,
    FilledParamData,
    RuleChecker,
    SemanticValidator,
    SlotValidationError,
    TemplateContentLoader,
    ValidationPipeline,
    ValidationResult,
    with_retry,
)

__all__ = [
    "DEFAULT_TEMPLATE_VERSION",
    "NEGOTIATION_CONTEXT_METADATA_KEY",
    "PromptResourceKey",
    "RETRYABLE_ERROR_CODES",
    "TEMPLATE_URI_METADATA_KEY",
    "ContentValidationError",
    "ContentValidator",
    "FilledParamData",
    "MetadataContent",
    "NegotiationContext",
    "NegotiationPerformative",
    "RuleChecker",
    "SemanticValidator",
    "SlotValidationError",
    "TemplateContentLoader",
    "TemplateUri",
    "ValidationPipeline",
    "ValidationResult",
    "decode_extension_value",
    "extension_value",
    "is_simple_segment",
    "require_simple_relative_path",
    "require_simple_segment",
    "with_retry",
]
