"""Focused tests of the client runtime assembly (no network)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from a2a.client import ClientConfig
from demo.adk_fake_model import CLIENT_AGENT_NAME, FakeLlm
from demo.composition import build_demo_runtime
from demo.scenario import SCENARIO_ABORT, SCENARIO_TARGET, get_scenario
from dotenv import dotenv_values
from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from protocol.client_runtime import (
    prepare_client_sdk_env,
)
from protocol.decision_provider import (
    AdkDecisionProvider,
    ManualDecisionProvider,
    ScriptedDecisionProvider,
)
from protocol.discovery import DiscoveredAgent
from protocol.server_app import AGENT_CARD_NAME, AGENT_CARD_ORGANIZATION, build_agent_card
from shared.adk_tracing import TracingLlm
from shared.config import MODEL_MODE_LIVE, SampleConfig


class _FakeA2AClient:
    """a2a-sdk client double recording its close."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _card() -> DiscoveredAgent:
    return DiscoveredAgent(
        agent_card=build_agent_card(host="127.0.0.1", port=8000),
        url="http://127.0.0.1:8000",
    )


async def test_manual_runtime_wires_scripted_env_and_streaming(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import protocol.client_runtime as client_runtime

    captured: dict[str, object] = {}
    fake_client = _FakeA2AClient()

    async def fake_discover(**kwargs: object) -> DiscoveredAgent:
        captured["discover"] = kwargs
        return _card()

    async def fake_create(agent: object, client_config: ClientConfig | None = None) -> _FakeA2AClient:
        captured["agent"] = agent
        captured["client_config"] = client_config
        return fake_client

    monkeypatch.setattr(client_runtime, "discover_agent", fake_discover)
    monkeypatch.setattr(client_runtime, "create_client", fake_create)
    monkeypatch.setenv("A2AT_LANGUAGE", offline_config.language)

    runtime = await build_demo_runtime(
        offline_config,
        "information",
        "manual",
        "sse",
        offline_config.discovery,
        env_path=tmp_path / "missing.env",
    )

    assert isinstance(runtime.provider, ManualDecisionProvider)
    assert runtime.manual_provider is runtime.provider
    assert runtime.transport_name == "sse"
    values = dotenv_values(runtime.sdk_env.path)
    assert values["A2AT_LLM_PROVIDER"] == "scripted_traced"
    assert values["A2AT_LLM_MODEL"] == "scripted"
    assert values["A2AT_LLM_API_KEY"] == "not-needed"
    assert values["A2AT_LANGUAGE"] == "en-US"
    client_config = captured["client_config"]
    assert isinstance(client_config, ClientConfig)
    assert client_config.streaming is True
    discovery = captured["discover"]
    assert isinstance(discovery, dict)
    assert discovery["organization"] == AGENT_CARD_ORGANIZATION
    assert discovery["name"] == AGENT_CARD_NAME
    assert discovery["direct_base_url"] == "http://127.0.0.1:8000"

    sdk_dir = runtime.sdk_env.directory
    await runtime.aclose()

    assert fake_client.closed is True
    assert not sdk_dir.exists()


async def test_scripted_mode_uses_the_scenario_decisions(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import protocol.client_runtime as client_runtime

    async def fake_discover(**kwargs: object) -> DiscoveredAgent:
        del kwargs
        return _card()

    async def fake_create(agent: object, client_config: ClientConfig | None = None) -> _FakeA2AClient:
        del agent, client_config
        return _FakeA2AClient()

    monkeypatch.setattr(client_runtime, "discover_agent", fake_discover)
    monkeypatch.setattr(client_runtime, "create_client", fake_create)
    monkeypatch.setenv("A2AT_LANGUAGE", offline_config.language)

    runtime = await build_demo_runtime(
        offline_config,
        SCENARIO_TARGET,
        "scripted",
        "sse",
        offline_config.discovery,
        env_path=tmp_path / "missing.env",
    )

    assert isinstance(runtime.provider, ScriptedDecisionProvider)
    assert runtime.provider.remaining == 2
    await runtime.aclose()


async def test_auto_live_mode_uses_the_live_decision_model(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Live mode must run the client ADK agent on the configured endpoint, not the fake model."""
    import protocol.client_runtime as client_runtime

    async def fake_discover(**kwargs: object) -> DiscoveredAgent:
        del kwargs
        return _card()

    async def fake_create(agent: object, client_config: ClientConfig | None = None) -> _FakeA2AClient:
        del agent, client_config
        return _FakeA2AClient()

    monkeypatch.setattr(client_runtime, "discover_agent", fake_discover)
    monkeypatch.setattr(client_runtime, "create_client", fake_create)
    monkeypatch.setenv("A2AT_LANGUAGE", offline_config.language)
    live_config = replace(offline_config, model_mode=MODEL_MODE_LIVE)

    runtime = await build_demo_runtime(
        live_config,
        "night",
        "auto",
        "sse",
        live_config.discovery,
        env_path=tmp_path / "missing.env",
    )
    try:
        assert isinstance(runtime.provider, AdkDecisionProvider)
        assert runtime.provider.is_offline is False
        model = runtime.provider.runner.agent.model
        assert isinstance(model, TracingLlm)
        assert isinstance(model.inner, LiteLlm)
    finally:
        await runtime.aclose()


@pytest.mark.parametrize(
    ("scenario_name", "expected_actions"),
    [
        (SCENARIO_TARGET, ["counter", "accept"]),
        (SCENARIO_ABORT, ["counter", "counter", "counter"]),
    ],
)
async def test_auto_offline_mode_scripts_the_scenario_decisions(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario_name: str,
    expected_actions: list[str],
) -> None:
    import protocol.client_runtime as client_runtime

    async def fake_discover(**kwargs: object) -> DiscoveredAgent:
        del kwargs
        return _card()

    async def fake_create(agent: object, client_config: ClientConfig | None = None) -> _FakeA2AClient:
        del agent, client_config
        return _FakeA2AClient()

    monkeypatch.setattr(client_runtime, "discover_agent", fake_discover)
    monkeypatch.setattr(client_runtime, "create_client", fake_create)
    monkeypatch.setenv("A2AT_LANGUAGE", offline_config.language)

    runtime = await build_demo_runtime(
        offline_config,
        scenario_name,
        "auto",
        "sse",
        offline_config.discovery,
        env_path=tmp_path / "missing.env",
    )
    try:
        assert isinstance(runtime.provider, AdkDecisionProvider)
        agent = runtime.provider.runner.agent
        assert isinstance(agent, LlmAgent)
        model = agent.model
        assert isinstance(model, TracingLlm)
        inner = model.inner
        assert isinstance(inner, FakeLlm)
        client_script = next(script for script in inner.scripts if script.marker == CLIENT_AGENT_NAME)
        actions = [json.loads(turn.text or "")["action"] for turn in client_script.turns]
        assert actions == expected_actions
        assert len(actions) == len(get_scenario(scenario_name).scripted_client_decisions)
    finally:
        await runtime.aclose()


async def test_runtime_attaches_the_trace_sink_before_building_and_clears_it_on_close(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import protocol.client_runtime as client_runtime
    from shared.trace import Span, emit_span

    async def fake_discover(**kwargs: object) -> DiscoveredAgent:
        del kwargs
        return _card()

    async def fake_create(agent: object, client_config: ClientConfig | None = None) -> _FakeA2AClient:
        del agent, client_config
        return _FakeA2AClient()

    monkeypatch.setattr(client_runtime, "discover_agent", fake_discover)
    monkeypatch.setattr(client_runtime, "create_client", fake_create)
    monkeypatch.setenv("A2AT_LANGUAGE", offline_config.language)

    spans: list[dict[str, object]] = []
    runtime = await build_demo_runtime(
        offline_config,
        "information",
        "auto",
        "sse",
        offline_config.discovery,
        env_path=tmp_path / "missing.env",
        trace_sink=spans.append,
    )
    try:
        emit_span(Span(run_id="run-1", span_id="s1", kind="llm"))
        assert [span["spanId"] for span in spans] == ["s1"]
    finally:
        await runtime.aclose()

    # The sink is cleared on close: later producers are no-ops.
    emit_span(Span(run_id="run-1", span_id="s2", kind="llm"))
    assert [span["spanId"] for span in spans] == ["s1"]


def test_prepare_client_sdk_env_does_not_mutate_the_source(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    source = tmp_path / ".env"
    original = (
        "A2AT_LLM_PROVIDER=openai\n"
        "A2AT_LLM_MODEL=my-model\n"
        "A2AT_LLM_BASE_URL=http://example.test/v1\n"
        "A2AT_LLM_TIMEOUT_SECONDS=99\n"
        "A2AT_LLM_MAX_ATTEMPTS=5\n"
    )
    source.write_text(original, encoding="utf-8")
    live_config = replace(offline_config, model_mode=MODEL_MODE_LIVE)

    sdk_env = prepare_client_sdk_env(live_config, source)

    assert source.read_text(encoding="utf-8") == original
    values = dotenv_values(sdk_env.path)
    assert values["A2AT_LLM_PROVIDER"] == "openai_traced"
    assert values["A2AT_LLM_MODEL"] == "my-model"
    assert values["A2AT_LLM_BASE_URL"] == "http://example.test/v1"
    assert values["A2AT_LANGUAGE"] == "en-US"
    # The sample's fail-fast policy always overrides the source values (both modes).
    assert values["A2AT_LLM_TIMEOUT_SECONDS"] == "180"
    assert values["A2AT_LLM_MAX_ATTEMPTS"] == "1"


def test_offline_sdk_env_carries_the_configured_fail_fast_policy(offline_config: SampleConfig, tmp_path: Path) -> None:
    config = replace(offline_config, llm_timeout_seconds=12.5, llm_max_attempts=2)

    values = dotenv_values(prepare_client_sdk_env(config, tmp_path / "missing.env").path)

    assert values["A2AT_LLM_PROVIDER"] == "scripted_traced"
    assert values["A2AT_LLM_TIMEOUT_SECONDS"] == "12.5"
    assert values["A2AT_LLM_MAX_ATTEMPTS"] == "2"


async def test_runtime_http_client_uses_the_configured_http_budget(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import protocol.client_runtime as client_runtime

    async def fake_discover(**kwargs: object) -> DiscoveredAgent:
        del kwargs
        return _card()

    async def fake_create(agent: object, client_config: ClientConfig | None = None) -> _FakeA2AClient:
        del agent, client_config
        return _FakeA2AClient()

    monkeypatch.setattr(client_runtime, "discover_agent", fake_discover)
    monkeypatch.setattr(client_runtime, "create_client", fake_create)
    config = replace(offline_config, http_timeout_seconds=42.0)

    runtime = await build_demo_runtime(
        config, "information", "manual", "sse", config.discovery, env_path=tmp_path / "missing.env"
    )
    try:
        assert runtime._httpx_client.timeout.read == 42.0
        assert runtime._httpx_client.timeout.connect == 42.0
    finally:
        await runtime.aclose()

    captured: list[dict[str, object]] = []

    async def fake_flow(**kwargs: object) -> object:
        captured.append(kwargs)
        return object()

    monkeypatch.setattr(client_runtime, "run_client_flow", fake_flow)
    runtime = await build_demo_runtime(
        config, "information", "manual", "sse", config.discovery, env_path=tmp_path / "missing.env"
    )
    try:
        await runtime.run(user_request="custom request from the UI")
    finally:
        await runtime.aclose()

    assert captured[0]["call_timeout_seconds"] == 42.0
    assert captured[0]["user_request"] == "custom request from the UI"


@pytest.mark.asyncio
async def test_a_free_text_request_reaches_the_sdk_unrewritten(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The SDK owns the Task-T conversion of free text; the intent layer is display only."""
    import protocol.client_runtime as client_runtime
    from protocol.intent import OperatorRequest, ProcessedIntent

    class FakeIntentProcessor:
        async def process(self, request: OperatorRequest) -> ProcessedIntent:
            return ProcessedIntent(
                raw_request=request.text,
                normalized_text="## Operation Type\nCreate\n\n## Task Object\nThe area is Region A.",
                plain_text="Create energy saving in Region A.",
                source="model",
            )

    async def fake_discover(**kwargs: object) -> DiscoveredAgent:
        del kwargs
        return _card()

    async def fake_create(agent: object, client_config: ClientConfig | None = None) -> _FakeA2AClient:
        del agent, client_config
        return _FakeA2AClient()

    monkeypatch.setattr(client_runtime, "discover_agent", fake_discover)
    monkeypatch.setattr(client_runtime, "create_client", fake_create)
    monkeypatch.setattr(
        "demo.intent.build_demo_intent_processor",
        lambda config, run_spec=None: FakeIntentProcessor(),
    )
    captured: list[dict[str, object]] = []

    async def fake_flow(**kwargs: object) -> object:
        captured.append(kwargs)
        return object()

    monkeypatch.setattr(client_runtime, "run_client_flow", fake_flow)
    config = replace(offline_config, intent_mode="processed")
    runtime = await build_demo_runtime(
        config, "information", "manual", "sse", config.discovery, env_path=tmp_path / "missing.env"
    )
    try:
        await runtime.run(user_request="custom request from the UI")
    finally:
        await runtime.aclose()

    assert captured[0]["user_request"] == "custom request from the UI"
    assert "## Operation Type" not in str(captured[0]["user_request"])


@pytest.mark.asyncio
async def test_a_raw_run_does_not_call_the_intent_agent(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A2A-T raw mode must not spend an ADK turn on the display-only intent layer."""
    import protocol.client_runtime as client_runtime
    from protocol.intent import OperatorRequest, ProcessedIntent

    calls = 0

    class FakeIntentProcessor:
        async def process(self, request: OperatorRequest) -> ProcessedIntent:
            nonlocal calls
            calls += 1
            return ProcessedIntent.raw(request.text)

    async def fake_discover(**kwargs: object) -> DiscoveredAgent:
        del kwargs
        return _card()

    async def fake_create(agent: object, client_config: ClientConfig | None = None) -> _FakeA2AClient:
        del agent, client_config
        return _FakeA2AClient()

    monkeypatch.setattr(client_runtime, "discover_agent", fake_discover)
    monkeypatch.setattr(client_runtime, "create_client", fake_create)
    monkeypatch.setattr(
        "demo.intent.build_demo_intent_processor",
        lambda config, run_spec=None: FakeIntentProcessor(),
    )
    captured: list[dict[str, object]] = []

    async def fake_flow(**kwargs: object) -> object:
        captured.append(kwargs)
        return object()

    monkeypatch.setattr(client_runtime, "run_client_flow", fake_flow)
    config = replace(offline_config, intent_mode="raw")
    runtime = await build_demo_runtime(
        config, "information", "manual", "sse", config.discovery, env_path=tmp_path / "missing.env"
    )
    try:
        await runtime.run(
            user_request="modify the running task",
            run_mode="modify",
            target_task_id="task-1",
            target_context_id="ctx-1",
        )
    finally:
        await runtime.aclose()

    assert calls == 0
    assert captured[0]["intent_fields"] == {}


async def test_a_pure_modify_sends_the_outer_task_ids(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pure A2A continues the peer's Task; the vendor id is only the picker's identity."""
    import protocol.client_runtime as client_runtime
    from protocol.client_flow import ClientFlowResult
    from shared.run_constants import FINAL_STATE_TASK_COMPLETED, PROTOCOL_PURE

    captured: dict[str, object] = {}

    async def fake_discover(**kwargs: object) -> DiscoveredAgent:
        del kwargs
        return _card()

    async def fake_create(agent: object, client_config: ClientConfig | None = None) -> _FakeA2AClient:
        del agent, client_config
        return _FakeA2AClient()

    async def fake_pure(**kwargs: object) -> ClientFlowResult:
        captured.update(kwargs)
        return ClientFlowResult(final_state=FINAL_STATE_TASK_COMPLETED)

    monkeypatch.setattr(client_runtime, "discover_agent", fake_discover)
    monkeypatch.setattr(client_runtime, "create_client", fake_create)
    monkeypatch.setattr(client_runtime, "run_pure_client_flow", fake_pure)
    runtime = await build_demo_runtime(
        offline_config,
        "outage",
        "auto",
        "sse",
        "direct",
        protocol=PROTOCOL_PURE,
        env_path=tmp_path / "missing.env",
    )
    try:
        await runtime.run(
            user_request="modify the running task",
            run_mode="modify",
            target_task_id="task-1",
            target_context_id="ctx-1",
            target_server_task_id="outer-1",
            target_server_context_id="outer-ctx",
        )
    finally:
        await runtime.aclose()

    assert captured["task_id"] == "outer-1"
    assert captured["context_id"] == "outer-ctx"


async def test_a_pure_modify_without_the_outer_ids_is_refused(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A vendor Task id cannot be continued over pure A2A without the peer's own Task id."""
    import protocol.client_runtime as client_runtime
    from protocol.client_runtime import ClientRuntimeError
    from shared.run_constants import PROTOCOL_PURE

    async def fake_discover(**kwargs: object) -> DiscoveredAgent:
        del kwargs
        return _card()

    async def fake_create(agent: object, client_config: ClientConfig | None = None) -> _FakeA2AClient:
        del agent, client_config
        return _FakeA2AClient()

    monkeypatch.setattr(client_runtime, "discover_agent", fake_discover)
    monkeypatch.setattr(client_runtime, "create_client", fake_create)
    runtime = await build_demo_runtime(
        offline_config,
        "outage",
        "auto",
        "sse",
        "direct",
        protocol=PROTOCOL_PURE,
        env_path=tmp_path / "missing.env",
    )
    try:
        with pytest.raises(ClientRuntimeError, match="outer Task id"):
            await runtime.run(
                user_request="modify the running task",
                run_mode="modify",
                target_task_id="task-1",
                target_context_id="ctx-1",
            )
    finally:
        await runtime.aclose()


def test_importing_the_server_does_not_import_adk() -> None:
    """The server is a plain A2A/A2A-T service: its assembly must not load Google ADK."""
    sample_root = Path(__file__).resolve().parents[1]
    script = (
        "import sys\n"
        "import protocol.server_app\n"
        "assert 'google.adk' not in sys.modules, 'google.adk imported'\n"
        "assert 'google.genai' not in sys.modules, 'google.genai imported'\n"
        "print('server-import-ok')\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(sample_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(sample_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "server-import-ok" in result.stdout


def test_importing_the_client_runtime_does_not_build_a_server_model() -> None:
    """The client process must not import the server assembly (which used to build LiteLlm).

    A fresh interpreter is required: every other test module of this process already imported
    ``protocol.server_app`` for its AgentCard tests.
    """
    sample_root = Path(__file__).resolve().parents[1]
    script = (
        "import sys\n"
        "import protocol.client_runtime\n"
        "assert 'protocol.server_app' not in sys.modules, 'server assembly imported'\n"
        "assert 'agents.server_agent.agent' not in sys.modules, 'server agent imported'\n"
        "assert 'agents.client_agent.agent' not in sys.modules, 'client agent imported'\n"
        "print('client-import-ok')\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(sample_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["A2AT_LANGUAGE"] = "en-US"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(sample_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "client-import-ok" in result.stdout


async def test_nms_runtime_uses_the_raw_transport_and_skips_the_a2a_sdk_client(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    import protocol.client_runtime as client_runtime
    from protocol.nms_client import NmsSseTransport

    async def fake_discover(**kwargs: object) -> DiscoveredAgent:
        return _card()

    async def fail_create(*args: object, **kwargs: object) -> object:
        raise AssertionError("the NMS runtime must not build an a2a-sdk client")

    monkeypatch.setattr(client_runtime, "discover_agent", fake_discover)
    monkeypatch.setattr(client_runtime, "create_client", fail_create)
    monkeypatch.setenv("A2AT_LANGUAGE", offline_config.language)

    nms_config = replace(
        offline_config,
        discovery="nms",
        nms_a2a_base="https://nms.example:31127/a2a/json",
        nms_token_url="https://nms.example:27417/rest/plat/smapp/v1/sessions",
        nms_token_user="northbound",
        nms_token_value="secret",
    )
    runtime = await build_demo_runtime(
        nms_config,
        "information",
        "manual",
        "sse",
        "nms",
        env_path=tmp_path / "missing.env",
    )

    assert isinstance(runtime.transport, NmsSseTransport)
    assert runtime.nms_client is not None
    assert runtime.a2a_client is None
    assert runtime._httpx_client is None
    await runtime.aclose()


def test_sdk_env_follows_a_shell_adk_model_over_the_source_sdk_keys(
    offline_config: SampleConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exporting only ADK_MODEL_* must switch the SDK facade to the same lab endpoint."""
    source = tmp_path / ".env"
    source.write_text(
        "A2AT_LLM_MODEL=deepseek-from-file\nA2AT_LLM_BASE_URL=http://127.0.0.1:31943/v1\nA2AT_LLM_API_KEY=file-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ADK_MODEL_NAME", "openai/qwen3-32b")
    monkeypatch.setenv("ADK_MODEL_API_BASE", "http://71.77.65.40:8008/v1")
    monkeypatch.setenv("ADK_MODEL_API_KEY", "lab-key")
    live_config = replace(
        offline_config,
        model_mode=MODEL_MODE_LIVE,
        adk_model_name="openai/qwen3-32b",
        adk_model_api_base="http://71.77.65.40:8008/v1",
        adk_model_api_key="lab-key",
    )

    values = dotenv_values(prepare_client_sdk_env(live_config, source).path)

    assert values["A2AT_LLM_MODEL"] == "qwen3-32b"
    assert values["A2AT_LLM_BASE_URL"] == "http://71.77.65.40:8008/v1"
    assert values["A2AT_LLM_API_KEY"] == "lab-key"
