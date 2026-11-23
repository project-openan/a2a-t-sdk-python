"""Independent OSP judge client.

The tester's decision-quality appendix (D8/D14) needs a model that is *not* the model that
authored the negotiation decisions. This module wraps the SDK's OpenAI-compatible structured call
behind a tiny surface so the quality checker can ask for one verdict and still be testable with an
injected transport.

A judge that is not configured, whose transport fails, or whose reply is not a JSON object must say
so (:class:`JudgeUnavailable`); it never fabricates a verdict. ``api_key`` may be empty because a
local OpenAI-compatible endpoint does not authenticate.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from a2a_t.llm import LLMClientConfig, LLMClientFactory

from shared.config import (
    DEFAULT_JUDGE_MAX_TOKENS,
    DEFAULT_JUDGE_TIMEOUT_SECONDS,
    SampleConfig,
)

#: Provider used for the OpenAI-compatible judge endpoint.
_JUDGE_PROVIDER = "openai"

#: Permissive object schema: the judge's caller decides which fields it needs, and OpenAI's
#: ``json_object`` mode accepts any JSON object.
_DEFAULT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}


class JudgeUnavailable(RuntimeError):  # noqa: N818 - public name fixed by the P2 contract
    """Raised when the judge cannot produce a trustworthy verdict."""


@dataclass(frozen=True)
class JudgeConfig:
    """Resolved endpoint and budget of the independent judge.

    Attributes:
        model: model id sent to the judge endpoint.
        api_base: OpenAI-compatible endpoint base URL.
        api_key: API key; empty for a local endpoint that does not authenticate.
        timeout_seconds: budget of one judge call.
        max_tokens: completion cap of one judge call.
    """

    model: str
    api_base: str
    api_key: str = ""
    timeout_seconds: float = DEFAULT_JUDGE_TIMEOUT_SECONDS
    max_tokens: int = DEFAULT_JUDGE_MAX_TOKENS

    @property
    def configured(self) -> bool:
        """Return whether a model and an endpoint are both configured."""
        return bool(self.model and self.api_base)


@dataclass(frozen=True)
class JudgeRequest:
    """One transport-agnostic judge request (the real transport and test doubles both use it).

    Attributes:
        model: model id.
        api_base: endpoint base URL.
        api_key: API key (may be empty).
        system: system prompt.
        prompt: user prompt.
        timeout_seconds: call budget.
        max_tokens: completion cap.
        json_schema: schema the caller wants the structured call to follow; ``None`` uses the
            permissive object schema.
    """

    model: str
    api_base: str
    api_key: str
    system: str
    prompt: str
    timeout_seconds: float
    max_tokens: int
    json_schema: dict[str, Any] | None = None


#: Sends one request and returns the raw model content. Async because a real call is off-loop.
JudgeTransport = Callable[[JudgeRequest], Awaitable[str]]

#: System prompt of the judge. It deliberately does not name a protocol or an implementation.
_DEFAULT_SYSTEM = (
    "You are an independent reviewer of a client agent's negotiation decision. Compare the "
    "expected negotiation reply with what the client actually decided. Decide whether the "
    "observed reply conveys the same substantive agreement (verdict), whether it stays within "
    "the client's stated conditions (covered), and explain the answer briefly. Never assume "
    "hidden context: if the evidence is insufficient, answer unclear."
)


def _openai_transport(config: JudgeConfig) -> JudgeTransport:
    """Build the real transport over the SDK's OpenAI-compatible structured call."""
    state: dict[str, Any] = {}

    def client() -> Any:
        created = state.get("client")
        if created is None:
            created = LLMClientFactory.create(
                _JUDGE_PROVIDER,
                LLMClientConfig(
                    provider=_JUDGE_PROVIDER,
                    model=config.model,
                    # The SDK client rejects an empty key; a local endpoint ignores a placeholder.
                    api_key=config.api_key or "not-needed",
                    base_url=config.api_base,
                    history_window=10,
                    max_tokens=config.max_tokens,
                    temperature=0.0,
                    timeout_seconds=config.timeout_seconds,
                    session_max_total=300,
                    session_max_per_provider=100,
                ),
            )
            state["client"] = created
        return created

    async def send(request: JudgeRequest) -> str:
        messages = [
            {"role": "system", "content": request.system or _DEFAULT_SYSTEM},
            {"role": "user", "content": request.prompt},
        ]
        response = await asyncio.to_thread(
            client().structured,
            messages=messages,
            json_schema=request.json_schema or _DEFAULT_JSON_SCHEMA,
            temperature=0.0,
            max_tokens=request.max_tokens,
        )
        return str(response.content or "")

    return send


class JudgeClient:
    """Separate structured LLM used to judge client negotiation decisions.

    Attributes:
        configured: whether a model and endpoint are present.
    """

    def __init__(self, config: JudgeConfig, *, transport: JudgeTransport | None = None) -> None:
        self._config = config
        self._transport = transport if transport is not None else _openai_transport(config)

    @property
    def configured(self) -> bool:
        """Return whether a model and endpoint are present."""
        return self._config.configured

    async def complete_json(
        self,
        prompt: str,
        *,
        system: str = "",
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        """Run one structured judge call and return the parsed JSON object.

        Raises:
            JudgeUnavailable: when the judge is unconfigured, the transport fails, or the content
                is not a JSON object. ``json_schema`` is accepted for callers that need to show
                the expected shape to the model; the transport owns how it is sent.
        """
        if not self.configured:
            raise JudgeUnavailable(
                "the independent judge is not configured; set "
                "A2AT_OSP_JUDGE_MODEL and A2AT_OSP_JUDGE_API_BASE"
            )
        request = JudgeRequest(
            model=self._config.model,
            api_base=self._config.api_base,
            api_key=self._config.api_key,
            system=system or _DEFAULT_SYSTEM,
            prompt=prompt,
            timeout_seconds=self._config.timeout_seconds,
            max_tokens=self._config.max_tokens,
            json_schema=json_schema,
        )
        try:
            raw = await self._transport(request)
        except JudgeUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - any transport failure means "unavailable"
            raise JudgeUnavailable(f"judge transport failed: {exc}") from exc
        try:
            data = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise JudgeUnavailable(f"judge returned non-JSON content: {raw[:200]!r}") from exc
        if not isinstance(data, dict):
            raise JudgeUnavailable(
                f"judge returned {type(data).__name__}, expected a JSON object"
            )
        return data


def _judge_config(config: SampleConfig | JudgeConfig) -> JudgeConfig:
    """Resolve a :class:`JudgeConfig` from the sample config or an already-built one."""
    if isinstance(config, JudgeConfig):
        return config
    return JudgeConfig(
        model=str(getattr(config, "judge_model", "") or "").strip(),
        api_base=str(getattr(config, "judge_api_base", "") or "").strip(),
        api_key=str(getattr(config, "judge_api_key", "") or "").strip(),
        timeout_seconds=float(
            getattr(config, "judge_timeout_seconds", DEFAULT_JUDGE_TIMEOUT_SECONDS)
        ),
        max_tokens=int(getattr(config, "judge_max_tokens", DEFAULT_JUDGE_MAX_TOKENS)),
    )


def build_judge_client(
    config: SampleConfig | JudgeConfig,
    *,
    transport: JudgeTransport | None = None,
) -> JudgeClient:
    """Build the judge client from the resolved sample configuration.

    ``transport`` is the test seam: when supplied, no SDK client is created, so a configured
    judge can be exercised offline. An unconfigured judge still builds, and every call then
    raises :class:`JudgeUnavailable`.
    """
    return JudgeClient(_judge_config(config), transport=transport)
