"""Contract tests of the independent OSP judge client.

The judge is deliberately separate from the negotiation model (D8/D14): it is an
OpenAI-compatible structured call configured by its own keys, and an unconfigured or failing
judge must say "unavailable" instead of returning a guess.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from shared.config import load_config
from shared.judge_llm import (
    JudgeRequest,
    JudgeUnavailable,
    build_judge_client,
)

#: External keys of the judge endpoint; the config tests pin the same names.
JUDGE_MODEL_ENV_KEY = "A2AT_OSP_JUDGE_MODEL"
JUDGE_API_BASE_ENV_KEY = "A2AT_OSP_JUDGE_API_BASE"
JUDGE_API_KEY_ENV_KEY = "A2AT_OSP_JUDGE_API_KEY"
JUDGE_TIMEOUT_ENV_KEY = "A2AT_OSP_JUDGE_TIMEOUT_SECONDS"
JUDGE_MAX_TOKENS_ENV_KEY = "A2AT_OSP_JUDGE_MAX_TOKENS"


@pytest.fixture(autouse=True)
def _without_judge_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the tests independent of a developer's live-trial shell exports."""
    for key in (
        JUDGE_MODEL_ENV_KEY,
        JUDGE_API_BASE_ENV_KEY,
        JUDGE_API_KEY_ENV_KEY,
        JUDGE_TIMEOUT_ENV_KEY,
        JUDGE_MAX_TOKENS_ENV_KEY,
    ):
        monkeypatch.delenv(key, raising=False)


def _config(
    tmp_path: Path,
    *,
    model: str = "judge-model",
    api_base: str = "http://judge.example/v1",
    api_key: str | None = None,
    timeout: str | None = None,
    max_tokens: str | None = None,
) -> Any:
    lines = []
    if model:
        lines.append(f"{JUDGE_MODEL_ENV_KEY}={model}")
    if api_base:
        lines.append(f"{JUDGE_API_BASE_ENV_KEY}={api_base}")
    if api_key is not None:
        lines.append(f"{JUDGE_API_KEY_ENV_KEY}={api_key}")
    if timeout is not None:
        lines.append(f"{JUDGE_TIMEOUT_ENV_KEY}={timeout}")
    if max_tokens is not None:
        lines.append(f"{JUDGE_MAX_TOKENS_ENV_KEY}={max_tokens}")
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return load_config(env_path=env_file)


def _transport(raw: str) -> Any:
    async def send(request: JudgeRequest) -> str:
        del request
        return raw

    return send


async def test_an_unconfigured_judge_raises_instead_of_guessing(tmp_path: Path) -> None:
    client = build_judge_client(_config(tmp_path, model="", api_base=""))

    assert client.configured is False
    with pytest.raises(JudgeUnavailable):
        await client.complete_json("compare the expected and observed replies")


async def test_a_configured_judge_returns_the_parsed_json_object(tmp_path: Path) -> None:
    seen: list[JudgeRequest] = []

    async def send(request: JudgeRequest) -> str:
        seen.append(request)
        return '{"verdict": "equal", "covered": true, "reason": "same"}'

    client = build_judge_client(
        _config(tmp_path, api_key="judge-secret"), transport=send
    )

    assert client.configured is True
    result = await client.complete_json("compare this", system="custom system")

    assert result == {"verdict": "equal", "covered": True, "reason": "same"}
    assert seen[0].model == "judge-model"
    assert seen[0].api_base == "http://judge.example/v1"
    assert seen[0].api_key == "judge-secret"
    assert seen[0].system == "custom system"
    assert seen[0].prompt == "compare this"


async def test_a_configured_judge_works_without_an_api_key(tmp_path: Path) -> None:
    client = build_judge_client(
        _config(tmp_path, api_key=None),
        transport=_transport(json.dumps({"verdict": "equal", "covered": True})),
    )

    assert client.configured is True
    assert (await client.complete_json("p"))["verdict"] == "equal"


@pytest.mark.parametrize(
    "raw",
    ["not json at all", "[]", "null", "42", '"a string"'],
)
async def test_invalid_json_raises_judge_unavailable(tmp_path: Path, raw: str) -> None:
    client = build_judge_client(_config(tmp_path), transport=_transport(raw))

    with pytest.raises(JudgeUnavailable):
        await client.complete_json("p")


async def test_a_transport_exception_becomes_judge_unavailable(tmp_path: Path) -> None:
    async def boom(request: JudgeRequest) -> str:
        raise RuntimeError("judge endpoint timed out")

    client = build_judge_client(_config(tmp_path), transport=boom)

    with pytest.raises(JudgeUnavailable):
        await client.complete_json("p")


async def test_the_request_carries_the_configured_budget(tmp_path: Path) -> None:
    seen: list[JudgeRequest] = []

    async def send(request: JudgeRequest) -> str:
        seen.append(request)
        return '{"verdict": "equal"}'

    client = build_judge_client(
        _config(tmp_path, timeout="12.5", max_tokens="777"), transport=send
    )

    await client.complete_json("p")

    assert seen[0].timeout_seconds == 12.5
    assert seen[0].max_tokens == 777
