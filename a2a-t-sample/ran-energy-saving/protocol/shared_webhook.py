"""Process-level webhook callback shared by every client run.

A callback port is a process resource, not a run resource. Starting one receiver per user request
meant that a run still waiting for an NMS push (the idle timeout can hold it for minutes) blocked
the next click with ``WinError 10048`` on port 9000. Configuration is chosen by the launch script,
so the receiver is started once for the process and every run uses it.

Runs that share the receiver are serialized by the run manager: one callback queue cannot safely
serve two interleaved task streams.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from shared.config import SampleConfig
from shared.webhook_receiver import (
    WebhookReceiver,
    start_receiver_with_fallback,
)

from protocol.client_flow import build_webhook_receiver

_LOGGER = logging.getLogger("ran_energy_saving.shared_webhook")


@dataclass
class SharedWebhook:
    """One process-level receiver and the queue feeding the active run."""

    receiver: WebhookReceiver
    queue: asyncio.Queue[dict[str, object]]
    loop: asyncio.AbstractEventLoop

    @property
    def url(self) -> str:
        """Callback URL to register in every task push config."""
        return self.receiver.url


#: Receivers keyed by loop and callback settings. Keeping the loop in the key keeps test event
#: loops (and a reloaded dev server) from reusing a receiver bound to a closed loop.
_shared: dict[tuple[object, ...], SharedWebhook] = {}


def _signature(config: SampleConfig) -> tuple[object, ...]:
    """Return the settings that identify one callback listener."""
    return (
        config.webhook_host,
        config.webhook_port,
        config.webhook_public_url,
        config.webhook_tls_cert,
        config.webhook_tls_key,
        config.webhook_auth_credentials,
        config.webhook_token,
    )


async def get_shared_webhook(config: SampleConfig) -> SharedWebhook:
    """Return the process receiver for ``config``, starting it on first use.

    Args:
        config: resolved sample configuration selecting the callback address and credentials.

    Returns:
        The long-lived receiver and its event queue.
    """
    loop = asyncio.get_running_loop()
    key = (id(loop), *_signature(config))
    existing = _shared.get(key)
    if existing is not None and not existing.loop.is_closed():
        return existing
    if existing is not None:
        _shared.pop(key, None)

    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

    def _make_receiver(port: int) -> WebhookReceiver:
        """Build the receiver for one callback port."""
        return build_webhook_receiver(
            token=config.webhook_token,
            queue=queue,
            host=config.webhook_host,
            port=port,
            public_url=config.webhook_public_url,
            tls_cert=config.webhook_tls_cert,
            tls_key=config.webhook_tls_key,
            auth_credentials=config.webhook_auth_credentials,
        )

    receiver = await start_receiver_with_fallback(
        make=_make_receiver,
        port=config.webhook_port,
        public_url=config.webhook_public_url,
    )
    shared = SharedWebhook(receiver=receiver, queue=queue, loop=loop)
    _shared[key] = shared
    _LOGGER.info("shared webhook ready: url=%s", shared.url)
    return shared


def drain_shared_webhook(shared: SharedWebhook) -> None:
    """Drop pushes left over from a previous run before a new one starts."""
    while True:
        try:
            shared.queue.get_nowait()
        except asyncio.QueueEmpty:
            return


async def close_shared_webhooks() -> None:
    """Stop every process receiver (Web UI shutdown / tests)."""
    entries = list(_shared.values())
    _shared.clear()
    for shared in entries:
        try:
            await shared.receiver.stop()
        except Exception:  # noqa: BLE001 - shutdown is best effort
            _LOGGER.warning("shared webhook stop failed", exc_info=True)


__all__: list[str] = [
    "SharedWebhook",
    "close_shared_webhooks",
    "drain_shared_webhook",
    "get_shared_webhook",
]
