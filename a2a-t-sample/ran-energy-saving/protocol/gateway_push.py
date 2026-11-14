"""Validation of inbound gateway push-notification registrations.

An external caller supplies the webhook URL, so the gateway must not accept arbitrary outbound
destinations. The wrapper keeps the check independent of the storage backend: the SQLite store and
the SDK's in-memory store both sit behind it.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from a2a.server.context import ServerCallContext
from a2a.server.tasks.push_notification_config_store import PushNotificationConfigStore
from a2a.types import TaskPushNotificationConfig
from a2a.utils.errors import InvalidParamsError
from shared.http_security import is_loopback


def validate_push_url(url: str) -> None:
    """Reject a webhook URL the gateway must not call.

    Args:
        url: caller-supplied webhook URL.

    Raises:
        InvalidParamsError: when the URL is not an absolute http(s) URL, embeds credentials, or uses
            plain HTTP outside loopback.
    """
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InvalidParamsError("Push notification URL must be an absolute http(s) URL.")
    if parsed.username is not None or parsed.password is not None:
        raise InvalidParamsError("Push notification URL must not embed credentials.")
    if parsed.scheme == "http" and not is_loopback(parsed.hostname):
        raise InvalidParamsError(
            "Plain HTTP push URLs are only allowed on loopback; use HTTPS for a remote caller."
        )


class ValidatingPushConfigStore(PushNotificationConfigStore):
    """Validate a webhook URL before delegating to the real configuration store."""

    def __init__(self, inner: PushNotificationConfigStore) -> None:
        """Wrap one store.

        Args:
            inner: SQLite or in-memory ``PushNotificationConfigStore``.
        """
        self._inner = inner

    @property
    def inner(self) -> PushNotificationConfigStore:
        """Return the wrapped store (test introspection)."""
        return self._inner

    async def set_info(
        self,
        task_id: str,
        notification_config: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> None:
        """Validate the URL, then persist the registration."""
        validate_push_url(notification_config.url)
        await self._inner.set_info(task_id, notification_config, context)

    async def get_info(
        self,
        task_id: str,
        context: ServerCallContext,
    ) -> list[TaskPushNotificationConfig]:
        """Return the caller-owned registrations of one task."""
        return await self._inner.get_info(task_id, context)

    async def get_info_for_dispatch(
        self,
        task_id: str,
    ) -> list[TaskPushNotificationConfig]:
        """Return every registration of one task for the dispatch path."""
        return await self._inner.get_info_for_dispatch(task_id)

    async def delete_info(
        self,
        task_id: str,
        context: ServerCallContext,
        config_id: str | None = None,
    ) -> None:
        """Delete one or every caller-owned registration."""
        await self._inner.delete_info(task_id, context, config_id)


__all__ = ["ValidatingPushConfigStore", "validate_push_url"]
