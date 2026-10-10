from __future__ import annotations

from a2a_t.observability.config import A2ATObservabilityConfig


def test_public_fields_defaults(monkeypatch) -> None:
    from a2a_t.observability.config import A2ATObservabilityConfig

    monkeypatch.delenv("A2AT_OBSERVABILITY_ENABLED", raising=False)
    monkeypatch.delenv("A2AT_TRACE_ENABLED", raising=False)
    monkeypatch.delenv("A2AT_METRIC_ENABLED", raising=False)
    monkeypatch.delenv("A2AT_LOG_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("A2AT_EXPORTER_PROTOCOL", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    c = A2ATObservabilityConfig()
    assert c.enabled is True
    assert c.trace_enabled is True
    assert c.metric_enabled is True
    assert c.log_enabled is True
    assert c.endpoint is None
    assert c.protocol == "grpc"
    assert c.service_name is None


def test_env_fills_unset(monkeypatch) -> None:
    from a2a_t.observability.config import A2ATObservabilityConfig

    monkeypatch.setenv("A2AT_OBSERVABILITY_ENABLED", "false")
    monkeypatch.setenv("A2AT_TRACE_ENABLED", "false")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://c:4317")
    monkeypatch.setenv("A2AT_EXPORTER_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "my-agent")
    c = A2ATObservabilityConfig()
    assert c.enabled is False
    assert c.trace_enabled is False
    assert c.endpoint == "http://c:4317"
    assert c.protocol == "http/protobuf"
    assert c.service_name == "my-agent"


def test_explicit_beats_env(monkeypatch) -> None:
    from a2a_t.observability.config import A2ATObservabilityConfig

    monkeypatch.setenv("A2AT_OBSERVABILITY_ENABLED", "true")
    c = A2ATObservabilityConfig(enabled=False)
    assert c.enabled is False


def test_internal_regex_fields() -> None:
    from a2a_t.observability.config import A2ATObservabilityConfig

    c = A2ATObservabilityConfig(
        task_type_regex=r"任务类型[:：]\s*(\S+)",
        notification_topic_regex=r"通知主题[:：]\s*(\S+)",
        authorization_policy_operation_type_regex=r"操作类型[:：]\s*(\S+)",
    )
    assert c.task_type_regex is not None
    assert c.invoke_task_type_provider is not None  # method exists from v2


def test_authorization_tail_cleanup_and_v3_key() -> None:
    from a2a_t.observability.attributes import ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE

    c = A2ATObservabilityConfig(
        authorization_policy_operation_type_regex=r"##\s*授权策略的操作类型\s*\n+\s*(\S[^\n]*)"
    )
    view = {"k": "## 授权策略的操作类型\n新增授权策略（必填）\n"}
    result = c.invoke_authorization_provider(view)
    assert result is not None
    assert result[ATTR_AUTHORIZATION_POLICY_OPERATION_TYPE] == "新增授权策略"


def test_task_type_multiline_heading_extraction() -> None:
    c = A2ATObservabilityConfig(task_type_regex=r"##\s*任务类型\(Task Type\)\s*\n+\s*(\S[^\n]*)")
    view = {"prompt": "## 任务类型(Task Type)\n\n新增基站开通\n\n要求\n"}
    assert c.invoke_task_type_provider(view) == "新增基站开通"
