"""Tests for the unified sample configuration and model selection."""

from __future__ import annotations

from pathlib import Path

import pytest
from shared.config import (
    ADK_MODEL_API_BASE_ENV_KEY,
    ADK_MODEL_API_KEY_ENV_KEY,
    ADK_MODEL_NAME_ENV_KEY,
    ADK_MODEL_OUTPUT_SCHEMA_WITH_TOOLS_ENV_KEY,
    ADK_OUTPUT_SCHEMA_WITH_TOOLS_FALSE,
    ADK_OUTPUT_SCHEMA_WITH_TOOLS_TRUE,
    ADK_SESSION_RETENTION_DAYS_ENV_KEY,
    CLIENT_DB_ENV_KEY,
    CLIENT_MEMORY_TURNS_ENV_KEY,
    CLIENT_MIN_THROUGHPUT_ENV_KEY,
    DECISION_RECOMMENDATION_TIMEOUT_ENV_KEY,
    DEFAULT_ADK_SESSION_RETENTION_DAYS,
    DEFAULT_DECISION_RECOMMENDATION_TIMEOUT_SECONDS,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_LLM_MAX_ATTEMPTS,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    DEFAULT_MANUAL_DECISION_TIMEOUT_SECONDS,
    DEFAULT_PLANNER_MAX_STEPS,
    DEFAULT_WEBHOOK_IDLE_TIMEOUT,
    GATEWAY_PORT_ENV_KEY,
    GATEWAY_SCENARIO_ENV_KEY,
    GATEWAY_TASK_DB_ENV_KEY,
    HTTP_TIMEOUT_ENV_KEY,
    INTENT_MODE_ENV_KEY,
    LANGFUSE_BASE_URL_ENV_KEY,
    LANGFUSE_CAPTURE_CONTENT_ENV_KEY,
    LANGFUSE_ENABLED_ENV_KEY,
    LANGFUSE_ENVIRONMENT_ENV_KEY,
    LANGFUSE_FLUSH_AT_ENV_KEY,
    LANGFUSE_PUBLIC_KEY_ENV_KEY,
    LANGFUSE_SECRET_KEY_ENV_KEY,
    LANGFUSE_UI_BASE_URL_ENV_KEY,
    LLM_MAX_ATTEMPTS_ENV_KEY,
    LLM_TIMEOUT_ENV_KEY,
    LONG_TASK_TRACKING_ENV_KEY,
    MANUAL_DECISION_TIMEOUT_ENV_KEY,
    MODEL_MODE_LIVE,
    NMS_A2A_BASE_ENV_KEY,
    NMS_A2A_PORT_ENV_KEY,
    NMS_HOST_ENV_KEY,
    NMS_IDLE_TIMEOUT_ENV_KEY,
    NMS_INFER_NEGOTIATION_KIND_ENV_KEY,
    NMS_POLL_AFTER_ENV_KEY,
    NMS_READ_AFTER_SEND_ENV_KEY,
    NMS_REGION_ENV_KEY,
    NMS_REQUEST_TIMEOUT_ENV_KEY,
    NMS_SCHEME_ENV_KEY,
    NMS_SEND_TIMEOUT_ENV_KEY,
    NMS_SITES_ENV_KEY,
    NMS_STATE_POLL_ENV_KEY,
    NMS_TOKEN_PORT_ENV_KEY,
    NMS_TOKEN_URL_ENV_KEY,
    NMS_TOKEN_USER_ENV_KEY,
    NMS_TOKEN_VALUE_ENV_KEY,
    NMS_VERIFY_TLS_ENV_KEY,
    NMS_WIRE_LOG_ENV_KEY,
    PLANNER_MAX_STEPS_ENV_KEY,
    PLANNER_MODEL_ENV_KEY,
    PLANNER_TIMEOUT_ENV_KEY,
    PROTOCOL_MODE_ENV_KEY,
    PURE_FOLLOWUP_ENV_KEY,
    PURE_HOP_DIRECT,
    PURE_HOP_ENV_KEY,
    PURE_HOP_NMS,
    PURE_MESSAGE_MODE_ENV_KEY,
    RUN_DIR_ENV_KEY,
    RUNS_DIR_ENV_KEY,
    SERVER_AUTH_TOKEN_ENV_KEY,
    TASK_DB_ENV_KEY,
    TASK_POLL_INTERVAL_ENV_KEY,
    TRACE_DIR_ENV_KEY,
    TRACE_TOKEN_ENV_KEY,
    TRACE_URL_ENV_KEY,
    WEBHOOK_AUTH_CREDENTIALS_ENV_KEY,
    WEBHOOK_PUBLIC_URL_ENV_KEY,
    WEBHOOK_TLS_CERT_ENV_KEY,
    WEBHOOK_TLS_KEY_ENV_KEY,
    SampleConfig,
    load_config,
)
from shared.model_factory import build_adk_model, build_traced_adk_model

_ENV_KEYS = (
    ADK_MODEL_NAME_ENV_KEY,
    ADK_MODEL_API_BASE_ENV_KEY,
    ADK_MODEL_API_KEY_ENV_KEY,
    ADK_MODEL_OUTPUT_SCHEMA_WITH_TOOLS_ENV_KEY,
    "A2AT_SAMPLE_PRE_SEND_VALIDATION",
    HTTP_TIMEOUT_ENV_KEY,
    LLM_TIMEOUT_ENV_KEY,
    LLM_MAX_ATTEMPTS_ENV_KEY,
    LONG_TASK_TRACKING_ENV_KEY,
    TASK_POLL_INTERVAL_ENV_KEY,
    NMS_POLL_AFTER_ENV_KEY,
    NMS_STATE_POLL_ENV_KEY,
    TASK_DB_ENV_KEY,
    CLIENT_DB_ENV_KEY,
    CLIENT_MEMORY_TURNS_ENV_KEY,
    ADK_SESSION_RETENTION_DAYS_ENV_KEY,
    GATEWAY_PORT_ENV_KEY,
    GATEWAY_SCENARIO_ENV_KEY,
    GATEWAY_TASK_DB_ENV_KEY,
    SERVER_AUTH_TOKEN_ENV_KEY,
    PROTOCOL_MODE_ENV_KEY,
    TRACE_URL_ENV_KEY,
    TRACE_TOKEN_ENV_KEY,
    TRACE_DIR_ENV_KEY,
    WEBHOOK_PUBLIC_URL_ENV_KEY,
    WEBHOOK_TLS_CERT_ENV_KEY,
    WEBHOOK_TLS_KEY_ENV_KEY,
    WEBHOOK_AUTH_CREDENTIALS_ENV_KEY,
    PLANNER_MODEL_ENV_KEY,
    PLANNER_TIMEOUT_ENV_KEY,
    PLANNER_MAX_STEPS_ENV_KEY,
    NMS_HOST_ENV_KEY,
    NMS_SCHEME_ENV_KEY,
    NMS_A2A_PORT_ENV_KEY,
    NMS_TOKEN_PORT_ENV_KEY,
    NMS_A2A_BASE_ENV_KEY,
    NMS_TOKEN_URL_ENV_KEY,
    NMS_TOKEN_USER_ENV_KEY,
    NMS_TOKEN_VALUE_ENV_KEY,
    NMS_VERIFY_TLS_ENV_KEY,
    NMS_IDLE_TIMEOUT_ENV_KEY,
    NMS_REQUEST_TIMEOUT_ENV_KEY,
    NMS_REGION_ENV_KEY,
    NMS_SITES_ENV_KEY,
    NMS_WIRE_LOG_ENV_KEY,
    LANGFUSE_BASE_URL_ENV_KEY,
    LANGFUSE_CAPTURE_CONTENT_ENV_KEY,
    LANGFUSE_ENABLED_ENV_KEY,
    LANGFUSE_ENVIRONMENT_ENV_KEY,
    LANGFUSE_FLUSH_AT_ENV_KEY,
    LANGFUSE_PUBLIC_KEY_ENV_KEY,
    LANGFUSE_SECRET_KEY_ENV_KEY,
    LANGFUSE_UI_BASE_URL_ENV_KEY,
    RUNS_DIR_ENV_KEY,
    PURE_FOLLOWUP_ENV_KEY,
    PURE_MESSAGE_MODE_ENV_KEY,
    "A2AT_OSP_JUDGE_MODEL",
    "A2AT_OSP_JUDGE_API_BASE",
    "A2AT_OSP_JUDGE_API_KEY",
    "A2AT_OSP_JUDGE_TIMEOUT_SECONDS",
    "A2AT_OSP_JUDGE_MAX_TOKENS",
)


@pytest.fixture(autouse=True)
def _clear_timeout_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the process environment from leaking file-based values into the tests."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_offline_config_loads_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_MODEL_MODE=live",
                "A2AT_LANGUAGE=en-US",
                "A2AT_SAMPLE_HOST=0.0.0.0",
                "A2AT_SAMPLE_PORT=8100",
                "A2AT_SAMPLE_WEBHOOK_PORT=0",
                "A2AT_SAMPLE_WEBHOOK_TOKEN=token-123",
                "A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT=15",
                "ADK_MODEL_NAME=openai/test-model",
                "ADK_MODEL_API_BASE=http://127.0.0.1:1234/v1",
                "ADK_MODEL_API_KEY=secret",
                "A2AT_SAMPLE_DISCOVERY=direct",
                "A2AT_SAMPLE_TRANSPORT=webhook",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.model_mode == MODEL_MODE_LIVE
    assert config.model_label.startswith("live (")
    assert config.language == "en-US"
    assert config.host == "0.0.0.0"
    assert config.port == 8100
    assert config.webhook_port == 0
    assert config.webhook_token == "token-123"
    assert config.webhook_idle_timeout == 15
    assert config.discovery == "direct"
    assert config.transport == "webhook"


def test_topology_and_trace_keys_load_from_the_env_file(tmp_path: Path) -> None:
    """A manual start must read the per-run ``.env``, not only the process environment.

    The launcher writes these keys into the per-run file. The server/OSS entry points used to read
    raw ``os.environ``, so a manually started process silently used the default protocol mode,
    callback host/port and no trace emitter.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_MODEL_MODE=live",
                f"{PROTOCOL_MODE_ENV_KEY}=both",
                f"{TRACE_URL_ENV_KEY}=http://127.0.0.1:8080",
                f"{TRACE_TOKEN_ENV_KEY}=trace-token",
                f"{TRACE_DIR_ENV_KEY}={tmp_path / 'traces'}",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.protocol_mode == "both"
    assert config.trace_url == "http://127.0.0.1:8080"
    assert config.trace_token == "trace-token"
    assert config.trace_dir == str(tmp_path / "traces")


def test_an_unsupported_protocol_mode_fails_loudly(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{PROTOCOL_MODE_ENV_KEY}=both-and-more\n", encoding="utf-8")

    with pytest.raises(ValueError, match=PROTOCOL_MODE_ENV_KEY):
        load_config(env_path=env_file)


def test_task_db_path_is_optional_and_must_be_absolute(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{TASK_DB_ENV_KEY}={tmp_path / 'tasks.sqlite3'}\n", encoding="utf-8")

    config = load_config(env_path=env_file)

    assert config.task_db_path == str(tmp_path / "tasks.sqlite3")

    env_file.write_text(f"{TASK_DB_ENV_KEY}=relative/tasks.sqlite3\n", encoding="utf-8")
    with pytest.raises(ValueError, match=TASK_DB_ENV_KEY):
        load_config(env_path=env_file)

    env_file.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")
    assert load_config(env_path=env_file).task_db_path is None


def test_client_db_gateway_and_planner_settings(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"{CLIENT_DB_ENV_KEY}={tmp_path / 'client.sqlite3'}",
                f"{CLIENT_MEMORY_TURNS_ENV_KEY}=3",
                f"{CLIENT_MIN_THROUGHPUT_ENV_KEY}=2.5",
                f"{ADK_SESSION_RETENTION_DAYS_ENV_KEY}=30",
                f"{GATEWAY_PORT_ENV_KEY}=8101",
                f"{GATEWAY_SCENARIO_ENV_KEY}=target",
                f"{GATEWAY_TASK_DB_ENV_KEY}={tmp_path / 'gateway-tasks.sqlite3'}",
                f"{SERVER_AUTH_TOKEN_ENV_KEY}=server-secret",
                f"{PLANNER_MODEL_ENV_KEY}=planner-model",
                f"{PLANNER_TIMEOUT_ENV_KEY}=22",
                f"{PLANNER_MAX_STEPS_ENV_KEY}=7",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.client_db_path == str(tmp_path / "client.sqlite3")
    assert config.client_memory_turns == 3
    assert config.client_min_throughput_mbps == 2.5
    assert config.adk_session_retention_days == 30
    assert config.gateway_port == 8101
    assert config.gateway_scenario == "target"
    assert config.gateway_task_db_path == str(tmp_path / "gateway-tasks.sqlite3")
    assert config.server_auth_token == "server-secret"
    assert config.planner_model == "planner-model"
    assert config.planner_timeout_seconds == 22.0
    assert config.planner_max_steps == 7


def test_planner_defaults_follow_the_adk_endpoint(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ADK_MODEL_NAME=openai/mass/volcengine/deepseek-v4.1-flash",
                "ADK_MODEL_API_BASE=http://127.0.0.1:31943/v1",
                "ADK_MODEL_API_KEY=not-needed",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.planner_model == "mass/volcengine/deepseek-v4.1-flash"
    assert config.planner_api_base == "http://127.0.0.1:31943/v1"
    assert config.planner_api_key == "not-needed"
    assert config.planner_max_steps == DEFAULT_PLANNER_MAX_STEPS


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (CLIENT_DB_ENV_KEY, "relative/client.sqlite3"),
        (CLIENT_MEMORY_TURNS_ENV_KEY, "-1"),
        (CLIENT_MIN_THROUGHPUT_ENV_KEY, "0"),
        (ADK_SESSION_RETENTION_DAYS_ENV_KEY, "0"),
        (PLANNER_MAX_STEPS_ENV_KEY, "0"),
    ],
)
def test_client_gateway_and_planner_reject_invalid_values(tmp_path: Path, key: str, value: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}={value}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=key):
        load_config(env_path=env_file)


def test_missing_env_file_uses_offline_defaults(tmp_path: Path) -> None:
    config = load_config(env_path=tmp_path / "missing.env")

    assert config.model_mode == MODEL_MODE_LIVE
    assert config.http_timeout_seconds == DEFAULT_HTTP_TIMEOUT_SECONDS == 300.0
    assert config.llm_timeout_seconds == DEFAULT_LLM_TIMEOUT_SECONDS == 180.0
    assert config.llm_max_attempts == DEFAULT_LLM_MAX_ATTEMPTS == 1
    assert (
        config.manual_decision_timeout_seconds
        == DEFAULT_MANUAL_DECISION_TIMEOUT_SECONDS
        == 300.0
    )
    assert (
        config.decision_recommendation_timeout_seconds
        == DEFAULT_DECISION_RECOMMENDATION_TIMEOUT_SECONDS
        == 120.0
    )
    assert config.webhook_idle_timeout == DEFAULT_WEBHOOK_IDLE_TIMEOUT == 300
    assert DEFAULT_WEBHOOK_IDLE_TIMEOUT == int(DEFAULT_HTTP_TIMEOUT_SECONDS)
    assert config.adk_session_retention_days == DEFAULT_ADK_SESSION_RETENTION_DAYS == 7


def test_timeout_budgets_are_read_from_the_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"{HTTP_TIMEOUT_ENV_KEY}=240",
                f"{LLM_TIMEOUT_ENV_KEY}=12.5",
                f"{LLM_MAX_ATTEMPTS_ENV_KEY}=3",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.http_timeout_seconds == 240.0
    assert config.llm_timeout_seconds == 12.5
    assert config.llm_max_attempts == 3


@pytest.mark.parametrize("key", [HTTP_TIMEOUT_ENV_KEY, LLM_TIMEOUT_ENV_KEY])
@pytest.mark.parametrize("value", ["not-a-number", "0", "-1", "nan", "inf"])
def test_timeout_budgets_reject_non_positive_values(tmp_path: Path, key: str, value: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}={value}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=key):
        load_config(env_path=env_file)


def test_decision_budgets_are_read_from_the_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"{MANUAL_DECISION_TIMEOUT_ENV_KEY}=45",
                f"{DECISION_RECOMMENDATION_TIMEOUT_ENV_KEY}=20",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.manual_decision_timeout_seconds == 45.0
    assert config.decision_recommendation_timeout_seconds == 20.0


def test_the_recommendation_budget_must_be_shorter_than_the_manual_window(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"{MANUAL_DECISION_TIMEOUT_ENV_KEY}=30",
                f"{DECISION_RECOMMENDATION_TIMEOUT_ENV_KEY}=30",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=DECISION_RECOMMENDATION_TIMEOUT_ENV_KEY):
        load_config(env_path=env_file)


@pytest.mark.parametrize(
    "key",
    [MANUAL_DECISION_TIMEOUT_ENV_KEY, DECISION_RECOMMENDATION_TIMEOUT_ENV_KEY],
)
@pytest.mark.parametrize("value", ["not-a-number", "0", "-1", "nan", "inf"])
def test_decision_budgets_reject_non_positive_values(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}={value}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=key):
        load_config(env_path=env_file)


@pytest.mark.parametrize("value", ["not-a-number", "0", "11", "-1"])
def test_llm_max_attempts_rejects_out_of_range_values(tmp_path: Path, value: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{LLM_MAX_ATTEMPTS_ENV_KEY}={value}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=LLM_MAX_ATTEMPTS_ENV_KEY):
        load_config(env_path=env_file)


def test_invalid_model_mode_is_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("A2AT_SAMPLE_MODEL_MODE=network\n", encoding="utf-8")

    with pytest.raises(ValueError, match="A2AT_SAMPLE_MODEL_MODE"):
        load_config(env_path=env_file)


def test_build_adk_model_rejects_empty_live_model_name(offline_config: SampleConfig) -> None:
    from dataclasses import replace

    live_config = replace(offline_config, model_mode=MODEL_MODE_LIVE, adk_model_name="")

    with pytest.raises(ValueError, match="ADK_MODEL_NAME"):
        build_adk_model(live_config)


def test_build_adk_model_applies_the_live_timeout_and_disables_retries(
    offline_config: SampleConfig,
) -> None:
    from dataclasses import replace

    live_config = replace(
        offline_config,
        model_mode=MODEL_MODE_LIVE,
        llm_timeout_seconds=42.0,
    )

    model = build_adk_model(live_config)

    assert model.model == live_config.adk_model_name
    additional_args = getattr(model, "_additional_args")
    assert additional_args["timeout"] == 42.0
    assert additional_args["num_retries"] == 0


def test_pre_send_validation_defaults_to_on(tmp_path: Path) -> None:
    config = load_config(env_path=tmp_path / "missing.env")

    assert config.pre_send_validation == "on"


@pytest.mark.parametrize("value", ["on", "always", "off", "ALWAYS"])
def test_pre_send_validation_accepts_supported_values(tmp_path: Path, value: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"A2AT_SAMPLE_PRE_SEND_VALIDATION={value}\n", encoding="utf-8")

    config = load_config(env_path=env_file)

    assert config.pre_send_validation == value.lower()


def test_pre_send_validation_rejects_an_unknown_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("A2AT_SAMPLE_PRE_SEND_VALIDATION=sometimes\n", encoding="utf-8")

    with pytest.raises(ValueError, match="A2AT_SAMPLE_PRE_SEND_VALIDATION"):
        load_config(env_path=env_file)


def test_adk_output_schema_with_tools_defaults_to_auto(tmp_path: Path) -> None:
    config = load_config(env_path=tmp_path / "missing.env")

    assert config.adk_model_output_schema_with_tools == "auto"


@pytest.mark.parametrize("value", ["auto", "true", "false", "FALSE"])
def test_adk_output_schema_with_tools_accepts_supported_values(tmp_path: Path, value: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{ADK_MODEL_OUTPUT_SCHEMA_WITH_TOOLS_ENV_KEY}={value}\n", encoding="utf-8")

    config = load_config(env_path=env_file)

    assert config.adk_model_output_schema_with_tools == value.lower()


def test_adk_output_schema_with_tools_rejects_an_unknown_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{ADK_MODEL_OUTPUT_SCHEMA_WITH_TOOLS_ENV_KEY}=sometimes\n", encoding="utf-8")

    with pytest.raises(ValueError, match=ADK_MODEL_OUTPUT_SCHEMA_WITH_TOOLS_ENV_KEY):
        load_config(env_path=env_file)


def test_build_adk_model_can_disable_schema_with_tools(offline_config: SampleConfig) -> None:
    from dataclasses import replace

    live_config = replace(
        offline_config,
        model_mode=MODEL_MODE_LIVE,
        adk_model_output_schema_with_tools=ADK_OUTPUT_SCHEMA_WITH_TOOLS_FALSE,
    )

    model = build_adk_model(live_config)

    assert model.capabilities.output_schema_and_tools is False


def test_build_adk_model_can_force_schema_with_tools(offline_config: SampleConfig) -> None:
    from dataclasses import replace

    live_config = replace(
        offline_config,
        model_mode=MODEL_MODE_LIVE,
        adk_model_name="gemini-2.0-flash",
        adk_model_output_schema_with_tools=ADK_OUTPUT_SCHEMA_WITH_TOOLS_TRUE,
    )

    model = build_adk_model(live_config)

    assert model.capabilities.output_schema_and_tools is True


def test_the_traced_wrapper_keeps_the_overridden_capability(offline_config: SampleConfig) -> None:
    from dataclasses import replace

    live_config = replace(
        offline_config,
        model_mode=MODEL_MODE_LIVE,
        adk_model_output_schema_with_tools=ADK_OUTPUT_SCHEMA_WITH_TOOLS_FALSE,
    )

    model = build_traced_adk_model(live_config)

    assert model.capabilities.output_schema_and_tools is False


def test_nms_discovery_loads_the_vendor_connection_settings(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{NMS_A2A_BASE_ENV_KEY}=https://nms.example:31127/a2a/json/",
                f"{NMS_TOKEN_URL_ENV_KEY}=https://nms.example:27417/rest/plat/smapp/v1/sessions",
                f"{NMS_TOKEN_USER_ENV_KEY}=northbound",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=secret",
                f"{NMS_VERIFY_TLS_ENV_KEY}=true",
                f"{NMS_IDLE_TIMEOUT_ENV_KEY}=4.5",
                f"{NMS_REQUEST_TIMEOUT_ENV_KEY}=90",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.discovery == "nms"
    assert config.nms_a2a_base == "https://nms.example:31127/a2a/json"
    assert config.nms_token_url.endswith("/rest/plat/smapp/v1/sessions")
    assert config.nms_token_user == "northbound"
    assert config.nms_token_value == "secret"
    assert config.nms_verify_tls is True
    assert config.nms_idle_timeout == 4.5
    assert config.nms_request_timeout == 90.0


def test_the_nms_send_timeout_defaults_to_300_and_can_be_overridden(tmp_path: Path) -> None:
    """The vendor's Create has been observed at 200+ seconds; sends get a separate budget."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{NMS_A2A_BASE_ENV_KEY}=https://nms.example:31127/a2a/json",
                f"{NMS_TOKEN_URL_ENV_KEY}=https://nms.example:27417/rest/plat/smapp/v1/sessions",
                f"{NMS_TOKEN_USER_ENV_KEY}=northbound",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=secret",
            ]
        ),
        encoding="utf-8",
    )
    assert load_config(env_path=env_file).nms_send_timeout == 300.0

    env_file.write_text(
        env_file.read_text(encoding="utf-8") + f"\n{NMS_SEND_TIMEOUT_ENV_KEY}=450\n",
        encoding="utf-8",
    )
    assert load_config(env_path=env_file).nms_send_timeout == 450.0


def test_nms_host_alone_derives_both_standard_urls(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{NMS_HOST_ENV_KEY}=141.71.97.174",
                f"{NMS_TOKEN_USER_ENV_KEY}=northbound",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=secret",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.nms_a2a_base == "https://141.71.97.174:31127/a2a/json"
    assert config.nms_token_url == ("https://141.71.97.174:27417/rest/plat/smapp/v1/sessions")


def test_nms_scheme_and_ports_override_the_standard_surface(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{NMS_HOST_ENV_KEY}=127.0.0.1",
                f"{NMS_SCHEME_ENV_KEY}=http",
                f"{NMS_A2A_PORT_ENV_KEY}=18080",
                f"{NMS_TOKEN_PORT_ENV_KEY}=18080",
                f"{NMS_TOKEN_USER_ENV_KEY}=northbound",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=secret",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.nms_a2a_base == "http://127.0.0.1:18080/a2a/json"
    assert config.nms_token_url == "http://127.0.0.1:18080/rest/plat/smapp/v1/sessions"


def test_nms_explicit_urls_win_over_the_derived_ones(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{NMS_HOST_ENV_KEY}=ignored.example",
                f"{NMS_A2A_BASE_ENV_KEY}=https://gateway.example/custom/a2a",
                f"{NMS_TOKEN_URL_ENV_KEY}=https://gateway.example/custom/sessions",
                f"{NMS_TOKEN_USER_ENV_KEY}=northbound",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=secret",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.nms_a2a_base == "https://gateway.example/custom/a2a"
    assert config.nms_token_url == "https://gateway.example/custom/sessions"


def test_nms_discovery_rejects_an_incomplete_configuration(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("A2AT_SAMPLE_DISCOVERY=nms\n", encoding="utf-8")

    with pytest.raises(ValueError, match="NMS discovery requires"):
        load_config(env_path=env_file)


def test_nms_region_and_wire_log_settings(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{NMS_HOST_ENV_KEY}=127.0.0.1",
                f"{NMS_TOKEN_USER_ENV_KEY}=user",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=value",
                f"{NMS_REGION_ENV_KEY}=A2ATestRegion",
                f"{NMS_WIRE_LOG_ENV_KEY}=0",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.nms_region == "A2ATestRegion"
    assert config.nms_wire_log is False


def test_nms_idle_defaults_to_two_minutes_and_read_after_send_is_on(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{NMS_HOST_ENV_KEY}=127.0.0.1",
                f"{NMS_TOKEN_USER_ENV_KEY}=user",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=value",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    # The vendor NMS can take well over a minute to send its first push; a short window turned
    # that wait into an early downgrade to polling and looked like the run had gone silent.
    assert config.nms_idle_timeout == 120.0
    assert config.nms_read_after_send is True


def test_nms_read_after_send_can_be_disabled(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"{NMS_READ_AFTER_SEND_ENV_KEY}=0",
                f"{NMS_IDLE_TIMEOUT_ENV_KEY}=60",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.nms_read_after_send is False
    assert config.nms_idle_timeout == 60.0


def test_nms_infers_a_missing_negotiation_template_uri_by_default(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{NMS_HOST_ENV_KEY}=127.0.0.1",
                f"{NMS_TOKEN_USER_ENV_KEY}=user",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=value",
            ]
        ),
        encoding="utf-8",
    )

    assert load_config(env_path=env_file).nms_infer_negotiation_kind is True


def test_nms_negotiation_kind_inference_can_be_disabled(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{NMS_INFER_NEGOTIATION_KIND_ENV_KEY}=0\n", encoding="utf-8")

    assert load_config(env_path=env_file).nms_infer_negotiation_kind is False


def test_nms_wire_log_defaults_to_on(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{NMS_HOST_ENV_KEY}=127.0.0.1",
                f"{NMS_TOKEN_USER_ENV_KEY}=user",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=value",
            ]
        ),
        encoding="utf-8",
    )

    assert load_config(env_path=env_file).nms_wire_log is True


def test_nms_tls_verification_defaults_to_off(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{NMS_A2A_BASE_ENV_KEY}=https://nms.example/a2a/json",
                f"{NMS_TOKEN_URL_ENV_KEY}=https://nms.example/sessions",
                f"{NMS_TOKEN_USER_ENV_KEY}=user",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=value",
            ]
        ),
        encoding="utf-8",
    )

    assert load_config(env_path=env_file).nms_verify_tls is False


def test_webhook_host_is_configurable_for_a_real_nms(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("A2AT_SAMPLE_WEBHOOK_HOST=192.0.2.10\n", encoding="utf-8")

    assert load_config(env_path=env_file).webhook_host == "192.0.2.10"


def test_intent_mode_defaults_to_raw(tmp_path: Path) -> None:
    """A2A-T sends the operator text to the SDK; the display-only ADK intent is opt-in."""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    assert load_config(env_path=env_file).intent_mode == "raw"


def test_intent_mode_can_be_processed(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{INTENT_MODE_ENV_KEY}=processed\n", encoding="utf-8")

    assert load_config(env_path=env_file).intent_mode == "processed"


def test_unsupported_intent_mode_is_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{INTENT_MODE_ENV_KEY}=guessed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="INTENT_MODE"):
        load_config(env_path=env_file)


def test_langfuse_defaults_are_disabled_and_capture_content(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    config = load_config(env_path=env_file)

    assert config.langfuse_configured is False
    assert config.langfuse_enabled is None
    assert config.langfuse_base_url == "https://us.cloud.langfuse.com"
    assert config.langfuse_ui_base_url == "https://us.cloud.langfuse.com"
    assert config.langfuse_capture_content is True
    assert config.langfuse_environment == "demo"
    assert config.langfuse_flush_at == 8


def test_langfuse_flush_at_can_be_tuned(tmp_path: Path) -> None:
    """The batch size bounds the OTLP request body behind a size-limited egress proxy."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"{LANGFUSE_FLUSH_AT_ENV_KEY}=1\n", encoding="utf-8")

    config = load_config(env_path=env_file)

    assert config.langfuse_flush_at == 1


@pytest.mark.parametrize("value", ["0", "33", "abc"])
def test_langfuse_rejects_an_invalid_flush_at(tmp_path: Path, value: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{LANGFUSE_FLUSH_AT_ENV_KEY}={value}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=LANGFUSE_FLUSH_AT_ENV_KEY):
        load_config(env_path=env_file)


def test_langfuse_keys_enable_the_export(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"{LANGFUSE_PUBLIC_KEY_ENV_KEY}=pk-lf-test",
                f"{LANGFUSE_SECRET_KEY_ENV_KEY}=sk-lf-test",
                f"{LANGFUSE_BASE_URL_ENV_KEY}=https://langfuse.internal/",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.langfuse_configured is True
    assert config.langfuse_public_key == "pk-lf-test"
    assert config.langfuse_secret_key == "sk-lf-test"
    assert config.langfuse_base_url == "https://langfuse.internal"


def test_langfuse_can_be_forced_off_and_content_capture_disabled(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"{LANGFUSE_PUBLIC_KEY_ENV_KEY}=pk-lf-test",
                f"{LANGFUSE_SECRET_KEY_ENV_KEY}=sk-lf-test",
                f"{LANGFUSE_ENABLED_ENV_KEY}=0",
                f"{LANGFUSE_CAPTURE_CONTENT_ENV_KEY}=0",
                f"{LANGFUSE_ENVIRONMENT_ENV_KEY}=blue-zone",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.langfuse_enabled is False
    assert config.langfuse_configured is False
    assert config.langfuse_capture_content is False
    assert config.langfuse_environment == "blue-zone"


def test_langfuse_rejects_an_invalid_switch(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{LANGFUSE_ENABLED_ENV_KEY}=sometimes\n", encoding="utf-8")

    with pytest.raises(ValueError, match=LANGFUSE_ENABLED_ENV_KEY):
        load_config(env_path=env_file)


def test_runs_dir_defaults_to_the_sample_runs_directory(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    config = load_config(env_path=env_file)

    assert Path(config.runs_dir).name == "runs"
    assert Path(config.runs_dir).is_absolute()


def test_runs_dir_resolves_a_relative_value_against_the_sample_root(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{RUNS_DIR_ENV_KEY}=runs-pure\n", encoding="utf-8")

    config = load_config(env_path=env_file)

    assert Path(config.runs_dir).name == "runs-pure"
    assert Path(config.runs_dir).is_absolute()


def test_runs_dir_accepts_an_absolute_value(tmp_path: Path) -> None:
    target = tmp_path / "custom-runs"
    env_file = tmp_path / ".env"
    env_file.write_text(f"{RUNS_DIR_ENV_KEY}={target}\n", encoding="utf-8")

    assert Path(load_config(env_path=env_file).runs_dir) == target


def test_run_dir_is_empty_by_default(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    assert load_config(env_path=env_file).run_dir == ""


def test_run_dir_resolves_a_relative_value_against_the_sample_root(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{RUN_DIR_ENV_KEY}=launcher-state\n", encoding="utf-8")

    resolved = Path(load_config(env_path=env_file).run_dir)

    assert resolved.is_absolute()
    assert resolved.name == "launcher-state"


def test_run_dir_accepts_an_absolute_value(tmp_path: Path) -> None:
    target = tmp_path / "launcher-state"
    env_file = tmp_path / ".env"
    env_file.write_text(f"{RUN_DIR_ENV_KEY}={target}\n", encoding="utf-8")

    assert Path(load_config(env_path=env_file).run_dir) == target


def test_pure_followup_defaults_to_both_and_accepts_the_three_modes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    assert load_config(env_path=env_file).pure_followup == "both"

    for value in ("both", "task", "context"):
        env_file.write_text(f"{PURE_FOLLOWUP_ENV_KEY}={value}\n", encoding="utf-8")
        assert load_config(env_path=env_file).pure_followup == value


def test_pure_followup_rejects_an_unknown_mode(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{PURE_FOLLOWUP_ENV_KEY}=guessed\n", encoding="utf-8")

    with pytest.raises(ValueError, match=PURE_FOLLOWUP_ENV_KEY):
        load_config(env_path=env_file)


def test_pure_hop_defaults_to_the_direct_ran_hop(tmp_path: Path) -> None:
    """A local deployment sends standard A2A straight to the RAN Agent; no OSS hop by default."""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    assert load_config(env_path=env_file).pure_hop == PURE_HOP_DIRECT


def test_pure_hop_direct_requires_a_local_discovery(tmp_path: Path) -> None:
    """The direct local hop has no NMS endpoint to call, so the pairing is rejected."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{PURE_HOP_ENV_KEY}={PURE_HOP_DIRECT}",
                f"{NMS_A2A_BASE_ENV_KEY}=https://nms.example:31127/a2a/json",
                f"{NMS_TOKEN_URL_ENV_KEY}=https://nms.example:27417/rest/plat/smapp/v1/sessions",
                f"{NMS_TOKEN_USER_ENV_KEY}=northbound",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=secret",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=PURE_HOP_ENV_KEY):
        load_config(env_path=env_file)


def test_pure_hop_nms_requires_the_nms_discovery(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{PURE_HOP_ENV_KEY}={PURE_HOP_NMS}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=PURE_HOP_ENV_KEY):
        load_config(env_path=env_file)


def test_pure_hop_nms_loads_with_the_nms_discovery(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{PURE_HOP_ENV_KEY}={PURE_HOP_NMS}",
                f"{NMS_A2A_BASE_ENV_KEY}=https://nms.example:31127/a2a/json",
                f"{NMS_TOKEN_URL_ENV_KEY}=https://nms.example:27417/rest/plat/smapp/v1/sessions",
                f"{NMS_TOKEN_USER_ENV_KEY}=northbound",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=secret",
            ]
        ),
        encoding="utf-8",
    )

    assert load_config(env_path=env_file).pure_hop == PURE_HOP_NMS


def test_the_nms_discovery_defaults_to_the_direct_hop(tmp_path: Path) -> None:
    """A vendor-NMS deployment uses the direct hop unless it explicitly asks for the proxy."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_DISCOVERY=nms",
                f"{NMS_A2A_BASE_ENV_KEY}=https://nms.example:31127/a2a/json",
                f"{NMS_TOKEN_URL_ENV_KEY}=https://nms.example:27417/rest/plat/smapp/v1/sessions",
                f"{NMS_TOKEN_USER_ENV_KEY}=northbound",
                f"{NMS_TOKEN_VALUE_ENV_KEY}=secret",
            ]
        ),
        encoding="utf-8",
    )

    assert load_config(env_path=env_file).pure_hop == PURE_HOP_NMS


def test_pure_hop_rejects_an_unknown_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{PURE_HOP_ENV_KEY}=guessed\n", encoding="utf-8")

    with pytest.raises(ValueError, match=PURE_HOP_ENV_KEY):
        load_config(env_path=env_file)


def test_pure_message_mode_defaults_to_auto_regardless_of_transport(tmp_path: Path) -> None:
    """The peer AgentCard, not the deployment transport, picks the pure RPC method."""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    assert load_config(env_path=env_file).pure_message_mode == "auto"

    env_file.write_text("A2AT_SAMPLE_TRANSPORT=webhook\n", encoding="utf-8")
    assert load_config(env_path=env_file).pure_message_mode == "auto"


def test_pure_message_mode_accepts_an_explicit_override(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"A2AT_SAMPLE_TRANSPORT=webhook\n{PURE_MESSAGE_MODE_ENV_KEY}=stream\n",
        encoding="utf-8",
    )
    assert load_config(env_path=env_file).pure_message_mode == "stream"

    env_file.write_text(f"{PURE_MESSAGE_MODE_ENV_KEY}=send\n", encoding="utf-8")
    assert load_config(env_path=env_file).pure_message_mode == "send"


def test_pure_message_mode_rejects_an_unknown_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{PURE_MESSAGE_MODE_ENV_KEY}=poll\n", encoding="utf-8")

    with pytest.raises(ValueError, match=PURE_MESSAGE_MODE_ENV_KEY):
        load_config(env_path=env_file)


def test_webhook_callback_defaults_are_empty(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    config = load_config(env_path=env_file)

    assert config.webhook_public_url == ""
    assert config.webhook_tls_cert == ""
    assert config.webhook_tls_key == ""
    assert config.webhook_auth_credentials == ""


def test_webhook_callback_keys_are_read(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "A2AT_SAMPLE_WEBHOOK_PUBLIC_URL=https://198.51.100.7:9000/webhook\n"
        "A2AT_SAMPLE_WEBHOOK_TLS_CERT=Orange/server.cer\n"
        "A2AT_SAMPLE_WEBHOOK_TLS_KEY=Orange/server_key_nopass.pem\n"
        "A2AT_SAMPLE_WEBHOOK_AUTH_CREDENTIALS=north:secret\n",
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.webhook_public_url == "https://198.51.100.7:9000/webhook"
    assert config.webhook_tls_cert == "Orange/server.cer"
    assert config.webhook_tls_key == "Orange/server_key_nopass.pem"
    assert config.webhook_auth_credentials == "north:secret"


def test_webhook_auth_credentials_require_user_and_password(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("A2AT_SAMPLE_WEBHOOK_AUTH_CREDENTIALS=no-colon\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected user:password"):
        load_config(env_path=env_file)


def _nms_env_file(tmp_path: Path, extra: str = "") -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "A2AT_SAMPLE_DISCOVERY=nms\n"
        "A2AT_SAMPLE_NMS_HOST=nms.example\n"
        "A2AT_SAMPLE_NMS_TOKEN_USER=northbound\n"
        "A2AT_SAMPLE_NMS_TOKEN_VALUE=secret\n" + extra,
        encoding="utf-8",
    )
    return env_file


def test_nms_discovery_has_no_implicit_region_or_sites(tmp_path: Path) -> None:
    """A real-NMS run must configure its region explicitly; the sample has no built-in value."""
    config = load_config(env_path=_nms_env_file(tmp_path))

    assert config.nms_region == ""
    assert config.nms_sites == ()


def test_nms_region_and_sites_can_be_overridden(tmp_path: Path) -> None:
    config = load_config(
        env_path=_nms_env_file(
            tmp_path,
            "A2AT_SAMPLE_NMS_REGION=OtherRegion\nA2AT_SAMPLE_NMS_SITES=SITE-A, SITE-B\n",
        )
    )

    assert config.nms_region == "OtherRegion"
    assert config.nms_sites == ("SITE-A", "SITE-B")


def test_the_local_demo_keeps_the_neutral_region_and_no_nms_sites(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("A2AT_SAMPLE_DISCOVERY=direct\n", encoding="utf-8")

    config = load_config(env_path=env_file)

    assert config.nms_region == ""
    assert config.nms_sites == ()


def test_the_process_environment_wins_over_the_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A launcher script exporting a model must override the source .env, not be shadowed by it."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "A2AT_LANGUAGE=zh-CN\n"
        "ADK_MODEL_NAME=openai/deepseek-from-file\n"
        "ADK_MODEL_API_BASE=http://127.0.0.1:31943/v1\n"
        "ADK_MODEL_API_KEY=file-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("A2AT_LANGUAGE", "en-US")
    monkeypatch.setenv("ADK_MODEL_NAME", "openai/qwen3-32b")
    monkeypatch.setenv("ADK_MODEL_API_BASE", "http://71.77.65.40:8008/v1")
    monkeypatch.setenv("ADK_MODEL_API_KEY", "lab-key")

    config = load_config(env_path=env_file)

    assert config.language == "en-US"
    assert config.adk_model_name == "openai/qwen3-32b"
    assert config.adk_model_api_base == "http://71.77.65.40:8008/v1"
    assert config.adk_model_api_key == "lab-key"


def test_the_langfuse_ui_base_is_separate_from_the_reporting_endpoint(tmp_path: Path) -> None:
    """A proxy is the SDK endpoint, not the URL the browser opens."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LANGFUSE_BASE_URL=http://langfuse-proxy.internal:8080\n"
        "A2AT_SAMPLE_LANGFUSE_UI_BASE_URL=https://us.cloud.langfuse.com/\n",
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)

    assert config.langfuse_base_url == "http://langfuse-proxy.internal:8080"
    assert config.langfuse_ui_base_url == "https://us.cloud.langfuse.com"


def test_trace_span_level_defaults_to_phase(tmp_path: Path) -> None:
    config = load_config(env_path=tmp_path / "missing.env")

    assert config.trace_span_level == "phase"


@pytest.mark.parametrize("value", ["full", "phase", "minimal", "MINIMAL"])
def test_trace_span_level_accepts_supported_values(tmp_path: Path, value: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"A2AT_SAMPLE_TRACE_SPAN_LEVEL={value}\n", encoding="utf-8")

    config = load_config(env_path=env_file)

    assert config.trace_span_level == value.lower()


def test_trace_span_level_rejects_an_unknown_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("A2AT_SAMPLE_TRACE_SPAN_LEVEL=verbose\n", encoding="utf-8")

    with pytest.raises(ValueError, match="A2AT_SAMPLE_TRACE_SPAN_LEVEL"):
        load_config(env_path=env_file)


def test_the_sdk_env_defaults_to_the_packaged_prompt_resources(
    offline_config: SampleConfig, tmp_path: Path
) -> None:
    """The packaged slot schema is the single authoritative baseline."""
    import shutil

    from dotenv import dotenv_values
    from shared.sdk_env import prepare_sdk_env

    sdk_env = prepare_sdk_env(offline_config, tmp_path / "missing.env")
    try:
        values = dotenv_values(sdk_env.path)
        assert values["A2AT_PROMPT_SOURCE_TYPE"] == "packaged"
        assert "A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR" not in values
    finally:
        shutil.rmtree(sdk_env.directory)


def test_the_sdk_env_drops_an_inherited_local_prompt_root(
    offline_config: SampleConfig, tmp_path: Path
) -> None:
    """A stale ``local_file`` root must not replace the packaged resources."""
    import shutil

    from dotenv import dotenv_values
    from shared.sdk_env import prepare_sdk_env

    source = tmp_path / "source.env"
    source.write_text(
        "A2AT_PROMPT_SOURCE_TYPE=local_file\n"
        "A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR=/tmp/old-overlay\n",
        encoding="utf-8",
    )
    sdk_env = prepare_sdk_env(offline_config, source)
    try:
        values = dotenv_values(sdk_env.path)
        assert values["A2AT_PROMPT_SOURCE_TYPE"] == "packaged"
        assert "A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR" not in values
    finally:
        shutil.rmtree(sdk_env.directory)


# ---------------------------------------------------------------------------
# Independent OSP judge model (P2)
# ---------------------------------------------------------------------------


#: Keys of the independent judge model. They are the external contract, so the tests name them
#: literally instead of importing constants that would turn a missing key into a collection error.
JUDGE_MODEL_KEY = "A2AT_OSP_JUDGE_MODEL"
JUDGE_API_BASE_KEY = "A2AT_OSP_JUDGE_API_BASE"
JUDGE_API_KEY_KEY = "A2AT_OSP_JUDGE_API_KEY"
JUDGE_TIMEOUT_KEY = "A2AT_OSP_JUDGE_TIMEOUT_SECONDS"
JUDGE_MAX_TOKENS_KEY = "A2AT_OSP_JUDGE_MAX_TOKENS"


def _judge_env_file(tmp_path: Path, body: str) -> Path:
    env_file = tmp_path / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(body, encoding="utf-8")
    return env_file


def _judge_configured(config: SampleConfig) -> object:
    return getattr(config, "judge_configured", False)


def test_the_judge_is_configured_only_with_both_model_and_endpoint(tmp_path: Path) -> None:
    """The judge is a separate client: a partial endpoint must stay unconfigured."""
    missing = tmp_path / "missing.env"
    assert _judge_configured(load_config(env_path=missing)) is False

    model_only = _judge_env_file(
        tmp_path / "model", f"{JUDGE_MODEL_KEY}=judge-model\n"
    )
    assert _judge_configured(load_config(env_path=model_only)) is False

    base_only = _judge_env_file(
        tmp_path / "base", f"{JUDGE_API_BASE_KEY}=http://judge.example/v1\n"
    )
    assert _judge_configured(load_config(env_path=base_only)) is False

    both = _judge_env_file(
        tmp_path / "both",
        f"{JUDGE_MODEL_KEY}=judge-model\n"
        f"{JUDGE_API_BASE_KEY}=http://judge.example/v1\n",
    )
    assert _judge_configured(load_config(env_path=both)) is True


def test_the_judge_api_key_is_optional(tmp_path: Path) -> None:
    """An OpenAI-compatible local endpoint accepts requests without a key."""
    env_file = _judge_env_file(
        tmp_path,
        f"{JUDGE_MODEL_KEY}=judge-model\n"
        f"{JUDGE_API_BASE_KEY}=http://judge.example/v1\n",
    )

    config = load_config(env_path=env_file)

    assert _judge_configured(config) is True
    assert getattr(config, "judge_model", None) == "judge-model"
    assert getattr(config, "judge_api_base", None) == "http://judge.example/v1"
    assert getattr(config, "judge_api_key", None) == ""


def test_the_judge_endpoint_keys_and_optional_api_key_are_read(tmp_path: Path) -> None:
    env_file = _judge_env_file(
        tmp_path,
        f"{JUDGE_MODEL_KEY}=judge-model\n"
        f"{JUDGE_API_BASE_KEY}=http://judge.example/v1\n"
        f"{JUDGE_API_KEY_KEY}=judge-secret\n",
    )

    config = load_config(env_path=env_file)

    assert _judge_configured(config) is True
    assert getattr(config, "judge_api_key", None) == "judge-secret"


def test_the_judge_timeout_and_token_budget_have_defaults(tmp_path: Path) -> None:
    config = load_config(env_path=tmp_path / "missing.env")

    timeout = getattr(config, "judge_timeout_seconds", None)
    max_tokens = getattr(config, "judge_max_tokens", None)
    assert isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
    assert timeout > 0
    assert isinstance(max_tokens, int) and not isinstance(max_tokens, bool)
    assert max_tokens > 0


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "inf", "not-a-number"])
def test_the_judge_timeout_rejects_an_invalid_value(tmp_path: Path, value: str) -> None:
    env_file = _judge_env_file(tmp_path, f"{JUDGE_TIMEOUT_KEY}={value}\n")

    with pytest.raises(ValueError, match=JUDGE_TIMEOUT_KEY):
        load_config(env_path=env_file)


@pytest.mark.parametrize("value", ["0", "-1", "2.5", "many"])
def test_the_judge_token_budget_rejects_an_invalid_value(tmp_path: Path, value: str) -> None:
    env_file = _judge_env_file(tmp_path, f"{JUDGE_MAX_TOKENS_KEY}={value}\n")

    with pytest.raises(ValueError, match=JUDGE_MAX_TOKENS_KEY):
        load_config(env_path=env_file)
