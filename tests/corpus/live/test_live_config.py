"""Unit tests of the live-family gating (D24): the ``A2AT_TEST_LLM_*`` environment matrix, the
skip gate, the ``.env`` bridge and the ``live`` marker filtering.

Port of the Java ``LiveLlmConfigTest`` and ``LiveLlmEnvWriterTest``, plus the Python-specific
marker assertions: the three required variables gate the family exactly like the Java environment
variables do (any missing one means skip, never fail), and ``-m 'not live'`` deselects the live
suite — the default CI exclusion. The tests stay hermetic: every resolution runs against an
injected environment mapping, and the marker assertions drive pytest in a subprocess with the
live variables scrubbed, so a developer machine that configured the live endpoint still runs the
offline gating matrix.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from a2a_t.llm.providers.openai import OpenAIClient
from tests.corpus.conftest import expanded_live_cases
from tests.corpus.live.config import (
    API_KEY_VARIABLE,
    BASE_URL_VARIABLE,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_SECONDS,
    MODEL_VARIABLE,
    TEMPERATURE_VARIABLE,
    TIMEOUT_SECONDS_VARIABLE,
    LiveLlmConfig,
    assume_configured,
    create_llm_client,
)
from tests.corpus.live.env_writer import env_file_for

#: Repository root (the subprocess pytest invocations run from here).
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

_CONFIGURED_ENV: Final[dict[str, str]] = {
    BASE_URL_VARIABLE: "https://live.example.test/v1",
    API_KEY_VARIABLE: "live-key",
    MODEL_VARIABLE: "qwen3-27b",
}

_LIVE_ENV_VARIABLES: Final[tuple[str, ...]] = (
    BASE_URL_VARIABLE,
    API_KEY_VARIABLE,
    MODEL_VARIABLE,
    TEMPERATURE_VARIABLE,
    TIMEOUT_SECONDS_VARIABLE,
)


def _hermetic_environ() -> dict[str, str]:
    """The process environment with every live-family variable scrubbed."""
    return {key: value for key, value in os.environ.items() if key not in _LIVE_ENV_VARIABLES}


# --------------------------------------------------------------------- gating matrix


def test_absent_when_no_required_variable_is_configured() -> None:
    assert LiveLlmConfig.from_current_process({}) is None


@pytest.mark.parametrize(
    "present",
    [
        pytest.param({BASE_URL_VARIABLE}, id="base-url-only"),
        pytest.param({API_KEY_VARIABLE}, id="api-key-only"),
        pytest.param({MODEL_VARIABLE}, id="model-only"),
        pytest.param({BASE_URL_VARIABLE, API_KEY_VARIABLE}, id="no-model"),
        pytest.param({BASE_URL_VARIABLE, MODEL_VARIABLE}, id="no-api-key"),
        pytest.param({API_KEY_VARIABLE, MODEL_VARIABLE}, id="no-base-url"),
    ],
)
def test_null_when_only_partially_configured(present: set[str]) -> None:
    """Any missing required variable means the family is not configured (skip, not a
    half-configured run)."""
    environ = {key: value for key, value in _CONFIGURED_ENV.items() if key in present}

    assert LiveLlmConfig.from_current_process(environ) is None


def test_all_three_required_variables_configure_the_family() -> None:
    config = LiveLlmConfig.from_current_process(_CONFIGURED_ENV)

    assert config is not None
    assert config.base_url == "https://live.example.test/v1"
    assert config.api_key == "live-key"
    assert config.model == "qwen3-27b"


def test_blank_value_counts_as_not_configured() -> None:
    environ = {**_CONFIGURED_ENV, API_KEY_VARIABLE: "   "}

    assert LiveLlmConfig.from_current_process(environ) is None, "a blank value is an unset value"


def test_surrounding_whitespace_is_trimmed() -> None:
    environ = {**_CONFIGURED_ENV, MODEL_VARIABLE: "  qwen3-27b  "}

    config = LiveLlmConfig.from_current_process(environ)
    assert config is not None
    assert config.model == "qwen3-27b"


def test_optional_values_fall_back_to_their_defaults() -> None:
    config = LiveLlmConfig.from_current_process(_CONFIGURED_ENV)

    assert config is not None
    assert config.temperature == DEFAULT_TEMPERATURE
    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS


def test_optional_values_are_honored_when_set() -> None:
    environ = {**_CONFIGURED_ENV, TEMPERATURE_VARIABLE: "0.2", TIMEOUT_SECONDS_VARIABLE: "120"}

    config = LiveLlmConfig.from_current_process(environ)
    assert config is not None
    assert config.temperature == "0.2"
    assert config.timeout_seconds == "120"


def test_the_process_environment_resolves_the_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default resolution channel reads :data:`os.environ` (the D24 variable names)."""
    for name in _LIVE_ENV_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(BASE_URL_VARIABLE, "https://live.example.test/v1")
    monkeypatch.setenv(API_KEY_VARIABLE, "live-key")
    monkeypatch.setenv(MODEL_VARIABLE, "qwen3-27b")

    config = LiveLlmConfig.from_current_process()

    assert config is not None
    assert config.model == "qwen3-27b"


# --------------------------------------------------------------------- skip gate


def test_assume_configured_skips_when_the_environment_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LIVE_ENV_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(pytest.skip.Exception) as skipped:
        assume_configured()

    assert "live LLM validation is disabled" in str(skipped.value)
    for required in (BASE_URL_VARIABLE, API_KEY_VARIABLE, MODEL_VARIABLE):
        assert required in str(skipped.value), f"the skip hint names the missing variable {required}"


def test_assume_configured_skips_on_a_partial_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LIVE_ENV_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(MODEL_VARIABLE, "qwen3-27b")

    with pytest.raises(pytest.skip.Exception):
        assume_configured()


# --------------------------------------------------------------------- configuration probe


def test_valid_configuration_builds_the_real_openai_client() -> None:
    config = LiveLlmConfig.from_current_process(_CONFIGURED_ENV)

    assert config is not None
    assert config.validation_error() is None, "the defaults pass the production validation"
    client_config = config.to_llm_client_config()
    assert client_config.provider == "openai"
    assert client_config.model == "qwen3-27b"
    assert client_config.temperature == 0.0
    assert client_config.timeout_seconds == 60.0
    assert isinstance(create_llm_client(config), OpenAIClient)


def test_invalid_optional_value_surfaces_as_a_configuration_failure() -> None:
    environ = {**_CONFIGURED_ENV, TEMPERATURE_VARIABLE: "not-a-number"}

    config = LiveLlmConfig.from_current_process(environ)

    assert config is not None
    error = config.validation_error()
    assert error is not None, "the production validation rejects the non-numeric temperature"
    assert "A2AT_LLM_TEMPERATURE" in error, f"the error names the offending variable: {error}"


def test_create_llm_client_surfaces_a_configuration_failure() -> None:
    environ = {**_CONFIGURED_ENV, TEMPERATURE_VARIABLE: "not-a-number"}

    config = LiveLlmConfig.from_current_process(environ)
    assert config is not None

    with pytest.raises(RuntimeError) as thrown:
        create_llm_client(config)

    assert "live LLM configuration is invalid" in str(thrown.value)


def test_assume_configured_fails_red_on_an_invalid_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LIVE_ENV_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(BASE_URL_VARIABLE, "https://live.example.test/v1")
    monkeypatch.setenv(API_KEY_VARIABLE, "live-key")
    monkeypatch.setenv(MODEL_VARIABLE, "qwen3-27b")
    monkeypatch.setenv(TEMPERATURE_VARIABLE, "not-a-number")

    with pytest.raises(RuntimeError) as thrown:
        assume_configured()

    assert "live LLM configuration is invalid" in str(thrown.value)
    assert "A2AT_LLM_TEMPERATURE" in str(thrown.value)


# --------------------------------------------------------------------- env bridge


def test_env_file_carries_the_live_bridge_entries() -> None:
    env_file = env_file_for(LiveLlmConfig("https://live.example.test/v1", "live-key", "qwen3-27b", "0", "60"))

    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "A2AT_LANGUAGE=zh-CN",
        "A2AT_PROMPT_SOURCE_TYPE=packaged",
        "A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR=",
        "A2AT_LLM_PROVIDER=openai",
        "A2AT_LLM_MODEL=qwen3-27b",
        "A2AT_LLM_API_KEY=live-key",
        "A2AT_LLM_BASE_URL=https://live.example.test/v1",
        "A2AT_LLM_TEMPERATURE=0",
        "A2AT_LLM_TIMEOUT_SECONDS=60",
        "A2AT_LLM_MAX_ATTEMPTS=3",
    ]


def test_overridden_optional_values_flow_into_the_env_file() -> None:
    env_file = env_file_for(LiveLlmConfig("https://live.example.test/v1", "live-key", "qwen3-27b", "0.2", "120"))

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines[7] == "A2AT_LLM_TEMPERATURE=0.2"
    assert lines[8] == "A2AT_LLM_TIMEOUT_SECONDS=120"


def test_env_file_is_cached_per_distinct_configuration() -> None:
    config = LiveLlmConfig("https://live.example.test/v1", "live-key", "qwen3-27b", "0", "60")
    other_temperature = LiveLlmConfig("https://live.example.test/v1", "live-key", "qwen3-27b", "0.2", "60")

    assert env_file_for(config) == env_file_for(config)
    assert env_file_for(config) != env_file_for(other_temperature)


# --------------------------------------------------------------------- marker filtering


def _pytest_collect(args: list[str]) -> str:
    """Run pytest in a subprocess with the live variables scrubbed and return its output."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=REPO_ROOT,
        env=_hermetic_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"pytest {args} failed:\n{output}"
    return output


def test_the_live_suite_is_collected_when_the_environment_resolves() -> None:
    """With the three required variables present the live cases stay in the collection."""
    environ = {**_hermetic_environ(), **_CONFIGURED_ENV}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/corpus/live", "--collect-only", "-q"],
        cwd=REPO_ROOT,
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert output.count("test_live_corpus_suite.py::test_live_corpus_case") == len(expanded_live_cases("live"))


def test_marker_not_live_deselects_the_live_suite() -> None:
    """``-m 'not live'`` deselects every live-marked test — the default CI exclusion."""
    unfiltered = _pytest_collect(["tests/corpus/live", "--collect-only", "-q"])
    filtered = _pytest_collect(["tests/corpus/live", "--collect-only", "-q", "-m", "not live"])

    assert unfiltered.count("test_live_corpus_suite.py::test_live_corpus_case") == len(expanded_live_cases("live"))
    assert "test_live_corpus_suite" not in filtered, "the live suite must be deselected by -m 'not live'"
    assert "deselected" in filtered


def test_the_live_suite_skips_without_the_environment() -> None:
    """Without the required variables the live family skips (never fails, never hangs)."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/corpus/live/test_live_corpus_suite.py", "-q"],
        cwd=REPO_ROOT,
        env=_hermetic_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert f"{len(expanded_live_cases('live'))} skipped" in output, (
        f"every live case must skip through the configuration gate:\n{output}"
    )
    assert "failed" not in output, f"a skip must never turn into a failure:\n{output}"


def test_the_live_suite_carries_the_live_marker() -> None:
    """The live suite module is marked, so the marker contract of the family holds."""
    import tests.corpus.live.test_live_corpus_suite as suite

    declared = suite.pytestmark
    marks = declared if isinstance(declared, list) else [declared]
    assert any(getattr(mark, "name", None) == "live" for mark in marks)
