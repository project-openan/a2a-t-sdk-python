"""Helpers for the fully offline, in-process client/server end-to-end tests.

Every test starts a real uvicorn server (in a background thread on an ephemeral port) and drives a
real ``A2ATClient`` against it through :func:`protocol.client_runtime.build_client_runtime`. The
offline model mode makes the whole flow deterministic and network-free; direct discovery avoids the
registry process.

:class:`RecordingTransport` wraps the runtime's transport so the tests can rebuild the complete
performative/round sequence — including the client-side messages, which never appear in the
client's received events — without changing the production flow.
"""

from __future__ import annotations

import socket
import tempfile
import threading
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from a2a.client.client import ClientCallContext
from a2a.types import SendMessageRequest
from demo.agents.server_agent.factory import build_server_agent
from demo.composition import (
    build_demo_negotiation_policy,
    build_demo_runtime,
    build_demo_task_t_precheck,
    install_offline_provider,
)
from demo.scenario import LANGUAGE_EN, ExpectedStep
from protocol.client_flow import ClientFlowResult, ClientTransport
from protocol.client_runtime import MODE_AUTO
from protocol.decision_provider import AdkDecisionProvider
from protocol.messages import is_negotiation_message, metadata_to_dict, parse_negotiation_context
from protocol.server_app import ServerRuntime, build_server_runtime
from shared.config import (
    DISCOVERY_DIRECT,
    MODEL_MODE_OFFLINE,
    TRANSPORT_SSE,
    SampleConfig,
)

#: Budgets of the in-process server: bind/publication and graceful shutdown.
SERVER_START_TIMEOUT_SECONDS: float = 10.0
SERVER_STOP_TIMEOUT_SECONDS: float = 5.0

#: Backstop idle timeout of the webhook client transport used by tests.
TEST_WEBHOOK_IDLE_TIMEOUT_SECONDS: int = 10

#: Temporary SDK ``.env`` directory prefix of the server runtime.
_SERVER_ENV_PREFIX = "a2at-ran-energy-saving-env-"


def free_tcp_port(host: str = "127.0.0.1") -> int:
    """Return an OS-assigned TCP port that is free at the time of the call."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def offline_direct_config(
    port: int,
    *,
    transport: str = TRANSPORT_SSE,
    webhook_port: int = 0,
) -> SampleConfig:
    """Build one offline, direct-discovery configuration bound to ``port``."""
    return SampleConfig(
        model_mode=MODEL_MODE_OFFLINE,
        language=LANGUAGE_EN,
        host="127.0.0.1",
        port=port,
        registry_url="http://127.0.0.1:5001",
        webhook_port=webhook_port,
        webhook_token="demo-webhook-token",
        webhook_idle_timeout=TEST_WEBHOOK_IDLE_TIMEOUT_SECONDS,
        adk_model_provider="litellm",
        adk_model_name="openai/offline-test-model",
        adk_model_api_base="http://127.0.0.1:9/v1",
        adk_model_api_key="not-needed",
        discovery=DISCOVERY_DIRECT,
        transport=transport,
        intent_mode="raw",
    )


def _server_env_dirs() -> set[Path]:
    """Return the temporary SDK ``.env`` directories the server runtime has created so far."""
    return {
        path
        for path in Path(tempfile.gettempdir()).glob(f"{_SERVER_ENV_PREFIX}*")
        if path.is_dir()
    }


@contextmanager
def running_server(
    config: SampleConfig,
    *,
    protocol_mode: str = "a2a-t",
    pure_policy: Any | None = None,
    mie_url: str | None = None,
    approval_gate: bool | None = None,
    approval_timeout_seconds: float | None = None,
) -> Generator[ServerRuntime, None, None]:
    """Run one real server in a background thread and stop it afterwards.

    The server keeps its own event loop; teardown flips ``should_exit`` and joins the thread. The
    temporary SDK ``.env`` directory created by the server runtime is removed on teardown, because
    that runtime does not own a cleanup hook.
    """
    env_dirs_before = _server_env_dirs()
    runtime = build_server_runtime(
        agent_factory=build_server_agent,
        policy=build_demo_negotiation_policy(),
        precheck=build_demo_task_t_precheck(),
        install_offline_provider=install_offline_provider,
        config=config,
        protocol_mode=protocol_mode,
        pure_policy=pure_policy,
        mie_url=mie_url,
        approval_gate=approval_gate,
        approval_timeout_seconds=approval_timeout_seconds,
    )
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(
            runtime.app,
            host=config.host,
            port=config.port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(
        target=uvicorn_server.run,
        name=f"ran-test-server-{config.port}",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    try:
        while not uvicorn_server.started:
            if not thread.is_alive():
                raise RuntimeError("The in-process test server exited before listening")
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"The in-process test server did not start within {SERVER_START_TIMEOUT_SECONDS:g}s"
                )
            time.sleep(0.01)
        yield runtime
    finally:
        uvicorn_server.should_exit = True
        thread.join(timeout=SERVER_STOP_TIMEOUT_SECONDS)
        for path in _server_env_dirs() - env_dirs_before:
            for child in path.rglob("*"):
                if child.is_file():
                    child.unlink(missing_ok=True)
            path.rmdir()


@contextmanager
def running_app_on_port(
    app: Any,
    port: int,
    *,
    host: str = "127.0.0.1",
    label: str = "app",
) -> Generator[None, None, None]:
    """Serve one ASGI app on a known port (needed when its AgentCard advertises that port)."""
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(
        target=uvicorn_server.run, name=f"ran-test-{label}-{port}", daemon=True
    )
    thread.start()
    deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    try:
        while not uvicorn_server.started:
            if not thread.is_alive():
                raise RuntimeError(f"The in-process {label} app exited before listening")
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"The in-process {label} app did not start within "
                    f"{SERVER_START_TIMEOUT_SECONDS:g}s"
                )
            time.sleep(0.01)
        yield
    finally:
        uvicorn_server.should_exit = True
        thread.join(timeout=SERVER_STOP_TIMEOUT_SECONDS)


@contextmanager
def running_http_app(app: Any, *, host: str = "127.0.0.1") -> Generator[str, None, None]:
    """Serve one ASGI app on an ephemeral port and yield its base URL.

    ``httpx.ASGITransport`` buffers a response body before the caller sees it, which hides whether
    an SSE stream forwards frames produced after the caller consumed ``finished``. A real uvicorn
    server keeps the stream interactive (the Run Explorer's live behavior).
    """
    port = free_tcp_port(host)
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=uvicorn_server.run, name=f"ran-test-ui-{port}", daemon=True)
    thread.start()
    deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    try:
        while not uvicorn_server.started:
            if not thread.is_alive():
                raise RuntimeError("The in-process Web UI exited before listening")
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"The in-process Web UI did not start within {SERVER_START_TIMEOUT_SECONDS:g}s"
                )
            time.sleep(0.01)
        yield f"http://{host}:{port}"
    finally:
        uvicorn_server.should_exit = True
        thread.join(timeout=SERVER_STOP_TIMEOUT_SECONDS)


@dataclass
class RecordedExchange:
    """One captured protocol exchange of a client flow.

    Attributes:
        steps: Performative/round sequence in chronological order, both directions.
        events: Every normalized event the client received, in arrival order.
    """

    steps: list[ExpectedStep]
    events: list[dict[str, object]]


class RecordingTransport:
    """Transport decorator recording the client-side messages and the received batch.

    ``send`` is called once per client message. The outbound negotiation message is recorded before
    the await (chronologically before the server answer); the inbound batch's message events are
    recorded after it. Non-negotiation inbound messages (the plain end message) become
    ``MESSAGE``/``round=None`` entries.
    """

    def __init__(self, inner: ClientTransport) -> None:
        self._inner = inner
        self.steps: list[ExpectedStep] = []
        self.events: list[dict[str, object]] = []

    def set_event_sink(self, event_sink: Any | None) -> None:
        """Delegate the live event sink hook to the wrapped transport, when it supports it."""
        setter = getattr(self._inner, "set_event_sink", None)
        if callable(setter):
            setter(event_sink)

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        """Send one request through the wrapped transport and record both directions."""
        self._record_outbound(request)
        batch = await self._inner.send(request, call_context)
        self._record_inbound(batch)
        self.events.extend(batch)
        return batch

    def _record_outbound(self, request: SendMessageRequest) -> None:
        """Record the negotiation message of one outbound request, when it carries one."""
        metadata = metadata_to_dict(request.message.metadata)
        if not is_negotiation_message(metadata):
            return
        session = parse_negotiation_context(metadata)
        if session is None:
            return
        self.steps.append(
            ExpectedStep(
                actor="client",
                performative=session.performative.value,
                round=session.round,
            )
        )

    def _record_inbound(self, batch: list[dict[str, object]]) -> None:
        """Record the server messages of one received batch."""
        for event in batch:
            if event.get("kind") != "message":
                continue
            metadata = event.get("metadata")
            if isinstance(metadata, Mapping):
                metadata_dict = metadata_to_dict(metadata)
                if is_negotiation_message(metadata_dict):
                    session = parse_negotiation_context(metadata_dict)
                    if session is None:
                        continue
                    self.steps.append(
                        ExpectedStep(
                            actor="server",
                            performative=session.performative.value,
                            round=session.round,
                        )
                    )
                    continue
            # A non-negotiation message is the server's plain end message (design §8, reject).
            self.steps.append(ExpectedStep(actor="server", performative="MESSAGE", round=None))

    def exchange(self) -> RecordedExchange:
        """Return the recorded sequence and events as one snapshot."""
        return RecordedExchange(steps=list(self.steps), events=list(self.events))


async def run_scenario_flow(
    config: SampleConfig,
    scenario_name: str,
    transport: str,
) -> tuple[ClientFlowResult, RecordedExchange]:
    """Run one complete offline ``auto``-mode scenario against the configured server.

    Args:
        config: offline direct-discovery configuration pointing at the running test server.
        scenario_name: one of the five demonstration scenarios.
        transport: ``sse`` or ``webhook``.

    Returns:
        The client flow result and the recorded two-way performative sequence.

    Raises:
        AssertionError: when the auto mode silently used a different decision provider.
    """
    client = await build_demo_runtime(config, scenario_name, MODE_AUTO, transport, config.discovery)
    assert isinstance(client.provider, AdkDecisionProvider), (
        "The auto mode must keep the ADK runner in the decision loop"
    )
    recorder = RecordingTransport(client.transport)
    client.transport = recorder
    try:
        result = await client.run()
    finally:
        await client.aclose()
    return result, recorder.exchange()
