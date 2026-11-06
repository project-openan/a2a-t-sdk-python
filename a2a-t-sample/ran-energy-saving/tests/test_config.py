"""Tests for the unified sample configuration and model selection."""

from __future__ import annotations

from pathlib import Path

import pytest
from demo.adk_fake_model import FakeLlm, build_fake_model
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
    DEFAULT_ADK_SESSION_RETENTION_DAYS,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_LLM_MAX_ATTEMPTS,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    DEFAULT_PLANNER_MAX_STEPS,
    DEFAULT_WEBHOOK_IDLE_TIMEOUT,
    GATEWAY_PORT_ENV_KEY,
    GATEWAY_REGISTER_ENV_KEY,
    GATEWAY_SCENARIO_ENV_KEY,
    GATEWAY_TASK_DB_ENV_KEY,
    HTTP_TIMEOUT_ENV_KEY,
    INTENT_MODE_ENV_KEY,
    LANGFUSE_BASE_URL_ENV_KEY,
    LANGFUSE_CAPTURE_CONTENT_ENV_KEY,
    LANGFUSE_ENABLED_ENV_KEY,
    LANGFUSE_ENVIRONMENT_ENV_KEY,
    LANGFUSE_PUBLIC_KEY_ENV_KEY,
    LANGFUSE_SECRET_KEY_ENV_KEY,
    LANGFUSE_UI_BASE_URL_ENV_KEY,
    LLM_MAX_ATTEMPTS_ENV_KEY,
    LLM_TIMEOUT_ENV_KEY,
    MODEL_MODE_LIVE,
    MODEL_MODE_OFFLINE,
    NMS_A2A_BASE_ENV_KEY,
    NMS_A2A_PORT_ENV_KEY,
    NMS_HOST_ENV_KEY,
    NMS_IDLE_TIMEOUT_ENV_KEY,
    NMS_READ_AFTER_SEND_ENV_KEY,
    NMS_REGION_ENV_KEY,
    NMS_REQUEST_TIMEOUT_ENV_KEY,
    NMS_SCHEME_ENV_KEY,
    NMS_SITES_ENV_KEY,
    NMS_TOKEN_PORT_ENV_KEY,
    NMS_TOKEN_URL_ENV_KEY,
    NMS_TOKEN_USER_ENV_KEY,
    NMS_TOKEN_VALUE_ENV_KEY,
    NMS_VERIFY_TLS_ENV_KEY,
    NMS_WIRE_LOG_ENV_KEY,
    PLANNER_MAX_STEPS_ENV_KEY,
    PLANNER_MODEL_ENV_KEY,
    PLANNER_TIMEOUT_ENV_KEY,
    PURE_FOLLOWUP_ENV_KEY,
    RUNS_DIR_ENV_KEY,
    SERVER_AUTH_TOKEN_ENV_KEY,
    TASK_DB_ENV_KEY,
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
    TASK_DB_ENV_KEY,
    CLIENT_DB_ENV_KEY,
    CLIENT_MEMORY_TURNS_ENV_KEY,
    ADK_SESSION_RETENTION_DAYS_ENV_KEY,
    GATEWAY_PORT_ENV_KEY,
    GATEWAY_REGISTER_ENV_KEY,
    GATEWAY_SCENARIO_ENV_KEY,
    GATEWAY_TASK_DB_ENV_KEY,
    SERVER_AUTH_TOKEN_ENV_KEY,
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
    LANGFUSE_PUBLIC_KEY_ENV_KEY,
    LANGFUSE_SECRET_KEY_ENV_KEY,
    LANGFUSE_UI_BASE_URL_ENV_KEY,
    RUNS_DIR_ENV_KEY,
    PURE_FOLLOWUP_ENV_KEY,
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
                "A2AT_SAMPLE_MODEL_MODE=offline",
                "A2AT_LANGUAGE=en-US",
                "A2AT_SAMPLE_HOST=0.0.0.0",
                "A2AT_SAMPLE_PORT=8100",
                "REGISTRY_CENTER_HOST=registry.local",
                "REGISTRY_CENTER_PORT=5002",
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

    assert config.model_mode == MODEL_MODE_OFFLINE
    assert config.is_offline
    assert config.model_label == "offline (scripted)"
    assert config.language == "en-US"
    assert config.host == "0.0.0.0"
    assert config.port == 8100
    assert config.registry_url == "http://registry.local:5002"
    assert config.webhook_port == 0
    assert config.webhook_token == "token-123"
    assert config.webhook_idle_timeout == 15
    assert config.discovery == "direct"
    assert config.transport == "webhook"


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
                f"{ADK_SESSION_RETENTION_DAYS_ENV_KEY}=30",
                f"{GATEWAY_PORT_ENV_KEY}=8101",
                f"{GATEWAY_REGISTER_ENV_KEY}=yes",
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
    assert config.adk_session_retention_days == 30
    assert config.gateway_port == 8101
    assert config.gateway_register is True
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
    assert config.gateway_register is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (CLIENT_DB_ENV_KEY, "relative/client.sqlite3"),
        (CLIENT_MEMORY_TURNS_ENV_KEY, "-1"),
        (ADK_SESSION_RETENTION_DAYS_ENV_KEY, "0"),
        (GATEWAY_REGISTER_ENV_KEY, "sometimes"),
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

    assert config.model_mode == MODEL_MODE_OFFLINE
    assert config.registry_url == "http://127.0.0.1:5001"
    assert config.http_timeout_seconds == DEFAULT_HTTP_TIMEOUT_SECONDS == 300.0
    assert config.llm_timeout_seconds == DEFAULT_LLM_TIMEOUT_SECONDS == 120.0
    assert config.llm_max_attempts == DEFAULT_LLM_MAX_ATTEMPTS == 1
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


def test_build_adk_model_returns_fake_model_offline(offline_config: SampleConfig) -> None:
    model = build_adk_model(offline_config, offline_model_factory=lambda: build_fake_model())

    assert isinstance(model, FakeLlm)


def test_build_adk_model_requires_an_offline_factory(offline_config: SampleConfig) -> None:
    with pytest.raises(ValueError, match="offline_model_factory"):
        build_adk_model(offline_config)


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
    env_file.write_text(
        f"{ADK_MODEL_OUTPUT_SCHEMA_WITH_TOOLS_ENV_KEY}={value}\n", encoding="utf-8"
    )

    config = load_config(env_path=env_file)

    assert config.adk_model_output_schema_with_tools == value.lower()


def test_adk_output_schema_with_tools_rejects_an_unknown_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"{ADK_MODEL_OUTPUT_SCHEMA_WITH_TOOLS_ENV_KEY}=sometimes\n", encoding="utf-8"
    )

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


def test_nms_idle_defaults_to_45_seconds_and_read_after_send_is_on(tmp_path: Path) -> None:
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

    assert config.nms_idle_timeout == 45.0
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


def test_intent_mode_defaults_to_processed(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    assert load_config(env_path=env_file).intent_mode == "processed"


def test_intent_mode_can_be_raw(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{INTENT_MODE_ENV_KEY}=raw\n", encoding="utf-8")

    assert load_config(env_path=env_file).intent_mode == "raw"


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
        "A2AT_SAMPLE_NMS_TOKEN_VALUE=secret\n"
        + extra,
        encoding="utf-8",
    )
    return env_file


def test_nms_discovery_defaults_to_the_910_region_and_sites(tmp_path: Path) -> None:
    """A real-NMS run must never send the neutral demo region or site names."""
    config = load_config(env_path=_nms_env_file(tmp_path))

    assert config.nms_region == "A2ATestRegion"
    assert config.nms_sites == ("NRES_XCS_6102937", "LTE_26441_LNR_OCL")


def test_nms_region_and_sites_can_be_overridden(tmp_path: Path) -> None:
    config = load_config(
        env_path=_nms_env_file(
            tmp_path,
            "A2AT_SAMPLE_NMS_REGION=OtherRegion\n"
            "A2AT_SAMPLE_NMS_SITES=SITE-A, SITE-B\n",
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


def test_the_process_environment_wins_over_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
