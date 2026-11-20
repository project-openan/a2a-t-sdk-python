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

"""Explicit API registry of the corpus engine (port of the Java ``ApiRegistry``).

Registration is declarative (never reflective): the wire API names of the shared corpus contract
(camelCase Java facade names) map to Python handlers binding the JSON step arguments to the Python
facades. Adding a new API is one registry line; unknown method names fail at load time.
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = ["ApiHandler", "ApiRegistry"]

#: One registered API: receives the resolved JSON arguments, returns the serializable payload.
ApiHandler = Callable[[dict[str, object]], dict[str, object]]


class ApiRegistry:
    """Explicit map from corpus API names to their handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, ApiHandler] = {}

    def register(self, api_name: str, handler: ApiHandler) -> "ApiRegistry":
        """Add one API handler; duplicate registration is a programming error."""
        if api_name in self._handlers:
            raise RuntimeError(f"API registered twice: {api_name}")
        self._handlers[api_name] = handler
        return self

    def handler(self, api_name: str) -> ApiHandler | None:
        """Return the handler of one API name, or ``None`` when unregistered."""
        return self._handlers.get(api_name)

    def api_names(self) -> frozenset[str]:
        """Return the registered API names."""
        return frozenset(self._handlers)
