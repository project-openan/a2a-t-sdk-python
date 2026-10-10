"""Client factory registration for automatic transport decoration (Task 9 deliverable).

``register_client_factory(factory)`` makes every client produced by an
a2a-sdk ``ClientFactory`` carry an :class:`A2ATClientTransportDecorator` as its
transport, so client spans/metrics/logs are created automatically with
a2a-java-parity naming — no per-call wiring needed.

Verified against a2a-sdk 1.1.2 structural reality:

- ``ClientFactory.create(card, interceptors=None)`` returns a ``BaseClient``;
- ``BaseClient.__init__`` stores its transport as ``client._transport``.

The wrapper is installed as an instance attribute on the factory (``factory.create
= observed``), never on the class, so other ClientFactory instances are untouched.
Calling ``register_client_factory`` twice is safe: a client whose transport is
already an ``A2ATClientTransportDecorator`` is returned as-is (no double wrap).
"""

from __future__ import annotations

from typing import Any

from a2a_t.observability.client.transport_decorator import A2ATClientTransportDecorator
from a2a_t.observability.config import A2ATObservabilityConfig

__all__ = ["register_client_factory"]


def register_client_factory(factory: Any, *, config: A2ATObservabilityConfig | None = None) -> None:
    """Wrap ``factory.create`` so produced clients get an observed transport.

    After registration, ``factory.create(card)`` returns a client whose transport
    is wrapped in :class:`A2ATClientTransportDecorator` — all client spans are
    created automatically with a2a-java-parity naming. ``config`` (optional) is
    forwarded to the decorator; the inner-transport``create`` flow (interceptors,
    tenant decorators) is untouched.
    """
    original_create = factory.create

    def _observed_create(card: Any, interceptors: Any = None) -> Any:
        client = original_create(card, interceptors)
        inner_transport = getattr(client, "_transport", None)
        if inner_transport is not None and not isinstance(inner_transport, A2ATClientTransportDecorator):
            client._transport = A2ATClientTransportDecorator(inner_transport, config=config)
        return client

    factory.create = _observed_create
