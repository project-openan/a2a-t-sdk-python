"""Pytest bootstrap for the RAN energy-saving ADK sample.

The sample root is added to ``sys.path`` so ``shared``, ``agents``, ``registry``, and ``protocol``
import as top-level packages without an installation step.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SAMPLE_ROOT = Path(__file__).resolve().parents[1]
if str(SAMPLE_ROOT) not in sys.path:
    sys.path.insert(0, str(SAMPLE_ROOT))
TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from demo.scenario import LANGUAGE_EN  # noqa: E402
from shared.config import (  # noqa: E402
    DISCOVERY_DIRECT,
    MODEL_MODE_LIVE,
    SAMPLE_ENV_PREFIXES,
    TRANSPORT_SSE,
    SampleConfig,
)


#: Nuisance warnings of the third-party a2a/ADK stack and of the SDK's retired negotiation modules.
#:
#: The a2a-sdk protobuf helper still calls the deprecated ``FieldDescriptor.label()``; ADK marks its
#: A2A integration and the JSON-schema function declaration experimental; and importing
#: ``A2ATClient`` pulls in the retired ``a2a_t.negotiation.*`` state-machine demo modules. None of
#: them indicates a problem in the sample, so the test output stays pristine. The entries match on
#: the warning message and category only, so an unexpected new warning is still reported.
@pytest.fixture(autouse=True)
def _clear_sample_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the shell's sample exports out of the file-based tests.

    The sample resolves the process environment before ``.env``; a developer with
    ``ADK_MODEL_NAME`` or ``A2AT_SAMPLE_*`` exported would otherwise change what every test sees.
    A test that needs an export sets it through ``monkeypatch`` after this fixture has run.
    """
    for key in list(os.environ):
        if key.startswith(SAMPLE_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)


#: Modules of the heavyweight tier: every test in them owns a real server, a subprocess (import
#: manifests, ``node --check``, the export CLI) or a real socket. ``-m "not slow"`` is the fast
#: unit/offline tier; the default run stays the full acceptance gate.
_SLOW_MODULES: frozenset[str] = frozenset(
    {
        "test_client_runtime",
        "test_demo_boundary",
        "test_net_proxy_integration",
        "test_pure_integration",
        "test_run_demo",
        "test_tester_console",
        "test_tester_service",
        "test_transports",
        "test_web_presentation",
        "test_web_ui",
    }
)


def pytest_configure(config: pytest.Config) -> None:
    """Register the targeted warning filters of the sample test suite."""
    config.addinivalue_line(
        "markers",
        "slow: owns a real server, subprocess or socket; skipped by the fast tier "
        "(-m 'not slow')",
    )
    config.addinivalue_line(
        "filterwarnings", r"ignore:label\(\) is deprecated.*:DeprecationWarning"
    )
    config.addinivalue_line("filterwarnings", r"ignore:\[EXPERIMENTAL\].*:UserWarning")
    config.addinivalue_line(
        "filterwarnings",
        r"ignore:The a2a_t\.negotiation\..* is deprecated.*:DeprecationWarning",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark the heavyweight modules so ``-m 'not slow'`` gives a fast feedback tier."""
    for item in items:
        if Path(str(item.path)).stem in _SLOW_MODULES:
            item.add_marker(pytest.mark.slow)


@pytest.fixture(autouse=True)
def install_test_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the test-only deterministic SDK providers and route SDK calls to them."""
    from tests.support.scripted_llm import register_scripted_provider
    from tests.support.traced_scripted import register_traced_scripted_provider

    register_scripted_provider()
    register_traced_scripted_provider()
    import shared.sdk_env as sdk_env

    monkeypatch.setattr(sdk_env, "sdk_provider_name", lambda: "scripted_traced")


@pytest.fixture
def live_sdk_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore the production traced-OpenAI provider for one test (undo the stub)."""
    import shared.sdk_env as sdk_env
    from shared.traced_llm import sdk_provider_name

    monkeypatch.setattr(sdk_env, "sdk_provider_name", sdk_provider_name)


@pytest.fixture(autouse=True)
def stub_live_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve deterministic ADK/planner models so tests never open a network model."""
    from tests.support.adk_fake_model import build_fake_model
    from tests.support.scripted_planner import ScriptedChatModel

    def traced_model(config: object = None, *, model: object = None) -> object:
        del config
        return model if model is not None else build_fake_model()

    import shared.model_factory as model_factory

    monkeypatch.setattr(model_factory, "build_traced_adk_model", traced_model)
    import demo.agents.client_agent.agent as client_agent
    import demo.agents.intent_agent.agent as intent_agent
    import demo.intent as intent_module

    monkeypatch.setattr(client_agent, "build_adk_model", lambda config=None: build_fake_model())
    monkeypatch.setattr(intent_agent, "build_adk_model", lambda config=None: build_fake_model())
    monkeypatch.setattr(intent_module, "build_traced_adk_model", traced_model)

    import demo.agents.server_agent.factory as server_factory
    from demo import ran_data

    def planner_model(config: object = None, *, operation: str = "Create", existing_plan_id: str = "") -> object:
        del config
        return ScriptedChatModel(
            dataset_date=ran_data.dataset_date(),
            operation=operation,
            existing_plan_id=existing_plan_id,
        )

    monkeypatch.setattr(server_factory, "build_planner_model", planner_model)

    import protocol.client_runtime as client_runtime

    monkeypatch.setattr(client_runtime, "build_traced_adk_model", traced_model)


@pytest.fixture
def english_language(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the process language to ``en-US`` for the scripted SDK provider.

    ``ScriptedLLMClient`` resolves the language from the process environment (then ``$CWD/.env``);
    the in-process integration tests pass the language to the sample code through ``SampleConfig``,
    so the environment has to agree or the canned responses would not match the schemas.
    """
    monkeypatch.setenv("A2AT_LANGUAGE", LANGUAGE_EN)


@pytest.fixture
def offline_config() -> SampleConfig:
    """Return a sample configuration whose network clients are replaced by tests."""
    return SampleConfig(
        model_mode=MODEL_MODE_LIVE,
        language=LANGUAGE_EN,
        host="127.0.0.1",
        port=8000,
        webhook_port=9000,
        webhook_token="demo-webhook-token",
        webhook_idle_timeout=30,
        adk_model_provider="litellm",
        adk_model_name="openai/mass/volcengine/deepseek-v4.1-flash",
        adk_model_api_base="http://127.0.0.1:31943/v1",
        adk_model_api_key="not-needed",
        discovery=DISCOVERY_DIRECT,
        transport=TRANSPORT_SSE,
        # Existing tests exercise the protocol directly; the processed intent layer has dedicated
        # tests that opt in explicitly.
        intent_mode="raw",
    )
