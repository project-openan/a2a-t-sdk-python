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

from demo.scenario import LANGUAGE_EN  # noqa: E402
from shared.config import (  # noqa: E402
    DISCOVERY_REGISTRY,
    MODEL_MODE_OFFLINE,
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
        "test_mie_agent",
        "test_net_proxy_integration",
        "test_observe_page",
        "test_oss_agent",
        "test_pure_integration",
        "test_run_demo",
        "test_scenarios",
        "test_tester_console",
        "test_tester_service",
        "test_transports",
        "test_web_presentation",
        "test_web_static",
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
def install_offline_providers() -> None:
    """Register both scripted LLM providers for every test.

    Several integration tests build the real ``A2ATClient`` with the ``scripted_traced`` provider.
    Registering both providers here makes each test independent of file order: running
    ``test_scripted_llm.py`` alone must not depend on another module having registered the traced
    provider first.
    """
    from demo.composition import install_offline_provider

    install_offline_provider()


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
    """Return a fully offline sample configuration (scripted LLM + fake ADK model)."""
    return SampleConfig(
        model_mode=MODEL_MODE_OFFLINE,
        language=LANGUAGE_EN,
        host="127.0.0.1",
        port=8000,
        registry_url="http://127.0.0.1:5001",
        webhook_port=9000,
        webhook_token="demo-webhook-token",
        webhook_idle_timeout=30,
        adk_model_provider="litellm",
        adk_model_name="openai/mass/volcengine/deepseek-v4.1-flash",
        adk_model_api_base="http://127.0.0.1:31943/v1",
        adk_model_api_key="not-needed",
        discovery=DISCOVERY_REGISTRY,
        transport=TRANSPORT_SSE,
        # Existing tests exercise the protocol directly; the processed intent layer has dedicated
        # tests that opt in explicitly.
        intent_mode="raw",
    )
