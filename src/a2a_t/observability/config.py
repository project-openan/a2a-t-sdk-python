"""A2ATObservabilityConfig: public switches (explicit > env > default) + internal settings.

Public fields (7): ``enabled`` (env A2AT_OBSERVABILITY_ENABLED, default true),
``trace_enabled`` / ``metric_enabled`` / ``log_enabled`` (env A2AT_TRACE_ENABLED /
A2AT_METRIC_ENABLED / A2AT_LOG_ENABLED, default true), ``endpoint`` (env
OTEL_EXPORTER_OTLP_ENDPOINT, None → Console fallback), ``protocol`` (env
A2AT_EXPORTER_PROTOCOL, default "grpc") and ``service_name`` (env OTEL_SERVICE_NAME,
None → "a2a-t-agent" at use site). Explicit constructor arguments always beat env;
unset fields are resolved from env in ``__post_init__`` so attribute access always
yields a concrete value.

Internal (non-exported) fields carry the business regexes, payload-extraction
switches and redaction callback; they stay None-sentinels with lazily-resolved
``resolved_*`` properties so env changes remain monkeypatch-friendly after
construction. The ``invoke_*`` methods keep the v2 guarded-invocation surface,
now backed by the configured regexes.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from a2a_t.observability.attributes import ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE

logger = logging.getLogger("a2at.observability")

AttributeValueProvider = Callable[[Mapping[str, Any]], "str | None"]
AuthorizationProvider = Callable[[Mapping[str, Any]], "Mapping[str, str] | None"]


def _to_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return _to_bool(raw)


@dataclass
class A2ATObservabilityConfig:
    """Configuration of the A2A-T observability adapters (explicit > env > default)."""

    enabled: bool | None = None
    trace_enabled: bool | None = None
    metric_enabled: bool | None = None
    log_enabled: bool | None = None
    endpoint: str | None = None
    protocol: str | None = None
    service_name: str | None = None

    task_type_regex: str | None = None
    notification_topic_regex: str | None = None
    authorization_policy_operation_type_regex: str | None = None
    extract_request: bool | None = None
    extract_response: bool | None = None
    payload_log_enabled: bool | None = None
    payload_log_max_length: int | None = None
    payload_redactor: Callable[[str], str] | None = None

    def __post_init__(self) -> None:
        if self.enabled is None:
            self.enabled = _env_bool("A2AT_OBSERVABILITY_ENABLED", True)
        if self.trace_enabled is None:
            self.trace_enabled = _env_bool("A2AT_TRACE_ENABLED", True)
        if self.metric_enabled is None:
            self.metric_enabled = _env_bool("A2AT_METRIC_ENABLED", True)
        if self.log_enabled is None:
            self.log_enabled = _env_bool("A2AT_LOG_ENABLED", True)
        if self.endpoint is None:
            self.endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or None
        if self.protocol is None:
            self.protocol = os.getenv("A2AT_EXPORTER_PROTOCOL", "").strip() or "grpc"
        if self.service_name is None:
            self.service_name = os.getenv("OTEL_SERVICE_NAME") or None

    @property
    def resolved_extract_request(self) -> bool:
        if self.extract_request is not None:
            return self.extract_request
        return _to_bool(os.getenv("A2AT_EXTRACT_REQUEST", "false"))

    @property
    def resolved_extract_response(self) -> bool:
        if self.extract_response is not None:
            return self.extract_response
        return _to_bool(os.getenv("A2AT_EXTRACT_RESPONSE", "false"))

    @property
    def resolved_payload_log_enabled(self) -> bool:
        if self.payload_log_enabled is not None:
            return self.payload_log_enabled
        return _to_bool(os.getenv("A2AT_LOGS_PAYLOAD_ENABLED", "false"))

    @property
    def resolved_payload_log_max_length(self) -> int:
        if self.payload_log_max_length is not None:
            return self.payload_log_max_length
        try:
            return int(os.getenv("A2AT_LOGS_PAYLOAD_MAX_LENGTH", "4096"))
        except ValueError:
            return 4096

    def _invoke(self, name: str, provider: Callable[..., Any] | None, view: Mapping[str, Any]) -> Any:
        if provider is None:
            return None
        try:
            return provider(view)
        except Exception:  # noqa: BLE001 - providers never break the business flow
            logger.warning("%s raised; attribute omitted", name, exc_info=True)
            return None

    def _regex_provider(self, name: str, pattern: str | None) -> AttributeValueProvider | None:
        if pattern is None:
            return None

        def provider(view: Mapping[str, Any]) -> str | None:
            for value in view.values():
                if not isinstance(value, str):
                    continue
                match = re.search(pattern, value)
                if match is not None:
                    return match.group(1) if match.groups() else match.group(0)
            return None

        return provider

    def invoke_task_type_provider(self, view: Mapping[str, Any]) -> str | None:
        provider = self._regex_provider("task_type_regex", self.task_type_regex)
        result = self._invoke("task_type_provider", provider, view)
        return result if isinstance(result, str) else None

    def invoke_notification_topic_provider(self, view: Mapping[str, Any]) -> str | None:
        provider = self._regex_provider("notification_topic_regex", self.notification_topic_regex)
        result = self._invoke("notification_topic_provider", provider, view)
        return result if isinstance(result, str) else None

    def invoke_authorization_provider(self, view: Mapping[str, Any]) -> dict[str, str] | None:
        provider = self._regex_provider(
            "authorization_policy_operation_type_regex", self.authorization_policy_operation_type_regex
        )
        result = self._invoke("authorization_provider", provider, view)
        if isinstance(result, str):
            cleaned = re.sub(r"（[^）]*）\s*$", "", result).strip()
            return {ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE: cleaned} if cleaned else None
        return None
