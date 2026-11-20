"""Webhook push sender of the NMS simulator.

``message:send`` returns the first event synchronously and every later state transition is POSTed
to the registered ``taskPushNotificationConfig`` URL. The vendor authenticates the callback with
``Authorization: Basic <credentials>``; the a2a-sdk push sender additionally carries
``X-A2A-Notification-Token``, so the simulator sends it when the client registered one. Failures
are logged and never affect the request that scheduled them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass
from typing import Any, Final

import httpx

_LOGGER = logging.getLogger("ran_energy_saving.nms_sim")

#: Budget of one push POST.
PUSH_TIMEOUT_SECONDS: Final[float] = 5.0


@dataclass(frozen=True)
class PushConfig:
    """One Task's registered push-notification config.

    Attributes:
        id: Client-generated config id.
        url: Callback URL.
        token: Optional ``X-A2A-Notification-Token``.
        scheme: Authentication scheme (``Basic`` for the vendor).
        credentials: Opaque Basic credentials echoed after the scheme.
    """

    id: str = ""
    url: str = ""
    token: str = ""
    scheme: str = ""
    credentials: str = ""

    @classmethod
    def from_request(cls, body: Mapping[str, object]) -> PushConfig | None:
        """Read ``configuration.taskPushNotificationConfig`` from one request body."""
        configuration = body.get("configuration")
        if not isinstance(configuration, Mapping):
            return None
        raw = configuration.get("taskPushNotificationConfig")
        if not isinstance(raw, Mapping):
            raw = configuration.get("task_push_notification_config")
        if not isinstance(raw, Mapping):
            return None
        authentication = raw.get("authentication")
        auth = authentication if isinstance(authentication, Mapping) else {}
        return cls(
            id=_text(raw.get("id")),
            url=_text(raw.get("url")),
            token=_text(raw.get("token")),
            scheme=_text(auth.get("scheme")),
            credentials=_text(auth.get("credentials")),
        )


class PushConfigExistsError(Exception):
    """A Task already owns a push-notification config."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task push notification config already exists for task {task_id}")


class PushRegistry:
    """Per-Task push configs, plus the config received before its Task existed."""

    def __init__(self) -> None:
        """Create one empty registry."""
        self._by_task: dict[str, PushConfig] = {}
        self._pending_by_context: dict[str, PushConfig] = {}

    def register(self, task_id: str, config: PushConfig, *, explicit: bool) -> None:
        """Attach one config to a Task.

        Args:
            task_id: Task the config belongs to.
            config: Registered callback settings.
            explicit: whether the request named this Task. A negotiation reply that re-registers
                implicitly keeps the original config, matching the client's own note that a second
                registration is rejected by the real NMS.

        Raises:
            PushConfigExistsError: when the Task already has a config and this is a real second
                registration (an explicit Task continuation, or a replayed config id).
        """
        existing = self._by_task.get(task_id)
        if existing is None:
            self._by_task[task_id] = config
            return
        if explicit or (config.id and config.id == existing.id):
            raise PushConfigExistsError(task_id)

    def for_task(self, task_id: str) -> PushConfig | None:
        """Return the config of one Task, if registered."""
        return self._by_task.get(task_id)

    def remember_pending(self, context_id: str, config: PushConfig) -> None:
        """Keep a config that arrived before the Task it will belong to."""
        if context_id:
            self._pending_by_context[context_id] = config

    def take_pending(self, context_id: str) -> PushConfig | None:
        """Return and forget the config held for one context."""
        return self._pending_by_context.pop(context_id, None)


class PushSender:
    """Schedule and deliver webhook pushes without blocking the HTTP request."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """Create one sender.

        Args:
            transport: optional httpx transport (tests inject a ``MockTransport``).
        """
        self._transport = transport
        self._tasks: set[asyncio.Task[None]] = set()

    def run(self, work: Coroutine[Any, Any, None]) -> None:
        """Run one background coroutine and keep it referenced until it finishes."""
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        """Wait for every scheduled background coroutine (tests and shutdown)."""
        while self._tasks:
            pending = tuple(self._tasks)
            await asyncio.gather(*pending, return_exceptions=True)
            for task in pending:
                self._tasks.discard(task)

    async def send(self, config: PushConfig | None, frame: Mapping[str, object]) -> None:
        """POST one ``statusUpdate``/``artifactUpdate`` frame to the registered URL."""
        if config is None or not config.url:
            return
        headers = {"Content-Type": "application/json"}
        if config.scheme.lower() == "basic" and config.credentials:
            headers["Authorization"] = f"Basic {config.credentials}"
        if config.token:
            headers["X-A2A-Notification-Token"] = config.token
        try:
            async with self._client() as client:
                response = await client.post(config.url, json=dict(frame), headers=headers)
        except httpx.HTTPError as exc:
            _LOGGER.warning("NMS push to %s failed: %s", config.url, exc)
            return
        if response.status_code >= 400:
            _LOGGER.warning(
                "NMS push to %s rejected with HTTP %s: %s",
                config.url,
                response.status_code,
                response.text[:200],
            )

    def _client(self) -> httpx.AsyncClient:
        """Build one push client; ``trust_env=False`` keeps proxies out of loopback callbacks."""
        return httpx.AsyncClient(
            trust_env=False,
            timeout=PUSH_TIMEOUT_SECONDS,
            transport=self._transport,
        )


def _text(value: object) -> str:
    """Return a stripped string value, empty for anything else."""
    return value.strip() if isinstance(value, str) else ""
