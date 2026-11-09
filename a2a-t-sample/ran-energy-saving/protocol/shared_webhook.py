"""Process-level webhook callback shared by every client run.

A callback port is a process resource, not a run resource. Starting one receiver per user request
meant that a run still waiting for an NMS push (the idle timeout can hold it for minutes) blocked
the next click with ``WinError 10048`` on port 9000. Configuration is chosen by the launch script,
so the receiver is started once for the process and every run uses it.

The receiver cannot feed one queue: a push only carries the server-side task/context identity, so
two runs sharing a queue would consume each other's events. :class:`WebhookRouter` keeps one queue
per run and routes each push to the queue that owns its ``taskId`` / ``contextId``. A run binds its
identities before it sends (a Modify request already names its Task) and again from the synchronous
RPC reply; a push that beats that bind in flight is held in a small pending buffer and flushed on
bind. That buffer is also what makes a Modify work: the run that continues a Task is the one that
receives its pushes, even when the previous run of the same Task has already released its
subscription. A push is only ever delivered to a run that explicitly bound its identity — never
guessed into whichever run happens to be alone on the receiver.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from shared.config import SampleConfig
from shared.webhook_receiver import (
    WebhookReceiver,
    start_receiver_with_fallback,
)

_LOGGER = logging.getLogger("ran_energy_saving.shared_webhook")

#: How long an unbound or retired task/context identity keeps matching pushes. Bounds the pending
#: buffer when a push names a task this process no longer owns; the value covers the configured
#: webhook idle timeout plus a margin.
_PENDING_TTL_SECONDS = 900.0

#: Hard cap per pending identity: a leaking/attacking sender must not grow the buffer without bound.
_PENDING_LIMIT = 256


def _text(value: object) -> str:
    """Return a stripped string value, empty for anything else."""
    return value.strip() if isinstance(value, str) else ""


def _payload_keys(payload: Mapping[str, object]) -> tuple[str, str]:
    """Return the ``(task_id, context_id)`` identity carried by one StreamResponse JSON body.

    The A2A push body uses the ``MessageToDict`` spelling: a ``task`` document carries ``id`` and
    ``contextId``; status/artifact updates carry ``taskId`` and ``contextId``; a bare message only
    carries ``contextId`` (the pre-task negotiation message is not pushed).
    """
    task = payload.get("task")
    if isinstance(task, Mapping):
        return _text(task.get("id")), _text(task.get("contextId") or task.get("context_id"))
    for key in ("statusUpdate", "artifactUpdate"):
        update = payload.get(key)
        if isinstance(update, Mapping):
            return (
                _text(update.get("taskId") or update.get("task_id")),
                _text(update.get("contextId") or update.get("context_id")),
            )
    message = payload.get("message")
    if isinstance(message, Mapping):
        return (
            _text(message.get("taskId") or message.get("task_id")),
            _text(message.get("contextId") or message.get("context_id")),
        )
    return "", ""


def _identity_key(kind: str, value: str) -> str:
    """Build the lookup key of one task/context identity."""
    return f"{kind}:{value}"


class WebhookRouter:
    """Dispatch webhook pushes to per-run queues by task/context identity.

    The public surface is :meth:`subscribe` for a run, :meth:`bind` once the run learns its
    server-side identities, and :meth:`on_event` as the receiver callback. All methods run on the
    event loop that owns the running receiver, so no locking is needed.
    """

    def __init__(
        self,
        *,
        pending_ttl: float = _PENDING_TTL_SECONDS,
        pending_limit: int = _PENDING_LIMIT,
    ) -> None:
        """Create an empty router.

        Args:
            pending_ttl: seconds an unbound identity keeps buffered pushes.
            pending_limit: maximum buffered pushes per unbound identity.
        """
        self._pending_ttl = pending_ttl
        self._pending_limit = pending_limit
        self._subscriptions: dict[str, WebhookSubscription] = {}
        self._by_task: dict[str, str] = {}
        self._by_context: dict[str, str] = {}
        self._pending: dict[str, list[tuple[float, dict[str, object]]]] = {}

    @property
    def active_subscriptions(self) -> int:
        """Number of runs currently subscribed."""
        return len(self._subscriptions)

    def subscribe(self, scope: str = "") -> WebhookSubscription:
        """Create and register the queue of one run.

        Every call gets a distinct subscription even when two runs report the same scope: a copied
        run id must not silently steal another run's callback stream.

        Args:
            scope: run identifier used in logs; a random one is generated when empty.

        Returns:
            The run's subscription; the caller must :meth:`unsubscribe` it when the run ends.
        """
        # Dashed UUID form: the A2A-T negotiation validator accepts a UUID context id, and the
        # subscription key doubles as the client-assigned context id of the run.
        key = str(uuid.uuid4())
        subscription = WebhookSubscription(scope=scope.strip() or key, key=key, router=self)
        self._subscriptions[key] = subscription
        return subscription

    def unsubscribe(self, subscription: WebhookSubscription) -> None:
        """Remove one run and release its identities.

        Pushes of a released identity stay buffered (bounded by the pending TTL) instead of being
        dropped: the run that continues the same Task — a Modify — is the next explicit owner, and
        it binds the identity before it sends.
        """
        if subscription.closed:
            return
        subscription.closed = True
        self._requeue(subscription)
        self._subscriptions.pop(subscription.key, None)
        for task_id in subscription.task_ids:
            # Another run may already have taken this identity over; never release its mapping.
            if self._by_task.get(task_id) == subscription.key:
                self._by_task.pop(task_id, None)
        for context_id in subscription.context_ids:
            if self._by_context.get(context_id) == subscription.key:
                self._by_context.pop(context_id, None)

    def bind(
        self,
        subscription: WebhookSubscription,
        *,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> None:
        """Associate a run with a task/context identity it is about to use.

        The transport binds before it sends (a Modify request already names its Task) and again from
        the synchronous RPC reply, so a push that beats the reply is never orphaned. Buffered pushes
        for those identities are delivered immediately, in arrival order; an identity a live run
        still owns is handed over to this one.
        """
        if subscription.closed:
            return
        identities = (
            ("task", _text(task_id), self._by_task, subscription.task_ids),
            ("context", _text(context_id), self._by_context, subscription.context_ids),
        )
        for kind, value, index, owned in identities:
            if not value or value in owned:
                continue
            owned.add(value)
            previous = index.get(value)
            if previous is not None and previous != subscription.key:
                # A continued Task (Modify) belongs to the run that most recently named it: hand the
                # identity over instead of letting the previous run keep consuming its pushes.
                stale = self._subscriptions.get(previous)
                if stale is not None and kind == "task":
                    stale.task_ids.discard(value)
                elif stale is not None:
                    stale.context_ids.discard(value)
                if stale is not None and not stale.closed:
                    _LOGGER.info(
                        "webhook identity handed over: %s -> run %s (was run %s)",
                        _identity_key(kind, value),
                        subscription.scope,
                        stale.scope,
                    )
            index[value] = subscription.key
            _LOGGER.info("webhook bind: %s -> run %s", _identity_key(kind, value), subscription.scope)
            self._flush(_identity_key(kind, value), subscription)

    def on_event(self, payload: dict[str, object]) -> None:
        """Route one authenticated push to the queue that owns its identity.

        Every identity the payload carries is tried: a Modify run binds its context before it sends
        and its Task on the reply, so a push in between must be routed by whichever of the two a
        live run already owns. A push with no live owner is buffered until a run binds it. Only a
        push that carries no identity at all may use the single-subscription fallback: an
        identity-bearing push is never guessed into the one active run, because a late push from a
        previous Task would otherwise cross into an unrelated run.
        """
        self._prune()
        task_id, context_id = _payload_keys(payload)
        task_key = _identity_key("task", task_id) if task_id else ""
        context_key = _identity_key("context", context_id) if context_id else ""

        for key, index, value in (
            (task_key, self._by_task, task_id),
            (context_key, self._by_context, context_id),
        ):
            if not key:
                continue
            owner_key = index.get(value)
            owner = self._subscriptions.get(owner_key) if owner_key else None
            if owner is not None and not owner.closed:
                self._deliver(owner, payload)
                return

        key = task_key or context_key
        if not key:
            if len(self._subscriptions) == 1:
                owner = next(iter(self._subscriptions.values()))
                self._deliver(owner, payload)
                return
            _LOGGER.warning(
                "webhook push carries neither taskId nor contextId while %d runs are active; dropped",
                len(self._subscriptions),
            )
            return
        _LOGGER.info("webhook push buffered: no run owns %s yet", key)
        self._pend(key, payload)

    def _deliver(self, subscription: WebhookSubscription, payload: dict[str, object]) -> None:
        """Put one payload on the queue of a live run."""
        if subscription.closed:
            return
        subscription.queue.put_nowait(payload)

    def _requeue(self, subscription: WebhookSubscription) -> None:
        """Return the pushes a stopping run never consumed to the unbound buffer.

        A run stopped for a handover can already hold events it did not read (it was waiting on its
        queue when the previous phase ended). Dropping them with its queue loses exactly the pushes
        the next run continues with; they are buffered under their identity instead and delivered
        when that run binds it. An event with no identity cannot be routed, so it is dropped.
        """
        while True:
            try:
                payload = subscription.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            task_id, context_id = _payload_keys(payload)
            if task_id:
                key = _identity_key("task", task_id)
            elif context_id:
                key = _identity_key("context", context_id)
            else:
                _LOGGER.warning("unconsumed webhook push without identity dropped on unsubscribe")
                continue
            _LOGGER.info("webhook push re-buffered on unsubscribe: %s", key)
            self._pend(key, payload)

    def _pend(self, key: str, payload: dict[str, object]) -> None:
        """Buffer one unbound push until a run binds its identity."""
        bucket = self._pending.setdefault(key, [])
        if len(bucket) >= self._pending_limit:
            dropped = bucket.pop(0)[1]
            _LOGGER.warning(
                "webhook pending buffer for %s is full; dropped the oldest push (%s)",
                key,
                next(iter(dropped), "?"),
            )
        bucket.append((time.monotonic(), payload))

    def _flush(self, key: str, subscription: WebhookSubscription) -> None:
        """Deliver every buffered push of one newly bound identity."""
        bucket = self._pending.pop(key, [])
        for _, payload in bucket:
            self._deliver(subscription, payload)

    def _prune(self) -> None:
        """Expire buffered pushes of identities no run bound."""
        now = time.monotonic()
        for key, bucket in list(self._pending.items()):
            kept = [(stamp, payload) for stamp, payload in bucket if now - stamp < self._pending_ttl]
            if kept:
                self._pending[key] = kept
            else:
                self._pending.pop(key, None)


@dataclass
class WebhookSubscription:
    """One run's private event queue and the identities bound to it."""

    scope: str
    router: WebhookRouter = field(repr=False)
    key: str = ""
    queue: asyncio.Queue[dict[str, object]] = field(default_factory=asyncio.Queue)
    task_ids: set[str] = field(default_factory=set)
    context_ids: set[str] = field(default_factory=set)
    closed: bool = False

    def bind(self, task_id: str | None = None, context_id: str | None = None) -> None:
        """Bind the task/context identities carried by this run's RPC reply."""
        self.router.bind(self, task_id=task_id, context_id=context_id)

    def unsubscribe(self) -> None:
        """Release this subscription; late pushes for its identities are dropped."""
        self.router.unsubscribe(self)


@dataclass
class SharedWebhook:
    """One process-level receiver and the router handing each run its own queue."""

    receiver: WebhookReceiver
    router: WebhookRouter
    loop: asyncio.AbstractEventLoop

    @property
    def url(self) -> str:
        """Callback URL to register in every task push config."""
        return self.receiver.url

    def subscribe(self, scope: str = "") -> WebhookSubscription:
        """Return the event queue of one run."""
        return self.router.subscribe(scope)


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
        The long-lived receiver and its per-run router.
    """
    loop = asyncio.get_running_loop()
    key = (id(loop), *_signature(config))
    existing = _shared.get(key)
    if existing is not None and not existing.loop.is_closed() and existing.receiver.serving:
        return existing
    if existing is not None:
        _shared.pop(key, None)

    router = WebhookRouter()

    def _make_receiver(port: int) -> WebhookReceiver:
        """Build the receiver for one callback port."""
        return WebhookReceiver(
            token=config.webhook_token,
            on_event=router.on_event,
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
    shared = SharedWebhook(receiver=receiver, router=router, loop=loop)
    _shared[key] = shared
    _LOGGER.info("shared webhook ready: url=%s", shared.url)
    return shared


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
    "WebhookRouter",
    "WebhookSubscription",
    "close_shared_webhooks",
    "get_shared_webhook",
]
