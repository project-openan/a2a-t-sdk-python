"""The webhook callback is a process resource, not a per-run one."""

from __future__ import annotations

from dataclasses import replace

from integration_helpers import free_tcp_port, offline_direct_config
from protocol.shared_webhook import (
    close_shared_webhooks,
    drain_shared_webhook,
    get_shared_webhook,
)
from shared.config import SampleConfig


def _config() -> SampleConfig:
    """Return a direct-mode configuration asking the OS for a callback port."""
    return replace(
        offline_direct_config(free_tcp_port()),
        webhook_port=0,
        webhook_public_url="",
    )


async def test_every_run_reuses_one_receiver_and_port() -> None:
    config = _config()
    first = await get_shared_webhook(config)
    try:
        second = await get_shared_webhook(config)

        assert second is first
        assert second.url == first.url
        assert first.receiver.port != 0
    finally:
        await close_shared_webhooks()


async def test_drain_discards_a_previous_runs_late_push() -> None:
    config = _config()
    shared = await get_shared_webhook(config)
    try:
        shared.queue.put_nowait({"task": {"id": "old-run"}})
        drain_shared_webhook(shared)

        assert shared.queue.empty()
    finally:
        await close_shared_webhooks()
