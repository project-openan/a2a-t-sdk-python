# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""register_client_factory tests (a2a-free: duck-typed factory mirroring ClientFactory.create)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from a2a_t.observability.client.factory import register_client_factory
from a2a_t.observability.client.transport_decorator import A2ATClientTransportDecorator
from a2a_t.observability.config import A2ATObservabilityConfig


class _FakeFactory:
    """Duck-typed ClientFactory: create(card, interceptors=None) -> client with _transport."""

    def __init__(self) -> None:
        self.transport = SimpleNamespace(name="inner-transport")

    def create(self, card: Any, interceptors: Any = None) -> Any:
        return SimpleNamespace(_transport=self.transport, card=card, interceptors=interceptors)


def test_register_client_factory_wraps_produced_transport() -> None:
    factory = _FakeFactory()
    config = A2ATObservabilityConfig()
    register_client_factory(factory, config=config)

    client = factory.create("agent-card", interceptors=["i1"])

    assert isinstance(client._transport, A2ATClientTransportDecorator)
    assert client._transport._inner is factory.transport
    assert client._transport._config is config
    assert client.interceptors == ["i1"]


def test_register_client_factory_double_register_does_not_double_wrap() -> None:
    factory = _FakeFactory()
    register_client_factory(factory)
    first = factory.create("agent-card")
    assert isinstance(first._transport, A2ATClientTransportDecorator)

    register_client_factory(factory)
    second = factory.create("agent-card")

    assert isinstance(second._transport, A2ATClientTransportDecorator)
    assert not isinstance(second._transport._inner, A2ATClientTransportDecorator)
    assert second._transport._inner is factory.transport


def test_register_client_factory_handles_transportless_client() -> None:
    """A client without _transport (structural contract drift) passes through untouched."""
    factory = _FakeFactory()

    def _transportless_create(card: Any, interceptors: Any = None) -> Any:
        return SimpleNamespace(card=card)

    factory.create = _transportless_create  # type: ignore[method-assign]
    register_client_factory(factory)

    client = factory.create("agent-card")

    assert not hasattr(client, "_transport")
    assert client.card == "agent-card"
