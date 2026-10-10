"""A2A-T observability module (v3.0 decorator architecture).

Design spec: docs/superpowers/specs/2026-09-11-a2at-observability-sdk-design.md (§5.1).

Structural core: importing this package never imports a2a-sdk — the decorators
wrap a2a objects structurally (guarded getattr/setattr against the inner
handler/transport/queue), and OpenTelemetry imports are lazy inside functions
(see ``_otel_compat``). The public exports below are the exact spec §5.1 set
("nothing more, nothing less"); the contract is enforced by
``tests/observability/test_public_api.py``.
"""

from a2a_t.observability.attributes import (
    ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE,
    ATTR_EXTENSION_NAME,
    ATTR_GEN_AI_CONVERSATION_ID,
    ATTR_GEN_AI_OPERATION_NAME,
    ATTR_GEN_AI_PROVIDER_NAME,
    ATTR_GEN_AI_REQUEST_MODEL,
    ATTR_GEN_AI_RESPONSE_MODEL,
    ATTR_GEN_AI_TOKEN_TYPE,
    ATTR_GEN_AI_USAGE_INPUT_TOKENS,
    ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
    ATTR_NEGOTIATION_ID,
    ATTR_NEGOTIATION_MAX_ROUNDS,
    ATTR_NEGOTIATION_PERFORMATIVE,
    ATTR_NEGOTIATION_ROUND,
    ATTR_NEGOTIATION_TOTAL_ROUNDS,
    ATTR_NOTIFICATION_TOPIC,
    ATTR_PUSH_NOTIFICATION_URL,
    ATTR_STREAMING,
    ATTR_STREAMING_EVENT_KIND,
    ATTR_TASK_ID,
    ATTR_TASK_STATUS,
    ATTR_TASK_TYPE,
)
from a2a_t.observability.client.factory import register_client_factory
from a2a_t.observability.config import A2ATObservabilityConfig
from a2a_t.observability.current_span import A2ATCurrentSpan, current_span
from a2a_t.observability.propagation import (
    TRACEPARENT_HEADER,
    extract_trace_context,
    inject_traceparent,
)
from a2a_t.observability.sdk_trace import trace_facade
from a2a_t.observability.server.handler_decorator import A2ATRequestHandlerDecorator
from a2a_t.observability.setup import is_otel_configured, setup
from a2a_t.observability.span import A2ATSpan

__all__ = [
    # OTel configuration (§5.1 Exporter-自动配置)
    "setup",
    "is_otel_configured",
    # Integration (§5.1 Trace-集成)
    "register_client_factory",
    "A2ATRequestHandlerDecorator",
    # Current span (§5.1 Trace-执行代码内)
    "current_span",
    "A2ATCurrentSpan",
    # Manual span (§5.1 Trace-手动 span)
    "A2ATSpan",
    # Facade tracing (§5.1 Trace-门面追踪)
    "trace_facade",
    # W3C TraceContext propagation
    "inject_traceparent",
    "extract_trace_context",
    "TRACEPARENT_HEADER",
    # Configuration
    "A2ATObservabilityConfig",
    # A2A-T span attribute constants (17, spec §4/§5.1)
    "ATTR_EXTENSION_NAME",
    "ATTR_TASK_ID",
    "ATTR_TASK_STATUS",
    "ATTR_TASK_TYPE",
    "ATTR_NEGOTIATION_ID",
    "ATTR_NEGOTIATION_ROUND",
    "ATTR_NEGOTIATION_MAX_ROUNDS",
    "ATTR_NEGOTIATION_PERFORMATIVE",
    "ATTR_NEGOTIATION_TOTAL_ROUNDS",
    "ATTR_NOTIFICATION_TOPIC",
    "ATTR_STREAMING_EVENT_KIND",
    "ATTR_PUSH_NOTIFICATION_URL",
    "ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE",
    "ATTR_GEN_AI_OPERATION_NAME",
    "ATTR_GEN_AI_CONVERSATION_ID",
    "ATTR_STREAMING",
    # GenAI attribute constants (6, spec §5.1 常量-GenAI)
    "ATTR_GEN_AI_USAGE_INPUT_TOKENS",
    "ATTR_GEN_AI_USAGE_OUTPUT_TOKENS",
    "ATTR_GEN_AI_REQUEST_MODEL",
    "ATTR_GEN_AI_RESPONSE_MODEL",
    "ATTR_GEN_AI_TOKEN_TYPE",
    "ATTR_GEN_AI_PROVIDER_NAME",
]
