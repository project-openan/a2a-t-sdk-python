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

"""W3C TraceContext propagation helpers built on the opentelemetry-api default propagator."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from a2a_t.observability import _otel_compat

TRACEPARENT_HEADER = "traceparent"


def inject_traceparent(headers: MutableMapping[str, str], *, context: Any | None = None) -> None:
    """Inject traceparent of the current (or given) context into headers; NoOp when disabled."""
    if not _otel_compat.is_enabled():
        return
    from opentelemetry.propagate import inject

    try:
        if context is None:
            inject(headers)
        else:
            inject(headers, context=context)
    except Exception:  # noqa: BLE001
        pass


def extract_trace_context(headers: Mapping[str, str]) -> Any | None:
    """Extract an opaque OTel Context from headers (case-insensitive); None when absent."""
    if not _otel_compat.is_enabled():
        return None
    from opentelemetry.propagate import extract

    try:
        lowered = {str(k).lower(): str(v) for k, v in headers.items()}
        context = extract(carrier=lowered)
    except Exception:  # noqa: BLE001
        return None
    span = _otel_compat.otel_trace.get_current_span(context)
    span_context = span.get_span_context()
    if span_context is None or not getattr(span_context, "is_valid", False):
        return None
    return context
