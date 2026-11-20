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

"""Shared pytest fixtures: OTel global isolation for per-test providers.

The opentelemetry-api set_tracer_provider / set_meter_provider are once-per-process;
per-test fixtures that create fresh providers must reset these globals first.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


def _reset_once(lock: object) -> None:
    if lock is None:
        return
    for attr in ("_set", "_done"):
        if hasattr(lock, attr):
            setattr(lock, attr, False)  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _reset_otel_globals() -> Iterator[None]:
    import opentelemetry.metrics._internal as metrics_internal
    import opentelemetry.trace as trace_api

    trace_api._TRACER_PROVIDER = None  # type: ignore[assignment]
    # The meter globals live on the private _internal module; the public
    # opentelemetry.metrics package does not re-export them.
    metrics_internal._METER_PROVIDER = None  # type: ignore[assignment]
    _reset_once(getattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", None))
    _reset_once(getattr(metrics_internal, "_METER_PROVIDER_SET_ONCE", None))
    yield
