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

"""Structured serialization of thrown errors (port of the Java ``ThrowableSerializer``).

The transcript contract requires every inspectable property of a failure: exception class, the
machine-readable catalog code for :class:`~a2a_t.core.errors.exceptions.A2ATError` instances, the
message, the instance state and the explicit cause chain (depth-limited). Python needs no
reflection: the ``A2ATError`` tree carries its state on the instance, so a small whitelist plus
``vars()`` covers the contract.
"""

from __future__ import annotations

from typing import Any, Final

from a2a_t.core.errors.exceptions import A2ATError

__all__ = ["to_map"]

#: Instance-state keys surfaced through dedicated transcript keys instead of the properties map.
_EXCLUDED_PROPERTIES: Final[frozenset[str]] = frozenset({"code", "args"})

#: Maximum depth of the serialized cause chain.
_MAX_CAUSE_DEPTH: Final[int] = 3


def to_map(error: BaseException) -> dict[str, object]:
    """Serialize one thrown error into the transcript structure."""
    serialized: dict[str, object] = {}
    _fill(serialized, error, 0)
    return serialized


def _fill(target: dict[str, object], error: BaseException, depth: int) -> None:
    target["exception"] = _qualified_name(error)
    target["message"] = str(error) or None
    if isinstance(error, A2ATError):
        target["errorCode"] = error.code_str
    target["properties"] = _properties_of(error)
    cause = error.__cause__ if error.__cause__ is not None else error.__context__
    if cause is not None and depth < _MAX_CAUSE_DEPTH:
        cause_map: dict[str, object] = {}
        _fill(cause_map, cause, depth + 1)
        target["cause"] = cause_map


def _properties_of(error: BaseException) -> dict[str, object]:
    """Collect the instance state of one exception, excluding the transcript-carried keys."""
    try:
        fields = vars(error)
    except TypeError:
        return {}
    properties: dict[str, object] = {}
    for key, value in fields.items():
        if key in _EXCLUDED_PROPERTIES or key.startswith("_"):
            continue
        if value is None:
            continue
        properties[key] = _to_serializable(value)
    return properties


def _to_serializable(value: Any) -> Any:
    """Render one instance-state value into a JSON-serializable form."""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, BaseException):
        return _qualified_name(value)
    if isinstance(value, dict):
        return {str(key): _to_serializable(entry) for key, entry in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_serializable(entry) for entry in value]
    return str(value)


def _qualified_name(error: BaseException) -> str:
    """Return the qualified class name of one exception."""
    return f"{type(error).__module__}.{type(error).__qualname__}"
